"""Data models for ActiveCaptain POI data.

Purpose: Frozen dataclasses representing the POI types the routing pipeline
    cares about (marinas, fuel, bridges, hazards, anchorages) plus the
    container that ties a POI to its distance from a route waypoint.
Version: v1 (2026-11-06)
Scope: PRODUCTION
Architecture: hylas-routing activecaptain subpackage, model layer.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class PoiType(enum.IntEnum):
    """ActiveCaptain marker type bitmask values (from the SDK's MarkerType enum)."""

    MARINA = 1
    HAZARD = 2
    ANCHORAGE = 4
    BOAT_RAMP = 8
    BUSINESS = 16
    COMMUNITY = 32
    INLET = 64
    LOCK = 128
    DAM = 256
    BRIDGE = 512
    FERRY = 1024


@dataclass(frozen=True, slots=True)
class ContactInfo:
    phone: str = ""
    vhf_channel: str = ""


@dataclass(frozen=True, slots=True)
class FuelInfo:
    has_diesel: bool = False
    has_gas: bool = False
    diesel_price: float | None = None
    gas_price: float | None = None
    currency: str = "USD"
    fuel_depth_ft: float | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class DockageInfo:
    total_slips: str = ""
    transient_slips: str = ""
    loa_max_ft: float | None = None
    beam_max_ft: float | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class NavigationInfo:
    depth_low_water_ft: float | None = None
    tide: str = ""
    current: str = ""
    bridge_type: str = ""
    bridge_closed_height_ft: float | None = None
    restriction_notes: str = ""
    navigational_notes: str = ""


@dataclass(frozen=True, slots=True)
class ServiceFlags:
    fuel_station: bool = False
    repair: bool = False
    grocery: bool = False
    restaurant: bool = False
    retail: bool = False
    restrooms: bool = False
    taxi_launch: bool = False
    dealer: bool = False
    boat_ramp: bool = False


@dataclass(frozen=True, slots=True)
class MarinaInfo:
    """A marina or mooring field with structured detail from multiple AC tables."""

    marker_id: int = 0
    name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    poi_type: PoiType = PoiType.MARINA
    contact: ContactInfo = field(default_factory=ContactInfo)
    fuel: FuelInfo = field(default_factory=FuelInfo)
    dockage: DockageInfo = field(default_factory=DockageInfo)
    navigation: NavigationInfo = field(default_factory=NavigationInfo)
    services: ServiceFlags = field(default_factory=ServiceFlags)
    rating: float | None = None
    review_count: int = 0


@dataclass(frozen=True, slots=True)
class BridgeInfo:
    marker_id: int = 0
    name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    navigation: NavigationInfo = field(default_factory=NavigationInfo)


@dataclass(frozen=True, slots=True)
class HazardInfo:
    marker_id: int = 0
    name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    navigation: NavigationInfo = field(default_factory=NavigationInfo)


@dataclass(frozen=True, slots=True)
class AnchorageInfo:
    marker_id: int = 0
    name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    navigation: NavigationInfo = field(default_factory=NavigationInfo)
    rating: float | None = None
    review_count: int = 0


@dataclass(frozen=True, slots=True)
class RoutePoi:
    """A POI paired with its distance from the nearest route waypoint."""

    poi: MarinaInfo | BridgeInfo | HazardInfo | AnchorageInfo
    distance_nm: float
    nearest_wp_index: int
