"""Tests for passage_planner.analysis (the leg analyzer)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from passage_planner import (
    PassagePlan,
    SailMode,
    Severity,
    analyze,
    load,
    load_polar,
    parse_gpx,
    refine,
)
from passage_planner.weather.model import ForecastPoint, RouteForecast

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEPART = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _plan(speed: float = 6.5) -> PassagePlan:
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    return PassagePlan(route=route, depart_utc=DEPART, planning_speed_kn=speed)


def _uniform_forecast(
    plan: PassagePlan,
    *,
    tws: float,
    wind_from: float,
    hs: float = 0.8,
    gust: float | None = None,
    step_nm: float = 8.0,
    current_kn: float | None = None,
    current_to: float | None = None,
) -> RouteForecast:
    fixes = plan.sample_fixes(step_nm)
    pts = tuple(
        ForecastPoint(
            cumulative_nm=f.cumulative_nm,
            leg_index=f.leg_index,
            lat=f.lat,
            lon=f.lon,
            valid_time=f.eta,
            source="test",
            model="synthetic",
            provenance_tier="secondary",
            wind_speed_kn=tws,
            wind_from_deg=wind_from,
            wind_gust_kn=gust if gust is not None else tws * 1.25,
            pressure_hpa=1015.0,
            wave_hs_m=hs,
            wave_from_deg=wind_from,
            wave_period_s=6.0,
            swell_hs_m=hs * 0.5,
            current_kn=current_kn,
            current_to_deg=current_to,
        )
        for f in fixes
    )
    return RouteForecast(route_name=plan.route.name, issued=DEPART, points=pts)


def _cfg():
    return load(ROOT / "config").vessel


def _polar():
    return load_polar(ROOT / "config" / "baseline.pol")


def test_beam_reach_moderate_wind_sails() -> None:
    plan = _plan()
    # Leg 0 bearing is roughly ENE; put the wind on the beam from the south.
    fc = _uniform_forecast(plan, tws=14.0, wind_from=170.0)
    result = analyze(_cfg(), plan, fc, _polar())

    assert result.polar_provisional is True
    assert result.polar_source == str(ROOT / "config" / "baseline.pol")
    assert len(result.legs) == len(plan.route.legs)
    sailed = [leg_a for leg_a in result.legs if leg_a.mode is SailMode.SAIL]
    assert sailed, "at least one leg should be sailable on a 14 kn beam reach"
    for leg_a in sailed:
        assert leg_a.fuel_gal == 0.0
        assert 3.0 < leg_a.projected_speed_kn <= _cfg().performance.hull_max_speed_kn
        assert leg_a.projected_speed_kn == pytest.approx(leg_a.polar_speed_kn)


def test_dead_upwind_leg_motors_or_motorsails() -> None:
    plan = _plan()
    leg0 = plan.route.legs[0]
    fc = _uniform_forecast(plan, tws=18.0, wind_from=leg0.bearing_deg)  # wind dead ahead
    result = analyze(_cfg(), plan, fc, _polar())

    a0 = result.legs[0]
    assert a0.is_beat
    assert a0.mode in (SailMode.MOTOR, SailMode.MOTORSAIL)
    assert a0.fuel_gal > 0.0


def test_big_seas_raise_a_warning() -> None:
    plan = _plan()
    fc = _uniform_forecast(plan, tws=22.0, wind_from=200.0, hs=3.6, gust=40.0)
    result = analyze(_cfg(), plan, fc, _polar())

    texts = " ".join(h.text for h in result.hazards)
    assert "seas" in texts.lower()
    assert any(h.severity is Severity.WARNING for h in result.hazards)


def test_fuel_and_time_rollup_is_consistent() -> None:
    plan = _plan()
    fc = _uniform_forecast(plan, tws=3.0, wind_from=200.0)  # near calm -> all motor
    result = analyze(_cfg(), plan, fc, _polar())

    assert result.motor_nm == pytest.approx(result.total_nm, abs=result.total_nm * 0.5)
    assert result.total_fuel_gal == pytest.approx(sum(a_.fuel_gal for a_ in result.legs))
    hrs = sum(a_.duration_h for a_ in result.legs)
    assert result.duration_h == pytest.approx(hrs, abs=0.1)
    assert result.eta > result.depart


def test_fuel_shortfall_flagged_on_a_long_motor() -> None:
    # Force a shortfall: tiny tank, near-calm passage (all motor).
    plan = _plan()
    fc = _uniform_forecast(plan, tws=2.0, wind_from=180.0)
    base = _cfg()
    thin_tank = replace(base, propulsion=replace(base.propulsion, fuel_capacity_gal=3.0))
    result = analyze(thin_tank, plan, fc, _polar())
    assert result.fuel_shortfall
    assert result.hazards[0].severity is Severity.WARNING
    assert "fuel" in result.hazards[0].text.lower()


def test_refine_converges_and_reports_iterations() -> None:
    plan = _plan()

    def fetch(fixes):
        # Constant conditions -> speeds stabilise immediately after iteration 1->2.
        return _uniform_forecast(plan, tws=12.0, wind_from=150.0)

    result = refine(_cfg(), plan, _polar(), fetch, sample_step_nm=10.0, max_iterations=5)
    assert 1 <= result.iterations <= 5
    assert result.eta > result.depart
    drift = abs((result.eta - result.depart) - timedelta(hours=result.duration_h))
    assert drift < timedelta(minutes=1)


# -- current correction (weather-routing step 2) -----------------------------


def test_favorable_current_increases_sog_over_stw() -> None:
    plan = _plan()
    leg0 = plan.route.legs[0]
    fc_no_current = _uniform_forecast(plan, tws=14.0, wind_from=170.0)
    fc_current = _uniform_forecast(
        plan, tws=14.0, wind_from=170.0, current_kn=1.5, current_to=leg0.bearing_deg
    )
    baseline = analyze(_cfg(), plan, fc_no_current, _polar()).legs[0]
    boosted = analyze(_cfg(), plan, fc_current, _polar()).legs[0]

    assert boosted.current_component_kn == pytest.approx(1.5, abs=0.01)
    assert boosted.projected_speed_kn == pytest.approx(baseline.projected_speed_kn + 1.5, abs=0.05)
    assert boosted.duration_h < baseline.duration_h


def test_foul_current_decreases_sog() -> None:
    plan = _plan()
    leg0 = plan.route.legs[0]
    opposing = (leg0.bearing_deg + 180.0) % 360.0
    fc_no_current = _uniform_forecast(plan, tws=14.0, wind_from=170.0)
    fc_current = _uniform_forecast(
        plan, tws=14.0, wind_from=170.0, current_kn=1.0, current_to=opposing
    )
    baseline = analyze(_cfg(), plan, fc_no_current, _polar()).legs[0]
    slowed = analyze(_cfg(), plan, fc_current, _polar()).legs[0]

    assert slowed.current_component_kn == pytest.approx(-1.0, abs=0.01)
    assert slowed.projected_speed_kn == pytest.approx(baseline.projected_speed_kn - 1.0, abs=0.05)
    assert slowed.duration_h > baseline.duration_h


def test_current_exceeding_boat_speed_flags_foul_current_not_a_crash() -> None:
    from passage_planner.analysis import FOUL_CURRENT

    plan = _plan()
    leg0 = plan.route.legs[0]
    opposing = (leg0.bearing_deg + 180.0) % 360.0
    # A near-calm, near-motoring leg with a current stronger than any boat speed here.
    fc = _uniform_forecast(plan, tws=2.0, wind_from=180.0, current_kn=20.0, current_to=opposing)
    result = analyze(_cfg(), plan, fc, _polar())

    a0 = result.legs[0]
    assert a0.projected_speed_kn > 0.0  # guarded, not zero/negative
    assert any(f.kind == FOUL_CURRENT and f.severity is Severity.WARNING for f in a0.flags)
    assert result.eta > result.depart  # ETA still computable, just very slow


def test_no_current_data_leaves_component_at_zero() -> None:
    plan = _plan()
    fc = _uniform_forecast(plan, tws=14.0, wind_from=170.0)  # current_kn/current_to left None
    result = analyze(_cfg(), plan, fc, _polar())
    assert all(la.current_component_kn == 0.0 for la in result.legs)


def test_wind_against_current_raises_a_caution() -> None:
    from passage_planner.analysis import WIND_AGAINST_CURRENT

    plan = _plan()
    # Strong wind blowing FROM the north (pushes TOWARD south), current flowing
    # TOWARD the north -> directly opposed -> classic steep-seas setup.
    fc = _uniform_forecast(
        plan, tws=20.0, wind_from=0.0, current_kn=2.5, current_to=0.0
    )
    result = analyze(_cfg(), plan, fc, _polar())
    kinds = [f.kind for la in result.legs for f in la.flags]
    assert WIND_AGAINST_CURRENT in kinds
    cautions = [f for la in result.legs for f in la.flags if f.kind == WIND_AGAINST_CURRENT]
    assert all(f.severity is Severity.CAUTION for f in cautions)


def test_wind_against_current_does_not_fire_below_thresholds() -> None:
    from passage_planner.analysis import WIND_AGAINST_CURRENT

    plan = _plan()
    # Opposed directions, but both wind and current are under the configured minimums.
    fc = _uniform_forecast(plan, tws=8.0, wind_from=0.0, current_kn=0.3, current_to=0.0)
    result = analyze(_cfg(), plan, fc, _polar())
    kinds = [f.kind for la in result.legs for f in la.flags]
    assert WIND_AGAINST_CURRENT not in kinds


def test_wind_and_current_aligned_does_not_fire() -> None:
    from passage_planner.analysis import WIND_AGAINST_CURRENT

    plan = _plan()
    # Wind FROM the north pushes TOWARD the south; a current also flowing TOWARD
    # the south is following, not opposing - should never fire regardless of speed.
    fc = _uniform_forecast(plan, tws=25.0, wind_from=0.0, current_kn=3.0, current_to=180.0)
    result = analyze(_cfg(), plan, fc, _polar())
    kinds = [f.kind for la in result.legs for f in la.flags]
    assert WIND_AGAINST_CURRENT not in kinds
