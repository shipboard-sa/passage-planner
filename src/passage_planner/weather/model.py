"""Provider-neutral forecast data model.

Purpose: the point-in-time forecast record every weather provider fills and
    `analysis.py` consumes.
Version: v2 (2026-09-22) - added `current_kn`/`current_to_deg` (weather-routing
    step 2).
Scope: PRODUCTION - passage-planner's provider-neutral weather model.
Architecture: passage-planner subsystem. `weather/openmeteo.py` fills it;
    `analysis.py` aggregates it per leg.

A :class:`ForecastPoint` is one place, one time, and whatever meteorological
values a provider returned for it. Every value is optional — providers differ
in coverage and a partial point is still useful to the leg analyzer. Units are
fixed and explicit so nothing downstream has to guess:

* wind / gust speed  — knots
* directions         — degrees true, the compass bearing the wind/sea comes FROM
* current speed      — knots
* current direction  — degrees true, the compass bearing the current flows
  TOWARD (oceanographic convention — the opposite sense from the wind/wave
  directions above, which are where they come FROM; named ``current_to_deg``,
  never ``current_from_deg``, to keep the two conventions from being silently
  confused)
* wave / swell height — metres (significant height)
* period             — seconds
* pressure           — hectopascals
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    cumulative_nm: float
    leg_index: int
    lat: float
    lon: float
    valid_time: datetime  # timezone-aware UTC

    source: str            # e.g. "open-meteo"
    model: str             # e.g. "best_match", "gfs_seamless", "ecmwf_ifs025"
    provenance_tier: str   # "primary" | "professional" | "secondary"

    wind_speed_kn: float | None = None
    wind_from_deg: float | None = None
    wind_gust_kn: float | None = None
    pressure_hpa: float | None = None
    wave_hs_m: float | None = None
    wave_from_deg: float | None = None
    wave_period_s: float | None = None
    swell_hs_m: float | None = None
    current_kn: float | None = None
    current_to_deg: float | None = None

    @property
    def has_wind(self) -> bool:
        return self.wind_speed_kn is not None and self.wind_from_deg is not None

    @property
    def has_sea_state(self) -> bool:
        return self.wave_hs_m is not None

    @property
    def has_current(self) -> bool:
        return self.current_kn is not None and self.current_to_deg is not None


@dataclass(frozen=True, slots=True)
class RouteForecast:
    """Forecast points along one passage, in along-route order."""

    route_name: str
    issued: datetime
    points: tuple[ForecastPoint, ...]

    def for_leg(self, leg_index: int) -> tuple[ForecastPoint, ...]:
        return tuple(p for p in self.points if p.leg_index == leg_index)

    @property
    def models(self) -> tuple[str, ...]:
        seen: list[str] = []
        for p in self.points:
            if p.model not in seen:
                seen.append(p.model)
        return tuple(seen)
