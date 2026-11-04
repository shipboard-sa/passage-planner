"""Tests for backend/main.py's /api/brief advanced options (no briefing is built:
run_brief is replaced by a fake that records what the form handed it)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backend.main as backend  # noqa: E402
from passage_planner.errors import RoutingError  # noqa: E402

GPX = (ROOT / "tests" / "fixtures" / "nola_to_gulfport.gpx").read_bytes()


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake(args: SimpleNamespace) -> Any:
        seen["args"] = args
        # the uploads must exist while the run is in progress
        seen["contents"] = {
            k: getattr(args, k).read_text(encoding="utf-8")
            for k in ("papers_file", "depths_file", "ciq_file", "comms_config")
            if getattr(args, k) is not None
        }
        raise RoutingError("stop here")

    monkeypatch.setattr(backend, "run_brief", fake)
    return seen


def _post(data: dict[str, str], files: dict[str, tuple[str, bytes]] | None = None) -> Any:
    body = {"route": ("r.gpx", GPX), **(files or {})}
    return TestClient(backend.app).post(
        "/api/brief", data={"depart": "2026-10-05T07:00", "crew": "Skipper", **data}, files=body
    )


def test_defaults_match_the_cli_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture(monkeypatch)
    assert _post({}).status_code == 400  # our fake stops the run
    a = seen["args"]
    assert (a.compare, a.no_tropical, a.no_tides, a.no_notices) == (False,) * 4
    assert (a.no_maintenance, a.no_papers, a.no_advisories) == (False, False, False)
    assert a.papers_file is a.depths_file is a.ciq_file is a.comms_config is None


def test_switches_and_override_files_reach_run_brief_then_are_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture(monkeypatch)
    resp = _post(
        {"compare": "true", "no_tropical": "true", "no_tides": "true",
         "no_notices": "true", "no_maintenance": "true", "no_papers": "true",
         "no_advisories": "true"},
        {
            "papers_file": ("papers.toml", b"# papers"),
            "depths_file": ("depths.toml", b"# depths"),
            "ciq_file": ("ciq.md", b"# ciq"),
            "comms_config": ("comms.toml", b"# comms"),
        },
    )
    assert resp.status_code == 400
    a = seen["args"]
    assert (a.compare, a.no_tropical, a.no_tides, a.no_notices) == (True,) * 4
    assert (a.no_maintenance, a.no_papers, a.no_advisories) == (True, True, True)
    assert seen["contents"] == {
        "papers_file": "# papers", "depths_file": "# depths",
        "ciq_file": "# ciq", "comms_config": "# comms",
    }
    for k in ("papers_file", "depths_file", "ciq_file", "comms_config"):
        assert not getattr(a, k).exists(), f"{k} upload was not removed"


def test_upload_names_cannot_escape_the_upload_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture(monkeypatch)
    _post({}, {"papers_file": ("../../evil.toml", b"# x")})
    assert seen["args"].papers_file.parent == backend.UPLOAD_DIR


def test_parse_roles_accepts_known_roles_and_rejects_the_rest() -> None:
    from fastapi import HTTPException

    assert backend._parse_roles(None) == {}
    assert backend._parse_roles("  ") == {}
    parsed = backend._parse_roles('{"West End": "check-in", "Nassau": ""}')
    assert parsed == {"West End": "check-in"}
    for bad in ("not json", "[1]", '{"A": 3}', '{"A": "party"}'):
        with pytest.raises(HTTPException) as exc:
            backend._parse_roles(bad)
        assert exc.value.status_code == 400
