"""Tests for passage_planner.briefing."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from passage_planner import (
    BriefingInputs,
    PassagePlan,
    analyze,
    build_briefing,
    load,
    load_polar,
    parse_gpx,
)
from passage_planner.polar import Polar
from passage_planner.weather.model import ForecastPoint, RouteForecast

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEPART = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _forecast(
    plan: PassagePlan, *, tws: float, wind_from: float, hs: float,
    current_kn: float | None = None, current_to: float | None = None,
) -> RouteForecast:
    pts = tuple(
        ForecastPoint(
            cumulative_nm=f.cumulative_nm, leg_index=f.leg_index, lat=f.lat, lon=f.lon,
            valid_time=f.eta, source="test", model="synthetic", provenance_tier="secondary",
            wind_speed_kn=tws, wind_from_deg=wind_from, wind_gust_kn=tws * 1.3,
            pressure_hpa=1014.0, wave_hs_m=hs, wave_from_deg=wind_from, wave_period_s=6.0,
            swell_hs_m=hs * 0.5, current_kn=current_kn, current_to_deg=current_to,
        )
        for f in plan.sample_fixes(8.0)
    )
    return RouteForecast(route_name=plan.route.name, issued=DEPART, points=pts)


def _inputs(**over) -> BriefingInputs:
    app = load(ROOT / "config")
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    plan = PassagePlan(route=route, depart_utc=DEPART, planning_speed_kn=6.5)
    fc = _forecast(
        plan, tws=over.pop("tws", 13.0), wind_from=over.pop("wind_from", 170.0),
        hs=over.pop("hs", 0.8), current_kn=over.pop("current_kn", None),
        current_to=over.pop("current_to", None),
    )
    analysis = analyze(app.vessel, plan, fc, load_polar(ROOT / "config" / "baseline.pol"))
    base = {
        "vessel": app.vessel, "plan": plan, "analysis": analysis, "forecast": fc,
        "crew": ("Skipper", "Mate"), "generated_at": DEPART,
    }
    base.update(over)
    return BriefingInputs(**base)


def test_all_sections_present() -> None:
    md = build_briefing(_inputs())
    for heading in [
        "# Passage Briefing",
        "## 1. Executive Voyage Briefing",
        "## 2. Vessel & Planning Assumptions",
        "## 3. Weather Picture",
        "## 4. Step-by-Step Weather Routing Analysis",
        "## 5. Depth, Clearance, Tide & Navigational Notices",
        "## 6. Arrival & Immigration Dossier",
        "## 7. Logistics & Support Matrix",
        "## 8. Watch Plan",
        "## 9. Float Plan",
        "CRITICAL NOTICE:",
    ]:
        assert heading in md, heading


def test_section_4_current_column_dashes_when_no_current_data() -> None:
    md = build_briefing(_inputs())
    assert "| Current | SOG kn |" in md
    # No current supplied by _forecast's default -> every leg row shows the dash.
    table = md.split("## 4. Step-by-Step Weather Routing Analysis")[1].split("##")[0]
    rows = [line for line in table.splitlines() if line.startswith("| ")][1:]  # skip header
    assert rows  # the fixture route has legs
    assert all(" | — | " in row for row in rows)


def test_section_4_current_column_shows_signed_correction() -> None:
    plan = PassagePlan(
        route=parse_gpx(FIXTURES / "nola_to_gulfport.gpx"), depart_utc=DEPART,
        planning_speed_kn=6.5,
    )
    leg0 = plan.route.legs[0]
    md = build_briefing(_inputs(current_kn=1.2, current_to=leg0.bearing_deg))
    table = md.split("## 4. Step-by-Step Weather Routing Analysis")[1].split("##")[0]
    first_row = next(line for line in table.splitlines() if line.startswith("| 0 |"))
    assert "+1.2" in first_row


def test_provisional_polar_banner_and_disclaimer() -> None:
    md = build_briefing(_inputs())
    assert "PROVISIONAL POLAR" in md
    assert md.rstrip().endswith(
        "the final on-the-water discretion of the Master."
    )


def test_polar_table_row_shows_actually_used_source_not_just_config_mode() -> None:
    """§2's Polar row must name what was actually resolved (e.g. a fallback to
    the static file), not just echo `[performance] polar_source`'s declared
    mode - a `derived` config with an unreachable InfluxDB should be visibly
    distinguishable from a `derived` config that actually read live data."""
    inputs = _inputs()
    # pin the declared mode here - the real config/vessel.toml says "baseline" since 2026-10-07
    v = inputs.vessel
    derived = replace(v.performance, polar_source="derived")
    inputs = replace(inputs, vessel=replace(v, performance=derived))
    fallback_analysis = replace(
        inputs.analysis, polar_source="config\\baseline.pol", polar_provisional=True
    )
    md = build_briefing(replace(inputs, analysis=fallback_analysis))
    assert "config\\baseline.pol" in md
    assert "`derived` mode" in md

    live_analysis = replace(
        inputs.analysis,
        polar_source="influxdb:boat/polar_baseline",
        polar_provisional=False,
    )
    md_live = build_briefing(replace(inputs, analysis=live_analysis))
    assert "influxdb:boat/polar_baseline" in md_live


def test_watch_table_short_passage_single_rotation() -> None:
    md = build_briefing(_inputs(crew=("Skipper", "Mate"), watch_hours=6.0))
    assert "Skipper" in md and "mate" in md
    assert "6-hour watches" in md
    assert "Handover checklist" in md
    assert "shorter than one watch rotation" in md


def test_watch_table_long_passage_rotates_all_crew() -> None:
    inp = _inputs(crew=("Skipper", "Mate", "Sam"), watch_hours=6.0)
    long_analysis = replace(inp.analysis, eta=inp.analysis.depart + timedelta(hours=36))
    md = build_briefing(replace(inp, analysis=long_analysis))
    assert "**Skipper**" in md and "**Mate**" in md and "**Sam**" in md


def test_bahamas_ciq_populates() -> None:
    md = build_briefing(_inputs(destination_country="Bahamas"))
    assert "Click2Clear" in md
    assert "click2clear.bahamas.gov.bs" in md
    assert "yellow Q flag" in md


def test_ciq_alias_resolves_tci() -> None:
    md = build_briefing(_inputs(destination_country="TCI"))
    assert "Turks & Caicos Islands" in md
    assert "Sapodilla Bay" in md


def test_unknown_country_gets_a_named_skeleton() -> None:
    md = build_briefing(_inputs(destination_country="Freedonia"))
    sec6 = md.split("## 6.")[1].split("## 7.")[0]
    assert "### Freedonia" in sec6
    assert "Not in the built-in registry" in sec6
    assert "ports of entry" in sec6.lower()


def test_multi_country_dossier() -> None:
    md = build_briefing(_inputs(destination_country="Bahamas, Turks and Caicos"))
    sec6 = md.split("## 6.")[1].split("## 7.")[0]
    assert "### The Bahamas" in sec6
    assert "### Turks & Caicos Islands" in sec6
    assert "click2clear.bahamas.gov.bs" in sec6


def test_unknown_destination_is_pending_not_omitted() -> None:
    md = build_briefing(_inputs(destination_country=None))
    assert "## 6. Arrival & Immigration Dossier" in md
    assert "PENDING" in md.split("## 6.")[1].split("## 7.")[0]


def test_supplied_ciq_markdown_wins() -> None:
    md = build_briefing(_inputs(ciq_markdown="Custom clearance notes for Cat Island."))
    assert "Custom clearance notes for Cat Island." in md


def test_fuel_shortfall_surfaces_in_executive() -> None:
    app = load(ROOT / "config")
    thin = replace(app.vessel, propulsion=replace(app.vessel.propulsion, fuel_capacity_gal=4.0))
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    plan = PassagePlan(route=route, depart_utc=DEPART, planning_speed_kn=6.5)
    fc = _forecast(plan, tws=2.0, wind_from=180.0, hs=0.5)  # calm -> all motor
    analysis = analyze(thin, plan, fc, load_polar(ROOT / "config" / "baseline.pol"))
    md = build_briefing(BriefingInputs(
        vessel=thin, plan=plan, analysis=analysis, forecast=fc, generated_at=DEPART,
    ))
    assert "SHORTFALL" in md


def test_buddy_boat_drives_fleet_governing_depth_and_clearance() -> None:
    md = build_briefing(_inputs())
    assert "SV Example Buddy" in md
    sec5 = md.split("## 5.")[1].split("## 6.")[0]
    # Arrival, departure and anchoring use the keel-UP draft: 8 ft + 2 ft UKC = 10;
    # 90 ft air draft + 5 ft buffer = 95. The 14 ft keel-down draft is open water only.
    assert "**10 ft** charted" in sec5
    assert "95 ft" in sec5
    assert "16 ft" not in sec5
    assert "shared stops govern" in sec5
    assert "Travelling in company with SV Example Buddy." in md


def _briefing_with_buddies(buddies) -> str:
    app = load(ROOT / "config")
    vessel = replace(app.vessel, buddy_boats=tuple(buddies))
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    plan = PassagePlan(route=route, depart_utc=DEPART, planning_speed_kn=6.5)
    fc = _forecast(plan, tws=13.0, wind_from=170.0, hs=0.8)
    analysis = analyze(vessel, plan, fc, load_polar(ROOT / "config" / "baseline.pol"))
    return build_briefing(BriefingInputs(
        vessel=vessel, plan=plan, analysis=analysis, forecast=fc, generated_at=DEPART,
    ))


def test_shipped_lifting_keel_gives_a_keel_up_minimum() -> None:
    md = build_briefing(_inputs())
    sec2 = md.split("## 2.")[1].split("## 3.")[0]
    assert "lifting keel" in sec2
    assert "for arrival, departure and anchoring" in sec2
    assert "open water only" in sec2 or "at sea only" in sec2
    assert "Fleet worst case" not in sec2
    sec5 = md.split("## 5.")[1].split("## 6.")[0]
    # keel-up fleet minimum: max(8 US, 8 SB up) + 2 UKC = 10 ft, and no 16 ft keel-down figure
    assert "**10 ft** charted" in sec5 and "16 ft" not in sec5


def test_unknown_retracted_draft_prompts_for_it() -> None:
    app = load(ROOT / "config")
    buddies = [replace(b, draft_up_ft=None) for b in app.vessel.buddy_boats]
    sec5 = _briefing_with_buddies(buddies).split("## 5.")[1].split("## 6.")[0]
    assert "retracted draft not on file" in sec5


def test_fuel_section_reports_computed_engine_hours() -> None:
    md = build_briefing(_inputs(tws=2.0, wind_from=180.0))
    seg = md.split("### 7a. Fuel")[1].split("### 7b.")[0]
    assert "engine time this passage" in seg
    assert "gal" in seg


def test_times_render_local_to_position() -> None:
    from passage_planner import Clock

    clock = Clock("America/Chicago", lambda la, lo: "America/Chicago")
    md = build_briefing(_inputs(clock=clock))
    assert "CDT" in md or "CST" in md
    assert "12:00Z" not in md
    assert "All times are local" in md
    assert "boat time = departure zone" in md  # watch plan note


def test_water_autonomy_block_flags_uncommissioned_watermaker() -> None:
    seg = build_briefing(_inputs()).split("### 7b.")[1].split("### 7c.")[0]
    assert "150 gal tankage" in seg
    assert "not commissioned" in seg
    assert "25.0 days" in seg  # 150 / (2 crew * 3 gpd)


def test_water_block_absent_when_unconfigured() -> None:
    inp = _inputs()
    vessel_no_water = replace(inp.vessel, water=None)
    seg = build_briefing(replace(inp, vessel=vessel_no_water)).split("### 7b.")[1]
    assert "gal tankage" not in seg


def test_logistics_pending_points_at_the_template_command() -> None:
    seg = build_briefing(_inputs()).split("### 7b.")[1].split("### 7c.")[0]
    assert "logistics-template" in seg


def test_logistics_markdown_renders_with_coverage_note() -> None:
    lm = "\n".join([
        "### Pensacola",
        "- **Groceries:** Publix, 1.2 mi — marina courtesy car",
        "- **Fuel dock:** Palafox Pier, diesel, 8 ft alongside",
    ])
    seg = build_briefing(_inputs(logistics_markdown=lm)).split("### 7b.")[1]
    assert "### Pensacola" in seg
    assert "Publix" in seg
    assert "⚠️ **Pensacola** — still to research:" in seg
    assert "Chandlery" in seg  # a missing field is named


def test_notices_section_pending_without_feed() -> None:
    md = build_briefing(_inputs())
    seg = md.split("## 5.")[1].split("## 6.")[0]
    assert "5d. Navigational warnings" in seg
    assert "PENDING" in seg and "msi.nga.mil" in seg


def test_notices_section_renders_warnings() -> None:
    from datetime import UTC, datetime

    from passage_planner.notices import NavWarning, NoticePosition, NoticeReport

    w = NavWarning(
        area="NAVAREA IV", number=42, year=2026, subregion="28",
        issued=datetime(2026, 9, 1, tzinfo=UTC), authority="NGA",
        text="GULF OF MEXICO.\nWRECK REPORTED IN 30-12.0N 089-00.0W.",
        positions=(NoticePosition(30.2, -89.0),), closest_nm=3.0,
    )
    report = NoticeReport(
        route_name="x", generated=datetime.now(UTC), corridor_nm=75.0,
        areas_checked=("NAVAREA IV", "HYDROLANT"), warnings=(w,), total_in_force=9,
    )
    seg = build_briefing(_inputs(notices=report)).split("### 5d.")[1]
    assert "NAVAREA IV 42/26" in seg
    assert "WRECK REPORTED" in seg
    assert "3 NM off track" in seg


def test_below_fleet_target_cautions_collapse_to_one_summary_line() -> None:
    from passage_planner.analysis import BELOW_FLEET_TARGET

    inp = _inputs(tws=10.0, wind_from=135.0)
    slow = [f for la in inp.analysis.legs for f in la.flags if f.kind == BELOW_FLEET_TARGET]
    assert slow, "fixture should produce at least one slow sailing leg"
    md = build_briefing(inp)
    executive = md.split("## 2.")[0]
    # one summary line, not one caution per leg
    assert executive.count("fleet target") == 1
    assert f"{len(slow)} of {len(inp.analysis.legs)} legs below" in executive
    # each slow leg is still listed in the Flags column, and not repeated as a call-out
    assert md.count("is below the") == len(slow)
    assert "Leg call-outs" not in md or "fleet target" not in md.split("Leg call-outs")[1]


def _briefing_with_cat(wind_from: float, boats=None) -> tuple[str, list]:
    from passage_planner import fleet
    from passage_planner.analysis import MULTIHULL_UPWIND

    app = load(ROOT / "config")
    cat = fleet.boat_from_dict({
        "name": "SV Salt Cat", "hull": "catamaran", "make": "Lagoon", "model": "45",
        "draft_ft": 4.0, "air_draft_ft": 62.0, "loa_ft": 45, "beam_ft": 24,
    })
    vessel = replace(app.vessel, buddy_boats=tuple(boats if boats is not None else [cat]))
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    plan = PassagePlan(route=route, depart_utc=DEPART, planning_speed_kn=6.5)
    fc = _forecast(plan, tws=12.0, wind_from=wind_from, hs=0.8)
    analysis = analyze(vessel, plan, fc, load_polar(ROOT / "config" / "baseline.pol"))
    md = build_briefing(BriefingInputs(
        vessel=vessel, plan=plan, analysis=analysis, forecast=fc, generated_at=DEPART,
    ))
    flags = [f for la in analysis.legs for f in la.flags if f.kind == MULTIHULL_UPWIND]
    return md, flags


def test_catamaran_in_company_flags_upwind_legs_once_in_the_summary() -> None:
    md, flags = _briefing_with_cat(60.0)  # legs 0 and 1 are close to the wind
    assert {f.leg_index for f in flags} == {0, 1}
    executive = md.split("## 2.")[0]
    assert executive.count("Upwind legs with multihull in company") == 1
    assert "SV Salt Cat" in executive and "legs 0, 1" in executive
    # each flagged leg is still in the Flags column, and not repeated as a call-out
    assert md.count("upwind with SV Salt Cat (catamaran) in company") == 2


def test_no_multihull_flag_for_a_monohull_or_when_travelling_alone() -> None:
    from passage_planner import fleet

    mono = fleet.boat_from_dict({"name": "SV Blue Mono", "draft_ft": 6, "air_draft_ft": 60})
    for boats in ([mono], []):
        md, flags = _briefing_with_cat(60.0, boats)
        assert flags == [] and "multihull in company" not in md


def test_section_2_describes_the_boat_with_make_model_hull_and_size() -> None:
    md, _ = _briefing_with_cat(180.0)
    sec2 = md.split("## 2.")[1].split("## 3.")[0]
    assert "**SV Salt Cat** (Lagoon 45, catamaran)" in sec2
    assert "45 ft LOA, beam 24 ft" in sec2
    assert "Fleet planning figures" in sec2
    # a boat with no make, model or size stays terse
    mono_md, _ = _briefing_with_cat(180.0, [])
    assert "Fleet (joint planning)" not in mono_md


def _briefing_with_buddy_polar(buddy_polars) -> tuple[str, list]:
    from passage_planner import fleet
    from passage_planner.analysis import BUDDY_BELOW_TARGET, MULTIHULL_UPWIND, refine

    app = load(ROOT / "config")
    cat = fleet.boat_from_dict({
        "name": "SV Salt Cat", "hull": "catamaran", "make": "Lagoon", "model": "45",
        "draft_ft": 4.0, "air_draft_ft": 62.0, "loa_ft": 45, "beam_ft": 24,
    })
    vessel = replace(app.vessel, buddy_boats=(cat,))
    route = parse_gpx(FIXTURES / "nola_to_gulfport.gpx")
    plan = PassagePlan(route=route, depart_utc=DEPART, planning_speed_kn=6.5)
    fc = _forecast(plan, tws=12.0, wind_from=60.0, hs=0.8)
    polars = {cat.id: buddy_polars} if buddy_polars is not None else {}
    analysis = refine(
        vessel, plan, load_polar(ROOT / "config" / "baseline.pol"),
        lambda _fixes: fc, buddy_polars=polars,
    )
    md = build_briefing(BriefingInputs(
        vessel=vessel, plan=plan, analysis=analysis, forecast=fc, generated_at=DEPART,
    ))
    below = [f for la in analysis.legs for f in la.flags if f.kind == BUDDY_BELOW_TARGET]
    upwind = [f for la in analysis.legs for f in la.flags if f.kind == MULTIHULL_UPWIND]
    return md, below, upwind


def test_buddy_with_slow_real_polar_flags_below_target_every_leg() -> None:
    # A polar that never reaches the (default) fleet target, at any TWA/TWS -
    # so it should catch legs the crude multihull-upwind heuristic wouldn't
    # (this fixture's route has legs both upwind and off the wind at 60 deg).
    slow = Polar(
        twa_axis=(0.0, 180.0), tws_axis=(0.0, 40.0),
        grid=((1.0, 1.0), (1.0, 1.0)), source="test", provisional=False,
    )
    md, below, upwind = _briefing_with_buddy_polar(slow)
    assert below, "a boat that never makes the fleet target should be flagged"
    assert upwind == [], "a real polar replaces the crude multihull heuristic, not adds to it"
    executive = md.split("## 2.")[0]
    assert "own polar falls below the fleet target" in executive
    assert executive.count("own polar falls below the fleet target") == 1


def test_buddy_with_fast_real_polar_flags_nothing() -> None:
    fast = Polar(
        twa_axis=(0.0, 180.0), tws_axis=(0.0, 40.0),
        grid=((20.0, 20.0), (20.0, 20.0)), source="test", provisional=False,
    )
    md, below, upwind = _briefing_with_buddy_polar(fast)
    assert below == [] and upwind == []
    assert "own polar falls below" not in md


def test_no_real_polar_keeps_the_multihull_heuristic() -> None:
    _md, below, upwind = _briefing_with_buddy_polar(None)
    assert below == []
    assert upwind, "with no real polar on file, the generic heuristic should still fire upwind"


# -- section 3d: Chris Parker professional forecast (weather-routing step 6) -

_PARKER_SAMPLE = (
    Path(__file__).resolve().parent / "fixtures" / "parker_sample.txt"
).read_text(encoding="utf-8")


def test_parker_section_absent_when_no_text_supplied() -> None:
    md = build_briefing(_inputs())
    assert "3d. Professional forecast" not in md
    assert "Professional Forecast" not in md


def test_parker_section_renders_when_text_supplied() -> None:
    md = build_briefing(_inputs(parker_text=_PARKER_SAMPLE))
    assert "### 3d. Professional forecast" in md
    assert "[Professional Forecast: Marine Weather Center]" in md
    assert "Wx Update, E Caribbean, Mon15, 8am" in md


def test_parker_section_flags_a_real_disagreement() -> None:
    # The sample's WIND section peaks at 27 kn; force a low model forecast
    # (light air) so the two peaks diverge past the default threshold.
    md = build_briefing(_inputs(parker_text=_PARKER_SAMPLE, tws=6.0, wind_from=170.0))
    sec3d = md.split("### 3d. Professional forecast")[1].split("## 4.")[0]
    assert "disagree" in sec3d.lower()
