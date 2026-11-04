"""Leg-by-leg passage analysis: forecast + polar + vessel config -> per-leg
point of sail, projected speed, propulsion mode, fuel, and safety flags, plus a
passage rollup.

Purpose: turn a timed route + forecast + polar into per-leg sail/motor
    decisions, speed-over-ground, fuel and safety flags.
Version: v4 (2026-09-22) - `analyze()`/`refine()` take an optional
    `buddy_polars` map (a buddy boat's own real polar, from a pasted ORC
    certificate - see boat_lookup.py/service.resolve_buddy_polars). A buddy
    with one is checked on every leg for the fleet-speed target (new
    BUDDY_BELOW_TARGET flag); the MULTIHULL_UPWIND heuristic now only fires
    for a buddy with no real polar on file. v3: current correction
    (weather-routing step 2): projected_speed_kn is now speed-over-ground
    (boat speed + the current's along-course component, not a
    steering-triangle solve — see wiki/Design-Decisions.md), a foul-current
    guard flag, and a wind-against-current caution. `decide_mode`/
    `mode_speed`/`burn_gph` made public (weather-routing step 4) so
    `isochrone.py`'s per-hop solver reuses the same sail/motor decision as
    the fixed-route analyzer.
Scope: PRODUCTION - passage-planner's leg analyzer, called by `service.py`
    (CLI/web) via `refine()`.
Architecture: passage-planner subsystem. Pure functions over
    `weather/model.py`'s `ForecastPoint`, `polar.py`'s `Polar` and
    `config.py`'s `VesselConfig`; output feeds `briefing.py` and
    `passage.py`'s ETA timing.

The decision logic is intentionally simple and inspectable — it analyses a
*given* route (drawn on the tablet in Navionics), it does not solve for an
optimal one. A leg the skipper drew straight into the wind comes back flagged as
a beat that will be motored or motorsailed, not silently re-routed. Current
correction follows the same philosophy: it corrects the along-course speed a
current produces, not the heading a helmsman would need to steer to make good
the drawn course through a cross-current.

``analyze`` is pure: it takes the forecast as given. ``refine`` re-times the
passage from the projected leg speeds and (via a caller-supplied fetch
callback) re-pulls the forecast at the corrected ETAs until the arrival time
stops moving.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .config import VesselConfig
from .geo import current_component_kn, point_of_sail, relative_wind_angle_deg
from .passage import PassagePlan, TimedFix
from .polar import Polar
from .weather.model import ForecastPoint, RouteForecast

# Motorsail is chosen into a dead beat only when there is enough wind for a
# steadying sail to actually help.
_MOTORSAIL_MIN_TWS_KN = 8.0
# Below this polar speed in light air there is no point trying to motorsail.
_MOTORSAIL_MIN_POLAR_KN = 2.0
# Flat sail-assist added to cruise speed when motorsailing.
_MOTORSAIL_ASSIST_KN = 0.5
# A beat only counts as "into head seas" above this wave height.
_HEAD_SEA_HS_M = 1.5


class SailMode(StrEnum):
    SAIL = "sail"
    MOTOR = "motor"
    MOTORSAIL = "motorsail"


class Severity(StrEnum):
    WARNING = "warning"   # exceeds a hard limit
    CAUTION = "caution"   # worth the skipper's attention


BELOW_FLEET_TARGET = "below_fleet_target"
MULTIHULL_UPWIND = "multihull_upwind"
# A generic heuristic, not a catamaran polar: a multihull in company on a leg tighter
# than this true wind angle will fall off or motor, however the monohull does. Used only
# for a buddy boat with no real polar on file - see BUDDY_BELOW_TARGET below.
MULTIHULL_UPWIND_TWA_DEG = 60.0
# A buddy boat with a real polar on file (from a pasted ORC certificate, see
# boat_lookup.py) is checked on every leg, any point of sail - not just the upwind
# heuristic above, which only ever covers multihulls beating.
BUDDY_BELOW_TARGET = "buddy_below_target"
WIND_AGAINST_CURRENT = "wind_against_current"
FOUL_CURRENT = "foul_current"


@dataclass(frozen=True, slots=True)
class Flag:
    severity: Severity
    text: str
    leg_index: int | None = None
    kind: str = ""  # a stable tag for flags the briefing groups, e.g. BELOW_FLEET_TARGET


@dataclass(frozen=True, slots=True)
class LegConditions:
    tws_kn: float
    wind_from_deg: float
    gust_kn: float | None
    wave_hs_m: float | None
    wave_from_deg: float | None
    pressure_hpa: float | None
    n_points: int
    current_kn: float | None = None
    current_to_deg: float | None = None


@dataclass(frozen=True, slots=True)
class LegAnalysis:
    leg_index: int
    distance_nm: float
    course_deg: float
    conditions: LegConditions
    twa_deg: float
    point_of_sail: str
    is_beat: bool
    polar_speed_kn: float
    mode: SailMode
    # Speed made good over the ground: the mode's boat speed through the water,
    # corrected by current_component_kn. Timing (duration_h, leg_speeds, ETA)
    # uses this, not the raw boat speed.
    projected_speed_kn: float
    # Signed along-course current correction already folded into
    # projected_speed_kn above (positive = favorable, negative = foul); 0.0
    # when no current data was available for this leg.
    current_component_kn: float
    duration_h: float
    fuel_gal: float
    flags: tuple[Flag, ...]


@dataclass(frozen=True, slots=True)
class PassageAnalysis:
    route_name: str
    legs: tuple[LegAnalysis, ...]
    depart: datetime
    eta: datetime

    total_nm: float
    sail_nm: float
    motor_nm: float
    motorsail_nm: float
    sail_h: float
    motor_h: float
    motorsail_h: float

    total_fuel_gal: float
    usable_fuel_gal: float

    hazards: tuple[Flag, ...]
    leg_speeds: dict[int, float]
    polar_provisional: bool
    polar_source: str
    iterations: int = 1

    @property
    def duration_h(self) -> float:
        return (self.eta - self.depart).total_seconds() / 3600.0

    @property
    def fuel_margin_gal(self) -> float:
        return self.usable_fuel_gal - self.total_fuel_gal

    @property
    def fuel_shortfall(self) -> bool:
        return self.total_fuel_gal > self.usable_fuel_gal

    @property
    def sailing_fraction(self) -> float:
        return 0.0 if self.total_nm <= 0.0 else self.sail_nm / self.total_nm

    @property
    def under_power_fraction(self) -> float:
        return 0.0 if self.total_nm <= 0.0 else (self.motor_nm + self.motorsail_nm) / self.total_nm

    @property
    def peak_wind_kn(self) -> float:
        """The highest wind (gust where known, else sustained) forecast on
        any leg - used by `sweep.py`'s tabulation and `marine_forecaster.py`'s
        model-vs-professional comparison, so both read this the same way."""
        if not self.legs:
            return 0.0
        return max(leg.conditions.gust_kn or leg.conditions.tws_kn for leg in self.legs)


# ---------------------------------------------------------------------------
# leg condition aggregation
# ---------------------------------------------------------------------------


def _circular_mean_deg(values: list[float]) -> float:
    s = sum(math.sin(math.radians(v)) for v in values)
    c = sum(math.cos(math.radians(v)) for v in values)
    if s == 0.0 and c == 0.0:
        return 0.0
    return (math.degrees(math.atan2(s, c)) + 360.0) % 360.0


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _points_for_leg(
    forecast: RouteForecast, leg_index: int, start_nm: float, end_nm: float
) -> list[ForecastPoint]:
    tagged = [p for p in forecast.points if p.leg_index == leg_index]
    if tagged:
        return tagged
    # No point landed on this leg (sample step > leg length). Fall back to the
    # forecast point nearest the leg midpoint by along-route distance.
    if not forecast.points:
        return []
    mid = 0.5 * (start_nm + end_nm)
    nearest = min(forecast.points, key=lambda p: abs(p.cumulative_nm - mid))
    return [nearest]


def _aggregate(points: list[ForecastPoint]) -> LegConditions:
    winds = [p.wind_speed_kn for p in points if p.wind_speed_kn is not None]
    dirs = [p.wind_from_deg for p in points if p.wind_from_deg is not None]
    gusts = [p.wind_gust_kn for p in points if p.wind_gust_kn is not None]
    hs = [p.wave_hs_m for p in points if p.wave_hs_m is not None]
    wave_dirs = [p.wave_from_deg for p in points if p.wave_from_deg is not None]
    press = [p.pressure_hpa for p in points if p.pressure_hpa is not None]
    currents = [p.current_kn for p in points if p.current_kn is not None]
    current_dirs = [p.current_to_deg for p in points if p.current_to_deg is not None]
    return LegConditions(
        tws_kn=_mean(winds) or 0.0,
        wind_from_deg=_circular_mean_deg(dirs) if dirs else 0.0,
        gust_kn=max(gusts) if gusts else None,
        wave_hs_m=max(hs) if hs else None,
        wave_from_deg=_circular_mean_deg(wave_dirs) if wave_dirs else None,
        pressure_hpa=_mean(press),
        n_points=len(points),
        current_kn=_mean(currents),
        current_to_deg=_circular_mean_deg(current_dirs) if current_dirs else None,
    )


# ---------------------------------------------------------------------------
# per-leg decision
# ---------------------------------------------------------------------------


def decide_mode(
    is_beat: bool, tws_kn: float, polar_speed_kn: float, light_air_kn: float
) -> SailMode:
    """Sail/motor/motorsail for one leg or hop, given its point of sail and wind.
    Public (not leg-index-specific) so `isochrone.py`'s per-hop solver can reuse
    the exact same decision `analyze()` uses, rather than a second copy."""
    if is_beat:
        if tws_kn >= _MOTORSAIL_MIN_TWS_KN:
            return SailMode.MOTORSAIL
        return SailMode.MOTOR
    if polar_speed_kn >= light_air_kn:
        return SailMode.SAIL
    if tws_kn >= 4.0 and polar_speed_kn >= _MOTORSAIL_MIN_POLAR_KN:
        return SailMode.MOTORSAIL
    return SailMode.MOTOR


def mode_speed(mode: SailMode, polar_speed_kn: float, vessel: VesselConfig) -> float:
    cruise = vessel.propulsion.cruise.speed_kn
    hull_max = vessel.performance.hull_max_speed_kn
    if mode is SailMode.SAIL:
        return min(polar_speed_kn, hull_max)
    if mode is SailMode.MOTOR:
        return min(cruise, hull_max)
    return min(hull_max, cruise + _MOTORSAIL_ASSIST_KN)


def burn_gph(mode: SailMode, vessel: VesselConfig) -> float:
    if mode is SailMode.SAIL:
        return 0.0
    if mode is SailMode.MOTOR:
        return vessel.propulsion.cruise.burn_gph
    return vessel.propulsion.cruise.motorsail_burn_gph


def _leg_flags(
    leg_index: int, cond: LegConditions, twa_deg: float, is_beat: bool, vessel: VesselConfig
) -> list[Flag]:
    thr = vessel.safety.thresholds
    out: list[Flag] = []
    if cond.wave_hs_m is not None and cond.wave_hs_m > thr.wave_hs_max_m:
        out.append(Flag(Severity.WARNING,
                        f"forecast seas Hs {cond.wave_hs_m:.1f} m exceed the "
                        f"{thr.wave_hs_max_m:.1f} m limit", leg_index))
    if cond.tws_kn > thr.wind_sustained_max_kn:
        out.append(Flag(Severity.WARNING,
                        f"sustained wind {cond.tws_kn:.0f} kn exceeds the "
                        f"{thr.wind_sustained_max_kn:.0f} kn limit", leg_index))
    if cond.gust_kn is not None and cond.gust_kn > thr.wind_gust_max_kn:
        out.append(Flag(Severity.WARNING,
                        f"gusts to {cond.gust_kn:.0f} kn exceed the "
                        f"{thr.wind_gust_max_kn:.0f} kn limit", leg_index))
    if is_beat and cond.wave_hs_m is not None and cond.wave_hs_m >= _HEAD_SEA_HS_M:
        out.append(Flag(Severity.CAUTION,
                        f"beating into head seas (TWA {twa_deg:.0f} deg, "
                        f"Hs {cond.wave_hs_m:.1f} m)", leg_index))
    if cond.wave_hs_m is not None and thr.wave_hs_max_m * 0.7 < cond.wave_hs_m <= thr.wave_hs_max_m:
        out.append(Flag(Severity.CAUTION,
                        f"seas Hs {cond.wave_hs_m:.1f} m approaching the "
                        f"{thr.wave_hs_max_m:.1f} m limit", leg_index))
    cur_thr = vessel.safety.current
    if (
        cond.current_kn is not None
        and cond.current_to_deg is not None
        and cond.tws_kn >= cur_thr.wind_against_current_tws_min_kn
        and cond.current_kn >= cur_thr.wind_against_current_current_min_kn
    ):
        # Direction the wind pushes TOWARD, to compare against current_to_deg's
        # same "toward" sense (cond.wind_from_deg is the opposite, "from" sense).
        wind_push_deg = (cond.wind_from_deg + 180.0) % 360.0
        opposition_deg = relative_wind_angle_deg(wind_push_deg, cond.current_to_deg)
        if opposition_deg >= cur_thr.wind_against_current_angle_deg:
            out.append(Flag(Severity.CAUTION,
                            f"wind against current: {cond.tws_kn:.0f} kn wind opposing "
                            f"{cond.current_kn:.1f} kn current - expect steep, breaking seas",
                            leg_index, WIND_AGAINST_CURRENT))
    return out


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def analyze(
    vessel: VesselConfig,
    plan: PassagePlan,
    forecast: RouteForecast,
    polar: Polar,
    buddy_polars: Mapping[str, Polar] | None = None,
) -> PassageAnalysis:
    """Analyse every leg against the forecast and polar. Pure (no I/O) - a buddy
    boat's own polar, when one is on file, must already be loaded by the caller
    (``service.resolve_buddy_polars``) and passed in as ``buddy_polars``, keyed by
    :attr:`BuddyBoat.id`."""
    light_air = vessel.performance.light_air_speed_kn
    fleet_target = vessel.performance.fleet_target_avg_kn
    buddy_polars = buddy_polars or {}

    leg_results: list[LegAnalysis] = []
    leg_speeds: dict[int, float] = {}
    hazards: list[Flag] = []

    for leg in plan.route.legs:
        pts = _points_for_leg(forecast, leg.index, leg.cumulative_start_nm, leg.cumulative_end_nm)
        cond = _aggregate(pts)

        twa = relative_wind_angle_deg(leg.bearing_deg, cond.wind_from_deg)
        is_beat = twa < vessel.safety.thresholds.beat_twa_min_deg
        polar_speed = min(polar.speed(cond.tws_kn, twa), vessel.performance.hull_max_speed_kn)

        mode = decide_mode(is_beat, cond.tws_kn, polar_speed, light_air)
        stw = mode_speed(mode, polar_speed, vessel)

        current_component = 0.0
        if cond.current_kn is not None and cond.current_to_deg is not None:
            current_component = current_component_kn(
                leg.bearing_deg, cond.current_to_deg, cond.current_kn
            )
        speed = stw + current_component  # speed over ground, used for timing below
        foul_current = speed <= 0.0
        speed = max(speed, 0.1)  # guard against a zero-or-negative-speed leg
        duration_h = leg.distance_nm / speed
        fuel = burn_gph(mode, vessel) * duration_h

        flags = _leg_flags(leg.index, cond, twa, is_beat, vessel)
        if foul_current:
            flags.append(Flag(Severity.WARNING,
                              f"foul current ({abs(current_component):.1f} kn) exceeds boat "
                              f"speed ({stw:.1f} kn) - leg not makeable as drawn at this "
                              "departure time", leg.index, FOUL_CURRENT))
        if mode is SailMode.SAIL and speed < fleet_target:
            flags.append(Flag(Severity.CAUTION,
                              f"sailing {speed:.1f} kn is below the {fleet_target:.1f} kn "
                              f"fleet target - engine needed to hold schedule", leg.index,
                              BELOW_FLEET_TARGET))

        for buddy in vessel.buddy_boats:
            buddy_polar = buddy_polars.get(buddy.id)
            if buddy_polar is not None:
                buddy_speed = buddy_polar.speed(cond.tws_kn, twa)
                if buddy_speed < fleet_target:
                    flags.append(Flag(
                        Severity.CAUTION,
                        f"{buddy.name}'s own polar makes {buddy_speed:.1f} kn here "
                        f"(TWA {twa:.0f}, TWS {cond.tws_kn:.0f}) - below the "
                        f"{fleet_target:.1f} kn fleet target", leg.index, BUDDY_BELOW_TARGET,
                    ))
            elif buddy.is_multihull and twa < max(
                vessel.safety.thresholds.beat_twa_min_deg, MULTIHULL_UPWIND_TWA_DEG
            ):
                flags.append(Flag(
                    Severity.CAUTION,
                    f"upwind with {buddy.name} ({buddy.hull}) in company - expect her "
                    "to fall off or motor",
                    leg.index, MULTIHULL_UPWIND,
                ))

        leg_results.append(LegAnalysis(
            leg_index=leg.index,
            distance_nm=leg.distance_nm,
            course_deg=leg.bearing_deg,
            conditions=cond,
            twa_deg=twa,
            point_of_sail=point_of_sail(twa),
            is_beat=is_beat,
            polar_speed_kn=polar_speed,
            mode=mode,
            projected_speed_kn=speed,
            current_component_kn=current_component,
            duration_h=duration_h,
            fuel_gal=fuel,
            flags=tuple(flags),
        ))
        leg_speeds[leg.index] = speed
        hazards.extend(flags)

    totals = _rollup(leg_results)
    eta = plan.eta(leg_speeds)
    usable = vessel.propulsion.usable_fuel_gal

    passage_flags = list(hazards)
    if totals["fuel"] > usable:
        passage_flags.insert(0, Flag(
            Severity.WARNING,
            f"projected fuel burn {totals['fuel']:.0f} gal exceeds usable "
            f"{usable:.0f} gal - plan a fuel stop or more sailing", None))

    return PassageAnalysis(
        route_name=plan.route.name,
        legs=tuple(leg_results),
        depart=plan.depart,
        eta=eta,
        total_nm=plan.route.total_nm,
        sail_nm=totals["sail_nm"],
        motor_nm=totals["motor_nm"],
        motorsail_nm=totals["motorsail_nm"],
        sail_h=totals["sail_h"],
        motor_h=totals["motor_h"],
        motorsail_h=totals["motorsail_h"],
        total_fuel_gal=totals["fuel"],
        usable_fuel_gal=usable,
        hazards=tuple(passage_flags),
        leg_speeds=leg_speeds,
        polar_provisional=polar.provisional,
        polar_source=polar.source,
    )


def _rollup(legs: list[LegAnalysis]) -> dict[str, float]:
    acc = {
        "sail_nm": 0.0, "motor_nm": 0.0, "motorsail_nm": 0.0,
        "sail_h": 0.0, "motor_h": 0.0, "motorsail_h": 0.0, "fuel": 0.0,
    }
    for a in legs:
        acc["fuel"] += a.fuel_gal
        if a.mode is SailMode.SAIL:
            acc["sail_nm"] += a.distance_nm
            acc["sail_h"] += a.duration_h
        elif a.mode is SailMode.MOTOR:
            acc["motor_nm"] += a.distance_nm
            acc["motor_h"] += a.duration_h
        else:
            acc["motorsail_nm"] += a.distance_nm
            acc["motorsail_h"] += a.duration_h
    return acc


ForecastFetcher = Callable[[list[TimedFix]], RouteForecast]


def refine(
    vessel: VesselConfig,
    plan: PassagePlan,
    polar: Polar,
    fetch: ForecastFetcher,
    *,
    sample_step_nm: float = 15.0,
    max_iterations: int = 4,
    eta_tolerance: timedelta = timedelta(minutes=30),
    buddy_polars: Mapping[str, Polar] | None = None,
) -> PassageAnalysis:
    """Iterate analyse -> re-time -> re-fetch until the ETA stabilises.

    ``fetch`` turns a list of timed fixes into a RouteForecast (e.g. a closure
    over :func:`passage_planner.weather.fetch_route_forecast`).
    """
    leg_speeds: dict[int, float] | None = None
    previous_eta: datetime | None = None
    result: PassageAnalysis | None = None

    for iteration in range(1, max_iterations + 1):
        fixes = plan.sample_fixes(sample_step_nm, leg_speeds)
        forecast = fetch(fixes)
        result = analyze(vessel, plan, forecast, polar, buddy_polars)
        result = _with_iteration(result, iteration)
        if previous_eta is not None and abs(result.eta - previous_eta) <= eta_tolerance:
            return result
        previous_eta = result.eta
        leg_speeds = dict(result.leg_speeds)

    assert result is not None
    return result


def _with_iteration(a: PassageAnalysis, n: int) -> PassageAnalysis:
    return PassageAnalysis(
        route_name=a.route_name, legs=a.legs, depart=a.depart, eta=a.eta,
        total_nm=a.total_nm, sail_nm=a.sail_nm, motor_nm=a.motor_nm,
        motorsail_nm=a.motorsail_nm, sail_h=a.sail_h, motor_h=a.motor_h,
        motorsail_h=a.motorsail_h, total_fuel_gal=a.total_fuel_gal,
        usable_fuel_gal=a.usable_fuel_gal, hazards=a.hazards,
        leg_speeds=a.leg_speeds, polar_provisional=a.polar_provisional,
        polar_source=a.polar_source, iterations=n,
    )
