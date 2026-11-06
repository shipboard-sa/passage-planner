"""ActiveCaptain Community API client — tile download and local query.

Purpose: Fetch tile-based SQLite exports from the Garmin ActiveCaptain
    third-party API, cache them on disk under ``var/cache/activecaptain/``,
    and expose a ``near_route()`` method that returns structured POI data
    along a route's waypoints.
Version: v1 (2026-11-06)
Scope: PRODUCTION
Architecture: hylas-routing activecaptain subpackage, client layer.

The API workflow (from the open-source SDK):
  1. POST bounding boxes -> tile coordinates
  2. POST tile coordinates -> export download URLs (gzipped SQLite)
  3. Download, decompress, cache, and query locally

The API key is read from the ``ACTIVECAPTAIN_API_KEY`` env var. Stage vs
production is controlled by ``ACTIVECAPTAIN_ENV`` (default ``production``).
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import shutil
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..errors import ActiveCaptainDataError, ActiveCaptainFetchError
from ..geo import great_circle_nm
from .model import (
    PoiType,
    RoutePoi,
)
from .query import query_bbox

log = logging.getLogger(__name__)

_STAGE_BASE = "https://activecaptain-stage.garmin.com/community/thirdparty/"
_PROD_BASE = "https://activecaptain.garmin.com/community/thirdparty/"

_TILES_PATH = "api/v2/points-of-interest/tiles"
_EXPORT_PATH = "api/v2/points-of-interest/export"

_TILE_MAX_AGE = timedelta(days=7)


class ActiveCaptainClient:
    """Download and query ActiveCaptain tile databases along a route.

    Parameters
    ----------
    cache_dir
        Root of the cache tree; tiles go under ``<cache_dir>/activecaptain/``.
    api_key
        The third-party API key. Falls back to ``ACTIVECAPTAIN_API_KEY``.
    env
        ``"stage"`` or ``"production"`` (default). Falls back to
        ``ACTIVECAPTAIN_ENV``.
    timeout_s
        HTTP timeout for API calls and tile downloads.
    http
        Optional shared ``httpx.Client``; one is created if not provided.
    """

    def __init__(
        self,
        *,
        cache_dir: str | Path = "var/cache",
        api_key: str | None = None,
        env: str | None = None,
        timeout_s: float = 60.0,
        http: httpx.Client | None = None,
    ) -> None:
        self._cache = Path(cache_dir) / "activecaptain"
        self._cache.mkdir(parents=True, exist_ok=True)

        self._api_key = api_key or os.environ.get("ACTIVECAPTAIN_API_KEY", "")
        if not self._api_key:
            raise ActiveCaptainFetchError(
                "No ActiveCaptain API key: set ACTIVECAPTAIN_API_KEY or pass api_key="
            )

        env_val = env or os.environ.get("ACTIVECAPTAIN_ENV", "production")
        self._base = _STAGE_BASE if env_val == "stage" else _PROD_BASE

        self._timeout = timeout_s
        self._http = http

    def _client(self) -> tuple[httpx.Client, bool]:
        if self._http is not None:
            return self._http, False
        return httpx.Client(timeout=self._timeout), True

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._api_key,
            "Content-Type": "application/json",
        }

    # -- API calls ---------------------------------------------------------

    def get_tiles(
        self,
        south: float,
        west: float,
        north: float,
        east: float,
    ) -> list[tuple[int, int]]:
        """POST a bounding box and return the covering tile coordinates."""
        body = [
            {
                "southwestCorner": {"latitude": south, "longitude": west},
                "northeastCorner": {"latitude": north, "longitude": east},
            }
        ]
        http, own = self._client()
        try:
            resp = http.post(
                f"{self._base}{_TILES_PATH}",
                headers=self._headers(),
                json=body,
            )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise ActiveCaptainFetchError(
                f"ActiveCaptain tiles endpoint unreachable: {exc}"
            ) from exc
        finally:
            if own:
                http.close()

        if resp.status_code != 200:
            raise ActiveCaptainFetchError(
                f"ActiveCaptain tiles: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return [(t["tileX"], t["tileY"]) for t in resp.json()]

    def get_export_urls(
        self,
        tiles: Sequence[tuple[int, int]],
    ) -> dict[tuple[int, int], dict[str, Any]]:
        """POST tile coordinates and return download metadata keyed by (x, y)."""
        body = [{"tileX": x, "tileY": y} for x, y in tiles]
        http, own = self._client()
        try:
            resp = http.post(
                f"{self._base}{_EXPORT_PATH}",
                headers=self._headers(),
                json=body,
            )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise ActiveCaptainFetchError(
                f"ActiveCaptain export endpoint unreachable: {exc}"
            ) from exc
        finally:
            if own:
                http.close()

        if resp.status_code != 200:
            raise ActiveCaptainFetchError(
                f"ActiveCaptain export: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        result: dict[tuple[int, int], dict[str, Any]] = {}
        for entry in resp.json():
            key = (entry["tileX"], entry["tileY"])
            result[key] = entry
        return result

    # -- Tile cache --------------------------------------------------------

    def _tile_path(self, tx: int, ty: int) -> Path:
        return self._cache / f"tile_{tx}_{ty}.db"

    def _tile_fresh(self, path: Path) -> bool:
        if not path.is_file():
            return False
        age = datetime.now(UTC).timestamp() - path.stat().st_mtime
        return age < _TILE_MAX_AGE.total_seconds()

    def _download_tile(
        self,
        url: str,
        md5_expected: str,
        dest: Path,
    ) -> None:
        http, own = self._client()
        gz_path = dest.with_suffix(".db.gz")
        try:
            with http.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise ActiveCaptainFetchError(
                        f"Tile download HTTP {resp.status_code}"
                    )
                hasher = hashlib.md5()
                with open(gz_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        hasher.update(chunk)
            if hasher.hexdigest() != md5_expected:
                gz_path.unlink(missing_ok=True)
                raise ActiveCaptainDataError(
                    f"MD5 mismatch for {dest.name}: "
                    f"expected {md5_expected}, got {hasher.hexdigest()}"
                )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            gz_path.unlink(missing_ok=True)
            raise ActiveCaptainFetchError(
                f"Tile download failed: {exc}"
            ) from exc
        finally:
            if own:
                http.close()

        with gzip.open(gz_path, "rb") as gz, open(dest, "wb") as out:
            shutil.copyfileobj(gz, out)
        gz_path.unlink(missing_ok=True)

        try:
            conn = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
            conn.execute("SELECT count(*) FROM markers")
            conn.close()
        except sqlite3.Error as exc:
            dest.unlink(missing_ok=True)
            raise ActiveCaptainDataError(
                f"Downloaded tile is not a valid SQLite database: {exc}"
            ) from exc

    def ensure_tiles(
        self,
        south: float,
        west: float,
        north: float,
        east: float,
    ) -> list[Path]:
        """Download (or use cached) tile databases covering the bounding box.

        Returns the list of SQLite file paths on disk.
        """
        tiles = self.get_tiles(south, west, north, east)
        if not tiles:
            return []

        paths: list[Path] = []
        stale = [(tx, ty) for tx, ty in tiles if not self._tile_fresh(self._tile_path(tx, ty))]

        if stale:
            exports = self.get_export_urls(stale)
            for tx, ty in stale:
                meta = exports.get((tx, ty))
                if not meta:
                    log.warning("No export URL for tile (%d, %d)", tx, ty)
                    continue
                gzip_meta = meta.get("gzip", meta.get("zip"))
                if not gzip_meta:
                    log.warning("No download URL for tile (%d, %d)", tx, ty)
                    continue
                dest = self._tile_path(tx, ty)
                log.info(
                    "Downloading tile (%d, %d): %.1f MB",
                    tx, ty, gzip_meta["fileSize"] / 1_000_000,
                )
                self._download_tile(
                    gzip_meta["url"],
                    gzip_meta["md5Hash"],
                    dest,
                )

        for tx, ty in tiles:
            p = self._tile_path(tx, ty)
            if p.is_file():
                paths.append(p)
        return paths

    # -- Route query -------------------------------------------------------

    def near_route(
        self,
        waypoints: Sequence[tuple[float, float]],
        *,
        radius_nm: float = 10.0,
        poi_types: frozenset[PoiType] | None = None,
    ) -> list[RoutePoi]:
        """Find POIs near a route's waypoints.

        Parameters
        ----------
        waypoints
            Sequence of (lat, lon) decimal-degree pairs.
        radius_nm
            Search radius around each waypoint, nautical miles.
        poi_types
            Restrict to these POI types; ``None`` means all.

        Returns
        -------
        list[RoutePoi]
            POIs sorted by distance from the nearest waypoint.
        """
        if not waypoints:
            return []

        lats = [w[0] for w in waypoints]
        lons = [w[1] for w in waypoints]
        margin = radius_nm / 60.0
        south = min(lats) - margin
        north = max(lats) + margin
        west = min(lons) - margin
        east = max(lons) + margin

        tile_paths = self.ensure_tiles(south, west, north, east)
        if not tile_paths:
            return []

        seen: set[int] = set()
        results: list[RoutePoi] = []

        for db_path in tile_paths:
            pois = query_bbox(db_path, south, west, north, east, poi_types)
            for poi in pois:
                mid = poi.marker_id
                if mid in seen:
                    continue
                seen.add(mid)

                best_dist = float("inf")
                best_idx = 0
                for i, (wlat, wlon) in enumerate(waypoints):
                    d = great_circle_nm(poi.lat, poi.lon, wlat, wlon)
                    if d < best_dist:
                        best_dist = d
                        best_idx = i

                if best_dist <= radius_nm:
                    results.append(RoutePoi(
                        poi=poi,
                        distance_nm=round(best_dist, 2),
                        nearest_wp_index=best_idx,
                    ))

        results.sort(key=lambda r: r.distance_nm)
        return results
