"""A time/space-indexed weather+current field, backed by Open-Meteo grid calls.

Purpose: the `Field.at(time, lat, lon)` interface weather-routing step 3
    (the gridded field provider) named, so a future isochrone solver (step
    4) has a source of wind/wave/current away from the drawn route, not
    just along it.
Version: v1 (2026-09-22)
Scope: PRODUCTION - passage-planner subsystem, not yet called from the CLI or
    web backend (no solver exists to call it yet - step 4).
Architecture: passage-planner subsystem. `corridor_grid()` builds the query
    points from a `Route`; `fetch_field()` reuses `OpenMeteoClient.fetch()`
    (the same production code path step 1/2 already exercise) rather than a
    new HTTP client, and caches the result under `var/cache/` (JSON, one
    file per grid+time-window signature) so repeated solver runs during
    development don't re-hit the API.

**Why a corridor grid, not a bounding box** (live-verified 2026-09-22, see
wiki/Design-Decisions.md): a full `Route.bounding_box()` rectangle over a
580 NM diagonal route balloons to hundreds of points (most of them off the
corridor, some over land) and tripped Open-Meteo's real per-minute rate
limit (HTTP 429, "Minutely API request limit exceeded") within seconds.
`corridor_grid()` instead follows the route: along-track samples, each
offset cross-track by a small set of distances - the same "band around the
Navionics route" shape step 4's isochrone solver plan already calls for,
so the grid this module builds is sized for what the solver will actually
need, not the route's full extent.

**Why nearest-point, nearest-time, not interpolation**: this is the v1
interface the roadmap named, meant to unblock step 4's development against
real data. A solver wanting smoother interpolation between grid cells can
build that on top of `Field` later; this module does not guess at that
shape in advance.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from ..errors import RoutingError
from ..geo import destination_point, great_circle_nm
from ..passage import TimedFix
from ..route import Route
from .model import ForecastPoint
from .openmeteo import OpenMeteoClient, OpenMeteoConfig

GridPoint = tuple[float, float]  # (lat, lon), rounded to 4dp so it's usable as a dict key


def corridor_grid(
    route: Route,
    *,
    along_step_nm: float = 30.0,
    cross_offsets_nm: tuple[float, ...] = (-15.0, 0.0, 15.0),
) -> list[GridPoint]:
    """(lat, lon) points in a band around ``route`` - along-track samples every
    ``along_step_nm``, each offset cross-track by every distance in
    ``cross_offsets_nm`` (0.0 = on the track; negative/positive = either side,
    perpendicular to that sample's leg bearing). Duplicate points (offsets
    that land on the same rounded cell, common near a sharp turn) are kept
    once.
    """
    if along_step_nm <= 0.0:
        raise ValueError("along_step_nm must be > 0")
    seen: set[GridPoint] = set()
    points: list[GridPoint] = []
    for d, lat, lon in route.sample_points(along_step_nm):
        leg = route.leg_at_distance(d)
        perp_deg = (leg.bearing_deg + 90.0) % 360.0
        for offset_nm in cross_offsets_nm:
            if offset_nm == 0.0:
                plat, plon = lat, lon
            else:
                plat, plon = destination_point(lat, lon, perp_deg, offset_nm)
            key = (round(plat, 4), round(plon, 4))
            if key not in seen:
                seen.add(key)
                points.append(key)
    return points


@dataclass(frozen=True, slots=True)
class Field:
    """Nearest-point, nearest-time lookup over a fetched grid."""

    by_point: dict[GridPoint, tuple[ForecastPoint, ...]]

    def at(self, time: datetime, lat: float, lon: float) -> ForecastPoint | None:
        """The forecast point whose grid cell is nearest ``(lat, lon)`` and
        whose ``valid_time`` is nearest ``time``. ``None`` only if the field
        has no points at all."""
        if not self.by_point:
            return None
        nearest_cell = min(
            self.by_point, key=lambda cell: great_circle_nm(lat, lon, cell[0], cell[1])
        )
        series = self.by_point[nearest_cell]
        return min(series, key=lambda p: abs((p.valid_time - time).total_seconds()))

    @property
    def n_points(self) -> int:
        return len(self.by_point)


def _cache_key(grid: list[GridPoint], times: list[datetime], model: str) -> str:
    payload = json.dumps(
        {"grid": grid, "times": [t.isoformat() for t in times], "model": model}, sort_keys=True
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _point_to_json(p: ForecastPoint) -> dict[str, object]:
    d = {
        "cumulative_nm": p.cumulative_nm, "leg_index": p.leg_index, "lat": p.lat, "lon": p.lon,
        "valid_time": p.valid_time.isoformat(), "source": p.source, "model": p.model,
        "provenance_tier": p.provenance_tier, "wind_speed_kn": p.wind_speed_kn,
        "wind_from_deg": p.wind_from_deg, "wind_gust_kn": p.wind_gust_kn,
        "pressure_hpa": p.pressure_hpa, "wave_hs_m": p.wave_hs_m,
        "wave_from_deg": p.wave_from_deg, "wave_period_s": p.wave_period_s,
        "swell_hs_m": p.swell_hs_m, "current_kn": p.current_kn,
        "current_to_deg": p.current_to_deg,
    }
    return d


def _point_from_json(d: dict[str, object]) -> ForecastPoint:
    raw = dict(d)
    raw["valid_time"] = datetime.fromisoformat(str(raw["valid_time"]))
    return ForecastPoint(**raw)  # type: ignore[arg-type]


def _field_from_points(points: list[ForecastPoint]) -> Field:
    by_point: dict[GridPoint, list[ForecastPoint]] = {}
    for p in points:
        key = (round(p.lat, 4), round(p.lon, 4))
        by_point.setdefault(key, []).append(p)
    for series in by_point.values():
        series.sort(key=lambda p: p.valid_time)
    return Field({k: tuple(v) for k, v in by_point.items()})


async def fetch_field(
    grid: list[GridPoint],
    times: list[datetime],
    route_name: str,
    *,
    config: OpenMeteoConfig | None = None,
    cache_dir: Path | None = Path("var/cache"),
    client: httpx.AsyncClient | None = None,
) -> Field:
    """Fetch wind/wave/current at every (grid point, time) pair, reusing
    :meth:`OpenMeteoClient.fetch` - the exact code path step 1/2 already
    exercise, so this needs no new HTTP handling of its own.

    Reads/writes a JSON cache under ``cache_dir`` (default ``var/cache/``,
    gitignored) keyed by the grid, times and model, so an unchanged solver
    query during development doesn't re-hit the API. Pass ``cache_dir=None``
    to always fetch live.
    """
    if not grid:
        raise RoutingError("fetch_field: grid is empty")
    if not times:
        raise RoutingError("fetch_field: times is empty")
    cfg = config or OpenMeteoConfig()

    cache_path: Path | None = None
    if cache_dir is not None:
        key = _cache_key(grid, times, cfg.model)
        cache_path = cache_dir / f"field_{key}.json"
        if cache_path.is_file():
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            return _field_from_points([_point_from_json(p) for p in raw["points"]])

    fixes = [
        TimedFix(cumulative_nm=float(i), lat=lat, lon=lon, eta=t, leg_index=0)
        for i, (lat, lon) in enumerate(grid)
        for t in times
    ]
    om = OpenMeteoClient(config=cfg)
    rf = await om.fetch(fixes, route_name, client=client)
    field = _field_from_points(list(rf.points))

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"points": [_point_to_json(p) for p in rf.points]}
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, cache_path)

    return field
