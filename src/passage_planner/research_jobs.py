"""In-memory registry for background shore-logistics research jobs.

Purpose: Run research_port() once per port, sequentially, on a background
    thread; expose progress as plain dicts for the web UI to poll; support
    cancelling a running job; and write each finished port to disk the moment it
    lands so a server restart or closed tab never loses paid-for research.
Version: v1 (2026-09-21) - state machine; backend/main.py exposes it as
    /api/logistics-research and frontend/src/ResearchPanel.tsx drives it.
Scope: PRODUCTION - passage-planner feature (live-verified with a fake researcher;
    not yet run end to end against the real CLI through the endpoints).
Architecture: passage-planner support module between research.py (one port, one
    subprocess) and backend/main.py (HTTP). Lives in src/ rather than backend/
    so the pytest/mypy gate covers it (backend/ is not in that gate). The
    per-port work is the injectable ``research`` callable, so tests use a fake
    and never launch Claude.

Rules encoded here (design page: wiki/Logistics-Research-Design.md):

* One port per subprocess, sequential, so a failure or the usage cap costs only
  that port and the finished ones are kept.
* One active job at a time (:class:`ResearchBusyError`, the web layer's 409).
* Cancel sets a shared event; the running port's process tree is killed, the
  ports not yet started are marked ``cancelled``, finished ports are kept.
* Only ``ok`` results are written to ``var/logistics/research_<id>_<slug>.md``.
  A failed port is an incomplete placeholder with nothing worth saving.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .config import VesselConfig
from .errors import ResearchError, ResearchUnavailableError
from .research import CANCELLED, PortResult, research_port
from .route import Route
from .service import slug

PortStatus = Literal["pending", "running", "ok", "failed", "cancelled"]
JobStatus = Literal["running", "done", "cancelled", "error"]

# Signature of research_port(); a test passes a fake with the same shape.
ResearchFn = Callable[..., PortResult]


class ResearchBusyError(ResearchError):
    """A research job is already running; only one may be active at a time."""


@dataclass(slots=True)
class PortJob:
    name: str
    role: str | None = None  # why the boat stops here: a research.STOP_ROLES key
    status: PortStatus = "pending"
    result: PortResult | None = None
    saved_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        r = self.result
        return {
            "name": self.name,
            "role": self.role,
            "status": self.status,
            "markdown": r.markdown if r else "",
            "gaps": list(r.gaps) if r else [],
            "resolved_as": r.resolved_as if r else None,
            "error": r.error if r else None,
            "warnings": list(r.warnings) if r else [],
            "seconds": r.duration_s if r else 0.0,
            "cost_usd": r.cost_usd if r else 0.0,
            "turns": r.turns if r else None,
            "usage": dict(r.usage) if r else {},
            "saved_as": self.saved_path.name if self.saved_path else None,
        }


@dataclass(slots=True)
class ResearchJob:
    id: str
    ports: list[PortJob]
    started_at: datetime
    status: JobStatus = "running"
    error: str | None = None
    finished_at: datetime | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class ResearchJobs:
    """The job index. Single-user LAN tool, so it is one object in the backend
    process; a restart loses the index but not the saved port files."""

    def __init__(
        self,
        out_dir: Path,
        *,
        research: ResearchFn = research_port,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._out_dir = out_dir
        self._research = research
        self._now = now or (lambda: datetime.now(UTC))
        self._jobs: dict[str, ResearchJob] = {}
        self._lock = threading.Lock()

    # -- starting and stopping ------------------------------------------------

    def start(
        self,
        ports: list[str],
        *,
        vessel: VesselConfig,
        exe: Path,
        route: Route | None = None,
        crew_count: int | None = None,
        env: Mapping[str, str] | None = None,
        roles: Mapping[str, str] | None = None,
    ) -> str:
        """Start a job for ``ports`` (blank and duplicate names dropped, order
        kept) and return its id. ``roles`` maps a port name to why the boat stops
        there (a ``research.STOP_ROLES`` key); ports it omits are ordinary stops.
        Raises :class:`ResearchBusyError` if one is running and ``ValueError`` if
        there is nothing to research."""
        names = list(dict.fromkeys(p.strip() for p in ports if p.strip()))
        if not names:
            raise ValueError("give at least one port")
        with self._lock:
            if any(j.status == "running" for j in self._jobs.values()):
                raise ResearchBusyError("a research job is already running")
            job = ResearchJob(
                id=uuid.uuid4().hex[:12],
                ports=[PortJob(n, (roles or {}).get(n)) for n in names],
                started_at=self._now(),
            )
            job.thread = threading.Thread(
                target=self._run,
                args=(job, vessel, exe, route, crew_count, env),
                name=f"research-{job.id}",
                daemon=True,
            )
            self._jobs[job.id] = job
            job.thread.start()
        return job.id

    def cancel(self, job_id: str) -> bool:
        """Ask a running job to stop. ``False`` if the id is unknown; cancelling a
        job that already finished is a harmless no-op that returns ``True``."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        job.cancel.set()
        return True

    # -- reading --------------------------------------------------------------

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        with self._lock:
            ports = [p.to_dict() for p in job.ports]
            finished = job.finished_at
            return {
                "id": job.id,
                "status": job.status,
                "error": job.error,
                "started_at": job.started_at.isoformat(),
                "finished_at": finished.isoformat() if finished else None,
                "total_cost_usd": round(sum(p["cost_usd"] for p in ports), 4),
                "total_seconds": round(sum(p["seconds"] for p in ports), 1),
                "ports": ports,
            }

    def wait(self, job_id: str, timeout: float = 5.0) -> None:
        """Block until the job's thread ends (tests; the web layer polls instead)."""
        job = self._jobs.get(job_id)
        if job is not None and job.thread is not None:
            job.thread.join(timeout)

    # -- the worker thread ----------------------------------------------------

    def _finish(self, job: ResearchJob, status: JobStatus, error: str | None = None) -> None:
        with self._lock:
            job.status = status
            job.error = error
            job.finished_at = self._now()

    def _save(self, job: ResearchJob, name: str, markdown: str) -> Path | None:
        """Write an ``ok`` port's Markdown to disk at once. A disk problem must not
        lose the in-memory result, so it is swallowed (the panel still has it)."""
        try:
            self._out_dir.mkdir(parents=True, exist_ok=True)
            path = self._out_dir / f"research_{job.id}_{slug(name)}.md"
            path.write_text(markdown, encoding="utf-8")
        except OSError:
            return None
        return path

    def _run(
        self,
        job: ResearchJob,
        vessel: VesselConfig,
        exe: Path,
        route: Route | None,
        crew_count: int | None,
        env: Mapping[str, str] | None,
    ) -> None:
        try:
            for port in job.ports:
                if job.cancel.is_set():
                    with self._lock:
                        port.status = "cancelled"
                    continue
                with self._lock:
                    port.status = "running"
                try:
                    result = self._research(
                        port.name, vessel=vessel, exe=exe, route=route,
                        crew_count=crew_count, env=env, cancel=job.cancel,
                        role=port.role,
                    )
                except ResearchUnavailableError as exc:
                    # The feature cannot run at all (token gone): stop, and say so.
                    with self._lock:
                        port.status = "failed"
                    self._finish(job, "error", str(exc))
                    self._cancel_rest(job)
                    return
                saved = (
                    self._save(job, port.name, result.markdown) if result.status == "ok" else None
                )
                with self._lock:
                    port.result = result
                    port.saved_path = saved
                    if result.status == "ok":
                        port.status = "ok"
                    elif result.error == CANCELLED:
                        port.status = "cancelled"
                    else:
                        port.status = "failed"
            self._finish(job, "cancelled" if job.cancel.is_set() else "done")
        except Exception as exc:  # a worker thread must never die silently
            self._finish(job, "error", f"{type(exc).__name__}: {exc}")
            self._cancel_rest(job)

    def _cancel_rest(self, job: ResearchJob) -> None:
        with self._lock:
            for p in job.ports:
                if p.status in ("pending", "running"):
                    p.status = "cancelled"

