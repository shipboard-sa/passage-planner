"""Garmin ActiveCaptain Community data client.

Purpose: Download tile-based SQLite exports of marina, fuel, bridge, hazard,
    and anchorage data from the ActiveCaptain Community API, cache them on disk,
    and query them for POIs near a route's waypoints.
Version: v1 (2026-11-06) - initial build from the open-source SDK's API
    contracts and SQLite schema.
Scope: PRODUCTION - feeds briefing sections 5 (bridges/hazards) and
    7 (fuel/marina/logistics) and the shore-logistics research agent.
Architecture: hylas-routing subpackage. ``ActiveCaptainClient`` is the public
    entry point; the rest is internal.
"""

from .client import ActiveCaptainClient
from .model import (
    AnchorageInfo,
    BridgeInfo,
    ContactInfo,
    FuelInfo,
    HazardInfo,
    MarinaInfo,
    PoiType,
    RoutePoi,
)

__all__ = [
    "ActiveCaptainClient",
    "AnchorageInfo",
    "BridgeInfo",
    "ContactInfo",
    "FuelInfo",
    "HazardInfo",
    "MarinaInfo",
    "PoiType",
    "RoutePoi",
]
