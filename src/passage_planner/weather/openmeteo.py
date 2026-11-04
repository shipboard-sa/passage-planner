"""Open-Meteo route-corridor forecast fetcher.

Purpose: fetch wind/wave/current forecasts along a route's timed fixes from
    Open-Meteo's forecast + marine APIs.
Version: v2 (2026-09-22) - added ocean_current_velocity/direction to the
    marine fetch (weather-routing step 2); `_marine_params` now also sends
    `wind_speed_unit=kn` (verified live: converts current velocity too).
Scope: PRODUCTION - passage-planner's primary weather source.
Architecture: passage-planner subsystem. Called by `service.py` (CLI/web) and
    `analysis.refine()`'s fetch callback; fills `weather/model.py`'s
    `ForecastPoint`.

Open-Meteo serves point forecasts drawn from the primary global models
(GFS, ECMWF IFS, ICON, and the GWAM/EWAM wave models) as free keyless JSON,
and accepts many coordinates in one request. That is a clean fit for
leg-by-leg analysis of a *given* route — we need the forecast at each sample
position at its ETA, not the full grid a routing *solver* would need.

Provenance: the values originate in a primary numerical model but reach us via
a third-party aggregator. The briefing tags them
``[Primary model: <model> via Open-Meteo]`` and the divergence between two
models (call this twice with different ``model`` values) is the
forecast-confidence signal.

The boat's always-on `weather_open_meteo_marine` sidecar hits the same marine
API for the *current* position; this module is the forward-timed, multi-point
companion and does not touch that pipeline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from ..errors import WeatherFetchError, WeatherParseError
from ..passage import TimedFix
from .model import ForecastPoint, RouteForecast

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

_FORECAST_HOURLY = ("wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "pressure_msl")
_MARINE_HOURLY = (
    "wave_height", "wave_direction", "wave_period", "swell_wave_height",
    "ocean_current_velocity", "ocean_current_direction",
)

# Open-Meteo's free global models run to +16 days.
_MAX_HORIZON_DAYS = 16


@dataclass(frozen=True, slots=True)
class OpenMeteoConfig:
    model: str = "best_match"
    marine_model: str = "best_match"
    provenance_tier: str = "secondary"
    timeout_s: float = 20.0
    max_points_per_request: int = 40
    retries: int = 2
    retry_backoff_s: float = 1.5


@dataclass
class OpenMeteoClient:
    config: OpenMeteoConfig = field(default_factory=OpenMeteoConfig)

    async def fetch(
        self,
        fixes: list[TimedFix],
        route_name: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> RouteForecast:
        """Fetch a forecast for every fix. Order of ``points`` matches ``fixes``."""
        if not fixes:
            raise ValueError("fixes is empty")
        self._check_horizon(fixes)

        own_client = client is None
        http = client or httpx.AsyncClient(timeout=self.config.timeout_s)
        try:
            points: list[ForecastPoint] = []
            for chunk in _chunk(fixes, self.config.max_points_per_request):
                fc_task = self._get_json(http, _FORECAST_URL, self._forecast_params(chunk))
                mar_task = self._get_json(http, _MARINE_URL, self._marine_params(chunk))
                fc_payload, mar_payload = await asyncio.gather(fc_task, mar_task)
                points.extend(self._merge_chunk(chunk, fc_payload, mar_payload))
        finally:
            if own_client:
                await http.aclose()

        return RouteForecast(
            route_name=route_name,
            issued=datetime.now(UTC),
            points=tuple(points),
        )

    async def fetch_wind_models(
        self,
        fixes: list[TimedFix],
        route_name: str,
        models: tuple[str, ...],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, RouteForecast]:
        """Wind-only forecast for every fix, from several models in ONE request.

        Open-Meteo suffixes the hourly keys with the model name when more than
        one model is asked for (``wind_speed_10m_gfs_seamless`` …). Used for the
        model-divergence / confidence check; marine fields are left None.
        """
        if not fixes:
            raise ValueError("fixes is empty")
        if len(models) < 2:
            raise ValueError("fetch_wind_models needs at least 2 models")
        self._check_horizon(fixes)

        own = client is None
        http = client or httpx.AsyncClient(timeout=self.config.timeout_s)
        try:
            per_model: dict[str, list[ForecastPoint]] = {m: [] for m in models}
            for chunk in _chunk(fixes, self.config.max_points_per_request):
                start, end = self._date_range(chunk)
                params = {
                    "latitude": ",".join(f"{f.lat:.4f}" for f in chunk),
                    "longitude": ",".join(f"{f.lon:.4f}" for f in chunk),
                    "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,pressure_msl",
                    "wind_speed_unit": "kn", "timezone": "UTC",
                    "start_date": start, "end_date": end, "models": ",".join(models),
                }
                payload = await self._get_json(http, _FORECAST_URL, params)
                blocks = _as_blocks(payload, len(chunk), "forecast")
                for fix, block in zip(chunk, blocks, strict=True):
                    hourly = _hourly(block, "forecast")
                    times = _times(hourly)
                    for m in models:
                        per_model[m].append(_wind_point(fix, hourly, times, m,
                                                        self.config.provenance_tier))
        finally:
            if own:
                await http.aclose()

        issued = datetime.now(UTC)
        return {
            m: RouteForecast(route_name=route_name, issued=issued, points=tuple(pts))
            for m, pts in per_model.items()
        }

    # -- request building --------------------------------------------------

    def _date_range(self, fixes: list[TimedFix]) -> tuple[str, str]:
        start = min(f.eta for f in fixes).astimezone(UTC).date()
        end = max(f.eta for f in fixes).astimezone(UTC).date()
        return start.isoformat(), end.isoformat()

    def _forecast_params(self, fixes: list[TimedFix]) -> dict[str, str]:
        start, end = self._date_range(fixes)
        return {
            "latitude": ",".join(f"{f.lat:.4f}" for f in fixes),
            "longitude": ",".join(f"{f.lon:.4f}" for f in fixes),
            "hourly": ",".join(_FORECAST_HOURLY),
            "wind_speed_unit": "kn",
            "timezone": "UTC",
            "start_date": start,
            "end_date": end,
            "models": self.config.model,
        }

    def _marine_params(self, fixes: list[TimedFix]) -> dict[str, str]:
        start, end = self._date_range(fixes)
        return {
            "latitude": ",".join(f"{f.lat:.4f}" for f in fixes),
            "longitude": ",".join(f"{f.lon:.4f}" for f in fixes),
            "hourly": ",".join(_MARINE_HOURLY),
            # Only ocean_current_velocity is a speed field here (wave height/period
            # need no unit param), but this same param name converts it too —
            # verified live 2026-09-22 against the real API.
            "wind_speed_unit": "kn",
            "timezone": "UTC",
            "start_date": start,
            "end_date": end,
            "models": self.config.marine_model,
        }

    def _check_horizon(self, fixes: list[TimedFix]) -> None:
        latest = max(f.eta for f in fixes).astimezone(UTC)
        horizon = latest - datetime.now(UTC)
        if horizon > timedelta(days=_MAX_HORIZON_DAYS):
            raise WeatherFetchError(
                f"passage reaches {horizon.days} days out; Open-Meteo free models "
                f"only run to +{_MAX_HORIZON_DAYS} days"
            )

    # -- HTTP with retry -------------------------------------------------

    async def _get_json(
        self, http: httpx.AsyncClient, url: str, params: dict[str, str]
    ) -> object:
        last_exc: Exception | None = None
        for attempt in range(self.config.retries + 1):
            try:
                resp = await http.get(url, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise WeatherParseError(f"{url}: response was not JSON") from exc
                # Open-Meteo returns 400 with {"reason": "..."} for bad params.
                reason = _safe_reason(resp)
                raise WeatherFetchError(f"{url}: HTTP {resp.status_code} - {reason}")
            if attempt < self.config.retries:
                await asyncio.sleep(self.config.retry_backoff_s * (attempt + 1))
        tries = self.config.retries + 1
        raise WeatherFetchError(f"{url}: unreachable after {tries} tries") from last_exc

    # -- response assembly ---------------------------------------------

    def _merge_chunk(
        self, fixes: list[TimedFix], fc_payload: object, mar_payload: object
    ) -> list[ForecastPoint]:
        fc_blocks = _as_blocks(fc_payload, len(fixes), "forecast")
        mar_blocks = _as_blocks(mar_payload, len(fixes), "marine")
        out: list[ForecastPoint] = []
        for fix, fc, mar in zip(fixes, fc_blocks, mar_blocks, strict=True):
            fc_h = _hourly(fc, "forecast")
            mar_h = _hourly(mar, "marine")
            fc_times = _times(fc_h)
            mar_times = _times(mar_h)
            out.append(
                ForecastPoint(
                    cumulative_nm=fix.cumulative_nm,
                    leg_index=fix.leg_index,
                    lat=fix.lat,
                    lon=fix.lon,
                    valid_time=fix.eta.astimezone(UTC),
                    source="open-meteo",
                    model=self.config.model,
                    provenance_tier=self.config.provenance_tier,
                    wind_speed_kn=_interp(fc_times, _vals(fc_h, "wind_speed_10m"), fix.eta),
                    wind_from_deg=_interp(fc_times, _vals(fc_h, "wind_direction_10m"), fix.eta),
                    wind_gust_kn=_interp(fc_times, _vals(fc_h, "wind_gusts_10m"), fix.eta),
                    pressure_hpa=_interp(fc_times, _vals(fc_h, "pressure_msl"), fix.eta),
                    wave_hs_m=_interp(mar_times, _vals(mar_h, "wave_height"), fix.eta),
                    wave_from_deg=_interp(mar_times, _vals(mar_h, "wave_direction"), fix.eta),
                    wave_period_s=_interp(mar_times, _vals(mar_h, "wave_period"), fix.eta),
                    swell_hs_m=_interp(mar_times, _vals(mar_h, "swell_wave_height"), fix.eta),
                    current_kn=_interp(mar_times, _vals(mar_h, "ocean_current_velocity"), fix.eta),
                    current_to_deg=_interp(
                        mar_times, _vals(mar_h, "ocean_current_direction"), fix.eta
                    ),
                )
            )
        return out


# ---------------------------------------------------------------------------
# free functions
# ---------------------------------------------------------------------------


def _chunk(items: list[TimedFix], size: int) -> list[list[TimedFix]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _wind_point(
    fix: TimedFix,
    hourly: dict[str, object],
    times: list[datetime],
    model: str,
    tier: str,
) -> ForecastPoint:
    def val(base: str) -> list[float | None]:
        # multi-model responses suffix the key; fall back to the bare key.
        keyed = _vals(hourly, f"{base}_{model}")
        return keyed if keyed else _vals(hourly, base)

    return ForecastPoint(
        cumulative_nm=fix.cumulative_nm,
        leg_index=fix.leg_index,
        lat=fix.lat,
        lon=fix.lon,
        valid_time=fix.eta.astimezone(UTC),
        source="open-meteo",
        model=model,
        provenance_tier=tier,
        wind_speed_kn=_interp(times, val("wind_speed_10m"), fix.eta),
        wind_from_deg=_interp(times, val("wind_direction_10m"), fix.eta),
        wind_gust_kn=_interp(times, val("wind_gusts_10m"), fix.eta),
        pressure_hpa=_interp(times, val("pressure_msl"), fix.eta),
    )


def _safe_reason(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict) and "reason" in body:
            return str(body["reason"])
    except ValueError:
        pass
    return resp.text[:200]


def _as_blocks(payload: object, n: int, label: str) -> list[dict[str, object]]:
    """Open-Meteo returns a list for multi-point, a bare object for one point."""
    if isinstance(payload, list):
        blocks = payload
    elif isinstance(payload, dict):
        blocks = [payload]
    else:
        raise WeatherParseError(f"{label}: unexpected response type {type(payload).__name__}")
    if len(blocks) != n:
        raise WeatherParseError(
            f"{label}: expected {n} location blocks, got {len(blocks)}"
        )
    for b in blocks:
        if not isinstance(b, dict):
            raise WeatherParseError(f"{label}: a location block is not an object")
    return blocks


def _hourly(block: dict[str, object], label: str) -> dict[str, object]:
    hourly = block.get("hourly")
    if not isinstance(hourly, dict):
        raise WeatherParseError(f"{label}: location block has no 'hourly' object")
    return hourly


def _times(hourly: dict[str, object]) -> list[datetime]:
    raw = hourly.get("time")
    if not isinstance(raw, list):
        raise WeatherParseError("hourly block has no 'time' array")
    out: list[datetime] = []
    for t in raw:
        try:
            out.append(datetime.fromisoformat(str(t)).replace(tzinfo=UTC))
        except ValueError as exc:
            raise WeatherParseError(f"unparseable hourly time {t!r}") from exc
    return out


def _vals(hourly: dict[str, object], key: str) -> list[float | None]:
    raw = hourly.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise WeatherParseError(f"hourly['{key}'] is not an array")
    out: list[float | None] = []
    for v in raw:
        out.append(None if v is None else float(v))
    return out


def _interp(
    times: list[datetime], values: list[float | None], target: datetime
) -> float | None:
    """Linear interpolation of an hourly series to ``target``.

    Returns ``None`` if the series is empty, the bracketing samples are missing,
    or a bracket value is null. Clamps to the endpoints outside the range.
    """
    if not times or not values or len(times) != len(values):
        return None
    tgt = target.astimezone(UTC)
    if tgt <= times[0]:
        return values[0]
    if tgt >= times[-1]:
        return values[-1]
    for i in range(len(times) - 1):
        t0, t1 = times[i], times[i + 1]
        if t0 <= tgt <= t1:
            v0, v1 = values[i], values[i + 1]
            if v0 is None or v1 is None:
                return None
            span = (t1 - t0).total_seconds()
            if span <= 0.0:
                return v0
            frac = (tgt - t0).total_seconds() / span
            return v0 + frac * (v1 - v0)
    return None


def fetch_route_forecast(
    fixes: list[TimedFix],
    route_name: str,
    *,
    config: OpenMeteoConfig | None = None,
) -> RouteForecast:
    """Blocking wrapper around :meth:`OpenMeteoClient.fetch` for CLI use."""
    client = OpenMeteoClient(config=config or OpenMeteoConfig())
    return asyncio.run(client.fetch(fixes, route_name))
