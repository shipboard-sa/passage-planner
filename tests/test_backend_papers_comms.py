"""Tests for the /api/papers and /api/comms endpoints (the Documents and
Comms form screens - see papers/overrides.py's save_overrides() and
comms.py's save_comms_config())."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.main as backend  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client whose CONFIG_DIR is a scratch copy, so papers.toml/comms.toml
    writes never land in the repo."""
    cfg = tmp_path / "config"
    shutil.copytree(
        ROOT / "config", cfg,
        ignore=shutil.ignore_patterns("fleet.json", "papers.toml", "comms.toml"),
    )
    monkeypatch.setattr(backend, "CONFIG_DIR", cfg)
    return TestClient(backend.app)


# -- /api/papers ---------------------------------------------------------------


def test_get_papers_with_no_file_lists_every_kind_blank(client: TestClient) -> None:
    body = client.get("/api/papers").json()
    docs = body["documents"]
    assert len(docs) == 16  # every DocKind
    assert all(d["path"] == "" and d["issued"] == "" for d in docs)
    passport = next(d for d in docs if d["kind"] == "passport")
    assert passport["label"] == "Passports"


def test_save_and_reload_a_document(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/api/papers", json={"documents": [
        {"kind": "passport", "issued": "2020-05-01", "expires": "2030-05-01",
         "note": "both crew"},
    ]})
    assert resp.status_code == 200
    assert (tmp_path / "config" / "papers.toml").is_file()
    passport = next(d for d in resp.json()["documents"] if d["kind"] == "passport")
    assert passport["expires"] == "2030-05-01" and passport["note"] == "both crew"

    reloaded = client.get("/api/papers").json()
    passport2 = next(d for d in reloaded["documents"] if d["kind"] == "passport")
    assert passport2 == passport


def test_save_empty_list_clears_every_override(client: TestClient) -> None:
    client.post("/api/papers", json={"documents": [
        {"kind": "passport", "expires": "2030-05-01"},
    ]})
    cleared = client.post("/api/papers", json={"documents": []}).json()
    assert all(d["expires"] == "" for d in cleared["documents"])


def test_bad_date_is_a_400(client: TestClient) -> None:
    resp = client.post("/api/papers", json={"documents": [
        {"kind": "passport", "expires": "not a date"},
    ]})
    assert resp.status_code == 400
    assert "not an ISO date" in resp.json()["detail"]


def test_unknown_kind_is_a_400(client: TestClient) -> None:
    resp = client.post("/api/papers", json={"documents": [{"kind": "spaceship_registration"}]})
    assert resp.status_code == 400
    assert "unknown document kind" in resp.json()["detail"]


# -- /api/comms ------------------------------------------------------------


def test_get_comms_with_no_file_is_all_blank(client: TestClient) -> None:
    body = client.get("/api/comms").json()
    assert body["starlink_plan"] == "" and body["starlink_billing_cycle_day"] is None


def test_save_and_reload_comms(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/api/comms", json={
        "starlink_plan": "Personal Maritime",
        "starlink_billing_cycle_day": 24,
        "starlink_ocean_mode_usd_per_gb": 6.0,
        "tmobile_plan": "Go5G Plus",
        "tmobile_roaming_included": True,
        "maya_esim_scope": "Caribbean",
        "router_model": "Spitz AX",
    })
    assert resp.status_code == 200
    assert (tmp_path / "config" / "comms.toml").is_file()
    body = resp.json()
    assert body["starlink_plan"] == "Personal Maritime"
    assert body["starlink_billing_cycle_day"] == 24
    assert body["tmobile_roaming_included"] is True

    reloaded = client.get("/api/comms").json()
    assert reloaded == body


def test_bad_cycle_day_is_a_400(client: TestClient) -> None:
    resp = client.post("/api/comms", json={"starlink_billing_cycle_day": 40})
    assert resp.status_code == 400
    assert "1-28" in resp.json()["detail"]


def test_the_post_routes_the_web_form_uses_match_put(client: TestClient) -> None:
    # mx-inv's /routing proxy only forwards GET and POST, so the form uses these.
    saved = client.post("/api/comms", json={"starlink_plan": "Roam"})
    assert saved.status_code == 200
    saved2 = client.post("/api/papers", json={"documents": [{"kind": "passport"}]})
    assert saved2.status_code == 200
