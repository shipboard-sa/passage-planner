"""Tests for passage_planner.geo."""

from __future__ import annotations

import pytest

from passage_planner import geo


def test_great_circle_known_distance() -> None:
    # 1 degree of latitude ~ 60 nm.
    d = geo.great_circle_nm(0.0, 0.0, 1.0, 0.0)
    assert d == pytest.approx(60.0, abs=0.3)


def test_great_circle_symmetric_and_zero() -> None:
    assert geo.great_circle_nm(29.9, -90.1, 29.9, -90.1) == pytest.approx(0.0, abs=1e-9)
    a = geo.great_circle_nm(25.0, -80.0, 27.0, -79.0)
    b = geo.great_circle_nm(27.0, -79.0, 25.0, -80.0)
    assert a == pytest.approx(b)


def test_initial_bearing_cardinals() -> None:
    assert geo.initial_bearing_deg(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0, abs=0.01)
    assert geo.initial_bearing_deg(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0, abs=0.01)
    assert geo.initial_bearing_deg(1.0, 0.0, 0.0, 0.0) == pytest.approx(180.0, abs=0.01)
    assert geo.initial_bearing_deg(0.0, 1.0, 0.0, 0.0) == pytest.approx(270.0, abs=0.01)


def test_destination_round_trips_with_distance_and_bearing() -> None:
    lat0, lon0 = 28.5, -89.0
    for bearing in (10.0, 95.0, 200.0, 350.0):
        lat1, lon1 = geo.destination_point(lat0, lon0, bearing, 45.0)
        back = geo.great_circle_nm(lat0, lon0, lat1, lon1)
        assert back == pytest.approx(45.0, abs=0.05)
        fwd = geo.initial_bearing_deg(lat0, lon0, lat1, lon1)
        assert fwd == pytest.approx(bearing, abs=0.2)


def test_relative_wind_angle_wraps() -> None:
    # Course 000, wind from 090 -> 90 deg off the bow.
    assert geo.relative_wind_angle_deg(0.0, 90.0) == pytest.approx(90.0)
    # Course 350, wind from 010 -> 20 deg off the bow (wrap across north).
    assert geo.relative_wind_angle_deg(350.0, 10.0) == pytest.approx(20.0)
    # Dead astern.
    assert geo.relative_wind_angle_deg(0.0, 180.0) == pytest.approx(180.0)


@pytest.mark.parametrize(
    ("twa", "label"),
    [(0.0, "dead upwind"), (45.0, "close-hauled"), (70.0, "close reach"),
     (90.0, "beam reach"), (120.0, "broad reach"), (170.0, "running")],
)
def test_point_of_sail(twa: float, label: str) -> None:
    assert geo.point_of_sail(twa) == label


# -- current_component_kn (weather-routing step 2) ---------------------------


def test_current_component_fully_favorable() -> None:
    # Course 000, current flowing TOWARD 000 (dead astern push) -> full speed added.
    assert geo.current_component_kn(0.0, 0.0, 2.0) == pytest.approx(2.0)


def test_current_component_fully_foul() -> None:
    # Course 000, current flowing TOWARD 180 (dead against the course) -> subtracted.
    assert geo.current_component_kn(0.0, 180.0, 2.0) == pytest.approx(-2.0)


def test_current_component_perpendicular_is_zero() -> None:
    # Current flowing TOWARD 090 across a course of 000 contributes nothing along-course.
    assert geo.current_component_kn(0.0, 90.0, 2.0) == pytest.approx(0.0, abs=1e-9)


def test_current_component_wraps_across_north() -> None:
    # Course 350, current flowing TOWARD 010 (20 deg off, mostly favorable):
    # 2.0 kn * cos(20 deg) ~= 1.879 kn.
    got = geo.current_component_kn(350.0, 10.0, 2.0)
    assert got == pytest.approx(1.879, abs=0.01)
