"""Tests for passage_planner.floatplan."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from passage_planner import analyze, load, load_polar, parse_gpx
from passage_planner.clock import Clock
from passage_planner.errors import FloatPlanError
from passage_planner.floatplan import (
    CrewMember,
    build_field_values,
    fill_float_plan_pdf,
    load_crew_roster,
    load_float_plan_config,
    resolve_crew,
)
from passage_planner.passage import PassagePlan
from passage_planner.weather.model import ForecastPoint, RouteForecast

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINI_FORM = FIXTURES / "mini_float_plan.pdf"
DEPART = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _forecast(plan: PassagePlan) -> RouteForecast:
    pts = tuple(
        ForecastPoint(
            cumulative_nm=f.cumulative_nm, leg_index=f.leg_index, lat=f.lat, lon=f.lon,
            valid_time=f.eta, source="test", model="synthetic", provenance_tier="secondary",
            wind_speed_kn=13.0, wind_from_deg=170.0, wind_gust_kn=17.0, pressure_hpa=1014.0,
            wave_hs_m=0.8, wave_from_deg=170.0, wave_period_s=6.0, swell_hs_m=0.4,
        )
        for f in plan.sample_fixes(8.0)
    )
    return RouteForecast(route_name=plan.route.name, issued=DEPART, points=pts)


def _analysis_bits():
    app = load(ROOT / "config")
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    plan = PassagePlan(route=route, depart_utc=DEPART, planning_speed_kn=6.5)
    fc = _forecast(plan)
    analysis = analyze(app.vessel, plan, fc, load_polar(ROOT / "config" / "baseline.pol"))
    return app.vessel, plan, analysis


# -- config / roster loading ---------------------------------------------


def test_load_shipped_float_plan_config() -> None:
    static = load_float_plan_config(ROOT / "config" / "float_plan.toml")
    assert static.official_number == "000000"
    assert static.vessel_type == "Cutter"
    assert "NAV-Radar" in static.equipment_aboard
    assert static.operator_dob == date(1964, 1, 30)


def test_float_plan_config_rejects_unknown_equipment(tmp_path: Path) -> None:
    bad = tmp_path / "float_plan.toml"
    bad.write_text('[equipment]\naboard = ["NAV-Radar", "NOT-A-FIELD"]\n', encoding="utf-8")
    with pytest.raises(FloatPlanError, match="unknown field"):
        load_float_plan_config(bad)


def test_missing_float_plan_config_raises(tmp_path: Path) -> None:
    with pytest.raises(FloatPlanError, match="not found"):
        load_float_plan_config(tmp_path / "nope.toml")


def test_load_shipped_crew_roster() -> None:
    roster = load_crew_roster(ROOT / "config" / "crew.toml")
    assert set(roster) == {"skipper", "mate"}
    assert roster["skipper"].gender == "M"
    assert roster["mate"].dob == date(1965, 3, 31)


def test_missing_crew_file_returns_empty(tmp_path: Path) -> None:
    assert load_crew_roster(tmp_path / "nope.toml") == {}


# -- crew complement, modified per passage --------------------------------


def test_resolve_crew_roster_lookup() -> None:
    roster = load_crew_roster(ROOT / "config" / "crew.toml")
    crew = resolve_crew(["skipper", "mate"], roster)
    assert [c.name for c in crew] == ["Jane Skipper", "Alex Mate"]


def test_resolve_crew_inline_guest_not_in_roster() -> None:
    crew = resolve_crew(["Sam Guest:555-1212:M:1990-06-15"], {})
    assert crew == [CrewMember(name="Sam Guest", phone="555-1212", gender="M",
                                dob=date(1990, 6, 15))]


def test_resolve_crew_unknown_bare_name_keeps_just_the_name() -> None:
    crew = resolve_crew(["Someone Else"], {})
    assert crew == [CrewMember(name="Someone Else")]


def test_crew_member_age() -> None:
    m = CrewMember(name="x", dob=date(1964, 1, 30))
    assert m.age(date(2026, 1, 29)) == "61"   # birthday not yet reached
    assert m.age(date(2026, 1, 30)) == "62"   # birthday today


# -- field mapping ---------------------------------------------------------


def test_build_field_values_itinerary_and_statics() -> None:
    vessel, plan, analysis = _analysis_bits()
    static = load_float_plan_config(ROOT / "config" / "float_plan.toml")
    crew = resolve_crew(["skipper", "mate"], load_crew_roster(ROOT / "config" / "crew.toml"))
    clock = Clock("UTC")
    values = build_field_values(
        vessel=vessel, plan=plan, analysis=analysis, clock=clock,
        static=static, crew=crew, food_days=21, today=date(2026, 9, 10),
    )
    assert values["ID-DocRegNum"] == "000000"
    assert values["COM-DSCNo"] == vessel.mmsi
    assert values["01DepartDate"] == f"{DEPART:%m-%d-%y}"
    assert values["01DepartMode"] in {"Sail", "Motor", "Motorsail"}
    n = len(plan.route.waypoints)
    assert f"{n:02d}ArriveLocation" in values
    assert "01ArriveDate" not in values          # row 01 is depart-only
    assert values["POB-01Name"] == "Jane Skipper"
    assert values["POB-02Name"] == "Alex Mate"
    assert values["POB-01Age"] == "62"
    assert values["ADD-FoodDays"] == "21"
    assert "ADD-WaterDays" in values              # vessel.water is configured
    for name in static.equipment_aboard:
        assert values[name] == "/Yes"


def test_build_field_values_omits_blank_fields() -> None:
    vessel, plan, analysis = _analysis_bits()
    static = load_float_plan_config(ROOT / "config" / "float_plan.toml")
    # Force a blank value rather than relying on the shipped config happening to
    # leave a field empty -- config/float_plan.toml gets filled in over time
    # (e.g. the EPIRB UIN, once real) and this test shouldn't break when it does.
    static = replace(static, epirb_uin="")
    clock = Clock("UTC")
    values = build_field_values(
        vessel=vessel, plan=plan, analysis=analysis, clock=clock,
        static=static, crew=[], today=date(2026, 9, 10),
    )
    assert "EPIRB-UIN" not in values              # blank -- forced above
    assert "ADD-FoodDays" not in values           # not supplied
    assert not any(k.startswith("POB-") for k in values)


def test_itinerary_uses_waypoint_name_over_placeholder() -> None:
    vessel, plan, analysis = _analysis_bits()
    static = load_float_plan_config(ROOT / "config" / "float_plan.toml")
    clock = Clock("UTC")
    values = build_field_values(
        vessel=vessel, plan=plan, analysis=analysis, clock=clock,
        static=static, crew=[], today=date(2026, 9, 10),
    )
    last = f"{len(plan.route.waypoints):02d}ArriveLocation"
    wp = plan.route.waypoints[-1]
    assert values[last] == wp.name or "N," in values[last]  # named or a DM position


# -- PDF fill ---------------------------------------------------------------


def test_fill_float_plan_pdf_round_trips(tmp_path: Path) -> None:
    from pypdf import PdfReader

    values = {
        "ID-VesselName": "YOUR VESSEL / Your City, ST",
        "COM-DSCNo": "000000000",
        "ID-Type": "Cutter",
        "NAV-Radar": "/Yes",
        "01DepartDate": "09-12-26",
        "01DepartTime": "6:00 am",
        "01DepartLocation": "Your Harbor",
        "02ArriveDate": "09-12-26",
        "02ArriveTime": "1:00 pm",
        "02ArriveLocation": "30 12.000N, 89 00.000W",
        "POB-01Name": "Jane Skipper",
        "POB-01Gender": "M",
    }
    out = tmp_path / "filled.pdf"
    written = fill_float_plan_pdf(MINI_FORM, out, values)
    assert written == out and out.is_file()

    filled = PdfReader(out).get_fields()
    assert filled["ID-VesselName"]["/V"] == "YOUR VESSEL / Your City, ST"
    assert filled["ID-Type"]["/V"] == "Cutter"
    assert filled["NAV-Radar"]["/V"] == "/Yes"
    assert filled["01DepartLocation"]["/V"] == "Your Harbor"


def test_fill_float_plan_pdf_rejects_unknown_field(tmp_path: Path) -> None:
    with pytest.raises(FloatPlanError, match="unknown field"):
        fill_float_plan_pdf(MINI_FORM, tmp_path / "out.pdf", {"NOT-A-REAL-FIELD": "x"})


def test_fill_float_plan_pdf_missing_template_raises(tmp_path: Path) -> None:
    with pytest.raises(FloatPlanError, match="not found"):
        fill_float_plan_pdf(tmp_path / "nope.pdf", tmp_path / "out.pdf", {})


def test_fill_float_plan_pdf_bad_pdf_raises(tmp_path: Path) -> None:
    bad = tmp_path / "not_a_pdf.pdf"
    bad.write_text("this is not a pdf", encoding="utf-8")
    with pytest.raises(FloatPlanError, match="could not be read"):
        fill_float_plan_pdf(bad, tmp_path / "out.pdf", {})
