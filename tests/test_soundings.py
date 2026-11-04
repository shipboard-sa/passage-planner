"""Tests for passage_planner.hydro.soundings (saved chart depths, matched by position)
and the /api/depth-check and /api/depth-soundings endpoints (no network)."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_hydro import _bathy_client, _enc_client, _tides_client

from passage_planner import PassagePlan, load, parse_gpx
from passage_planner.errors import HydroError
from passage_planner.geo import destination_point
from passage_planner.hydro import assess_route
from passage_planner.hydro import soundings as sd

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 9, 21)
_T = datetime(2026, 10, 15, tzinfo=UTC)


def _add(
    lib: list[sd.Sounding], lat: float, lon: float, depth: float, **kw: Any
) -> list[sd.Sounding]:
    return sd.upsert(lib, lat=lat, lon=lon, depth_ft=depth, today=TODAY, **kw)


# --- matching by position --------------------------------------------------------


def test_match_uses_position_within_half_a_mile_not_the_waypoint_name() -> None:
    lib = _add([], 25.0, -77.0, 7.5, source="Navionics", label="WP3")
    lat, lon = destination_point(25.0, -77.0, 90.0, 0.4)  # 0.4 NM east
    assert sd.match(lib, lat, lon) is not None
    far_lat, far_lon = destination_point(25.0, -77.0, 90.0, 0.7)  # 0.7 NM east
    assert sd.match(lib, far_lat, far_lon) is None


def test_the_shallowest_saved_depth_within_range_wins() -> None:
    lib = _add([], 25.0, -77.0, 9.0)
    near = destination_point(25.0, -77.0, 0.0, 0.3)  # a different spot, 0.3 NM north
    lib = _add(lib, near[0], near[1], 6.5)
    hit = sd.match(lib, 25.0, -77.0)
    assert hit is not None and hit.depth_ft == 6.5  # the conservative one


def test_upsert_updates_the_same_spot_and_adds_a_new_one() -> None:
    lib = _add([], 25.0, -77.0, 9.0, source="first")
    ident = lib[0].id
    tiny = destination_point(25.0, -77.0, 45.0, 0.05)  # 0.05 NM away: the same spot
    lib = _add(lib, tiny[0], tiny[1], 8.0, source="second")
    assert len(lib) == 1 and lib[0].id == ident
    assert lib[0].depth_ft == 8.0 and lib[0].source == "second" and lib[0].recorded == "2026-09-21"
    lib = _add(lib, 26.0, -78.0, 12.0)
    assert len(lib) == 2


@pytest.mark.parametrize("depth", [0, -3, 250])
def test_upsert_rejects_silly_depths(depth: float) -> None:
    with pytest.raises(HydroError, match="depth must be between"):
        _add([], 25.0, -77.0, depth)


def test_upsert_rejects_a_bad_position() -> None:
    with pytest.raises(HydroError, match="position"):
        _add([], 95.0, -77.0, 10.0)


def test_remove_by_id() -> None:
    lib = _add(_add([], 25.0, -77.0, 9.0), 26.0, -78.0, 12.0)
    assert [s.depth_ft for s in sd.remove(lib, lib[0].id)] == [lib[1].depth_ft]


# --- the file ---------------------------------------------------------------------


def test_save_load_round_trip_is_atomic_and_missing_file_is_empty(tmp_path: Path) -> None:
    path = sd.library_path(tmp_path)
    assert sd.load_soundings(path) == []
    lib = _add([], 25.0, -77.0, 9.5, source="Explorer Abaco", notes="sand bar", label="WP3")
    sd.save_soundings(path, lib)
    assert not path.with_suffix(".tmp").exists()
    assert sd.load_soundings(path) == lib


def test_a_corrupt_file_is_an_error_not_a_silent_empty_library(tmp_path: Path) -> None:
    path = sd.library_path(tmp_path)
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(HydroError, match="cannot read the saved depths"):
        sd.load_soundings(path)


# --- feeding the depth assessment ---------------------------------------------------


def test_saved_depths_become_operator_supplied_charted_depths() -> None:
    lib = _add([], 25.0, -77.0, 7.5, source="Navionics", notes="sand bar", label="WP3")
    out = sd.soundings_to_depths([("WP9", 25.0, -77.0), ("WP1", 30.0, -70.0)], lib)
    assert set(out) == {"wp9"}  # lower-cased name; the far waypoint gets nothing
    c = out["wp9"]
    assert c.depth_ft == 7.5 and c.is_operator_supplied is True
    assert "operator (saved depth): Navionics" in c.source
    assert "saved 2026-09-21" in c.notes and "sand bar" in c.notes


def test_a_saved_depth_takes_a_waypoint_off_the_needs_a_reading_list(tmp_path: Path) -> None:
    app = load(ROOT / "config")
    route = parse_gpx(FIXTURES / "navionics_style.gpx")
    plan = PassagePlan(route=route, depart_utc=_T + timedelta(hours=2), planning_speed_kn=6.5)
    deep = [-3.0] + [-2000.0] * (len(route.waypoints) - 1)  # WP1 shoal, the rest blue water

    def report(supplied: dict[str, Any] | None) -> Any:
        return assess_route(
            app.vessel, plan, tides=_tides_client(tmp=tmp_path), enc=_enc_client([]),
            bathy=_bathy_client(deep), waypoint_depths=supplied,
        )

    first = report(None)
    assert first.waypoints[0].needs_screenshot
    rows = sd.depth_rows(first, [])
    assert [r["status"] for r in rows] == ["needs_depth"]
    assert rows[0]["name"] == route.waypoints[0].name
    assert "Ocean-depth data" in rows[0]["automatic"] and rows[0]["saved"] is None
    assert rows[0]["required_ft"] == pytest.approx(10.0)

    w0 = route.waypoints[0]
    lib = _add([], w0.lat, w0.lon, 14.0, source="Navionics", label=w0.name)
    supplied = sd.soundings_to_depths([(w.name, w.lat, w.lon) for w in route.waypoints], lib)
    second = report(supplied)
    assert not second.waypoints[0].needs_screenshot
    assert second.waypoints[0].charted is not None
    assert second.waypoints[0].charted.depth_ft == 14.0
    saved_rows = sd.depth_rows(second, lib)
    assert [r["status"] for r in saved_rows] == ["saved"]
    assert saved_rows[0]["saved"]["depth_ft"] == 14.0

    shallow = _add([], w0.lat, w0.lon, 5.0, source="Navionics")  # too shallow for the boat
    tight = report(sd.soundings_to_depths([(w.name, w.lat, w.lon) for w in route.waypoints],
                                          shallow))
    assert [r["status"] for r in sd.depth_rows(tight, shallow)] == ["tight"]


# --- the endpoints -----------------------------------------------------------------

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(ROOT))
import backend.main as backend  # noqa: E402

GPX = (FIXTURES / "navionics_style.gpx").read_bytes()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(backend, "CONFIG_DIR", tmp_path)
    return TestClient(backend.app)


def test_put_get_delete_soundings(client: TestClient) -> None:
    body = {"soundings": [{"lat": 25.0, "lon": -77.0, "depth_ft": 8.0, "source": "Navionics",
                           "label": "WP3"}]}
    assert client.put("/api/depth-soundings", json=body).json() == {"saved": 1, "total": 1}
    body["soundings"][0]["depth_ft"] = 9.0  # same spot: updated, not duplicated
    assert client.put("/api/depth-soundings", json=body).json()["total"] == 1
    listed = client.get("/api/depth-soundings").json()["soundings"]
    assert len(listed) == 1 and listed[0]["depth_ft"] == 9.0 and listed[0]["label"] == "WP3"
    assert client.delete(f"/api/depth-soundings/{listed[0]['id']}").json()["total"] == 0
    assert client.delete("/api/depth-soundings/nope").status_code == 404


def test_put_rejects_a_nonsense_depth_with_a_readable_message(client: TestClient) -> None:
    resp = client.put("/api/depth-soundings",
                      json={"soundings": [{"lat": 25.0, "lon": -77.0, "depth_ft": 900}]})
    assert resp.status_code == 400 and "depth must be between" in resp.json()["detail"]


def test_depth_check_returns_rows_and_uses_saved_depths(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    route = parse_gpx(FIXTURES / "navionics_style.gpx")
    deep = [-3.0] + [-2000.0] * (len(route.waypoints) - 1)
    real = backend.assess_route

    def offline(vessel: Any, plan: Any, **kw: Any) -> Any:
        return real(vessel, plan, tides=_tides_client(tmp=tmp_path), enc=_enc_client([]),
                    bathy=_bathy_client(deep), **kw)

    monkeypatch.setattr(backend, "assess_route", offline)
    monkeypatch.setattr(backend, "routing_config", SimpleNamespace(
        load=lambda _d: load(ROOT / "config")))

    def check() -> dict[str, Any]:
        resp = client.post("/api/depth-check", data={"depart": "2026-10-15T07:00"},
                           files={"route": ("r.gpx", GPX)})
        assert resp.status_code == 200, resp.text
        return resp.json()  # type: ignore[no-any-return]

    first = check()
    assert first["required_ft"] == pytest.approx(10.0) and first["draft_ft"] == 8.0
    assert [r["status"] for r in first["rows"]] == ["needs_depth"]
    w0 = first["rows"][0]
    client.put("/api/depth-soundings", json={"soundings": [
        {"lat": w0["lat"], "lon": w0["lon"], "depth_ft": 15.0, "source": "Navionics"}]})
    second = check()
    assert [r["status"] for r in second["rows"]] == ["saved"]
    assert second["rows"][0]["saved"]["depth_ft"] == 15.0
