"""Tests for the fleet endpoints and the ``fleet`` choice on the brief/research endpoints."""

from __future__ import annotations

import shutil
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
from passage_planner.research import ResearchStatus  # noqa: E402

CAT = {
    "name": "SV Salt Cat", "hull": "catamaran", "make": "Lagoon", "model": "45",
    "draft_ft": 4.0, "air_draft_ft": 62.0, "loa_ft": 45, "beam_ft": 24,
    "usual_companion": True,
}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client whose CONFIG_DIR is a scratch copy, so fleet.json never lands in the repo."""
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg, ignore=shutil.ignore_patterns("fleet.json"))
    monkeypatch.setattr(backend, "CONFIG_DIR", cfg)
    return TestClient(backend.app)


def test_get_fleet_starts_from_the_vessel_toml_boats(client: TestClient) -> None:
    body = client.get("/api/fleet").json()
    assert [b["id"] for b in body["boats"]] == ["sv_example_buddy"]
    assert body["usual"] == ["sv_example_buddy"]
    assert body["hulls"] == ["monohull", "catamaran", "trimaran"]
    sb = body["boats"][0]
    assert sb["draft_up_ft"] == 8.0 and sb["hull"] == "monohull"


def test_add_edit_rename_and_remove_a_boat(client: TestClient, tmp_path: Path) -> None:
    resp = client.put("/api/fleet", json={"boat": CAT})
    assert resp.status_code == 200
    assert (tmp_path / "config" / "fleet.json").is_file()
    ids = [b["id"] for b in resp.json()["boats"]]
    assert ids == ["sv_example_buddy", "sv_salt_cat"]  # saved list keeps the seeded boat too
    assert resp.json()["usual"] == ["sv_example_buddy", "sv_salt_cat"]

    edited = client.put("/api/fleet", json={"boat": {**CAT, "air_draft_ft": 64.0}}).json()
    assert next(b for b in edited["boats"] if b["id"] == "sv_salt_cat")["air_draft_ft"] == 64.0

    renamed = client.put(
        "/api/fleet", json={"boat": {**CAT, "name": "SV Salt Cat II"}, "replaces": "sv_salt_cat"}
    ).json()
    assert [b["id"] for b in renamed["boats"]] == ["sv_example_buddy", "sv_salt_cat_ii"]

    gone = client.delete("/api/fleet/sv_salt_cat_ii")
    assert gone.status_code == 200 and [b["id"] for b in gone.json()["boats"]] == [
        "sv_example_buddy"
    ]
    assert client.get("/api/fleet").json()["usual"] == ["sv_example_buddy"]


def test_invalid_boats_are_a_400_with_a_readable_message(client: TestClient) -> None:
    bad = {**CAT, "hull": "raft"}
    resp = client.put("/api/fleet", json={"boat": bad})
    assert resp.status_code == 400 and "hull must be one of" in resp.json()["detail"]
    missing = {"name": "SV Nothing"}
    resp = client.put("/api/fleet", json={"boat": missing})
    assert resp.status_code == 400 and "draft ft is required" in resp.json()["detail"]
    clash = client.put("/api/fleet", json={"boat": {**CAT, "name": "SV Example Buddy"},
                                           "replaces": "sv_salt_cat"})
    assert clash.status_code == 400


def test_the_post_routes_the_web_form_uses_match_put_and_delete(client: TestClient) -> None:
    # mx-inv's /routing proxy only forwards GET and POST, so the form uses these.
    saved = client.post("/api/fleet", json={"boat": CAT})
    assert saved.status_code == 200
    assert [b["id"] for b in saved.json()["boats"]] == ["sv_example_buddy", "sv_salt_cat"]
    gone = client.post("/api/fleet/sv_salt_cat/remove")
    assert gone.status_code == 200
    assert [b["id"] for b in gone.json()["boats"]] == ["sv_example_buddy"]
    assert client.post("/api/fleet/ghost/remove").status_code == 404


def test_removing_an_unknown_boat_is_a_404(client: TestClient) -> None:
    assert client.delete("/api/fleet/ghost").status_code == 404


def test_removing_the_last_boat_means_travelling_alone(client: TestClient) -> None:
    assert client.delete("/api/fleet/sv_example_buddy").status_code == 200
    body = client.get("/api/fleet").json()
    assert body["boats"] == [] and body["usual"] == []  # the empty saved file, not the seed


def test_brief_passes_the_fleet_choice_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake(args: SimpleNamespace) -> Any:
        seen["fleet"] = args.fleet
        raise RoutingError("stop here")

    monkeypatch.setattr(backend, "run_brief", fake)
    gpx = (ROOT / "tests" / "fixtures" / "nola_to_gulfport.gpx").read_bytes()
    for value in ('["sv_salt_cat"]', "[]"):
        client.post("/api/brief", data={"depart": "2026-10-05T07:00", "fleet": value},
                    files={"route": ("r.gpx", gpx)})
        assert seen["fleet"] == value
    client.post("/api/brief", data={"depart": "2026-10-05T07:00"},
                files={"route": ("r.gpx", gpx)})
    assert seen["fleet"] is None  # not given: the usual companions


# -- POST /api/fleet/lookup and polar_pol_text on save -----------------------

_ORC_TEXT = "\n".join([
    "LOA[m] 12.50",
    "BMAX[m] 3.90",
    "Draft[m] 2.20",
    "DSPL[kg] 7200",
    "twa/tws   6      10     20",
    "52        5.8    7.4    8.1",
    "90        6.2    8.4    10.4",
])
_MARINETRAFFIC_TEXT = "\n".join([
    "MMSI 000000001",
    "Call sign XYZ0000",
    "Her length overall (LOA) is 19 meters and her width is 6 meters.",
])


def test_lookup_endpoint_parses_and_merges_both_sources(client: TestClient) -> None:
    resp = client.post("/api/fleet/lookup", json={
        "marinetraffic_text": _MARINETRAFFIC_TEXT, "orc_text": _ORC_TEXT,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["mmsi"] == "000000001"
    assert body["call_sign"] == "XYZ0000"
    assert body["draft_ft"] is not None  # only ORC has draft
    assert body["polar_pol_text"] is not None
    assert "twa/tws" in body["polar_pol_text"]


def test_lookup_endpoint_with_empty_text_finds_nothing_and_does_not_error(
    client: TestClient,
) -> None:
    resp = client.post("/api/fleet/lookup", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mmsi"] == "" and body["polar_pol_text"] is None


def test_saving_with_polar_pol_text_writes_a_pol_file_and_sets_polar_file(
    client: TestClient, tmp_path: Path,
) -> None:
    pol_text = "twa/tws\t10\t20\n60\t7\t9\n120\t8\t10\n"
    resp = client.post(
        "/api/fleet", json={"boat": CAT, "polar_pol_text": pol_text},
    )
    assert resp.status_code == 200
    saved = next(b for b in resp.json()["boats"] if b["id"] == "sv_salt_cat")
    assert saved["polar_file"] == "polars/sv_salt_cat.pol"
    on_disk = tmp_path / "config" / "polars" / "sv_salt_cat.pol"
    assert on_disk.is_file() and on_disk.read_text(encoding="utf-8") == pol_text


def test_editing_without_a_new_paste_keeps_the_existing_polar(client: TestClient) -> None:
    pol_text = "twa/tws\t10\n60\t7\n"
    client.post("/api/fleet", json={"boat": CAT, "polar_pol_text": pol_text})
    edited = client.post(
        "/api/fleet", json={"boat": {**CAT, "air_draft_ft": 65.0}, "replaces": "sv_salt_cat"},
    ).json()
    saved = next(b for b in edited["boats"] if b["id"] == "sv_salt_cat")
    assert saved["polar_file"] == "polars/sv_salt_cat.pol"
    assert saved["air_draft_ft"] == 65.0


def test_research_uses_the_chosen_fleet_and_rejects_an_unknown_boat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backend, "research_status",
                        lambda: ResearchStatus(True, "ready", Path("claude.exe")))
    seen: dict[str, Any] = {}

    class FakeJobs:
        def start(self, ports: list[str], **kw: Any) -> str:
            seen["buddies"] = [b.name for b in kw["vessel"].buddy_boats]
            return "job1"

    monkeypatch.setattr(backend, "_RESEARCH", FakeJobs())
    client.put("/api/fleet", json={"boat": CAT})
    assert client.post("/api/logistics-research", data={"ports": "Nassau\n"}).status_code == 200
    assert sorted(seen["buddies"]) == ["SV Example Buddy", "SV Salt Cat"]  # usual companions
    client.post("/api/logistics-research", data={"ports": "Nassau\n", "fleet": '["sv_salt_cat"]'})
    assert seen["buddies"] == ["SV Salt Cat"]
    client.post("/api/logistics-research", data={"ports": "Nassau\n", "fleet": "[]"})
    assert seen["buddies"] == []
    bad = client.post("/api/logistics-research", data={"ports": "Nassau\n", "fleet": "ghost"})
    assert bad.status_code == 400 and "no boat" in bad.json()["detail"]
