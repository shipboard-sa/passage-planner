"""NHC tropical check: active systems and the Atlantic Tropical Weather Outlook,
screened against the route corridor.

Purpose: Screen NHC active-storm and Tropical Weather Outlook feeds against
    the route corridor and render the result for briefing section 3a.
Version: v2 (2026-09-22) - trimmed rendered text to terse facts, no
    commentary.
Scope: PRODUCTION - feeds briefing.py section 3a of passage-planner.
Architecture: passage-planner weather submodule. render_tropical_markdown() is
    called by briefing.py's _weather(); no auth, keyless NHC JSON/XML feeds.

Two feeds, both public and keyless:

* ``CurrentStorms.json`` — active cyclones with current position and motion
  vector. NHC does not publish forecast track points here (only KMZ/zip GIS),
  so a system's near future is **dead-reckoned** from its current heading and
  speed. That is only good for a day or two; beyond that the real forecast
  cone on nhc.noaa.gov governs, and the briefing says so.
* ``TWOAT.xml`` — the Atlantic Tropical Weather Outlook text (areas of
  interest and 7-day formation odds).

A system counts as "in the corridor" when its current position or any
dead-reckoned position within the lookahead window comes within
``corridor_nm`` of the route.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ..errors import WeatherFetchError, WeatherParseError
from ..geo import destination_point, great_circle_nm
from ..route import Route

_CURRENT_STORMS_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
_TWO_ATLANTIC_URL = "https://www.nhc.noaa.gov/xml/TWOAT.xml"

_CLASS_LABEL = {
    "TD": "Tropical Depression", "TS": "Tropical Storm", "HU": "Hurricane",
    "PTC": "Potential Tropical Cyclone", "STD": "Subtropical Depression",
    "STS": "Subtropical Storm", "PC": "Post-Tropical Cyclone",
}


@dataclass(frozen=True, slots=True)
class TropicalSystem:
    id: str
    name: str
    classification: str
    intensity_kn: float | None
    lat: float
    lon: float
    movement_dir_deg: float | None
    movement_speed_kn: float | None
    last_update: str
    advisory_url: str | None
    graphics_url: str | None
    closest_nm: float
    closest_at_h: int          # 0 = now; otherwise the DR step hour
    in_corridor: bool

    @property
    def label(self) -> str:
        return _CLASS_LABEL.get(self.classification, self.classification)


@dataclass(frozen=True, slots=True)
class TropicalOutlook:
    issued: datetime
    systems: tuple[TropicalSystem, ...]
    atlantic_two_text: str
    formation_expected: bool
    corridor_nm: float
    lookahead_h: int

    @property
    def any_in_corridor(self) -> bool:
        return any(s.in_corridor for s in self.systems)

    @property
    def clear(self) -> bool:
        return not self.any_in_corridor and not self.formation_expected


# ---------------------------------------------------------------------------


def _num(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _route_min_distance(route: Route, lat: float, lon: float) -> float:
    return min(
        great_circle_nm(lat, lon, s_lat, s_lon)
        for _, s_lat, s_lon in route.sample_points(step_nm=20.0)
    )


def _screen_system(
    raw: dict[str, object], route: Route, corridor_nm: float, lookahead_h: int, step_h: int
) -> TropicalSystem | None:
    lat = _num(raw.get("latitudeNumeric"))
    lon = _num(raw.get("longitudeNumeric"))
    if lat is None or lon is None:
        return None

    mdir = _num(raw.get("movementDir"))
    mspd = _num(raw.get("movementSpeed"))

    best_nm = _route_min_distance(route, lat, lon)
    best_h = 0
    if mdir is not None and mspd is not None and mspd > 0.0:
        h = step_h
        while h <= lookahead_h:
            plat, plon = destination_point(lat, lon, mdir, mspd * h)
            d = _route_min_distance(route, plat, plon)
            if d < best_nm:
                best_nm, best_h = d, h
            h += step_h

    adv = raw.get("publicAdvisory")
    gfx = raw.get("forecastGraphics")
    return TropicalSystem(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        classification=str(raw.get("classification", "")),
        intensity_kn=_num(raw.get("intensity")),
        lat=lat,
        lon=lon,
        movement_dir_deg=mdir,
        movement_speed_kn=mspd,
        last_update=str(raw.get("lastUpdate", "")),
        advisory_url=str(adv["url"]) if isinstance(adv, dict) and "url" in adv else None,
        graphics_url=str(gfx["url"]) if isinstance(gfx, dict) and "url" in gfx else None,
        closest_nm=round(best_nm, 1),
        closest_at_h=best_h,
        in_corridor=best_nm <= corridor_nm,
    )


def _parse_two(xml_text: str) -> tuple[str, bool]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise WeatherParseError(f"TWOAT.xml: {exc}") from exc
    desc = ""
    for item in root.iter("item"):
        d = item.findtext("description") or ""
        if "Tropical Weather Outlook" in d:
            desc = d
            break
    text = re.sub(r"<br\s*/?>", "\n", desc)
    text = re.sub(r"<[^>]+>", "", text).strip()
    # Trim the AWIPS header and the "$$" sign-off.
    if "Tropical Weather Outlook" in text:
        text = text[text.index("Tropical Weather Outlook"):]
    text = text.split("$$")[0].strip()
    formation_expected = "formation is not expected" not in text.lower()
    return text, formation_expected


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    try:
        resp = await client.get(url)
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        raise WeatherFetchError(f"{url}: {exc}") from exc
    if resp.status_code != 200:
        raise WeatherFetchError(f"{url}: HTTP {resp.status_code}")
    return resp


async def fetch_tropical_outlook(
    route: Route,
    *,
    corridor_nm: float = 200.0,
    lookahead_h: int = 96,
    dr_step_h: int = 12,
    timeout_s: float = 20.0,
    client: httpx.AsyncClient | None = None,
) -> TropicalOutlook:
    own = client is None
    http = client or httpx.AsyncClient(timeout=timeout_s)
    try:
        storms_resp, two_resp = await asyncio.gather(
            _get(http, _CURRENT_STORMS_URL), _get(http, _TWO_ATLANTIC_URL)
        )
    finally:
        if own:
            await http.aclose()

    try:
        payload = storms_resp.json()
    except ValueError as exc:
        raise WeatherParseError("CurrentStorms.json: not JSON") from exc
    active = payload.get("activeStorms") if isinstance(payload, dict) else None
    if not isinstance(active, list):
        active = []

    # A system more than this far from the route (now or dead-reckoned) is
    # irrelevant to this passage and just clutters the briefing.
    relevance_nm = max(corridor_nm * 3.0, 600.0)
    systems: list[TropicalSystem] = []
    for raw in active:
        if isinstance(raw, dict):
            s = _screen_system(raw, route, corridor_nm, lookahead_h, dr_step_h)
            if s is not None and s.closest_nm <= relevance_nm:
                systems.append(s)
    systems.sort(key=lambda s: s.closest_nm)

    two_text, formation_expected = _parse_two(two_resp.text)

    return TropicalOutlook(
        issued=datetime.now(UTC),
        systems=tuple(systems),
        atlantic_two_text=two_text,
        formation_expected=formation_expected,
        corridor_nm=corridor_nm,
        lookahead_h=lookahead_h,
    )


def fetch_tropical_outlook_sync(route: Route, **kwargs: object) -> TropicalOutlook:
    return asyncio.run(fetch_tropical_outlook(route, **kwargs))  # type: ignore[arg-type]


def render_tropical_markdown(outlook: TropicalOutlook) -> str:
    lines: list[str] = []
    if outlook.clear:
        lines += [
            "> **No tropical threat to the route.** No active system within "
            f"{outlook.corridor_nm:.0f} NM (now or dead-reckoned {outlook.lookahead_h} h "
            "out). Atlantic Tropical Weather Outlook: no formation expected.",
            "",
        ]
    elif outlook.any_in_corridor:
        lines += ["> ⚠️ **TROPICAL SYSTEM NEAR THE ROUTE — do not treat any window "
                  "as clear.**", ""]
    else:
        lines += ["> **Active systems present but clear of the corridor.** Recheck "
                  "before departure.", ""]

    for s in outlook.systems:
        intensity = f", {s.intensity_kn:.0f} kn" if s.intensity_kn is not None else ""
        motion = (
            f"{s.movement_speed_kn:.0f} kn toward {s.movement_dir_deg:.0f}°"
            if s.movement_dir_deg is not None and s.movement_speed_kn is not None
            else "motion n/a"
        )
        when = "now" if s.closest_at_h == 0 else f"~{s.closest_at_h} h out (dead-reckoned)"
        mark = "⚠️ " if s.in_corridor else ""
        lines.append(
            f"- {mark}**{s.name}** ({s.label}{intensity}) at {s.lat:.1f}, {s.lon:.1f}, "
            f"moving {motion}. Closest approach to the route: **{s.closest_nm:.0f} NM** "
            f"{when}."
            + (f"  [advisory]({s.advisory_url})" if s.advisory_url else "")
        )

    lines.append("")
    lines.append("**Atlantic Tropical Weather Outlook (NHC):**")
    lines.append("")
    for para in outlook.atlantic_two_text.split("\n"):
        if para.strip():
            lines.append(f"> {para.strip()}")
    lines.append("")
    lines.append(
        "*Track/cone is dead-reckoned from current motion — confirm the official "
        "forecast cone at nhc.noaa.gov before committing.*"
    )
    return "\n".join(lines)
