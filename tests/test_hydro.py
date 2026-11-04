"""Tests for the hydro subpackage (mocked HTTP — no NOAA calls)."""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from passage_planner import PassagePlan, load, load_waypoint_depths, parse_gpx
from passage_planner.errors import HydroError
from passage_planner.hydro import assess_route, depths_template
from passage_planner.hydro.bathy import BathyClient
from passage_planner.hydro.enc import EncClient
from passage_planner.hydro.model import ChartedDepth, TideExtreme
from passage_planner.hydro.tides import (
    TidesClient,
    bracketing_extremes,
    gate_windows,
    tide_height,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

_T = datetime(2026, 10, 15, tzinfo=UTC)


def _ex(hours: float, ft: float, kind: str) -> TideExtreme:
    return TideExtreme(time=_T + timedelta(hours=hours), height_ft=ft, kind=kind)


# -- pure curve maths -------------------------------------------------

_SERIES = [_ex(0, 0.4, "L"), _ex(6, 2.4, "H"), _ex(12, 0.6, "L"), _ex(18, 2.2, "H")]


def test_tide_height_endpoints_and_shape() -> None:
    assert tide_height(_SERIES, _T) == pytest.approx(0.4)
    assert tide_height(_SERIES, _T + timedelta(hours=6)) == pytest.approx(2.4)
    mid = tide_height(_SERIES, _T + timedelta(hours=3))
    assert mid == pytest.approx((0.4 + 2.4) / 2)  # cosine midpoint == mean
    assert tide_height(_SERIES, _T + timedelta(hours=2)) < mid  # rising, still low
    assert tide_height(_SERIES, _T - timedelta(hours=1)) is None


def test_gate_windows_finds_span_above_threshold() -> None:
    wins = gate_windows(_SERIES, _T, _T + timedelta(hours=12), 1.5)
    assert len(wins) == 1
    # brackets the 06:00 high
    assert _T + timedelta(hours=3) < wins[0].start < _T + timedelta(hours=6)
    assert _T + timedelta(hours=6) < wins[0].end <= _T + timedelta(hours=9, minutes=30)
    assert wins[0].threshold_ft == 1.5


def test_gate_windows_empty_when_threshold_never_met() -> None:
    assert gate_windows(_SERIES, _T, _T + timedelta(hours=12), 5.0) == []


def test_bracketing_extremes() -> None:
    b = bracketing_extremes(_SERIES, _T + timedelta(hours=7), each_side=2)
    assert [e.kind for e in b] == ["L", "H", "L", "H"]


# -- TidesClient (mocked) ------------------------------------------

_STATIONS = {
    "count": 2,
    "stations": [
        {"id": "8760000", "name": "Near", "lat": 30.2, "lng": -89.0},
        {"id": "8770000", "name": "Far", "lat": 27.0, "lng": -82.0},
    ],
}
_PREDICTIONS = {
    "predictions": [
        {"t": "2026-10-15 01:00", "v": "0.40", "type": "L"},
        {"t": "2026-10-15 07:00", "v": "2.30", "type": "H"},
        {"t": "2026-10-15 13:00", "v": "0.50", "type": "L"},
        {"t": "2026-10-15 19:00", "v": "2.10", "type": "H"},
    ]
}


def _tides_client(*, stations=_STATIONS, predictions=_PREDICTIONS, tmp: Path) -> TidesClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "mdapi" in request.url.host or "stations" in str(request.url):
            return httpx.Response(200, json=stations)
        return httpx.Response(200, text=json.dumps(predictions))

    return TidesClient(
        cache_dir=tmp, http=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_nearest_station_picks_closest(tmp_path: Path) -> None:
    c = _tides_client(tmp=tmp_path)
    st = c.nearest_station(30.15, -89.05)
    assert st.station_id == "8760000"
    assert st.distance_nm < 10
    assert st.is_distant is False


def test_extremes_parsed(tmp_path: Path) -> None:
    c = _tides_client(tmp=tmp_path)
    ex = c.extremes("8760000", _T, _T + timedelta(hours=20))
    assert [e.kind for e in ex] == ["L", "H", "L", "H"]
    assert ex[1].height_ft == pytest.approx(2.30)
    assert ex[0].time.tzinfo is UTC


# -- EncClient (mocked) ------------------------------------------


def _enc_client(features: list[dict] | None) -> EncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/layers"):
            return httpx.Response(200, json={"layers": [
                {"id": 76, "name": "Harbor.Sounding_point"},
            ]})
        return httpx.Response(200, json={"features": features or []})

    return EncClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_enc_returns_shallowest_sounding() -> None:
    feats = [
        {"attributes": {"Z": 3.0, "DSNM": "US5AAA.000", "SORDAT": "20190101"}},
        {"attributes": {"Z": 1.8, "DSNM": "US5AAA.000", "SORDAT": "20190101"}},
        {"attributes": {"Z": 5.1, "DSNM": "US5AAA.000", "SORDAT": "20190101"}},
    ]
    d = _enc_client(feats).sounding_near(25.76, -80.13)
    assert d is not None
    assert d.depth_ft == pytest.approx(1.8 * 3.28084, abs=0.1)
    assert d.is_operator_supplied is False
    assert "US5AAA.000" in d.source


def test_enc_none_when_no_coverage() -> None:
    assert _enc_client([]).sounding_near(24.0, -76.0) is None  # Bahamas -> no features


# -- BathyClient (mocked) ------------------------------------------


def _bathy_client(elevations: list[float | None]) -> BathyClient:
    def handler(request: httpx.Request) -> httpx.Response:
        n = len(request.url.params["locations"].split("|"))
        rows = [{"elevation": elevations[i] if i < len(elevations) else None}
                for i in range(n)]
        return httpx.Response(200, json={"results": rows, "status": "OK"})

    return BathyClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_bathy_converts_elevation_to_positive_feet() -> None:
    out = _bathy_client([-2.0, -4783.0, 5.0, None]).depths_ft(
        [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
    )
    assert out[0] == pytest.approx(2.0 * 3.28084, abs=1)
    assert out[1] == pytest.approx(4783.0 * 3.28084, abs=2)
    assert out[2] is None  # land
    assert out[3] is None


# -- operator depths + template ----------------------------------


def test_load_waypoint_depths(tmp_path: Path) -> None:
    body = """
    [waypoints."Whale Cay Passage"]
    controlling_ft = 6.0
    source = "Explorer Abaco 2024"
    notes = "sand bar"
    """
    p = tmp_path / "d.toml"
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    d = load_waypoint_depths(p)
    assert "whale cay passage" in d
    wp = d["whale cay passage"]
    assert wp.depth_ft == 6.0
    assert wp.is_operator_supplied is True
    assert "Explorer Abaco 2024" in wp.source


def test_load_waypoint_depths_missing_controlling(tmp_path: Path) -> None:
    p = tmp_path / "d.toml"
    p.write_text('[waypoints."X"]\nsource = "y"\n', encoding="utf-8")
    with pytest.raises(HydroError, match="controlling_ft"):
        load_waypoint_depths(p)


def test_depths_template_marks_known_and_unknown() -> None:
    pre = {"a": ChartedDepth(7.0, "MLLW", "NOAA ENC US5X.000", False, "20200101")}
    text = depths_template([("A", 25.0, -80.0), ("B", 26.0, -79.0)], enc_prefill=pre)
    assert '[waypoints."A"]' in text and '[waypoints."B"]' in text
    assert "7.0" in text and "VERIFY" in text
    assert "screenshot needed" in text


# -- assess_route (fully mocked clients) ------------------------


def test_assess_route_flags_marginal_and_computes_gate(tmp_path: Path) -> None:
    app = load(ROOT / "config")
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    plan = PassagePlan(route=route, depart_utc=_T + timedelta(hours=2), planning_speed_kn=6.5)

    tides = _tides_client(tmp=tmp_path)
    # ENC reports a shoal 5 ft everywhere; GEBCO agrees it's shallow.
    enc = _enc_client([{"attributes": {"Z": 5.0 / 3.28084, "DSNM": "US5X.000"}}])
    bathy = _bathy_client([-2.0] * len(route.waypoints))

    report = assess_route(app.vessel, plan, tides=tides, enc=enc, bathy=bathy)
    assert len(report.waypoints) == len(route.waypoints)
    assert report.required_charted_ft == pytest.approx(10.0)  # 8 ft keel-up + 2 UKC
    assert report.required_keeldown_ft == pytest.approx(16.0)
    w = report.waypoints[0]
    assert w.charted is not None and w.charted.depth_ft == pytest.approx(5.0, abs=0.1)
    assert w.marginal is True  # 5 ft + ~2 ft tide << 10 ft
    # A gate window may or may not exist for 5 ft against a 2.3 ft tide range;
    # either way the field is a TideWindow or None, never an exception.
    assert w.gate_window is None or w.gate_window.threshold_ft == pytest.approx(5.0)


def test_assess_route_deep_water_waypoints_need_no_screenshot(tmp_path: Path) -> None:
    app = load(ROOT / "config")
    route = parse_gpx(FIXTURES / "navionics_style.gpx")  # FLD -> Bahamas, mostly deep
    plan = PassagePlan(route=route, depart_utc=_T + timedelta(hours=2), planning_speed_kn=6.5)
    tides = _tides_client(tmp=tmp_path)
    enc = _enc_client([])  # no ENC coverage
    # First waypoint shoal, rest blue water.
    depths = [-3.0] + [-2000.0] * (len(route.waypoints) - 1)
    report = assess_route(
        app.vessel, plan, tides=tides, enc=enc, bathy=_bathy_client(depths)
    )
    assert report.waypoints[0].needs_screenshot is True
    assert all(not w.needs_screenshot for w in report.waypoints[1:])
    assert all(w.deep_water for w in report.waypoints[1:])
