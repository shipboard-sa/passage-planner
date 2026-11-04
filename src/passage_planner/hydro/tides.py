"""NOAA CO-OPS tide predictions.

The station list (~3500 tide-prediction stations) is fetched once and cached
on disk; nearest-station is then a local great-circle search. Predictions are
pulled per station per passage as high/low pairs and the continuous curve
between them is reconstructed with the standard cosine (rule-of-twelfths)
segment fit — exact at the turns, correct shape between.

All times are UTC. Heights are feet above **MLLW**, which is the datum US
chart soundings use, so ``charted_depth + tide_height`` is the water under the
keel.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx

from ..errors import HydroError, HydroFetchError
from ..geo import great_circle_nm
from .model import TideExtreme, TideStation, TideWindow

_STATIONS_URL = (
    "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
    "?type=tidepredictions"
)
_DATAGETTER_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
_APP = "passage-planner"
_CACHE_MAX_AGE = timedelta(days=30)


class TidesClient:
    def __init__(
        self,
        *,
        cache_dir: str | Path = "var/cache",
        timeout_s: float = 20.0,
        http: httpx.Client | None = None,
    ) -> None:
        self._cache = Path(cache_dir) / "coops_stations.json"
        self._timeout = timeout_s
        self._http = http
        self._stations: list[dict[str, Any]] | None = None

    # -- stations ----------------------------------------------------

    def _client(self) -> tuple[httpx.Client, bool]:
        if self._http is not None:
            return self._http, False
        return httpx.Client(timeout=self._timeout), True

    def _load_stations(self) -> list[dict[str, Any]]:
        if self._stations is not None:
            return self._stations

        if self._cache.is_file():
            age = datetime.now(UTC).timestamp() - self._cache.stat().st_mtime
            if age < _CACHE_MAX_AGE.total_seconds():
                try:
                    self._stations = json.loads(self._cache.read_text(encoding="utf-8"))
                    return self._stations
                except (OSError, ValueError):
                    pass  # fall through to refetch

        http, own = self._client()
        try:
            resp = http.get(_STATIONS_URL)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise HydroFetchError(f"CO-OPS station list unreachable: {exc}") from exc
        finally:
            if own:
                http.close()
        if resp.status_code != 200:
            raise HydroFetchError(f"CO-OPS station list: HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise HydroFetchError("CO-OPS station list: not JSON") from exc

        stations = [
            {"id": str(s["id"]), "name": str(s.get("name", "")),
             "lat": float(s["lat"]), "lon": float(s["lng"])}
            for s in payload.get("stations", [])
            if s.get("lat") is not None and s.get("lng") is not None
        ]
        if not stations:
            raise HydroFetchError("CO-OPS station list came back empty")
        try:
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            self._cache.write_text(json.dumps(stations), encoding="utf-8")
        except OSError:
            pass  # cache is an optimisation, not required
        self._stations = stations
        return stations

    def nearest_station(self, lat: float, lon: float) -> TideStation:
        best: dict[str, Any] | None = None
        best_nm = math.inf
        for s in self._load_stations():
            d = great_circle_nm(lat, lon, s["lat"], s["lon"])
            if d < best_nm:
                best, best_nm = s, d
        if best is None:  # pragma: no cover - station list is non-empty by construction
            raise HydroError("no CO-OPS stations available")
        return TideStation(
            station_id=best["id"], name=best["name"],
            lat=best["lat"], lon=best["lon"], distance_nm=round(best_nm, 1),
        )

    # -- predictions -----------------------------------------------

    def extremes(
        self, station_id: str, start: datetime, end: datetime
    ) -> list[TideExtreme]:
        """High/low predictions covering [start, end], padded a day each side."""
        s = (start.astimezone(UTC) - timedelta(days=1)).strftime("%Y%m%d")
        e = (end.astimezone(UTC) + timedelta(days=1)).strftime("%Y%m%d")
        params = {
            "product": "predictions", "application": _APP, "datum": "MLLW",
            "station": station_id, "begin_date": s, "end_date": e,
            "interval": "hilo", "units": "english", "time_zone": "gmt", "format": "json",
        }
        http, own = self._client()
        try:
            resp = http.get(_DATAGETTER_URL, params=params)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise HydroFetchError(f"CO-OPS predictions unreachable: {exc}") from exc
        finally:
            if own:
                http.close()
        if resp.status_code != 200:
            raise HydroFetchError(f"CO-OPS predictions: HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise HydroFetchError("CO-OPS predictions: not JSON") from exc
        if "error" in payload:
            raise HydroFetchError(f"CO-OPS: {payload['error'].get('message', 'error')}")

        out: list[TideExtreme] = []
        for row in payload.get("predictions", []):
            try:
                t = datetime.strptime(str(row["t"]), "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
                out.append(TideExtreme(time=t, height_ft=float(row["v"]), kind=str(row["type"])))
            except (KeyError, ValueError):
                continue
        out.sort(key=lambda x: x.time)
        return out


# ---------------------------------------------------------------------------
# curve reconstruction (pure)
# ---------------------------------------------------------------------------


def tide_height(extremes: list[TideExtreme], when: datetime) -> float | None:
    """Interpolated height of tide (ft above MLLW) at ``when``.

    Cosine fit between the bracketing high and low. Returns None when ``when``
    is outside the range of the supplied extremes.
    """
    when = when.astimezone(UTC)
    for a, b in pairwise(extremes):
        if a.time <= when <= b.time:
            span = (b.time - a.time).total_seconds()
            if span <= 0.0:
                return a.height_ft
            frac = (when - a.time).total_seconds() / span
            shaped = (1.0 - math.cos(math.pi * frac)) / 2.0
            return a.height_ft + (b.height_ft - a.height_ft) * shaped
    return None


def bracketing_extremes(
    extremes: list[TideExtreme], when: datetime, *, each_side: int = 2
) -> tuple[TideExtreme, ...]:
    when = when.astimezone(UTC)
    before = [e for e in extremes if e.time <= when][-each_side:]
    after = [e for e in extremes if e.time > when][:each_side]
    return tuple(before + after)


def gate_windows(
    extremes: list[TideExtreme],
    start: datetime,
    end: datetime,
    min_ft: float,
    *,
    step: timedelta = timedelta(minutes=10),
) -> list[TideWindow]:
    """Contiguous spans in [start, end] where the height of tide is >= ``min_ft``."""
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    windows: list[TideWindow] = []
    run_start: datetime | None = None
    t = start
    while t <= end:
        h = tide_height(extremes, t)
        ok = h is not None and h >= min_ft
        if ok and run_start is None:
            run_start = t
        elif not ok and run_start is not None:
            windows.append(TideWindow(start=run_start, end=t - step, threshold_ft=min_ft))
            run_start = None
        t += step
    if run_start is not None:
        windows.append(TideWindow(start=run_start, end=end, threshold_ft=min_ft))
    return windows
