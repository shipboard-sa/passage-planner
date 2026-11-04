"""Tests for passage_planner.passage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from passage_planner import PassagePlan, parse_gpx

_SEC = timedelta(seconds=1)
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEPART = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _plan(speed: float = 6.0) -> PassagePlan:
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    return PassagePlan(route=route, depart_utc=DEPART, planning_speed_kn=speed)


def test_eta_matches_distance_over_speed() -> None:
    plan = _plan(speed=6.0)
    expected_h = plan.route.total_nm / 6.0
    got = plan.eta() - DEPART
    assert got == pytest.approx(timedelta(hours=expected_h), abs=_SEC)


def test_timed_legs_are_contiguous_and_cover_the_route() -> None:
    plan = _plan()
    legs = plan.timed_legs()
    assert legs[0].start_eta == DEPART
    assert legs[-1].end_eta == plan.eta()
    for a, b in pairwise(legs):
        assert a.end_eta == b.start_eta
        assert b.duration.total_seconds() > 0


def test_sample_fixes_include_endpoints_and_increase_in_time() -> None:
    plan = _plan()
    fixes = plan.sample_fixes(step_nm=8.0)
    assert fixes[0].cumulative_nm == 0.0
    assert fixes[0].eta == DEPART
    assert fixes[-1].cumulative_nm == pytest.approx(plan.route.total_nm)
    for a, b in pairwise(fixes):
        assert b.eta >= a.eta
        assert 0 <= b.leg_index < len(plan.route.legs)


def test_per_leg_speed_override_changes_eta() -> None:
    plan = _plan(speed=6.0)
    base = plan.eta()
    # Halve the speed on leg 0 only -> total time must increase.
    slower = plan.eta(leg_speeds={0: 3.0})
    assert slower > base


def test_naive_departure_rejected() -> None:
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    with pytest.raises(ValueError, match="timezone-aware"):
        PassagePlan(route=route, depart_utc=datetime(2026, 10, 15, 12, 0), planning_speed_kn=6.0)


def test_nonpositive_speed_rejected() -> None:
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    with pytest.raises(ValueError, match="planning_speed_kn"):
        PassagePlan(route=route, depart_utc=DEPART, planning_speed_kn=0.0)
