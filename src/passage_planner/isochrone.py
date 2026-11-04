"""Corridor-limited isochrone-style route solver (weather-routing step 4).

Purpose: given a drawn route, a departure time and a weather+current
    `Field`, find the fastest path within a band around that route -
    a route/departure optimizer, not just an analysis of the route as drawn.
Version: v2 (2026-09-22) - added `to_gpx()`, a clean GPX export of the
    solved hop-by-hop path (weather-routing step 5's "GPX export").
Scope: PRODUCTION - passage-planner subsystem, not yet wired into the CLI or
    web backend (no caller builds a `Field` and invokes this yet).
Architecture: passage-planner subsystem. Consumes `weather.field.Field`
    (step 3), `polar.Polar`, and `analysis.py`'s public `decide_mode` /
    `mode_speed` / `burn_gph` - the exact same sail/motor decision the
    fixed-route analyzer uses, so a solved passage and a fixed-route
    briefing never disagree about what mode a given wind/current/bearing
    combination implies.

**Why a grid + dynamic program, not a classic isochrone-polygon expansion**:
this solves for the fastest path through a corridor grid built the same way
`weather.field.corridor_grid()` builds one - along-track samples, each
offset cross-track by a small set of distances, one row forced to the
route's start waypoint and one row forced to its end waypoint (the "anchored
at start, end and any must-pass waypoints" requirement: each leg of the
drawn route is solved independently, so every original waypoint stays a
mandatory pass-through point). Because a hop only ever moves from one
along-track row to the next, the graph is a DAG - a hop's arrival time
depends only on its predecessor's arrival time, so a single forward pass
over the rows (a per-row dynamic program, not a priority-queue search) finds
the minimum-time path exactly, using the real conditions at the exact time
each hop is attempted rather than a static snapshot.

**What this deliberately does not do**: model set-and-drift steering (same
choice as `analysis.py` - each hop reports achieved speed-over-ground along
its own bearing, not a correction to a target course); consider depth or
charted hazards (a corridor could cross the Bahamas Banks; global depth data
is below the resolution needed to rule that out - the caller must still
check the result in Navionics, this is a planning suggestion, not a safety
claim); or search laterally without bound (the corridor width is a
parameter the caller chooses, not something this module infers).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from xml.sax.saxutils import escape as _xml_escape

from .analysis import SailMode, burn_gph, decide_mode, mode_speed
from .config import VesselConfig
from .geo import (
    current_component_kn,
    destination_point,
    great_circle_nm,
    initial_bearing_deg,
    relative_wind_angle_deg,
)
from .polar import Polar
from .route import Leg, Route
from .weather.field import Field

_LatLon = tuple[float, float]

# A hop whose speed-over-ground would be this close to zero or negative
# (current overwhelming boat speed) is treated as infeasible and skipped,
# not clamped - the solver has the freedom to choose a different hop instead,
# unlike the fixed-route analyzer, which has no alternative to the drawn leg.
_MIN_FEASIBLE_SOG_KN = 0.05


@dataclass(frozen=True, slots=True)
class SolvedHop:
    """One grid-to-grid step of a solved leg."""

    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float
    depart: datetime
    arrive: datetime
    distance_nm: float
    bearing_deg: float
    mode: SailMode
    boat_speed_kn: float          # through the water (STW)
    current_component_kn: float   # signed, along this hop's bearing
    sog_kn: float                 # boat_speed_kn + current_component_kn, floored
    fuel_gal: float

    @property
    def duration_h(self) -> float:
        return (self.arrive - self.depart).total_seconds() / 3600.0


@dataclass(frozen=True, slots=True)
class SolvedLeg:
    """One drawn-route leg's solved path - one or more hops from the leg's
    start waypoint to its end waypoint."""

    leg_index: int
    hops: tuple[SolvedHop, ...]
    depart: datetime
    arrive: datetime
    fallback: bool  # True if no corridor path was feasible and this is the
    # direct rhumb-line hop instead (see module docstring)

    @property
    def distance_nm(self) -> float:
        return sum(h.distance_nm for h in self.hops)

    @property
    def duration_h(self) -> float:
        return (self.arrive - self.depart).total_seconds() / 3600.0

    @property
    def fuel_gal(self) -> float:
        return sum(h.fuel_gal for h in self.hops)


@dataclass(frozen=True, slots=True)
class SolvedPassage:
    """A full corridor-optimized passage - one :class:`SolvedLeg` per leg of
    the drawn route, chained so each leg's arrival is the next leg's
    departure (the same shape `PassageAnalysis` uses for the fixed-route
    case, so the two are easy to compare)."""

    route_name: str
    legs: tuple[SolvedLeg, ...]
    depart: datetime
    eta: datetime
    warnings: tuple[str, ...]

    @property
    def total_nm(self) -> float:
        return sum(leg.distance_nm for leg in self.legs)

    @property
    def total_fuel_gal(self) -> float:
        return sum(leg.fuel_gal for leg in self.legs)

    @property
    def duration_h(self) -> float:
        return (self.eta - self.depart).total_seconds() / 3600.0

    @property
    def sail_nm(self) -> float:
        return sum(h.distance_nm for leg in self.legs for h in leg.hops if h.mode is SailMode.SAIL)

    @property
    def motor_nm(self) -> float:
        return sum(
            h.distance_nm for leg in self.legs for h in leg.hops if h.mode is SailMode.MOTOR
        )

    @property
    def motorsail_nm(self) -> float:
        return sum(
            h.distance_nm for leg in self.legs for h in leg.hops if h.mode is SailMode.MOTORSAIL
        )


def _leg_rows(
    leg: Leg, along_step_nm: float, cross_offsets_nm: tuple[float, ...]
) -> list[list[_LatLon]]:
    """The grid rows for one leg: row 0 is the leg's start waypoint alone,
    the last row is its end waypoint alone (both mandatory pass-through
    points), interior rows are along-track samples offset cross-track."""
    rows: list[list[_LatLon]] = [[(leg.start.lat, leg.start.lon)]]
    perp_deg = (leg.bearing_deg + 90.0) % 360.0
    d = along_step_nm
    while d < leg.distance_nm - 1e-6:
        clat, clon = destination_point(leg.start.lat, leg.start.lon, leg.bearing_deg, d)
        row: list[_LatLon] = []
        seen: set[_LatLon] = set()
        for offset_nm in cross_offsets_nm:
            if offset_nm == 0.0:
                cell = (round(clat, 6), round(clon, 6))
            else:
                plat, plon = destination_point(clat, clon, perp_deg, offset_nm)
                cell = (round(plat, 6), round(plon, 6))
            if cell not in seen:
                seen.add(cell)
                row.append(cell)
        rows.append(row)
        d += along_step_nm
    rows.append([(leg.end.lat, leg.end.lon)])
    return rows


@dataclass(frozen=True, slots=True)
class _Achieved:
    mode: SailMode
    stw_kn: float
    current_component_kn: float

    @property
    def sog_kn(self) -> float:
        return self.stw_kn + self.current_component_kn


def _achieved(
    vessel: VesselConfig, polar: Polar, field: Field, at: datetime, from_ll: _LatLon, bearing: float
) -> _Achieved:
    """Mode, boat speed and current component for a hop starting at ``from_ll``
    on ``bearing``, departing ``at`` - the one place `_hop` and the direct
    fallback both compute this, so they can never disagree."""
    point = field.at(at, from_ll[0], from_ll[1])
    tws = point.wind_speed_kn if point is not None and point.wind_speed_kn is not None else 0.0
    wind_from = (
        point.wind_from_deg if point is not None and point.wind_from_deg is not None else 0.0
    )
    twa = relative_wind_angle_deg(bearing, wind_from)
    is_beat = twa < vessel.safety.thresholds.beat_twa_min_deg
    polar_speed = min(polar.speed(tws, twa), vessel.performance.hull_max_speed_kn)
    mode = decide_mode(is_beat, tws, polar_speed, vessel.performance.light_air_speed_kn)
    stw = mode_speed(mode, polar_speed, vessel)

    current_component = 0.0
    if point is not None and point.current_kn is not None and point.current_to_deg is not None:
        current_component = current_component_kn(bearing, point.current_to_deg, point.current_kn)
    return _Achieved(mode=mode, stw_kn=stw, current_component_kn=current_component)


def _hop(
    vessel: VesselConfig,
    polar: Polar,
    field: Field,
    from_ll: _LatLon,
    to_ll: _LatLon,
    depart: datetime,
) -> SolvedHop | None:
    """The best achievable hop from ``from_ll`` to ``to_ll`` departing at
    ``depart``, or ``None`` if a foul current would make the speed-over-ground
    non-positive (the solver skips it rather than clamping, unlike
    `analysis.py`'s fixed-route guard - see module docstring)."""
    dist = great_circle_nm(from_ll[0], from_ll[1], to_ll[0], to_ll[1])
    bearing = initial_bearing_deg(from_ll[0], from_ll[1], to_ll[0], to_ll[1])
    got = _achieved(vessel, polar, field, depart, from_ll, bearing)
    if got.sog_kn < _MIN_FEASIBLE_SOG_KN:
        return None

    duration_h = dist / got.sog_kn if dist > 0.0 else 0.0
    arrive = depart + timedelta(hours=duration_h)
    fuel = burn_gph(got.mode, vessel) * duration_h
    return SolvedHop(
        from_lat=from_ll[0], from_lon=from_ll[1], to_lat=to_ll[0], to_lon=to_ll[1],
        depart=depart, arrive=arrive, distance_nm=dist, bearing_deg=bearing, mode=got.mode,
        boat_speed_kn=got.stw_kn, current_component_kn=got.current_component_kn,
        sog_kn=got.sog_kn, fuel_gal=fuel,
    )


def _direct_fallback_hop(
    vessel: VesselConfig, polar: Polar, field: Field, leg: Leg, depart: datetime
) -> SolvedHop:
    """The leg's own rhumb-line hop, with the same foul-current guard
    `analysis.analyze()` uses (floor, don't skip) - used only when every
    corridor path was infeasible, so the solver always returns a leg."""
    from_ll = (leg.start.lat, leg.start.lon)
    hop = _hop(vessel, polar, field, from_ll, (leg.end.lat, leg.end.lon), depart)
    if hop is not None:
        return hop
    # Even the direct hop is foul-current-blocked: floor the speed instead of
    # failing, exactly like analysis.analyze()'s guard.
    got = _achieved(vessel, polar, field, depart, from_ll, leg.bearing_deg)
    sog = max(got.sog_kn, 0.1)
    duration_h = leg.distance_nm / sog
    arrive = depart + timedelta(hours=duration_h)
    fuel = burn_gph(got.mode, vessel) * duration_h
    return SolvedHop(
        from_lat=leg.start.lat, from_lon=leg.start.lon, to_lat=leg.end.lat, to_lon=leg.end.lon,
        depart=depart, arrive=arrive, distance_nm=leg.distance_nm, bearing_deg=leg.bearing_deg,
        mode=got.mode, boat_speed_kn=got.stw_kn, current_component_kn=got.current_component_kn,
        sog_kn=sog, fuel_gal=fuel,
    )


def solve_leg(
    vessel: VesselConfig,
    polar: Polar,
    field: Field,
    leg: Leg,
    depart: datetime,
    *,
    along_step_nm: float = 15.0,
    cross_offsets_nm: tuple[float, ...] = (-20.0, -10.0, 0.0, 10.0, 20.0),
) -> tuple[SolvedLeg, str | None]:
    """The fastest corridor path across one leg. Returns the solved leg and,
    if every corridor path was infeasible, a warning describing the fallback
    (``None`` when a real corridor path was found)."""
    if along_step_nm <= 0.0:
        raise ValueError("along_step_nm must be > 0")
    if not cross_offsets_nm:
        raise ValueError("cross_offsets_nm must not be empty")

    rows = _leg_rows(leg, along_step_nm, cross_offsets_nm)
    n_rows = len(rows)

    arrival: list[list[datetime | None]] = [[None] * len(r) for r in rows]
    prev: list[list[tuple[int, int] | None]] = [[None] * len(r) for r in rows]
    hop_into: list[list[SolvedHop | None]] = [[None] * len(r) for r in rows]
    arrival[0][0] = depart

    for i in range(n_rows - 1):
        for j, from_ll in enumerate(rows[i]):
            t0 = arrival[i][j]
            if t0 is None:
                continue
            for k, to_ll in enumerate(rows[i + 1]):
                hop = _hop(vessel, polar, field, from_ll, to_ll, t0)
                if hop is None:
                    continue
                existing = arrival[i + 1][k]
                if existing is None or hop.arrive < existing:
                    arrival[i + 1][k] = hop.arrive
                    prev[i + 1][k] = (i, j)
                    hop_into[i + 1][k] = hop

    if arrival[-1][0] is not None:
        hops: list[SolvedHop] = []
        i, j = n_rows - 1, 0
        back = prev[i][j]
        while back is not None:
            hop = hop_into[i][j]
            assert hop is not None
            hops.append(hop)
            i, j = back
            back = prev[i][j]
        hops.reverse()
        solved = SolvedLeg(
            leg_index=leg.index, hops=tuple(hops), depart=depart, arrive=arrival[-1][0],
            fallback=False,
        )
        return solved, None

    fallback_hop = _direct_fallback_hop(vessel, polar, field, leg, depart)
    solved = SolvedLeg(
        leg_index=leg.index, hops=(fallback_hop,), depart=depart, arrive=fallback_hop.arrive,
        fallback=True,
    )
    warning = (
        f"leg {leg.index}: no corridor path was feasible (every hop into the "
        "destination was blocked by foul current) - used the direct rhumb-line "
        "hop instead"
    )
    return solved, warning


def solve_passage(
    vessel: VesselConfig,
    polar: Polar,
    field: Field,
    route: Route,
    depart: datetime,
    *,
    along_step_nm: float = 15.0,
    cross_offsets_nm: tuple[float, ...] = (-20.0, -10.0, 0.0, 10.0, 20.0),
) -> SolvedPassage:
    """Solve every leg of ``route`` in order, chaining each leg's arrival
    into the next leg's departure - the corridor-optimized counterpart to
    `analysis.analyze()`'s fixed-route pass."""
    legs: list[SolvedLeg] = []
    warnings: list[str] = []
    t = depart
    for leg in route.legs:
        solved, warning = solve_leg(
            vessel, polar, field, leg, t,
            along_step_nm=along_step_nm, cross_offsets_nm=cross_offsets_nm,
        )
        legs.append(solved)
        if warning is not None:
            warnings.append(warning)
        t = solved.arrive

    return SolvedPassage(
        route_name=route.name, legs=tuple(legs), depart=depart, eta=t, warnings=tuple(warnings),
    )


def to_gpx(passage: SolvedPassage) -> str:
    """A minimal, clean GPX 1.1 ``<rte>`` of the solved hop-by-hop path, for
    the skipper to check the corridor solver's suggestion in Navionics/
    OpenCPN - see the module docstring's disclaimer, this is a planning
    suggestion, not a safety claim. One waypoint per hop boundary (the
    solved path's own points, not the original drawn route's)."""
    points: list[tuple[float, float, datetime]] = []
    for leg in passage.legs:
        for hop in leg.hops:
            if not points:
                points.append((hop.from_lat, hop.from_lon, hop.depart))
            points.append((hop.to_lat, hop.to_lon, hop.arrive))

    name = _xml_escape(f"{passage.route_name} (corridor-optimized)")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="passage-planner" '
        'xmlns="http://www.topografix.com/GPX/1/1">',
        "  <rte>",
        f"    <name>{name}</name>",
    ]
    for i, (lat, lon, t) in enumerate(points):
        lines.append(f'    <rtept lat="{lat:.6f}" lon="{lon:.6f}">')
        lines.append(f"      <name>{_xml_escape(f'WP{i}')}</name>")
        lines.append(f'      <time>{t.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}</time>')
        lines.append("    </rtept>")
    lines += ["  </rte>", "</gpx>"]
    return "\n".join(lines)
