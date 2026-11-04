"""Coarse ocean depth from the GEBCO 2020 global grid (via opentopodata.org).

~450 m resolution — nowhere near a navigation depth, but plenty to tell a
waypoint in 6 ft of water from one in 6000 ft, so the depth-data request only
lists the waypoints that could actually be a draft problem.
"""

from __future__ import annotations

import time

import httpx

from ..errors import HydroFetchError
from .model import METERS_TO_FEET

_URL = "https://api.opentopodata.org/v1/gebco2020"
_MAX_PER_CALL = 100
_MIN_INTERVAL_S = 1.1  # opentopodata free tier: 1 call/sec


class BathyClient:
    def __init__(self, *, timeout_s: float = 20.0, http: httpx.Client | None = None) -> None:
        self._timeout = timeout_s
        self._http = http
        self._last_call = 0.0

    def _client(self) -> tuple[httpx.Client, bool]:
        if self._http is not None:
            return self._http, False
        return httpx.Client(timeout=self._timeout), True

    def depths_ft(self, points: list[tuple[float, float]]) -> list[float | None]:
        """Water depth (positive feet) per point; None for land or on error."""
        out: list[float | None] = []
        for start in range(0, len(points), _MAX_PER_CALL):
            chunk = points[start : start + _MAX_PER_CALL]
            out.extend(self._one_call(chunk))
        return out

    def _one_call(self, chunk: list[tuple[float, float]]) -> list[float | None]:
        wait = _MIN_INTERVAL_S - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        locs = "|".join(f"{lat:.5f},{lon:.5f}" for lat, lon in chunk)
        http, own = self._client()
        try:
            resp = http.get(_URL, params={"locations": locs})
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise HydroFetchError(f"GEBCO bathymetry unreachable: {exc}") from exc
        finally:
            self._last_call = time.monotonic()
            if own:
                http.close()
        if resp.status_code != 200:
            raise HydroFetchError(f"GEBCO bathymetry: HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise HydroFetchError("GEBCO bathymetry: not JSON") from exc

        results = payload.get("results", [])
        depths: list[float | None] = []
        for r in results:
            elev = r.get("elevation")
            if elev is None or elev >= 0.0:
                depths.append(None)  # land / at-or-above sea level
            else:
                depths.append(round(-float(elev) * METERS_TO_FEET, 0))
        # Pad if the API returned fewer rows than asked (shouldn't happen).
        while len(depths) < len(chunk):
            depths.append(None)
        return depths
