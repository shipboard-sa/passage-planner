"""Depth, clearance, and tide-gate assessment for a route.

Tides come from NOAA CO-OPS (everywhere in US waters). Charted depth is
best-effort from NOAA ENC where it has coverage, otherwise operator-supplied
via ``waypoint_depths.toml``.
"""

from __future__ import annotations

from .assess import assess_route
from .bathy import BathyClient
from .depths import depths_template, load_waypoint_depths
from .enc import EncClient
from .model import (
    ChartedDepth,
    HydroReport,
    TideExtreme,
    TideStation,
    TideWindow,
    WaypointHydro,
)
from .tides import TidesClient, bracketing_extremes, gate_windows, tide_height

__all__ = [
    "BathyClient",
    "ChartedDepth",
    "EncClient",
    "HydroReport",
    "TideExtreme",
    "TideStation",
    "TideWindow",
    "TidesClient",
    "WaypointHydro",
    "assess_route",
    "bracketing_extremes",
    "depths_template",
    "gate_windows",
    "load_waypoint_depths",
    "tide_height",
]
