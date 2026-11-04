"""Tests for passage_planner.isochrone (weather-routing step 4)."""

from __future__ import annotations

import textwrap
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from passage_planner import analyze, load, load_polar, parse_gpx
from passage_planner.isochrone import _leg_rows, solve_leg, solve_passage, to_gpx
from passage_planner.passage import PassagePlan
from passage_planner.weather.field import Field
from passage_planner.weather.model import ForecastPoint, RouteForecast

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
T0 = (datetime.now(UTC) + timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)


def _cfg():
    return load(ROOT / "config").vessel


def _polar():
    return load_polar(ROOT / "config" / "baseline.pol")


def _single_leg_route(tmp_path: Path, *, lat0=26.0, lon0=-79.0, lat1=26.6, lon1=-78.3):
    body = f"""
    <?xml version="1.0"?>
    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
      <rte><name>test leg</name>
        <rtept lat="{lat0}" lon="{lon0}"/>
        <rtept lat="{lat1}" lon="{lon1}"/>
      </rte>
    </gpx>
    """
    path = tmp_path / "t.gpx"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return parse_gpx(path)


def _uniform_field(tws: float, wind_from: float, *, current_kn=None, current_to=None) -> Field:
    pt = ForecastPoint(
        cumulative_nm=0.0, leg_index=0, lat=0.0, lon=0.0, valid_time=T0,
        source="test", model="synthetic", provenance_tier="secondary",
        wind_speed_kn=tws, wind_from_deg=wind_from,
        current_kn=current_kn, current_to_deg=current_to,
    )
    return Field(by_point={(0.0, 0.0): (pt,)})


def _uniform_forecast(plan: PassagePlan, *, tws: float, wind_from: float) -> RouteForecast:
    pts = tuple(
        ForecastPoint(
            cumulative_nm=f.cumulative_nm, leg_index=f.leg_index, lat=f.lat, lon=f.lon,
            valid_time=f.eta, source="test", model="synthetic", provenance_tier="secondary",
            wind_speed_kn=tws, wind_from_deg=wind_from,
        )
        for f in plan.sample_fixes(8.0)
    )
    return RouteForecast(route_name=plan.route.name, issued=T0, points=pts)


# -- _leg_rows ----------------------------------------------------------


def test_leg_rows_start_and_end_are_forced_single_cells(tmp_path: Path) -> None:
    route = _single_leg_route(tmp_path)
    leg = route.legs[0]
    rows = _leg_rows(leg, along_step_nm=15.0, cross_offsets_nm=(-10.0, 0.0, 10.0))
    assert rows[0] == [(leg.start.lat, leg.start.lon)]
    assert rows[-1] == [(leg.end.lat, leg.end.lon)]
    assert len(rows) > 2  # the leg is long enough for at least one interior row
    assert all(len(r) == 3 for r in rows[1:-1])


def test_leg_rows_whole_leg_shorter_than_step_has_no_interior_rows(tmp_path: Path) -> None:
    route = _single_leg_route(tmp_path, lat1=26.05, lon1=-78.95)  # a few NM
    leg = route.legs[0]
    rows = _leg_rows(leg, along_step_nm=50.0, cross_offsets_nm=(-10.0, 0.0, 10.0))
    assert rows == [[(leg.start.lat, leg.start.lon)], [(leg.end.lat, leg.end.lon)]]


# -- solve_leg: correctness against the fixed-route analyzer -------------


def test_on_track_single_hop_matches_fixed_route_leg_exactly(tmp_path: Path) -> None:
    route = _single_leg_route(tmp_path)
    leg = route.legs[0]
    vessel = _cfg()
    polar = _polar()
    field = _uniform_field(tws=14.0, wind_from=170.0)

    # along_step_nm bigger than the leg -> exactly one hop, start to end directly,
    # the same geometry analyze() uses for this leg.
    solved, warning = solve_leg(
        vessel, polar, field, leg, T0, along_step_nm=1000.0, cross_offsets_nm=(0.0,),
    )
    assert warning is None
    assert len(solved.hops) == 1

    plan = PassagePlan(route=route, depart_utc=T0, planning_speed_kn=6.5)
    fc = _uniform_forecast(plan, tws=14.0, wind_from=170.0)
    fixed = analyze(vessel, plan, fc, polar)

    assert solved.duration_h == pytest.approx(fixed.legs[0].duration_h, rel=1e-6)
    assert solved.hops[0].mode == fixed.legs[0].mode
    assert solved.hops[0].sog_kn == pytest.approx(fixed.legs[0].projected_speed_kn, rel=1e-6)


def test_finer_grid_on_track_stays_close_to_fixed_route(tmp_path: Path) -> None:
    # Several hops instead of one, still confined to the on-track lane - should
    # still reproduce the fixed-route duration closely (small great-circle vs.
    # along-track-marching differences over a modest leg length).
    route = _single_leg_route(tmp_path)
    leg = route.legs[0]
    vessel = _cfg()
    polar = _polar()
    field = _uniform_field(tws=14.0, wind_from=170.0)

    solved, warning = solve_leg(
        vessel, polar, field, leg, T0, along_step_nm=15.0, cross_offsets_nm=(0.0,),
    )
    assert warning is None
    assert len(solved.hops) > 1

    plan = PassagePlan(route=route, depart_utc=T0, planning_speed_kn=6.5)
    fc = _uniform_forecast(plan, tws=14.0, wind_from=170.0)
    fixed = analyze(vessel, plan, fc, polar)

    assert solved.duration_h == pytest.approx(fixed.legs[0].duration_h, rel=0.02)


# -- solve_leg: the solver actually optimizes ----------------------------


def test_solver_prefers_a_favorable_current_off_track(tmp_path: Path) -> None:
    route = _single_leg_route(tmp_path)
    leg = route.legs[0]
    vessel = _cfg()
    polar = _polar()
    cross_offsets = (-15.0, 0.0, 15.0)
    rows = _leg_rows(leg, along_step_nm=15.0, cross_offsets_nm=cross_offsets)

    # Give every interior-row cell light wind and no current, except the +15 NM
    # lane (offset index 2, since cross_offsets_nm=(-15, 0, +15) in that order),
    # which gets a strong current flowing straight along the leg's bearing.
    by_point: dict[tuple[float, float], tuple[ForecastPoint, ...]] = {}
    for row in rows:
        for idx, cell in enumerate(row):
            favorable = len(row) == 3 and idx == 2
            pt = ForecastPoint(
                cumulative_nm=0.0, leg_index=0, lat=cell[0], lon=cell[1], valid_time=T0,
                source="test", model="synthetic", provenance_tier="secondary",
                wind_speed_kn=10.0, wind_from_deg=170.0,
                # Strong enough to clearly outweigh the extra distance of
                # detouring into and back out of the lane (checked against a
                # range of strengths against this exact geometry: 5 kn is
                # already enough, 8 kn gives a comfortable margin).
                current_kn=8.0 if favorable else None,
                current_to_deg=leg.bearing_deg if favorable else None,
            )
            by_point[cell] = (pt,)
    field = Field(by_point=by_point)

    with_offset, warn_a = solve_leg(
        vessel, polar, field, leg, T0, along_step_nm=15.0, cross_offsets_nm=cross_offsets,
    )
    on_track_only, warn_b = solve_leg(
        vessel, polar, field, leg, T0, along_step_nm=15.0, cross_offsets_nm=(0.0,),
    )
    assert warn_a is None and warn_b is None
    assert with_offset.duration_h < on_track_only.duration_h
    assert any(h.current_component_kn > 0.0 for h in with_offset.hops)


def test_solver_never_produces_a_foul_current_hop_when_an_alternative_exists(
    tmp_path: Path,
) -> None:
    route = _single_leg_route(tmp_path)
    leg = route.legs[0]
    vessel = _cfg()
    polar = _polar()
    cross_offsets = (-15.0, 0.0, 15.0)
    rows = _leg_rows(leg, along_step_nm=15.0, cross_offsets_nm=cross_offsets)

    # The on-track and -15 NM lanes carry a huge foul current; +15 NM is clear.
    by_point: dict[tuple[float, float], tuple[ForecastPoint, ...]] = {}
    for row in rows:
        for idx, cell in enumerate(row):
            foul = len(row) == 3 and idx in (0, 1)  # -15 NM and on-track columns
            pt = ForecastPoint(
                cumulative_nm=0.0, leg_index=0, lat=cell[0], lon=cell[1], valid_time=T0,
                source="test", model="synthetic", provenance_tier="secondary",
                wind_speed_kn=10.0, wind_from_deg=170.0,
                current_kn=50.0 if foul else None,
                current_to_deg=((leg.bearing_deg + 180.0) % 360.0) if foul else None,
            )
            by_point[cell] = (pt,)
    field = Field(by_point=by_point)

    solved, warning = solve_leg(
        vessel, polar, field, leg, T0, along_step_nm=15.0, cross_offsets_nm=cross_offsets,
    )
    assert warning is None
    assert all(h.sog_kn > 0.0 for h in solved.hops)


def test_solver_falls_back_when_every_hop_is_infeasible(tmp_path: Path) -> None:
    route = _single_leg_route(tmp_path)
    leg = route.legs[0]
    vessel = _cfg()
    polar = _polar()
    # An enormous, directly-opposing current everywhere blocks every hop into
    # the forced final cell, whatever lane it came from.
    field = _uniform_field(
        tws=10.0, wind_from=170.0, current_kn=500.0,
        current_to=(leg.bearing_deg + 180.0) % 360.0,
    )

    solved, warning = solve_leg(
        vessel, polar, field, leg, T0, along_step_nm=15.0, cross_offsets_nm=(-15.0, 0.0, 15.0),
    )
    assert warning is not None
    assert solved.fallback is True
    assert len(solved.hops) == 1
    assert solved.duration_h > 0.0
    assert solved.hops[0].sog_kn > 0.0  # floored, not zero/negative


# -- solve_leg: validation -------------------------------------------------


def test_solve_leg_rejects_non_positive_step(tmp_path: Path) -> None:
    route = _single_leg_route(tmp_path)
    field = _uniform_field(10.0, 170.0)
    with pytest.raises(ValueError):
        solve_leg(_cfg(), _polar(), field, route.legs[0], T0, along_step_nm=0.0)


def test_solve_leg_rejects_empty_offsets(tmp_path: Path) -> None:
    route = _single_leg_route(tmp_path)
    with pytest.raises(ValueError):
        solve_leg(
            _cfg(), _polar(), _uniform_field(10.0, 170.0), route.legs[0], T0,
            cross_offsets_nm=(),
        )


# -- solve_passage --------------------------------------------------------


def test_solve_passage_chains_legs_and_reaches_the_destination() -> None:
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    vessel = _cfg()
    polar = _polar()
    field = _uniform_field(tws=12.0, wind_from=150.0)

    result = solve_passage(
        vessel, polar, field, route, T0, along_step_nm=1000.0, cross_offsets_nm=(0.0,),
    )
    assert len(result.legs) == len(route.legs)
    assert result.depart == T0
    assert result.eta > result.depart
    for a, b in zip(result.legs, result.legs[1:], strict=False):
        assert a.arrive == b.depart
    # On-track-only, one hop per leg -> total distance matches the drawn route.
    assert result.total_nm == pytest.approx(route.total_nm, rel=1e-6)
    assert result.warnings == ()


def test_solve_passage_rollup_splits_add_up() -> None:
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    vessel = _cfg()
    polar = _polar()
    field = _uniform_field(tws=12.0, wind_from=150.0)

    result = solve_passage(vessel, polar, field, route, T0, along_step_nm=20.0)
    assert result.sail_nm + result.motor_nm + result.motorsail_nm == pytest.approx(
        result.total_nm, rel=1e-6
    )
    assert result.total_fuel_gal >= 0.0


# -- to_gpx -----------------------------------------------------------------


def test_to_gpx_is_well_formed_and_starts_at_the_route_start() -> None:
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    vessel = _cfg()
    polar = _polar()
    field = _uniform_field(tws=12.0, wind_from=150.0)
    solved = solve_passage(vessel, polar, field, route, T0, along_step_nm=20.0)

    xml_text = to_gpx(solved)
    root = ET.fromstring(xml_text)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    points = root.findall(".//g:rte/g:rtept", ns)

    # One point per hop boundary: the first hop's start, then every hop's end.
    total_hops = sum(len(leg.hops) for leg in solved.legs)
    assert len(points) == total_hops + 1
    assert float(points[0].attrib["lat"]) == pytest.approx(route.waypoints[0].lat)
    assert float(points[0].attrib["lon"]) == pytest.approx(route.waypoints[0].lon)
    assert float(points[-1].attrib["lat"]) == pytest.approx(route.waypoints[-1].lat)
    assert float(points[-1].attrib["lon"]) == pytest.approx(route.waypoints[-1].lon)
    for p in points:
        assert p.find("g:time", ns) is not None


def test_to_gpx_escapes_the_route_name(tmp_path: Path) -> None:
    body = """
    <?xml version="1.0"?>
    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
      <rte><name>Tom &amp; Jerry &lt;test&gt;</name>
        <rtept lat="26.0" lon="-79.0"/>
        <rtept lat="26.1" lon="-78.9"/>
      </rte>
    </gpx>
    """
    path = tmp_path / "named.gpx"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    route = parse_gpx(path)
    field = _uniform_field(tws=10.0, wind_from=170.0)
    solved = solve_passage(_cfg(), _polar(), field, route, T0, along_step_nm=1000.0)

    xml_text = to_gpx(solved)
    ET.fromstring(xml_text)  # raises if not well-formed despite the special characters
    assert "Tom & Jerry <test>" in xml_text.replace("&amp;", "&").replace(
        "&lt;", "<"
    ).replace("&gt;", ">")
