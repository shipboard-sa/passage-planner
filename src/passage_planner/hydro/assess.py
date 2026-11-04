"""Combine tide predictions, charted depth, and the fleet's draft into a
per-waypoint depth / tide-gate assessment.

Pure orchestration around :class:`TidesClient` and :class:`EncClient`. Network
failure on one waypoint's tide pull degrades that waypoint (station/tide set to
None) rather than failing the whole report — the briefing still renders.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..config import VesselConfig
from ..errors import HydroFetchError
from ..passage import PassagePlan
from .bathy import BathyClient
from .enc import EncClient
from .model import (
    ChartedDepth,
    HydroReport,
    TideExtreme,
    WaypointHydro,
)
from .tides import TidesClient, bracketing_extremes, gate_windows, tide_height

# How far either side of the ETA to look for a usable tide window.
_GATE_SEARCH = timedelta(hours=6)
# GEBCO depth (ft) at or above which a waypoint is clearly no draft concern for
# any fleet member and needs no precise sounding.
_DEEP_WATER_FT = 40.0


def assess_route(
    vessel: VesselConfig,
    plan: PassagePlan,
    *,
    leg_speeds: dict[int, float] | None = None,
    waypoint_depths: dict[str, ChartedDepth] | None = None,
    tides: TidesClient | None = None,
    enc: EncClient | None = None,
    bathy: BathyClient | None = None,
    use_enc: bool = True,
) -> HydroReport:
    tides = tides or TidesClient()
    enc = enc or EncClient()
    bathy = bathy or BathyClient()
    supplied = waypoint_depths or {}

    ukc = vessel.draft.ukc_margin_ft
    # Best case: any lifting-keel buddy raises it for a shared thin-water stop.
    required_charted = vessel.fleet_draft_min_ft + ukc
    required_keeldown = vessel.fleet_draft_ft + ukc
    required_clearance = vessel.fleet_air_draft_ft + vessel.air_draft.clearance_buffer_ft

    etas = plan.waypoint_etas(leg_speeds)
    passage_start = etas[0]
    passage_end = etas[-1]

    wps = plan.route.waypoints
    try:
        gebco = bathy.depths_ft([(w.lat, w.lon) for w in wps])
    except HydroFetchError:
        gebco = [None] * len(wps)

    extremes_cache: dict[str, list[TideExtreme]] = {}
    results: list[WaypointHydro] = []

    for idx, wp in enumerate(wps):
        eta = etas[idx].astimezone(UTC)
        leg_index = min(idx, len(plan.route.legs) - 1)
        ocean_ft = gebco[idx]
        deep_water = ocean_ft is not None and ocean_ft >= _DEEP_WATER_FT

        station = None
        tide_now: float | None = None
        brackets: tuple[TideExtreme, ...] = ()
        try:
            station = tides.nearest_station(wp.lat, wp.lon)
            if station.station_id not in extremes_cache:
                extremes_cache[station.station_id] = tides.extremes(
                    station.station_id, passage_start, passage_end
                )
            ex = extremes_cache[station.station_id]
            tide_now = tide_height(ex, eta)
            brackets = bracketing_extremes(ex, eta)
        except HydroFetchError:
            station = station  # keep whatever we got; tide stays None
            ex = []

        charted = _charted_for(wp.name, wp.lat, wp.lon, supplied, enc, use_enc, deep_water)

        needs_tide = charted is not None and charted.depth_ft < required_charted
        marginal = charted is not None and (
            charted.depth_ft + (tide_now or 0.0) < required_charted
        )

        window = None
        if needs_tide and ex:
            min_ft = required_charted - charted.depth_ft  # type: ignore[union-attr]
            wins = gate_windows(ex, eta - _GATE_SEARCH, eta + _GATE_SEARCH, min_ft)
            window = wins[0] if wins else None

        results.append(WaypointHydro(
            name=wp.name, lat=wp.lat, lon=wp.lon, eta=eta, leg_index=leg_index,
            station=station, tide_at_eta_ft=tide_now, extremes=brackets,
            charted=charted, ocean_depth_ft=ocean_ft, deep_water=deep_water,
            required_charted_ft=required_charted,
            required_keeldown_ft=required_keeldown,
            gate_window=window, marginal=marginal,
        ))

    return HydroReport(
        generated=datetime.now(UTC),
        required_charted_ft=required_charted,
        required_keeldown_ft=required_keeldown,
        required_clearance_ft=required_clearance,
        waypoints=tuple(results),
    )


def _charted_for(
    name: str,
    lat: float,
    lon: float,
    supplied: dict[str, ChartedDepth],
    enc: EncClient,
    use_enc: bool,
    deep_water: bool,
) -> ChartedDepth | None:
    hit = supplied.get(name.strip().lower())
    if hit is not None:
        return hit
    if deep_water or not use_enc:
        return None  # GEBCO already rules it out; skip the ENC round-trip
    try:
        return enc.sounding_near(lat, lon)
    except HydroFetchError:
        return None
