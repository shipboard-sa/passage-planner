"""Saved chart soundings: depths a person read off a chart, kept by position.

Purpose: Store the depths the skipper (or anyone aboard) reads off Navionics or an
    Explorer chartbook for waypoints the automatic data cannot judge, and reuse them
    automatically on every later route that passes the same spot.
Version: v1 (2026-09-21)
Scope: PRODUCTION - feeds briefing section 5 (depth and clearance) and the web
    form's "Depth check" screen.
Architecture: passage-planner hydro/ support module. Pure functions plus one JSON file,
    ``config/depth_soundings.json``. Replaces hand-editing ``waypoint_depths.toml``
    for the crew (that file and ``--depths-file`` still work and take precedence).
    Called by service.run_brief() (library depths become the ``waypoint_depths``
    that assess.assess_route already accepts) and by backend/main.py's
    /api/depth-check and /api/depth-soundings endpoints.

Two decisions worth knowing:

* **By position, not by name.** The old file matched route waypoint NAMES, but a
  Navionics export names every waypoint WP1, WP2, ..., so a name means nothing on
  the next route. A sounding is knowledge about a place, so a waypoint within
  ``MATCH_NM`` (0.5 NM) of a saved sounding picks it up, whatever it is called.
* **The shallowest wins.** Where several saved soundings are within range, the
  shallowest is used: a controlling depth is by definition the minimum, and the
  conservative choice is the safe one.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ..errors import HydroError
from ..geo import great_circle_nm
from .model import ChartedDepth, HydroReport

MATCH_NM = 0.5  # a waypoint this close to a saved sounding uses it
SAME_SPOT_NM = 0.1  # saving a sounding this close to an existing one updates it
MAX_DEPTH_FT = 200.0
LIBRARY_NAME = "depth_soundings.json"
DATUM_NOTE = "chart datum as shown on the chart"


@dataclass(frozen=True, slots=True)
class Sounding:
    id: str
    lat: float
    lon: float
    depth_ft: float
    source: str = ""  # where it was read, e.g. "Navionics", "Explorer Chartbook Abaco"
    notes: str = ""
    label: str = ""  # what it was entered for, e.g. "WP5"
    recorded: str = ""  # ISO date


def library_path(config_dir: Path) -> Path:
    return config_dir / LIBRARY_NAME


def load_soundings(path: Path) -> list[Sounding]:
    """The saved soundings; a missing file is an empty library."""
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw["soundings"]
        return [Sounding(**item) for item in items]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HydroError(f"{path.name}: cannot read the saved depths ({exc})") from exc


def save_soundings(path: Path, soundings: list[Sounding]) -> None:
    """Write the library atomically (temp file then replace), so a crash mid-write
    cannot lose the depths someone spent time reading off a chart."""
    payload = {"soundings": [asdict(s) for s in sorted(soundings, key=lambda x: x.id)]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def validate(depth_ft: float, lat: float, lon: float) -> None:
    if not (0 < depth_ft <= MAX_DEPTH_FT):
        raise HydroError(f"depth must be between 0 and {MAX_DEPTH_FT:g} ft (got {depth_ft:g})")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HydroError("position out of range")


def upsert(
    library: list[Sounding],
    *,
    lat: float,
    lon: float,
    depth_ft: float,
    source: str = "",
    notes: str = "",
    label: str = "",
    today: date | None = None,
) -> list[Sounding]:
    """The library with a sounding added, or updated if one is already within
    ``SAME_SPOT_NM`` of this position (so re-saving a waypoint corrects it rather
    than piling up duplicates). Returns a new list."""
    validate(depth_ft, lat, lon)
    stamp = (today or datetime.now(UTC).date()).isoformat()
    near = [s for s in library if great_circle_nm(lat, lon, s.lat, s.lon) <= SAME_SPOT_NM]
    keep = [s for s in library if s not in near]
    ident = near[0].id if near else uuid.uuid4().hex[:8]
    keep.append(Sounding(
        id=ident, lat=round(lat, 5), lon=round(lon, 5), depth_ft=float(depth_ft),
        source=source.strip(), notes=notes.strip(), label=label.strip(), recorded=stamp,
    ))
    return keep


def remove(library: list[Sounding], ident: str) -> list[Sounding]:
    return [s for s in library if s.id != ident]


def match(library: list[Sounding], lat: float, lon: float) -> Sounding | None:
    """The saved sounding a waypoint at (lat, lon) should use: the shallowest within
    ``MATCH_NM``, or ``None``."""
    close = [s for s in library if great_circle_nm(lat, lon, s.lat, s.lon) <= MATCH_NM]
    return min(close, key=lambda s: s.depth_ft) if close else None


def _charted(s: Sounding, dist_nm: float) -> ChartedDepth:
    bits = [f"saved {s.recorded}" if s.recorded else "saved", f"{dist_nm:.2f} NM from waypoint"]
    if s.label:
        bits.append(f"entered for {s.label}")
    if s.notes:
        bits.append(s.notes)
    return ChartedDepth(
        depth_ft=s.depth_ft,
        datum=DATUM_NOTE,
        source="operator (saved depth): " + (s.source or "hand-read from chart"),
        is_operator_supplied=True,
        notes="; ".join(bits),
    )


def soundings_to_depths(
    waypoints: list[tuple[str, float, float]], library: list[Sounding]
) -> dict[str, ChartedDepth]:
    """The ``waypoint_depths`` mapping (lower-cased waypoint name to depth) that
    ``assess_route`` takes, built from saved soundings matched by position."""
    out: dict[str, ChartedDepth] = {}
    for name, lat, lon in waypoints:
        hit = match(library, lat, lon)
        if hit is not None:
            out[name.strip().lower()] = _charted(hit, great_circle_nm(lat, lon, hit.lat, hit.lon))
    return out


def depth_rows(report: HydroReport, library: list[Sounding]) -> list[dict[str, Any]]:
    """What the Depth check screen shows: the waypoints that need someone's attention
    (no depth and not deep water, or too shallow at the tide), plus those already
    covered by a saved sounding so it can be corrected or forgotten. Route order."""
    rows: list[dict[str, Any]] = []
    for w in report.waypoints:
        saved = match(library, w.lat, w.lon)
        if w.needs_screenshot:
            status = "needs_depth"
        elif w.marginal:
            status = "tight"
        elif saved is not None and w.charted is not None and w.charted.is_operator_supplied:
            status = "saved"
        else:
            continue
        rows.append({
            "name": w.name,
            "lat": w.lat,
            "lon": w.lon,
            "status": status,
            "automatic": _automatic_text(w),
            "tide_ft": w.tide_at_eta_ft,
            "required_ft": w.required_charted_ft,
            "saved": None if saved is None else {
                "id": saved.id, "depth_ft": saved.depth_ft, "source": saved.source,
                "notes": saved.notes, "recorded": saved.recorded,
            },
        })
    return rows


def _automatic_text(w: Any) -> str:
    """One plain sentence on what the automatic data says about this waypoint."""
    c = w.charted
    if c is not None and not c.is_operator_supplied:
        return f"NOAA chart shows {c.depth_ft:.1f} ft ({c.source})."
    if w.ocean_depth_ft is not None:
        return (
            f"No detailed chart data here. Ocean-depth data says about {w.ocean_depth_ft:.0f} ft, "
            "which is too rough to trust close to shore."
        )
    return "No automatic depth data for this spot."
