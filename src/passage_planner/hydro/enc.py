"""Best-effort charted depth from the NOAA ENC Direct-to-GIS service.

US waters only. Queries the ``*.Sounding_point`` layer (attribute ``Z``, metres
at MLLW) in a small envelope around a waypoint, from the most detailed usage
band that has coverage down to the coarsest. Returns the shallowest sounding
found, tagged with the source ENC cell and survey date.

This is an approximation for planning, not a safety check — it samples charted
soundings near a point, it does not evaluate a route against ENC depth areas,
rocks, or obstructions. The briefing says so.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from ..errors import HydroFetchError
from .model import METERS_TO_FEET, ChartedDepth

_BASE = "https://gis.charttools.noaa.gov/arcgis/rest/services/encdirect"
# Most detailed first. Overview/general rarely help a shoal-draft decision.
_BANDS = ("enc_harbour", "enc_approach", "enc_coastal", "enc_general")

_NM_IN_DEG_LAT = 1.0 / 60.0


class EncClient:
    def __init__(self, *, timeout_s: float = 25.0, http: httpx.Client | None = None) -> None:
        self._timeout = timeout_s
        self._http = http
        self._layer_ids: dict[str, int | None] = {}

    def _client(self) -> tuple[httpx.Client, bool]:
        if self._http is not None:
            return self._http, False
        return httpx.Client(timeout=self._timeout), True

    def _get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        http, own = self._client()
        try:
            resp = http.get(url, params=params)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise HydroFetchError(f"NOAA ENC unreachable: {exc}") from exc
        finally:
            if own:
                http.close()
        if resp.status_code != 200:
            raise HydroFetchError(f"NOAA ENC: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise HydroFetchError("NOAA ENC: not JSON") from exc
        if isinstance(data, dict) and "error" in data:
            raise HydroFetchError(f"NOAA ENC: {data['error'].get('message', 'error')}")
        return data if isinstance(data, dict) else {}

    def _sounding_layer_id(self, band: str) -> int | None:
        if band in self._layer_ids:
            return self._layer_ids[band]
        data = self._get_json(f"{_BASE}/{band}/MapServer/layers", {"f": "json"})
        layer_id: int | None = None
        for layer in data.get("layers", []):
            if str(layer.get("name", "")).endswith(".Sounding_point"):
                layer_id = int(layer["id"])
                break
        self._layer_ids[band] = layer_id
        return layer_id

    def sounding_near(
        self, lat: float, lon: float, *, radius_nm: float = 0.3
    ) -> ChartedDepth | None:
        d_lat = radius_nm * _NM_IN_DEG_LAT
        d_lon = d_lat / max(0.1, abs(math.cos(math.radians(lat))))
        envelope = f"{lon - d_lon},{lat - d_lat},{lon + d_lon},{lat + d_lat}"

        for band in _BANDS:
            layer_id = self._sounding_layer_id(band)
            if layer_id is None:
                continue
            data = self._get_json(
                f"{_BASE}/{band}/MapServer/{layer_id}/query",
                {
                    "geometry": envelope, "geometryType": "esriGeometryEnvelope",
                    "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "Z,DSNM,SORDAT", "returnGeometry": "false", "f": "json",
                },
            )
            features = data.get("features", [])
            depths = [
                (float(f["attributes"]["Z"]), f["attributes"])
                for f in features
                if isinstance(f.get("attributes"), dict)
                and f["attributes"].get("Z") is not None
            ]
            if not depths:
                continue
            z_m, attrs = min(depths, key=lambda p: p[0])
            cell = str(attrs.get("DSNM", "")).strip()
            return ChartedDepth(
                depth_ft=round(z_m * METERS_TO_FEET, 1),
                datum="MLLW",
                source=f"NOAA ENC {cell}" if cell else "NOAA ENC",
                is_operator_supplied=False,
                survey_date=str(attrs.get("SORDAT", "")).strip(),
                notes=f"shallowest of {len(depths)} soundings within {radius_nm:.1f} NM",
            )
        return None
