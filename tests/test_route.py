"""Tests for passage_planner.route (GPX ingest + route model)."""

from __future__ import annotations

import textwrap
from itertools import pairwise
from pathlib import Path

import pytest

from passage_planner import parse_gpx
from passage_planner.errors import RouteContentError, RouteFileError
from passage_planner.geo import great_circle_nm

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parses_shipped_fixture() -> None:
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")

    assert route.name.startswith("NOLA")
    assert len(route.waypoints) == 4
    assert len(route.legs) == 3
    assert route.waypoints[0].name == "Rigolets Pass"
    assert route.waypoints[0].description is not None

    # Legs are contiguous and cumulative distance is monotonic.
    assert route.legs[0].cumulative_start_nm == 0.0
    for a, b in pairwise(route.legs):
        assert a.cumulative_end_nm == pytest.approx(b.cumulative_start_nm)
    assert route.total_nm == pytest.approx(route.legs[-1].cumulative_end_nm)

    # Whole route is a plausible ~40-60 nm day hop.
    assert 30.0 < route.total_nm < 70.0

    # Each leg's own distance matches a direct great-circle calc.
    for leg in route.legs:
        direct = great_circle_nm(leg.start.lat, leg.start.lon, leg.end.lat, leg.end.lon)
        assert leg.distance_nm == pytest.approx(direct)
        assert 0.0 <= leg.bearing_deg < 360.0


def test_bounding_box_contains_all_waypoints_with_margin() -> None:
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    min_lat, min_lon, max_lat, max_lon = route.bounding_box(margin_deg=0.5)
    for wp in route.waypoints:
        assert min_lat + 0.49 < wp.lat < max_lat - 0.49
        assert min_lon + 0.49 < wp.lon < max_lon - 0.49


def test_position_and_sampling_along_route() -> None:
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")

    start = route.position_at_distance(0.0)
    assert start == pytest.approx((route.waypoints[0].lat, route.waypoints[0].lon))
    end = route.position_at_distance(route.total_nm)
    assert end == pytest.approx((route.waypoints[-1].lat, route.waypoints[-1].lon))

    samples = route.sample_points(step_nm=10.0)
    assert samples[0][0] == 0.0
    assert samples[-1][0] == pytest.approx(route.total_nm)
    # Monotonic non-decreasing cumulative distance.
    for a, b in pairwise(samples):
        assert b[0] >= a[0]

    with pytest.raises(ValueError):
        route.position_at_distance(route.total_nm + 5.0)


def test_real_navionics_export_format() -> None:
    # Navionics Boating app: <rte>, XML-entity in <name>, <time> as a Unix
    # epoch, and <rtept> with NO <name> child.
    route = parse_gpx(FIXTURES / "navionics_style.gpx")
    assert route.name == "FLD to T&C"  # entity decoded
    assert len(route.waypoints) == 5
    assert route.waypoints[0].name == "WP1"  # sequence fallback
    assert route.waypoints[0].description is None
    assert len(route.legs) == 4
    assert 100.0 < route.total_nm < 200.0
    for leg in route.legs:
        assert leg.distance_nm > 0.0


def test_track_fallback(tmp_path: Path) -> None:
    body = """
    <?xml version="1.0"?>
    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
      <trk><name>recorded track</name><trkseg>
        <trkpt lat="27.0" lon="-82.5"/>
        <trkpt lat="27.5" lon="-82.7"/>
        <trkpt lat="28.0" lon="-83.0"/>
      </trkseg></trk>
    </gpx>
    """
    path = tmp_path / "t.gpx"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    route = parse_gpx(path)
    assert route.name == "recorded track"
    assert len(route.legs) == 2


def test_duplicate_points_do_not_create_zero_legs(tmp_path: Path) -> None:
    body = """
    <?xml version="1.0"?>
    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
      <rte><name>dupes</name>
        <rtept lat="29.0" lon="-89.0"/>
        <rtept lat="29.0" lon="-89.0"/>
        <rtept lat="29.5" lon="-89.0"/>
      </rte>
    </gpx>
    """
    path = tmp_path / "d.gpx"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    route = parse_gpx(path)
    assert len(route.waypoints) == 2
    assert len(route.legs) == 1


def test_missing_file() -> None:
    with pytest.raises(RouteFileError):
        parse_gpx(FIXTURES / "does-not-exist.gpx")


def test_bad_xml(tmp_path: Path) -> None:
    path = tmp_path / "bad.gpx"
    path.write_text("<gpx><rte><rtept lat=", encoding="utf-8")
    with pytest.raises(RouteFileError):
        parse_gpx(path)


def test_single_point_route_rejected(tmp_path: Path) -> None:
    body = """
    <?xml version="1.0"?>
    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
      <rte><name>x</name><rtept lat="29.0" lon="-89.0"/></rte>
    </gpx>
    """
    path = tmp_path / "s.gpx"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    with pytest.raises(RouteContentError):
        parse_gpx(path)


def test_out_of_range_latitude_rejected(tmp_path: Path) -> None:
    body = """
    <?xml version="1.0"?>
    <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
      <rte><name>x</name>
        <rtept lat="99.0" lon="-89.0"/>
        <rtept lat="29.5" lon="-89.0"/>
      </rte>
    </gpx>
    """
    path = tmp_path / "r.gpx"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    with pytest.raises(RouteContentError):
        parse_gpx(path)
