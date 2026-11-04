"""Tests for passage_planner.research_jobs and the cancel path of research.py
(no Claude CLI is ever launched; the subprocess tests run a tiny Python child)."""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from passage_planner import config
from passage_planner.errors import ResearchUnavailableError
from passage_planner.research import (
    CANCELLED,
    ENV_TOKEN,
    PortResult,
    RunOutput,
    RunRequest,
    incomplete_section,
    research_port,
    run_subprocess,
)
from passage_planner.research_jobs import ResearchBusyError, ResearchJobs

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
EXE = Path("claude.exe")


def _vessel() -> config.VesselConfig:
    return config.load(CONFIG_DIR).vessel


def _ok(port: str) -> PortResult:
    return PortResult(
        port=port, status="ok", markdown=f"### {port}\n- **Fuel:** diesel.\n",
        duration_s=60.0, cost_usd=0.4, turns=15,
    )


def _failed(port: str, reason: str) -> PortResult:
    return PortResult(
        port=port, status="failed", error=reason,
        markdown=incomplete_section(port, reason, datetime.now(UTC)),
    )


def _jobs(tmp_path: Path, research: Callable[..., PortResult]) -> ResearchJobs:
    return ResearchJobs(tmp_path / "logistics", research=research)


def _start(jobs: ResearchJobs, ports: list[str]) -> str:
    return jobs.start(ports, vessel=_vessel(), exe=EXE)


def _snap(jobs: ResearchJobs, job_id: str) -> dict[str, Any]:
    snap = jobs.snapshot(job_id)
    assert snap is not None
    return snap


def _statuses(jobs: ResearchJobs, job_id: str) -> list[str]:
    return [p["status"] for p in _snap(jobs, job_id)["ports"]]


def _wait_for(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        time.sleep(0.01)
    assert pred(), "condition not reached in time"


# --- the state machine --------------------------------------------------------


def test_job_runs_ports_in_order_and_saves_each_ok_result(tmp_path: Path) -> None:
    order: list[str] = []

    def fake(port: str, **kw: Any) -> PortResult:
        order.append(port)
        return _ok(port)

    jobs = _jobs(tmp_path, fake)
    jid = _start(jobs, ["Spanish Wells", "  ", "West End", "Spanish Wells"])
    jobs.wait(jid)
    snap = _snap(jobs, jid)
    assert order == ["Spanish Wells", "West End"]  # blank and duplicate dropped
    assert snap["status"] == "done" and snap["finished_at"]
    assert _statuses(jobs, jid) == ["ok", "ok"]
    assert snap["total_cost_usd"] == pytest.approx(0.8)
    files = sorted((tmp_path / "logistics").glob("research_*.md"))
    assert [f.name for f in files] == [
        f"research_{jid}_spanish_wells.md", f"research_{jid}_west_end.md",
    ]
    assert files[0].read_text(encoding="utf-8").startswith("### Spanish Wells")
    assert snap["ports"][0]["saved_as"] == files[0].name


def test_a_failed_port_is_kept_as_a_placeholder_and_others_continue(tmp_path: Path) -> None:
    def fake(port: str, **kw: Any) -> PortResult:
        return _failed(port, "usage limit reached") if port == "B" else _ok(port)

    jobs = _jobs(tmp_path, fake)
    jid = _start(jobs, ["A", "B", "C"])
    jobs.wait(jid)
    snap = _snap(jobs, jid)
    assert snap["status"] == "done"
    assert _statuses(jobs, jid) == ["ok", "failed", "ok"]
    assert "RESEARCH INCOMPLETE" in snap["ports"][1]["markdown"]
    assert snap["ports"][1]["error"] == "usage limit reached"
    assert snap["ports"][1]["saved_as"] is None  # nothing worth saving
    assert len(list((tmp_path / "logistics").glob("research_*.md"))) == 2


def test_only_one_job_runs_at_a_time(tmp_path: Path) -> None:
    gate = threading.Event()

    def fake(port: str, **kw: Any) -> PortResult:
        gate.wait(5)
        return _ok(port)

    jobs = _jobs(tmp_path, fake)
    first = _start(jobs, ["A"])
    with pytest.raises(ResearchBusyError):
        _start(jobs, ["B"])
    gate.set()
    jobs.wait(first)
    second = _start(jobs, ["B"])  # free again once the first is done
    jobs.wait(second)
    assert _snap(jobs, second)["status"] == "done"


def test_start_needs_at_least_one_port(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _start(_jobs(tmp_path, lambda p, **kw: _ok(p)), [" ", ""])


def test_cancel_keeps_finished_ports_and_skips_the_rest(tmp_path: Path) -> None:
    def fake(port: str, **kw: Any) -> PortResult:
        if port == "A":
            return _ok(port)
        kw["cancel"].wait(5)  # a real run is killed when this event is set
        return _failed(port, CANCELLED)

    jobs = _jobs(tmp_path, fake)
    jid = _start(jobs, ["A", "B", "C"])
    _wait_for(lambda: _statuses(jobs, jid) == ["ok", "running", "pending"])
    assert jobs.cancel(jid) is True
    jobs.wait(jid)
    snap = _snap(jobs, jid)
    assert snap["status"] == "cancelled"
    assert _statuses(jobs, jid) == ["ok", "cancelled", "cancelled"]
    assert snap["ports"][0]["saved_as"]  # the finished port survives


def test_unknown_job_ids_are_reported(tmp_path: Path) -> None:
    jobs = _jobs(tmp_path, lambda p, **kw: _ok(p))
    assert jobs.cancel("nope") is False
    assert jobs.snapshot("nope") is None


def test_feature_becoming_unavailable_stops_the_job_with_the_reason(tmp_path: Path) -> None:
    def fake(port: str, **kw: Any) -> PortResult:
        if port == "A":
            return _ok(port)
        raise ResearchUnavailableError("CLAUDE_CODE_OAUTH_TOKEN is not set")

    jobs = _jobs(tmp_path, fake)
    jid = _start(jobs, ["A", "B", "C"])
    jobs.wait(jid)
    snap = _snap(jobs, jid)
    assert snap["status"] == "error" and "TOKEN" in snap["error"]
    assert _statuses(jobs, jid) == ["ok", "failed", "cancelled"]


def test_an_unexpected_crash_in_the_worker_is_reported_not_swallowed(tmp_path: Path) -> None:
    def fake(port: str, **kw: Any) -> PortResult:
        raise RuntimeError("boom")

    jobs = _jobs(tmp_path, fake)
    jid = _start(jobs, ["A", "B"])
    jobs.wait(jid)
    snap = _snap(jobs, jid)
    assert snap["status"] == "error" and "boom" in snap["error"]
    # nothing is left looking "running" forever
    assert _statuses(jobs, jid) == ["cancelled", "cancelled"]


def test_a_disk_error_saving_does_not_lose_the_result(tmp_path: Path) -> None:
    (tmp_path / "logistics").write_text("a file where the folder should be")
    jobs = _jobs(tmp_path, lambda p, **kw: _ok(p))
    jid = _start(jobs, ["A"])
    jobs.wait(jid)
    port = _snap(jobs, jid)["ports"][0]
    assert port["status"] == "ok" and port["saved_as"] is None
    assert port["markdown"].startswith("### A")


# --- cancelling research_port / the real subprocess ---------------------------


def test_research_port_short_circuits_if_already_cancelled() -> None:
    called: list[RunRequest] = []

    def run(req: RunRequest) -> RunOutput:
        called.append(req)
        return RunOutput(0, "{}", "")

    ev = threading.Event()
    ev.set()
    result = research_port(
        "X", vessel=_vessel(), exe=EXE, env={ENV_TOKEN: "t"}, run=run, cancel=ev,
    )
    assert result.status == "failed" and result.error == CANCELLED
    assert called == []


def test_research_port_maps_a_cancelled_run_to_a_cancelled_failure() -> None:
    ev = threading.Event()

    def run(req: RunRequest) -> RunOutput:
        assert req.cancel is ev  # handed through to the process runner
        return RunOutput(-1, "", "", cancelled=True)

    result = research_port(
        "X", vessel=_vessel(), exe=EXE, env={ENV_TOKEN: "t"}, run=run, cancel=ev,
    )
    assert result.status == "failed" and result.error == CANCELLED
    assert "RESEARCH INCOMPLETE" in result.markdown


def _py(code: str, *, cancel: threading.Event | None, timeout: float = 20.0) -> RunRequest:
    return RunRequest(
        (sys.executable, "-c", code), "hello", dict(os.environ), Path.cwd(), timeout, cancel,
    )


def test_run_subprocess_normal_run_still_round_trips_stdin() -> None:
    out = run_subprocess(
        _py("import sys; sys.stdout.write(sys.stdin.read().upper())", cancel=threading.Event())
    )
    assert out.returncode == 0 and out.stdout == "HELLO"
    assert not out.cancelled and not out.timed_out


def test_run_subprocess_cancel_kills_the_child_promptly() -> None:
    ev = threading.Event()
    threading.Timer(0.6, ev.set).start()
    started = time.monotonic()
    out = run_subprocess(_py("import time; time.sleep(60)", cancel=ev))
    assert out.cancelled and not out.timed_out
    assert time.monotonic() - started < 10  # nowhere near the 60 s sleep


def test_run_subprocess_still_times_out() -> None:
    out = run_subprocess(
        _py("import time; time.sleep(60)", cancel=threading.Event(), timeout=1.2)
    )
    assert out.timed_out and not out.cancelled


def test_each_port_is_researched_with_its_own_stop_role(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fake(port: str, **kw: Any) -> PortResult:
        seen[port] = kw.get("role")
        return _ok(port)

    jobs = _jobs(tmp_path, fake)
    jid = jobs.start(
        ["West End", "Salt Pond", "Nassau"], vessel=_vessel(), exe=EXE,
        roles={"West End": "check-in", "Salt Pond": "check-out"},
    )
    jobs.wait(jid)
    assert seen == {"West End": "check-in", "Salt Pond": "check-out", "Nassau": None}
    roles = {p["name"]: p["role"] for p in _snap(jobs, jid)["ports"]}
    assert roles == {"West End": "check-in", "Salt Pond": "check-out", "Nassau": None}
