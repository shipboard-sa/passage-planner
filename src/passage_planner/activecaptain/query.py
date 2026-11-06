"""SQLite queries against a downloaded ActiveCaptain tile database.

Purpose: Read markers, fuel, dockage, navigation, contact, services, and
    reviews from a tile's SQLite file using the R-tree spatial index.
Version: v1 (2026-11-06)
Scope: PRODUCTION
Architecture: hylas-routing activecaptain subpackage, data-access layer.
    Called by client.py; never imported directly outside this subpackage.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .model import (
    AnchorageInfo,
    BridgeInfo,
    ContactInfo,
    DockageInfo,
    FuelInfo,
    HazardInfo,
    MarinaInfo,
    NavigationInfo,
    PoiType,
    ServiceFlags,
)

_SEMI_TO_DEG = 180.0 / (2**31)


def _deg_to_semi(deg: float) -> int:
    """Convert decimal degrees to Garmin semicircle int32."""
    return int(deg / _SEMI_TO_DEG)


def _resolve_text(
    cur: sqlite3.Cursor,
    handle: int | None,
    lang_id: int = 1,
) -> str:
    if handle is None:
        return ""
    row = cur.execute(
        "SELECT translation FROM translations WHERE id=? AND langId=?",
        (handle, lang_id),
    ).fetchone()
    return row[0] if row else ""


def _parse_labeled_value(
    cur: sqlite3.Cursor,
    item: dict[str, Any],
) -> tuple[str, str]:
    """Return (field_name, value) from a labeled JSON item."""
    fname = _resolve_text(cur, item.get("fieldTextHandle"))
    val = item.get("value", "")
    vtx = item.get("valueTextHandle")
    if vtx is not None:
        val = _resolve_text(cur, vtx)
    return fname, str(val)


def _parse_yesno_value(
    cur: sqlite3.Cursor,
    item: dict[str, Any],
) -> tuple[str, str]:
    fname = _resolve_text(cur, item.get("fieldTextHandle"))
    val = item.get("value", "")
    return fname, str(val)


def _safe_float(val: str) -> float | None:
    if not val:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _meters_to_feet(m: float | None) -> float | None:
    if m is None:
        return None
    return round(m * 3.28084, 1)


def _build_navigation(cur: sqlite3.Cursor, marker_id: int) -> NavigationInfo:
    row = cur.execute(
        "SELECT labeled, sectionNote, distanceUnit FROM navigation WHERE id=?",
        (marker_id,),
    ).fetchone()
    if not row:
        return NavigationInfo()

    labeled_json, note_json, dist_unit = row
    depth_ft: float | None = None
    tide = ""
    current = ""
    bridge_type = ""
    closed_height_ft: float | None = None
    restriction = ""
    nav_notes = ""

    # distanceUnit: 0=unset, 1=feet(?), 2=meters. In practice all observed
    # tile data uses unit=2 with values in meters. Convert to feet always
    # when the unit is metric (2) or unknown (0, treat as meters since the
    # data set is metric-first).
    is_metric = dist_unit != 1

    if labeled_json:
        for item in json.loads(labeled_json):
            fname, val = _parse_labeled_value(cur, item)
            fl = fname.lower()
            if "depth" in fl:
                raw = _safe_float(val)
                if raw is not None and is_metric:
                    depth_ft = _meters_to_feet(raw)
                elif raw is not None:
                    depth_ft = raw
            elif "tide" in fl:
                tide = val
            elif "current" in fl:
                current = val
            elif "bridge type" in fl:
                bridge_type = val
            elif "closed height" in fl or "clearance" in fl:
                raw = _safe_float(val)
                if raw is not None and is_metric:
                    closed_height_ft = _meters_to_feet(raw)
                elif raw is not None:
                    closed_height_ft = raw
            elif "restriction" in fl:
                restriction = val
            elif "navigational" in fl or "note" in fl:
                nav_notes = val

    if note_json:
        parsed = json.loads(note_json)
        if isinstance(parsed, dict):
            extra = parsed.get("value", "")
            if extra:
                nav_notes = f"{nav_notes}  {extra}".strip() if nav_notes else extra

    return NavigationInfo(
        depth_low_water_ft=depth_ft,
        tide=tide,
        current=current,
        bridge_type=bridge_type,
        bridge_closed_height_ft=closed_height_ft,
        restriction_notes=restriction,
        navigational_notes=nav_notes,
    )


def _build_contact(cur: sqlite3.Cursor, marker_id: int) -> ContactInfo:
    row = cur.execute(
        "SELECT phone, vhfChannel FROM contact WHERE id=?",
        (marker_id,),
    ).fetchone()
    if not row:
        return ContactInfo()
    return ContactInfo(phone=row[0] or "", vhf_channel=row[1] or "")


def _build_fuel(cur: sqlite3.Cursor, marker_id: int) -> FuelInfo:
    row = cur.execute(
        "SELECT dieselPrice, gasPrice, currency, volumeUnit, "
        "yesNo, labeled, sectionNote, distanceUnit FROM fuel WHERE id=?",
        (marker_id,),
    ).fetchone()
    if not row:
        return FuelInfo()

    diesel_price, gas_price, currency, _vol_unit, yn_json, lab_json, note_json, dist_unit = row

    has_diesel = False
    has_gas = False
    fuel_depth: float | None = None
    notes = ""

    if yn_json:
        for item in json.loads(yn_json):
            fname, val = _parse_yesno_value(cur, item)
            fl = fname.lower()
            if "ethanol" in fl:
                pass
            elif "diesel" in fl:
                has_diesel = val.lower() not in ("no", "unknown", "")
            elif "propane" in fl:
                pass
            elif "gas" in fl:
                has_gas = val.lower() not in ("no", "unknown", "")

    if not has_diesel and diesel_price and diesel_price > 0:
        has_diesel = True
    if not has_gas and gas_price and gas_price > 0:
        has_gas = True

    is_metric = dist_unit != 1

    if lab_json:
        for item in json.loads(lab_json):
            fname, val = _parse_labeled_value(cur, item)
            if "depth" in fname.lower():
                raw = _safe_float(val)
                if raw is not None and is_metric:
                    fuel_depth = _meters_to_feet(raw)
                elif raw is not None:
                    fuel_depth = raw

    if note_json:
        parsed = json.loads(note_json)
        if isinstance(parsed, dict):
            notes = parsed.get("value", "")

    return FuelInfo(
        has_diesel=has_diesel,
        has_gas=has_gas,
        diesel_price=diesel_price if diesel_price else None,
        gas_price=gas_price if gas_price else None,
        currency=currency or "USD",
        fuel_depth_ft=fuel_depth,
        notes=notes,
    )


def _build_dockage(cur: sqlite3.Cursor, marker_id: int) -> DockageInfo:
    row = cur.execute(
        "SELECT labeled, sectionNote, distanceUnit FROM dockage WHERE id=?",
        (marker_id,),
    ).fetchone()
    if not row:
        return DockageInfo()

    lab_json, note_json, dist_unit = row
    total = ""
    transient = ""
    loa: float | None = None
    beam: float | None = None
    notes = ""

    is_metric = dist_unit != 1

    if lab_json:
        for item in json.loads(lab_json):
            fname, val = _parse_labeled_value(cur, item)
            fl = fname.lower()
            if "total" in fl:
                total = val
            elif "transient" in fl:
                transient = val
            elif "loa" in fl:
                raw = _safe_float(val)
                if raw is not None and is_metric:
                    loa = _meters_to_feet(raw)
                elif raw is not None:
                    loa = raw
            elif "beam" in fl:
                raw = _safe_float(val)
                if raw is not None and is_metric:
                    beam = _meters_to_feet(raw)
                elif raw is not None:
                    beam = raw

    if note_json:
        parsed = json.loads(note_json)
        if isinstance(parsed, dict):
            notes = parsed.get("value", "")

    return DockageInfo(
        total_slips=total,
        transient_slips=transient,
        loa_max_ft=loa,
        beam_max_ft=beam,
        notes=notes,
    )


def _build_service_flags(search_filter: int) -> ServiceFlags:
    return ServiceFlags(
        fuel_station=bool(search_filter & (1 << 1)),
        repair=bool(search_filter & (1 << 5)),
        grocery=bool(search_filter & (1 << 8)),
        restaurant=bool(search_filter & (1 << 2)),
        retail=bool(search_filter & (1 << 3)),
        restrooms=bool(search_filter & (1 << 9)),
        taxi_launch=bool(search_filter & (1 << 4)),
        dealer=bool(search_filter & (1 << 6)),
        boat_ramp=bool(search_filter & (1 << 7)),
    )


def _review_stats(cur: sqlite3.Cursor, marker_id: int) -> tuple[float | None, int]:
    row = cur.execute(
        "SELECT AVG(rating), COUNT(*) FROM reviews WHERE markerId=?",
        (marker_id,),
    ).fetchone()
    if not row or row[1] == 0:
        return None, 0
    return row[0], row[1]


def query_bbox(
    db_path: Path,
    south: float,
    west: float,
    north: float,
    east: float,
    poi_types: frozenset[PoiType] | None = None,
) -> list[MarinaInfo | BridgeInfo | HazardInfo | AnchorageInfo]:
    """Query a tile database for POIs within a lat/lon bounding box.

    Coordinates are decimal degrees. The R-tree stores Garmin semicircles
    (int32); this function converts on the fly.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    s_south = _deg_to_semi(south)
    s_north = _deg_to_semi(north)
    s_west = _deg_to_semi(west)
    s_east = _deg_to_semi(east)

    type_mask = 0
    if poi_types:
        for t in poi_types:
            type_mask |= t
    else:
        type_mask = 0xFFFFFFFF

    rows = cur.execute(
        """
        SELECT m.id, m.poi_type, m.name, m.searchFilter,
               ri.minLat, ri.minLon
        FROM markers m
        JOIN rIndex ri ON ri.id = m.id
        WHERE ri.minLat >= ? AND ri.maxLat <= ?
          AND ri.minLon >= ? AND ri.maxLon <= ?
          AND (m.poi_type & ?) != 0
        """,
        (s_south, s_north, s_west, s_east, type_mask),
    ).fetchall()

    results: list[MarinaInfo | BridgeInfo | HazardInfo | AnchorageInfo] = []
    for row in rows:
        mid = row["id"]
        ptype = row["poi_type"]
        name = row["name"] or ""
        lat = row["minLat"] * _SEMI_TO_DEG
        lon = row["minLon"] * _SEMI_TO_DEG
        sf = row["searchFilter"] or 0

        if ptype == PoiType.BRIDGE:
            results.append(BridgeInfo(
                marker_id=mid,
                name=name,
                lat=lat,
                lon=lon,
                navigation=_build_navigation(cur, mid),
            ))
        elif ptype == PoiType.HAZARD:
            results.append(HazardInfo(
                marker_id=mid,
                name=name,
                lat=lat,
                lon=lon,
                navigation=_build_navigation(cur, mid),
            ))
        elif ptype == PoiType.ANCHORAGE:
            rating, count = _review_stats(cur, mid)
            results.append(AnchorageInfo(
                marker_id=mid,
                name=name,
                lat=lat,
                lon=lon,
                navigation=_build_navigation(cur, mid),
                rating=rating,
                review_count=count,
            ))
        else:
            rating, count = _review_stats(cur, mid)
            results.append(MarinaInfo(
                marker_id=mid,
                name=name,
                lat=lat,
                lon=lon,
                poi_type=(
                    PoiType(ptype)
                    if ptype in PoiType.__members__.values()
                    else PoiType.MARINA
                ),
                contact=_build_contact(cur, mid),
                fuel=_build_fuel(cur, mid),
                dockage=_build_dockage(cur, mid),
                navigation=_build_navigation(cur, mid),
                services=_build_service_flags(sf),
                rating=rating,
                review_count=count,
            ))

    conn.close()
    return results
