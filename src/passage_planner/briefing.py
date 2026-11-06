"""Assemble a passage briefing (Markdown) from the analysis, on the phase
structure of the skipper's Departure/Arrival SOP.

Purpose: Build the 9-section passage briefing Markdown (executive summary,
    assumptions, weather, routing table, depth/clearance, arrival dossier,
    logistics, watch plan, float plan) from BriefingInputs.
Version: v5 (2026-09-22) - a new grouped executive-summary line for
    BUDDY_BELOW_TARGET (a buddy boat's own real polar falling below the
    fleet target, see analysis.py/boat_lookup.py). v4: trimmed all sections' rendered text to terse
    facts/limitation callouts, no commentary; wired comms Calendar-check
    arrival date through; section 4's speed column is now labeled SOG kn
    with a Current column (weather-routing step 2 - the value already
    includes the current correction, see analysis.py); section 3d, the
    optional a marine forecaster professional forecast (weather-routing step 6) -
    renders only when BriefingInputs.parker_text is set, no PENDING block
    when absent (see marine_forecaster.py's module docstring for why).
Scope: PRODUCTION - core output of passage-planner; called by service.py
    (CLI and web) and converted by export.write_briefing().
Architecture: Sections the tool can fully compute are populated from the
    passage analysis. Sections that depend on data feeds not yet wired (NHC
    tropical, ENC depth, the CIQ portals, the maintenance DB) render a clear
    **PENDING** block naming what is needed, and accept hand-supplied
    Markdown via :class:`BriefingInputs` so the document is complete now.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .activecaptain.model import BridgeInfo, HazardInfo, MarinaInfo, RoutePoi
from .analysis import (
    BELOW_FLEET_TARGET,
    BUDDY_BELOW_TARGET,
    MULTIHULL_UPWIND,
    PassageAnalysis,
    SailMode,
    Severity,
)
from .ciq import render_dossier
from .clock import Clock
from .comms import CommsConfig, render_comms
from .config import VesselConfig
from .consular import ConsularReport, render_consular_markdown
from .hydro.model import HydroReport
from .logistics import coverage_gaps, strip_template_preamble
from .marine_forecaster import compare_with_model, render_parker_markdown
from .marine_forecaster import parse as parse_parker
from .notices import NoticeReport, render_notices_markdown
from .ops import DueStatus as MaintenanceDue
from .ops import MaintenanceItem, MaintenanceReport
from .papers.model import DocFinding, PapersReport
from .passage import PassagePlan
from .weather.compare import ForecastComparison, render_confidence_markdown
from .weather.model import RouteForecast
from .weather.tropical import TropicalOutlook, render_tropical_markdown

TOOL_TAG = "passage-planner"

MARITIME_DISCLAIMER = (
    "CRITICAL NOTICE: This analysis is a preliminary planning draft generated using AI "
    "modeling and crowd-sourced data. It does not replace official hydrographic office "
    "publications, active local Notice to Mariners, localized ECDIS/Garmin MFD mapping, or "
    "the final on-the-water discretion of the Master."
)

_MODE_LABEL = {
    SailMode.SAIL: "Sail",
    SailMode.MOTOR: "Motor",
    SailMode.MOTORSAIL: "Motorsail",
}


@dataclass(frozen=True, slots=True)
class BriefingInputs:
    vessel: VesselConfig
    plan: PassagePlan
    analysis: PassageAnalysis
    forecast: RouteForecast

    crew: tuple[str, ...] = ("Skipper", "Mate")
    watch_hours: float = 6.0
    destination_country: str | None = None

    hydro: HydroReport | None = None
    notices: NoticeReport | None = None
    notices_markdown: str | None = None
    consular: ConsularReport | None = None
    maintenance: MaintenanceReport | None = None
    papers: PapersReport | None = None
    forecast_comparison: ForecastComparison | None = None
    ciq_markdown: str | None = None
    logistics_markdown: str | None = None
    comms_config: CommsConfig | None = None
    tropical: TropicalOutlook | None = None
    tropical_markdown: str | None = None
    float_plan_recipient: str = "Shore Contact"
    generated_at: datetime | None = None
    forecast_models_note: str | None = None
    clock: Clock | None = None
    parker_text: str | None = None
    activecaptain_pois: tuple[RoutePoi, ...] = ()


# ---------------------------------------------------------------------------
# small format helpers
# ---------------------------------------------------------------------------

_DEFAULT_CLOCK = Clock()
# Flag kinds shown once in the executive summary (and per leg in the Flags column)
# instead of again as a caution and a leg call-out.
_GROUPED_FLAG_KINDS = frozenset({BELOW_FLEET_TARGET, MULTIHULL_UPWIND, BUDDY_BELOW_TARGET})


def _clk(inp: BriefingInputs) -> Clock:
    return inp.clock or _DEFAULT_CLOCK


def _hm(hours: float) -> str:
    h = int(hours)
    m = round((hours - h) * 60)
    if m == 60:
        h, m = h + 1, 0
    return f"{h} h {m:02d} m"


def _compass(deg: float) -> str:
    pts = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return pts[int((deg % 360) / 22.5 + 0.5) % 16]


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------


def _header(inp: BriefingInputs) -> str:
    a = inp.analysis
    gen = inp.generated_at or datetime.now(inp.plan.depart.tzinfo)
    wp0 = inp.plan.route.waypoints[0]
    lines = [
        f"# Passage Briefing — {a.route_name}",
        "",
        f"*Generated {_clk(inp).label(gen, wp0.lat, wp0.lon)} by {TOOL_TAG} v{_version()} · "
        f"{a.iterations} analysis iteration(s)*",
        "",
        "*All times are local to the position they describe.*",
    ]
    if a.polar_provisional:
        lines += [
            "",
            "> **PROVISIONAL POLAR** — hand-built, not measured. Sail-leg timings: "
            "wide-band. Motoring: firm.",
        ]
    return "\n".join(lines)


def _executive(inp: BriefingInputs) -> str:
    a = inp.analysis
    v = inp.vessel
    wps = inp.plan.route.waypoints
    dep = _clk(inp).label(a.depart, wps[0].lat, wps[0].lon)
    eta = _clk(inp).label(a.eta, wps[-1].lat, wps[-1].lon)
    hazards_warn = [h for h in a.hazards if h.severity is Severity.WARNING]
    hazards_caution = [h for h in a.hazards if h.severity is Severity.CAUTION]
    # One line for the many "sailing below the fleet target" cautions; the Flags column
    # in section 4 still lists each leg.
    slow = [h for h in hazards_caution if h.kind == BELOW_FLEET_TARGET]
    upwind_multi = [h for h in hazards_caution if h.kind == MULTIHULL_UPWIND]
    buddy_slow = [h for h in hazards_caution if h.kind == BUDDY_BELOW_TARGET]
    hazards_caution = [h for h in hazards_caution if h.kind not in _GROUPED_FLAG_KINDS]

    lines = [
        "## 1. Executive Voyage Briefing",
        "",
        f"- **Route:** {a.route_name} — {len(a.legs)} legs, **{a.total_nm:.0f} NM**",
        f"- **Depart:** {dep}  →  **ETA:** {eta}  "
        f"(**{_hm(a.duration_h)}**, avg {a.total_nm / max(a.duration_h, 0.1):.1f} kn)",
        f"- **Under sail:** {a.sail_nm:.0f} NM ({a.sailing_fraction * 100:.0f}%) · "
        f"**under power:** {a.motor_nm + a.motorsail_nm:.0f} NM "
        f"({a.under_power_fraction * 100:.0f}%)",
        f"- **Fuel:** projected **{a.total_fuel_gal:.0f} gal** of "
        f"{a.usable_fuel_gal:.0f} usable (reserve {v.propulsion.fuel_reserve_frac * 100:.0f}% "
        f"held back) — margin **{a.fuel_margin_gal:.0f} gal**"
        + ("  ⚠️ **SHORTFALL**" if a.fuel_shortfall else ""),
    ]
    lines.append("")
    if hazards_warn:
        lines.append("**Critical hazards:**")
        lines += [f"- ⚠️ {h.text}" + (f" (leg {h.leg_index})" if h.leg_index is not None else "")
                  for h in hazards_warn]
    else:
        lines.append("**Critical hazards:** none flagged against the current forecast.")
    if hazards_caution or slow or upwind_multi or buddy_slow:
        lines.append("")
        lines.append("**Cautions:**")
        lines += [f"- {h.text}" + (f" (leg {h.leg_index})" if h.leg_index is not None else "")
                  for h in hazards_caution]
        if slow:
            target = inp.vessel.performance.fleet_target_avg_kn
            legs = ", ".join(str(h.leg_index) for h in slow)
            lines.append(
                f"- {len(slow)} of {len(a.legs)} legs below {target:.1f} kn fleet target "
                f"(legs {legs}) — engine time needed to hold it. Per-leg speeds: Flags "
                "column, section 4."
            )
        if upwind_multi:
            boats = ", ".join(sorted({b.name for b in v.buddy_boats if b.is_multihull}))
            leg_ids = {h.leg_index for h in upwind_multi if h.leg_index is not None}
            legs = ", ".join(str(i) for i in sorted(leg_ids))
            lines.append(
                f"- Upwind legs with multihull in company ({boats}): legs {legs} — expect "
                "her to fall off or motor; plan fleet pace/fuel accordingly."
            )
        if buddy_slow:
            legs = ", ".join(str(h.leg_index) for h in buddy_slow)
            lines.append(
                f"- {len(buddy_slow)} leg(s) where a buddy boat's own polar falls below "
                f"the fleet target (legs {legs}) — Flags column, section 4, for which boat "
                "and speed."
            )
    lines += [
        "",
        "**Go/no-go:** manual call. Confirm NWS Offshore/Coastal Waters text forecast and "
        "NHC Tropical Weather Outlook agree with the model picture below.",
    ]
    return "\n".join(lines)


def _assumptions(inp: BriefingInputs) -> str:
    v = inp.vessel
    d, ad, pr, pf, th, wa = (
        v.draft, v.air_draft, v.propulsion, v.performance, v.safety.thresholds,
        v.safety.watch_alert,
    )
    lines = [
        "## 2. Vessel & Planning Assumptions",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Vessel | {v.name} — {v.model} ({v.year}), {v.rig} |",
        f"| Planning draft | {d.planning_ft:.1f} ft + {d.ukc_margin_ft:.1f} ft UKC → "
        f"need **{d.required_charted_ft:.1f} ft** charted + tide |",
        f"| Air draft | {ad.planning_ft:.0f} ft + {ad.clearance_buffer_ft:.0f} ft buffer → "
        f"need **{ad.required_clearance_ft:.0f} ft** clearance (no fixed-bridge ICW) |",
        f"| Engine | {pr.engine} — cruise {pr.cruise.speed_kn:.1f} kn @ "
        f"{pr.cruise.burn_gph:.2f} gph (motorsail {pr.cruise.motorsail_burn_gph:.2f} gph) |",
        f"| Fuel | {pr.fuel_capacity_gal:.0f} gal capacity, {pr.usable_fuel_gal:.0f} usable |",
        f"| Polar | `{pf.polar_source}` mode → actually used `{inp.analysis.polar_source}`"
        + ("  *(PROVISIONAL)*" if inp.analysis.polar_provisional else "") + " |",
        f"| Sail/motor rule | sail to {pf.light_air_speed_kn:.1f} kn, motor below; "
        f"hold {pf.fleet_target_avg_kn:.1f} kn fleet average with the engine |",
        "",
        "**Watch-standing limits (from the SOP — wake the captain):**",
        "",
        f"- Sustained wind over **{th.wind_sustained_max_kn:.0f} kn** or gusts over "
        f"**{th.wind_gust_max_kn:.0f} kn**; forecast seas over **{th.wave_hs_max_m:.1f} m**",
        f"- Wind speed change of **±{wa.wind_speed_change_kn:.0f} kn** or a persistent "
        f"shift of **{wa.wind_dir_shift_deg:.0f}°**",
        f"- Barometric drop of **{wa.pressure_drop_mb_3h:.0f} mb in 3 h**",
        f"- Any CPA under **{wa.hazard_cpa_nm:.1f} NM** without a VHF-coordinated passing plan",
        f"- Reef above **{pf.reef_heel_deg:.0f}° heel** or ahead of any expected squall",
    ]
    if v.buddy_boats:
        lines += ["", "**Fleet (joint planning):**", ""]
        for b in v.buddy_boats:
            tele = "live data" if b.telemetry else "no live data"
            if b.keel_up_known:
                draft = (
                    f"draws {b.draft_up_ft:.0f} ft for arrival, departure and anchoring "
                    f"(keel up); {b.draft_ft:.0f} ft keel down is for open water only"
                )
            elif b.lifting_keel:
                draft = f"draft {b.draft_ft:.0f} ft keel-down (lifting keel, up-draft TBD)"
            else:
                draft = f"draft {b.draft_ft:.0f} ft"
            kind = f" ({b.description})" if b.make or b.model or b.is_multihull else ""
            size = ", ".join(
                x for x in (
                    f"{b.loa_ft:.0f} ft LOA" if b.loa_ft else "",
                    f"beam {b.beam_ft:.0f} ft" if b.beam_ft else "",
                ) if x
            )
            lines.append(
                f"- **{b.name}**{kind} — {draft}, air draft {b.air_draft_ft:.0f} ft"
                + (f", {size}" if size else "")
                + f" ({tele})"
                + (f". {b.notes}" if b.notes else "")
            )
        if v.fleet_has_lifting_keel and all(
            b.keel_up_known for b in v.buddy_boats if b.lifting_keel
        ):
            draft_note = (
                f"draft **{v.fleet_draft_min_ft:.0f} ft** for arrival, departure and "
                "anchoring (lifting keels up; the keel-down draft matters at sea only)"
            )
        else:
            draft_note = f"draft **{v.fleet_draft_ft:.0f} ft**"
        lines.append(
            f"- **Fleet planning figures:** {draft_note}, "
            f"air draft **{v.fleet_air_draft_ft:.0f} ft** — governs shared anchorages, "
            f"channels, bridges (§5). Routing analysis below is {v.name} only; buddy "
            "polars differ."
        )
    return "\n".join(lines)


def _weather(inp: BriefingInputs) -> str:
    fc = inp.forecast
    winds = [p.wind_speed_kn for p in fc.points if p.wind_speed_kn is not None]
    gusts = [p.wind_gust_kn for p in fc.points if p.wind_gust_kn is not None]
    hs = [p.wave_hs_m for p in fc.points if p.wave_hs_m is not None]
    press = [p.pressure_hpa for p in fc.points if p.pressure_hpa is not None]
    dirs = [p.wind_from_deg for p in fc.points if p.wind_from_deg is not None]

    lines = ["## 3. Weather Picture", "", "### 3a. Tropical outlook", ""]
    if inp.tropical is not None:
        lines.append(render_tropical_markdown(inp.tropical))
    elif inp.tropical_markdown:
        lines.append(inp.tropical_markdown.strip())
    else:
        lines.append(
            "> **PENDING** — NHC feed not supplied. Check the NHC Tropical Weather Outlook "
            "and 5-day graphical outlook (nhc.noaa.gov). Hurricane season (Jun–Nov): not "
            "clear if an Invest or named system's cone touches route corridor + 200 NM / "
            "96 h."
        )

    lines += ["", "### 3b. Model summary (along the route)", ""]
    if winds:
        lines.append(
            f"- **Wind:** {min(winds):.0f}–{max(winds):.0f} kn"
            + (f", gusts to {max(gusts):.0f} kn" if gusts else "")
            + (f", generally from the {_compass(_avg_dir(dirs))}" if dirs else "")
        )
    if hs:
        lines.append(f"- **Seas:** Hs {min(hs):.1f}–{max(hs):.1f} m")
    if len(press) >= 2:
        trend = press[-1] - press[0]
        if trend > 0.5:
            note = f"rising {trend:+.0f} hPa"
        elif trend < -0.5:
            note = f"falling {trend:+.0f} hPa"
        else:
            note = "steady"
        lines.append(
            f"- **Pressure:** {press[0]:.0f} → {press[-1]:.0f} hPa over the passage ({note})"
        )
    lines.append(
        f"- **Source:** {inp.forecast_models_note or _models_note(inp, fc)} "
        "`[Primary model via Open-Meteo]`"
    )
    lines += ["", "### 3c. Forecast confidence", ""]
    if inp.forecast_comparison is not None:
        lines.append(render_confidence_markdown(inp.forecast_comparison))
    elif inp.forecast_models_note:
        lines.append("See the model note above for the multi-model comparison.")
    else:
        lines.append(
            "> **PENDING** — run with `--compare` for ECMWF-vs-GFS divergence. Single-model: "
            "no spread estimate."
        )
    # No section, not a PENDING block, when nothing was pasted — this input is
    # optional and normally absent (the owner is not subscribed most of the time),
    # not a wired-but-missing feed. See marine_forecaster.py's module docstring.
    if inp.parker_text:
        parker = parse_parker(inp.parker_text)
        comparison = compare_with_model(parker, inp.analysis)
        lines += ["", "### 3d. Professional forecast", "",
                   render_parker_markdown(parker, comparison)]
    return "\n".join(lines)


def _routing_analysis(inp: BriefingInputs) -> str:
    a = inp.analysis
    plan = inp.plan
    timed = plan.timed_legs(a.leg_speeds)
    wps = plan.route.waypoints

    cols = ["#", "From → To", "NM", "Brg°T", "Wind", "Seas",
            "TWA / point of sail", "Plan", "Current", "SOG kn", "Leg ETA (local)", "Flags"]
    lines = [
        "## 4. Step-by-Step Weather Routing Analysis",
        "",
        "| " + " | ".join(cols) + " |",
        "|--:|---|--:|--:|---|--:|---|---|--:|--:|---|---|",
    ]
    for la, tl in zip(a.legs, timed, strict=True):
        c = la.conditions
        frm = wps[la.leg_index].name
        to = wps[la.leg_index + 1].name
        wind = (f"{c.tws_kn:.0f} kn {_compass(c.wind_from_deg)}"
                + (f" g{c.gust_kn:.0f}" if c.gust_kn else ""))
        seas = f"{c.wave_hs_m:.1f} m" if c.wave_hs_m is not None else "—"
        current = f"{la.current_component_kn:+.1f}" if c.current_kn is not None else "—"
        flags = "; ".join(f.text for f in la.flags) or "—"
        lines.append(
            f"| {la.leg_index} | {frm} → {to} | {la.distance_nm:.1f} | "
            f"{la.course_deg:03.0f} | {wind} | {seas} | "
            f"{la.twa_deg:.0f}° {la.point_of_sail} | {_MODE_LABEL[la.mode]} | "
            f"{current} | {la.projected_speed_kn:.1f} | "
            f"{_clk(inp).label(tl.end_eta, wps[la.leg_index + 1].lat, wps[la.leg_index + 1].lon)}"
            f" | {flags} |"
        )

    # The fleet-target flags stay in the Flags column and the executive summary line;
    # repeating each one here as well was noise.
    flagged = [
        la for la in a.legs if any(f.kind not in _GROUPED_FLAG_KINDS for f in la.flags)
    ]
    if flagged:
        lines += ["", "**Leg call-outs:**", ""]
        for la in flagged:
            for f in la.flags:
                if f.kind in _GROUPED_FLAG_KINDS:
                    continue
                prefix = "⚠️ " if f.severity is Severity.WARNING else ""
                lines.append(f"- {prefix}**Leg {la.leg_index}** ({la.point_of_sail}): {f.text}")
    return "\n".join(lines)


def _depth_clearance(inp: BriefingInputs) -> str:
    body = _depth_clearance_hydro(inp) if inp.hydro is not None else _depth_clearance_pending(inp)
    ac_block = _ac_bridges_hazards(inp)
    if ac_block:
        body += "\n\n" + ac_block
    notices = _notices_block(inp)
    if notices:
        body += "\n\n### 5d. Navigational warnings (NGA broadcast / NTM)\n\n" + notices
    return body


def _ac_bridges_hazards(inp: BriefingInputs) -> str:
    if not inp.activecaptain_pois:
        return ""
    bridges = [
        rp for rp in inp.activecaptain_pois
        if isinstance(rp.poi, BridgeInfo)
    ]
    hazards = [
        rp for rp in inp.activecaptain_pois
        if isinstance(rp.poi, HazardInfo)
    ]
    if not bridges and not hazards:
        return ""

    v = inp.vessel
    fleet_clearance = v.air_draft.required_clearance_ft
    lines = ["### 5c. Bridge clearances & hazards (ActiveCaptain)"]

    if bridges:
        lines += [
            "",
            f"Fleet required clearance: **{fleet_clearance:.0f} ft** "
            f"(air draft {v.air_draft.planning_ft:.0f} ft + "
            f"{v.air_draft.clearance_buffer_ft:.0f} ft buffer).",
            "",
            "| Bridge | Closed height | Clears? | Type | Dist (NM) | Notes |",
            "|---|--:|---|---|--:|---|",
        ]
        for rp in bridges:
            b = rp.poi
            assert isinstance(b, BridgeInfo)
            nav = b.navigation
            ht = nav.bridge_closed_height_ft
            if ht is not None:
                ht_str = f"{ht:.0f} ft"
                if ht >= fleet_clearance:
                    clears = f"✅ +{ht - fleet_clearance:.0f} ft"
                else:
                    clears = f"⚠️ **{fleet_clearance - ht:.0f} ft short**"
            else:
                ht_str = "unknown"
                clears = "?"
            btype = nav.bridge_type or "—"
            notes = nav.restriction_notes or nav.navigational_notes or "—"
            if len(notes) > 60:
                notes = notes[:57] + "…"
            lines.append(
                f"| {b.name} | {ht_str} | {clears} | {btype} | {rp.distance_nm:.1f} | {notes} |"
            )

    if hazards:
        lines += ["", "**Hazards near route:**", ""]
        for rp in hazards:
            h = rp.poi
            assert isinstance(h, HazardInfo)
            note = h.navigation.navigational_notes or h.navigation.restriction_notes or ""
            depth = h.navigation.depth_low_water_ft
            parts = [f"**{h.name}** ({rp.distance_nm:.1f} NM)"]
            if depth is not None:
                parts.append(f"depth {depth:.1f} ft")
            if note:
                short = note[:80] + ("…" if len(note) > 80 else "")
                parts.append(short)
            lines.append(f"- {' — '.join(parts)}")

    return "\n".join(lines)


def _notices_block(inp: BriefingInputs) -> str:
    if inp.notices is not None:
        return render_notices_markdown(inp.notices)
    if inp.notices_markdown:
        return inp.notices_markdown.strip()
    return (
        "> **PENDING** — NGA broadcast-warning feed not supplied. Check active NAVAREA IV / "
        "HYDROLANT warnings at msi.nga.mil, and the USCG Local Notice to Mariners for the "
        "district(s) on the route, before departure."
    )


def _depth_clearance_hydro(inp: BriefingInputs) -> str:
    h = inp.hydro
    assert h is not None
    concern = [w for w in h.waypoints if not w.deep_water]
    deep = [w for w in h.waypoints if w.deep_water and w.ocean_depth_ft is not None]

    lines = [
        "## 5. Depth, Clearance, Tide & Navigational Notices",
        "",
        f"- **Water needed at a shared stop:** **{h.required_charted_ft:.0f} ft** "
        "charted + height of tide.",
        f"- **Overhead:** **{h.required_clearance_ft:.0f} ft** clearance for any fixed "
        "structure — rules out the fixed-bridge ICW, stay outside.",
    ]
    if deep:
        depths = sorted(w.ocean_depth_ft for w in deep if w.ocean_depth_ft is not None)
        lines.append(
            f"- **{len(deep)} of {len(h.waypoints)} waypoints open water** "
            f"(GEBCO {depths[0]:.0f}–{depths[-1]:.0f} ft) — not listed below."
        )
    if not concern:
        lines.append("")
        lines.append("Every waypoint open water. Nothing to gate on this route.")
        return "\n".join(lines)

    lines += [
        "- Tides: NOAA CO-OPS nearest station, height above MLLW at ETA. Charted depth: "
        "NOAA ENC or manual input, planning sample only — not a route-safety check.",
        "",
        "| Waypoint | ETA (local) | Nearest tide stn | Tide @ETA | Charted | Clears? | Tide gate |",
        "|---|---|---|--:|---|---|---|",
    ]
    for w in concern:
        stn = (
            f"{w.station.name} ({w.station.distance_nm:.0f} NM)"
            + ("⚠️" if w.station.is_distant else "")
            if w.station else "—"
        )
        tide = f"{w.tide_at_eta_ft:+.1f} ft" if w.tide_at_eta_ft is not None else "—"
        if w.charted is None:
            charted = "**screenshot needed**"
            clears = "?"
        else:
            charted = f"{w.charted.depth_ft:.1f} ft ({w.charted.source})"
            c = w.clearance_at_eta_ft
            if c is None:
                clears = "?"
            elif c >= 0:
                clears = f"✅ +{c:.1f} ft"
            else:
                clears = f"⚠️ **{-c:.1f} ft short**"
        gate = "—"
        if w.gate_window is not None:
            gate = (
                f"{_clk(inp).hhmm(w.gate_window.start, w.lat, w.lon)}–"
                f"{_clk(inp).hhmm(w.gate_window.end, w.lat, w.lon)} "
                f"(≥{w.gate_window.threshold_ft:.1f} ft tide)"
            )
        elif w.marginal and w.charted is not None:
            need = w.required_charted_ft - w.charted.depth_ft
            gate = f"**needs {need:.1f} ft of tide — no window ±6 h**"
        lines.append(
            f"| {w.name} | {_clk(inp).label(w.eta, w.lat, w.lon)} | {stn} | {tide} "
            f"| {charted} | {clears} | {gate} |"
        )

    missing = h.missing_depth
    if missing:
        lines += [
            "",
            "**Charted depth still needed** for: "
            + ", ".join(w.name for w in missing)
            + " — enter via the web form's Depth check, or `passage-planner "
            "depths-template`, then re-run.",
        ]
    return "\n".join(lines)


def _depth_clearance_pending(inp: BriefingInputs) -> str:
    v = inp.vessel
    wps = inp.plan.route.waypoints
    ukc = v.draft.ukc_margin_ft
    buf = v.air_draft.clearance_buffer_ft

    lines = [
        "## 5. Depth, Clearance, Tide & Navigational Notices",
        "",
        "> **PENDING** — no tide/depth assessment supplied (run with `--tides`). Check "
        "each waypoint/leg by hand on MFD/Navionics against the figures below.",
        "",
        f"- **{v.name}:** need **{v.draft.required_charted_ft:.0f} ft** charted + tide "
        f"(draft {v.draft.planning_ft:.0f} ft + {ukc:.0f} ft UKC); "
        f"**{v.air_draft.required_clearance_ft:.0f} ft** clearance for any fixed "
        "structure — no fixed-bridge ICW, stay outside.",
    ]
    if v.buddy_boats:
        drivers = ", ".join(b.name for b in v.buddy_boats)
        # Arrival, departure and anchoring use the keel-up draft; the keel-down draft
        # only matters in open water. An unknown keel-up draft falls back to keel-down.
        lines.append(
            f"- **Fleet (shared stops govern):** need "
            f"**{v.fleet_draft_min_ft + ukc:.0f} ft** charted + tide and "
            f"**{v.fleet_air_draft_ft + buf:.0f} ft** clearance — driven by {drivers}."
        )
        unknown_boats = [b.name for b in v.buddy_boats if b.lifting_keel and not b.keel_up_known]
        if unknown_boats:
            lines.append(
                f"- **{', '.join(unknown_boats)}:** lifting keel, retracted draft not on "
                "file — keel-down draft used above."
            )
    lines += [
        "- Marginal crossing: compute the tide window where governing draft + tide "
        "clears, time the passage to it.",
        "",
        "**Waypoints to verify:**",
        "",
    ]
    lines += [f"- {w.name} ({w.lat:.4f}, {w.lon:.4f})"
              + (f" — {w.description}" if w.description else "")
              for w in wps]
    return "\n".join(lines)


def _arrival_dossier(inp: BriefingInputs) -> str:
    lines = ["## 6. Arrival & Immigration Dossier", ""]
    if inp.ciq_markdown:
        lines.append(inp.ciq_markdown.strip())
        if inp.consular is not None:
            lines += ["", render_consular_markdown(inp.consular).rstrip()]
        return "\n".join(lines)

    countries = [c.strip() for c in (inp.destination_country or "").split(",") if c.strip()]
    if not countries:
        lines.append(
            "> **PENDING** — no destination given. Set `--to \"country\"` (comma-separate "
            "several if the route crosses jurisdictions) or supply `--ciq-file`."
        )
    else:
        lines.append(render_dossier(countries))

    if inp.consular is not None and countries:
        lines += ["", render_consular_markdown(inp.consular).rstrip()]

    doc_block = _document_readiness(inp)
    if doc_block:
        lines += ["", doc_block]
    return "\n".join(lines)


def _document_readiness(inp: BriefingInputs) -> str:
    p = inp.papers
    if p is None:
        return ""
    article = "an" if p.trip[:1] in "aeiou" else "a"
    lines = ["### Document readiness", "", f"*Checked against {article} {p.trip} trip.*", ""]

    def _entry(f: DocFinding) -> list[str]:
        out = [f"- {f.message}"]
        note = f.doc.note if f.doc is not None else ""
        if note:
            out.append(f"  - {note}")
        return out

    if p.blocking:
        lines.append("**⚠️ Blocking — fix before departure:**")
        for f in p.blocking:
            lines += _entry(f)
        lines.append("")
    if p.warnings:
        lines.append("**Check / renew:**")
        for f in p.warnings:
            lines += _entry(f)
        lines.append("")
    ok = p.ok
    if ok:
        lines.append("**On file:** " + " · ".join(
            f.message.split(":", 1)[0].strip() for f in ok
        ))
    lines.append("")
    dated = any(
        f.doc is not None and (f.doc.issued is not None or f.doc.expires is not None)
        for f in p.findings
    )
    if dated:
        lines.append(
            "*Dates from `config/papers.toml`, transcribed from source PDFs. Re-check "
            "before departure.*"
        )
    else:
        lines.append(
            "*Filenames classify docs only — add issue/expiry dates to "
            "`config/papers.toml` to flag lapses.*"
        )
    return "\n".join(lines)


def _logistics(inp: BriefingInputs) -> str:
    a = inp.analysis
    v = inp.vessel
    motor_h = a.motor_h + a.motorsail_h
    lines = [
        "## 7. Logistics & Support Matrix",
        "",
        "### 7a. Fuel (computed)",
        "",
        f"- Projected engine time this passage: **{_hm(motor_h)}** "
        f"({a.motor_nm + a.motorsail_nm:.0f} NM under power).",
        f"- Projected burn: **{a.total_fuel_gal:.0f} gal**. Top the tanks before departure "
        f"({v.propulsion.fuel_capacity_gal:.0f} gal) and treat with BioMuncher.",
    ]
    if a.fuel_margin_gal < 0.25 * v.propulsion.fuel_capacity_gal:
        lines.append(
            f"- ⚠️ Margin thin ({a.fuel_margin_gal:.0f} gal) — jerry cans and/or fuel stop."
        )
    else:
        lines.append(f"- Margin comfortable ({a.fuel_margin_gal:.0f} gal). Jerry cans optional.")

    fuel_block = _ac_fuel_stops(inp)
    if fuel_block:
        lines += ["", fuel_block]

    lines += ["", "### 7b. Water, provisioning, dinghy access", ""]
    water = _water_block(inp)
    if water:
        lines += [water, ""]
    marinas_block = _ac_marinas(inp)
    if marinas_block:
        lines += [marinas_block, ""]
    if inp.logistics_markdown:
        lines.append(strip_template_preamble(inp.logistics_markdown))
        gaps = coverage_gaps(inp.logistics_markdown)
        if gaps:
            lines.append("")
            for port, missing in gaps.items():
                shown = ", ".join(missing[:6]) + (" …" if len(missing) > 6 else "")
                lines.append(f"> ⚠️ **{port}** — still to research: {shown}")
    else:
        lines += [
            "> **PENDING** — per-port shore logistics (groceries, chandlery, fuel/water, "
            "haul-out, dinghy landing, money, contacts) not supplied. Run "
            "`passage-planner logistics-template --ports \"…\"`, fill it, and pass it with "
            "`brief --logistics-file`.",
            "",
            "- Precook and freeze passage meals.",
            "- Confirm shore-power voltage/frequency/plug at the arrival dock before arrival.",
        ]

    lines += ["", "### 7c. Preventative maintenance", ""]
    lines.append(_maintenance_block(inp))

    lines += ["", "### 7d. Communications configuration", ""]
    lines.append(_comms_block(inp))
    return "\n".join(lines)


def _ac_fuel_stops(inp: BriefingInputs) -> str:
    if not inp.activecaptain_pois:
        return ""
    fuel_marinas = [
        rp for rp in inp.activecaptain_pois
        if isinstance(rp.poi, MarinaInfo) and rp.poi.fuel.has_diesel
    ]
    if not fuel_marinas:
        return ""

    lines = [
        "### 7a-ii. Diesel stops near route (ActiveCaptain)",
        "",
        "| Marina | Dist (NM) | Diesel $/gal | Depth | VHF | Phone | Notes |",
        "|---|--:|--:|---|---|---|---|",
    ]
    for rp in fuel_marinas[:15]:
        m = rp.poi
        assert isinstance(m, MarinaInfo)
        price = f"${m.fuel.diesel_price:.2f}" if m.fuel.diesel_price else "—"
        depth = f"{m.fuel.fuel_depth_ft:.0f} ft" if m.fuel.fuel_depth_ft else "—"
        vhf = m.contact.vhf_channel or "—"
        phone = m.contact.phone or "—"
        notes = m.fuel.notes or "—"
        if len(notes) > 50:
            notes = notes[:47] + "…"
        lines.append(
            f"| {m.name} | {rp.distance_nm:.1f} | {price} | {depth} | {vhf} | {phone} | {notes} |"
        )
    return "\n".join(lines)


def _ac_marinas(inp: BriefingInputs) -> str:
    if not inp.activecaptain_pois:
        return ""
    marinas = [
        rp for rp in inp.activecaptain_pois
        if isinstance(rp.poi, MarinaInfo)
        and not rp.poi.fuel.has_diesel
    ]
    transient = [
        rp for rp in marinas
        if isinstance(rp.poi, MarinaInfo)
        and rp.poi.dockage.transient_slips
        and rp.poi.dockage.transient_slips not in ("0", "")
    ]
    if not transient:
        return ""

    lines = [
        "**Transient dockage near route (ActiveCaptain):**",
        "",
    ]
    for rp in transient[:10]:
        m = rp.poi
        assert isinstance(m, MarinaInfo)
        parts = [f"**{m.name}** ({rp.distance_nm:.1f} NM)"]
        if m.dockage.transient_slips:
            parts.append(f"{m.dockage.transient_slips} transient slips")
        if m.contact.vhf_channel:
            parts.append(f"VHF {m.contact.vhf_channel}")
        if m.contact.phone:
            parts.append(m.contact.phone)
        svc = []
        if m.services.grocery:
            svc.append("grocery")
        if m.services.repair:
            svc.append("repair")
        if m.services.fuel_station:
            svc.append("fuel")
        if svc:
            parts.append(", ".join(svc))
        lines.append(f"- {' — '.join(parts)}")
    return "\n".join(lines)


def _comms_block(inp: BriefingInputs) -> str:
    countries = [c.strip() for c in (inp.destination_country or "").split(",") if c.strip()]
    today = inp.analysis.depart.date()
    arrival = inp.analysis.eta.date()
    return render_comms(countries, inp.comms_config, today, arrival)


def _water_block(inp: BriefingInputs) -> str:
    w = inp.vessel.water
    if w is None:
        return ""
    n = max(len(inp.crew), 1)
    passage_days = inp.analysis.duration_h / 24.0
    tank_days = w.tank_days(n)
    lines = [
        f"**Fresh water — {w.tank_gal:.0f} gal tankage (cooking + hygiene; drinking is bottled):**",
        f"- ~{w.per_person_gpd:.1f} gal/person/day for {n} crew → tanks last "
        f"**≈ {tank_days:.1f} days** vs a {passage_days:.1f}-day passage.",
    ]
    if w.watermaker_producing:
        rate = f" (~{w.watermaker_gph:.1f} gph)" if w.watermaker_gph > 0 else ""
        lines.append(
            f"- Watermaker commissioned{rate} — run it to hold the tanks; tankage is a "
            "reserve, not the limit."
        )
    elif w.watermaker_installed:
        lines.append(
            "- ⚠️ Watermaker installed, **not commissioned** — fill tanks dockside, carry "
            "bottled drinking water."
        )
    else:
        lines.append("- No watermaker — fill dockside and carry bottled drinking water.")
    if tank_days < passage_days * 1.5:
        lines.append(
            f"- ⚠️ Tank autonomy ({tank_days:.1f} d) is tight against the passage plus an "
            "arrival buffer — carry water jerry cans or plan a stop."
        )
    return "\n".join(lines)


def _maintenance_block(inp: BriefingInputs) -> str:
    m = inp.maintenance
    if m is None:
        return (
            "> **PENDING** — run with the ops-panel DB reachable to list PM coming due "
            "this passage."
        )
    def interval_days(i: MaintenanceItem) -> int:
        try:
            return int(float(i.period))
        except (TypeError, ValueError):
            return 0

    all_overdue = m.by_status(MaintenanceDue.OVERDUE)
    overdue = [i for i in all_overdue if interval_days(i) >= 30][:15]
    short_overdue = len(all_overdue) - len([i for i in all_overdue if interval_days(i) >= 30])
    due = m.by_status(MaintenanceDue.DUE)
    soon = m.by_status(MaintenanceDue.SOON)
    per_passage = m.by_status(MaintenanceDue.PER_PASSAGE)[:20]
    per_passage_total = len(m.by_status(MaintenanceDue.PER_PASSAGE))
    hours = m.by_status(MaintenanceDue.HOURS)

    out: list[str] = []

    def rows(items: tuple[MaintenanceItem, ...], *, dated: bool) -> list[str]:
        if not items:
            return []
        head = (
            "| System | Task | Due |\n|---|---|---|" if dated
            else "| System | Task |\n|---|---|"
        )
        body = [
            (f"| {i.system} | {i.task} | {i.next_due:%Y-%m-%d} |" if dated and i.next_due
             else f"| {i.system} | {i.task} |")
            for i in items
        ]
        return [head, *body, ""]

    if overdue:
        out.append(f"**⚠️ Overdue, 30-day+ intervals ({len(overdue)})** — clear before departure:")
        out += rows(tuple(overdue), dated=True)
    if short_overdue:
        out.append(
            f"- Plus **{short_overdue}** short-interval routine items overdue (mostly seed "
            "dates) — clear on pre-departure walk."
        )
    if due:
        out.append(f"**Comes due during the passage ({len(due)})** — do it before you go:")
        out += rows(due, dated=True)
    if soon:
        out.append(f"**Due shortly after arrival ({len(soon)})** — stage the parts:")
        out += rows(soon, dated=True)
    if per_passage:
        extra = f" (of {per_passage_total})" if per_passage_total > len(per_passage) else ""
        out.append(f"**Per-outing items{extra}** — before and/or after every passage:")
        out += rows(tuple(per_passage), dated=False)
    if hours:
        out.append(
            f"*{len(hours)} engine-hours-interval items not listed — the ops-panel isn't "
            "tracking engine hours yet.*"
        )
    if not out:
        out.append("Nothing due this passage window per the ops-panel.")
    return "\n".join(out).rstrip()


def _watch_plan(inp: BriefingInputs) -> str:
    crew = list(inp.crew)
    n = max(len(crew), 1)
    wh = inp.watch_hours
    depart = inp.analysis.depart
    total_h = inp.analysis.duration_h
    wps = inp.plan.route.waypoints
    dep_wp, eta_wp = wps[0], wps[-1]
    clk = _clk(inp)
    boat_zone = clk.zone_at(dep_wp.lat, dep_wp.lon)
    boat_abbr = clk.abbr(depart, dep_wp.lat, dep_wp.lon)

    lines = [
        "## 8. Watch Plan",
        "",
        f"- Crew: {', '.join(crew)} ({n}) · **{wh:.0f}-hour watches**",
        f"- The schedule below keeps **boat time = departure zone ({boat_abbr})** for the "
        "whole passage; switch to the arrival zone once alongside.",
    ]
    arr_abbr = clk.abbr(inp.analysis.eta, eta_wp.lat, eta_wp.lon)
    if arr_abbr != boat_abbr:
        lines.append(
            f"- Crosses into **{arr_abbr}** en route — §5 tide gates and arrival dossier "
            "already local-to-position time."
        )
    if n < 2:
        lines += ["", "> Single-hand: no rotation. Use short sleep cycles, an AIS/CPA "
                  "alarm, and a kitchen timer for horizon scans."]
        return "\n".join(lines)

    end_time = depart + timedelta(hours=total_h)
    zi = ZoneInfo(boat_zone)
    lines += ["", f"| Watch ({boat_abbr}) | On | Off |", "|---|---|---|"]
    slot = 0
    t = 0.0
    while t < total_h - 1e-6 and slot < 40:
        on = crew[slot % n]
        start = depart + timedelta(hours=t)
        finish = min(depart + timedelta(hours=t + wh), end_time)
        s_txt = clk.label(start, dep_wp.lat, dep_wp.lon)
        same_day = start.astimezone(zi).date() == finish.astimezone(zi).date()
        fin = (
            clk.hhmm(finish, dep_wp.lat, dep_wp.lon)
            if same_day
            else clk.label(finish, dep_wp.lat, dep_wp.lon)
        )
        offs = ", ".join(c for c in crew if c != on) or "—"
        lines.append(f"| {s_txt} – {fin} | **{on}** | {offs} |")
        slot += 1
        t += wh

    if slot <= 1:
        lines += ["", "*Passage is shorter than one watch rotation — run a single "
                  "watch, skipper's discretion on rest.*"]

    lines += [
        "",
        "**Handover checklist (each watch change):** current conditions · expected events "
        "(weather + traffic) · vessel condition and anything broken · fuel and water "
        "used / remaining · current sail plan · confirm the Garmin Montana is tracking.",
    ]
    return "\n".join(lines)


def _float_plan(inp: BriefingInputs) -> str:
    a = inp.analysis
    sc = inp.vessel.shore_contact
    wps = inp.plan.route.waypoints
    dep = _clk(inp).label(a.depart, wps[0].lat, wps[0].lon)
    eta = _clk(inp).label(a.eta, wps[-1].lat, wps[-1].lon)
    recipient = (
        f"{sc.name} <{sc.email}>" if sc is not None else inp.float_plan_recipient
    )
    return "\n".join([
        "## 9. Float Plan",
        "",
        f"- **Send to:** {recipient} (cc crew designates).",
        f"- Vessel {inp.vessel.name}, {inp.vessel.model}"
        + (f", MMSI {inp.vessel.mmsi}" if inp.vessel.mmsi else "") + ".",
        f"- Departing {dep} from {wps[0].name}.",
        f"- Bound for {wps[-1].name}, ETA {eta} "
        f"({_hm(a.duration_h)} passage, {a.total_nm:.0f} NM).",
        f"- Crew: {', '.join(inp.crew)}.",
        *([f"- Travelling in company with {', '.join(b.name for b in inp.vessel.buddy_boats)}."]
          if inp.vessel.buddy_boats else []),
        "- Tracking: Garmin Montana, position reports at each watch handover.",
        "- Overdue trigger: **ETA + 12 h** with no contact → begin the notification steps "
        "in the float-plan template.",
        "",
        "> Fill and send the full `float plan template.pdf` — this is the summary, not a "
        "substitute for the form.",
    ])


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def build_briefing(inp: BriefingInputs) -> str:
    sections = [
        _header(inp),
        _executive(inp),
        _assumptions(inp),
        _weather(inp),
        _routing_analysis(inp),
        _depth_clearance(inp),
        _arrival_dossier(inp),
        _logistics(inp),
        _watch_plan(inp),
        _float_plan(inp),
        "---\n\n" + MARITIME_DISCLAIMER,
    ]
    return "\n\n".join(s.strip() for s in sections) + "\n"


def _version() -> str:
    from . import __version__

    return __version__


def _models_note(inp: BriefingInputs, fc: RouteForecast) -> str:
    models = ", ".join(fc.models) or "unknown"
    wp0 = inp.plan.route.waypoints[0]
    return f"Open-Meteo ({models}), issued {_clk(inp).label(fc.issued, wp0.lat, wp0.lon)}"


def _avg_dir(dirs: list[float]) -> float:
    s = sum(math.sin(math.radians(d)) for d in dirs)
    c = sum(math.cos(math.radians(d)) for d in dirs)
    return (math.degrees(math.atan2(s, c)) + 360.0) % 360.0
