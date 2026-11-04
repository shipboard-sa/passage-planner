"""Navigational warnings (NTM) for the route corridor, from NGA.

Purpose: Fetch NGA broadcast NAVAREA IV/HYDROLANT warnings, filter to the
    route corridor, and render them for briefing section 5d.
Version: v2 (2026-09-22) - trimmed rendered text to terse facts, no
    commentary.
Scope: PRODUCTION - feeds briefing.py section 5d of passage-planner.
Architecture: passage-planner support module. render_notices_markdown() is
    called by briefing.py's _notices_block(); no auth, keyless NGA JSON feed.

The NGA Maritime Safety Information broadcast-warning service publishes every
in-force NAVAREA IV and HYDROLANT warning as keyless JSON. NAVAREA IV is the
US-coordinated western North Atlantic area — the Gulf of Mexico, the Florida
coast, the Bahamas and the Caribbean — so one call covers the whole 2026
cruising ground.

The service gives no structured coordinates: positions live in the free-text
``text`` field as ``DD-MM.mmN DDD-MM.mmW`` groups. This module pulls those out,
keeps a warning only when one of its positions is within ``corridor_nm`` of the
route, and renders the survivors newest-hazard-first.

This is the *broadcast* warning layer (offshore obstructions, lights out,
firing exercises, drilling units). Inshore aid-to-navigation and bridge detail
is the USCG Local Notice to Mariners, a separate district-level feed — a
planned follow-up, not covered here.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .errors import NoticeFetchError, NoticeParseError
from .geo import great_circle_nm
from .route import Route

_BROADCAST_WARN_URL = "https://msi.nga.mil/api/publications/broadcast-warn"

# The USCG Local Notice to Mariners is the inshore layer (aids to navigation off
# station, bridge status, dredging, waterway obstructions). It is published only
# as a weekly per-district PDF — no queryable feed — so the briefing points at it
# rather than parsing it. For the Gulf / Florida / Caribbean cruising ground the
# relevant USCG districts are Heartland (Gulf Coast section) and Southeast.
_LNM_POINTER = (
    "*Also check the **USCG Local Notice to Mariners** — inshore aids, bridges, "
    "dredging — for the districts the route touches (Gulf Coast: Heartland; Florida "
    "and the SE / Caribbean approaches: Southeast). Weekly PDF at "
    "navcen.uscg.gov/local-notices-to-mariners-by-cg-district.*"
)

# NGA area codes -> the label mariners use.
_AREA_LABEL = {"4": "NAVAREA IV", "A": "HYDROLANT", "12": "NAVAREA XII", "P": "NAVAREA XII"}
_DEFAULT_AREAS = ("4", "A")

# DD-MM.mm(H)  DDD-MM.mm(H)  — degrees and decimal minutes, the NGA broadcast form.
_POS_RE = re.compile(
    r"(\d{1,3})-(\d{1,2}(?:\.\d+)?)\s?([NS])[ ,/]+(\d{1,3})-(\d{1,2}(?:\.\d+)?)\s?([EW])"
)


@dataclass(frozen=True, slots=True)
class NoticePosition:
    lat: float
    lon: float


# Lines that only tag the region / chart, not the substance of the warning.
_TAG_LINE_RE = re.compile(r"^(DNC \d+\.?|CHART \d.*|[A-Z][A-Z ,'()/-]{2,32}\.?)$")

# A search-and-rescue broadcast is a live-VHF/DSC matter, not a route-planning
# input — and NGA leaves them "active" long after the case closes. Split them out.
_SAR_RE = re.compile(
    r"\b(DISTRESS|OVERDUE|ADRIFT|DISABLED AND|PERSON(S)? IN WATER|\bPIW\b|EPIRB|406 ?MHZ"
    r"|SHARP LOOKOUT|ASSIST IF POSSIBLE|SEARCH AND RESCUE|MISSING (VESSEL|PERSON)|MAYDAY)\b"
)


def _fmt_pos(p: NoticePosition) -> str:
    ns, ew = ("N" if p.lat >= 0 else "S"), ("E" if p.lon >= 0 else "W")
    return f"{abs(p.lat):.2f}{ns} {abs(p.lon):.2f}{ew}"


@dataclass(frozen=True, slots=True)
class NavWarning:
    area: str                       # "NAVAREA IV", "HYDROLANT"
    number: int
    year: int
    subregion: str
    issued: datetime | None
    authority: str
    text: str
    positions: tuple[NoticePosition, ...]
    closest_nm: float               # nearest charted position to the route
    nearest_position: NoticePosition | None = None

    @property
    def ident(self) -> str:
        return f"{self.area} {self.number}/{self.year % 100:02d}"

    @property
    def is_sar(self) -> bool:
        return bool(_SAR_RE.search(self.text.upper()))

    @property
    def headline(self) -> str:
        """The substantive line(s), past the region / chart-reference tags."""
        lines = [ln.strip() for ln in self.text.splitlines() if ln.strip()]
        i = 0
        while i < len(lines) and _TAG_LINE_RE.fullmatch(lines[i]):
            i += 1
        body = lines[i:] or lines
        out: list[str] = []
        for ln in body:
            if re.fullmatch(r"\d+\.", ln) or ln.upper().startswith("CANCEL "):
                if out:
                    break
                continue
            out.append(ln)
            if sum(len(p) for p in out) >= 200 or len(out) >= 3:
                break
        head = re.sub(r"^\d+\.\s*", "", " ".join(out)).strip()
        # A colon usually separates the gist from an itemised list — keep the gist.
        gist = head.split(":", 1)[0]
        return gist if len(gist) >= 40 else head


@dataclass(frozen=True, slots=True)
class NoticeReport:
    route_name: str
    generated: datetime
    corridor_nm: float
    areas_checked: tuple[str, ...]
    warnings: tuple[NavWarning, ...]     # every in-corridor warning, nearest first
    total_in_force: int                  # across the checked areas, before filtering
    unreachable: bool = False

    @property
    def hazards(self) -> tuple[NavWarning, ...]:
        """Navigational warnings — obstructions, lights, firing areas, chart fixes."""
        return tuple(w for w in self.warnings if not w.is_sar)

    @property
    def sar(self) -> tuple[NavWarning, ...]:
        """Search-and-rescue broadcasts in the corridor (info only for planning)."""
        return tuple(w for w in self.warnings if w.is_sar)

    @property
    def any(self) -> bool:
        return bool(self.hazards)


# ---------------------------------------------------------------------------


def parse_positions(text: str) -> tuple[NoticePosition, ...]:
    """Every ``DD-MM.mmN DDD-MM.mmW`` position embedded in a warning's text."""
    out: list[NoticePosition] = []
    for d_lat, m_lat, hemi_ns, d_lon, m_lon, hemi_ew in _POS_RE.findall(text):
        lat = int(d_lat) + float(m_lat) / 60.0
        lon = int(d_lon) + float(m_lon) / 60.0
        if hemi_ns == "S":
            lat = -lat
        if hemi_ew == "W":
            lon = -lon
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            out.append(NoticePosition(round(lat, 4), round(lon, 4)))
    return tuple(out)


def parse_issue_date(raw: object) -> datetime | None:
    """``081653Z MAY 2024`` -> aware UTC datetime, or None if unparseable."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.strptime(raw.strip(), "%d%H%MZ %b %Y")
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC)


def _route_min_distance(route: Route, lat: float, lon: float) -> float:
    return min(
        great_circle_nm(lat, lon, s_lat, s_lon)
        for _, s_lat, s_lon in route.sample_points(20.0)
    )


def _warning(raw: dict[str, object]) -> NavWarning | None:
    text = str(raw.get("text", "")).strip()
    if not text:
        return None
    area_code = str(raw.get("navArea", ""))
    try:
        number = int(raw.get("msgNumber", raw.get("number", 0)))  # type: ignore[call-overload]
        year = int(raw.get("msgYear", raw.get("year", 0)))        # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return NavWarning(
        area=_AREA_LABEL.get(area_code, area_code or "?"),
        number=number,
        year=year,
        subregion=str(raw.get("subregion", "")),
        issued=parse_issue_date(raw.get("issueDate")),
        authority=str(raw.get("authority", "")).strip(),
        text=text,
        positions=parse_positions(text),
        closest_nm=float("inf"),
    )


def screen_warnings(
    raws: list[dict[str, object]], route: Route, corridor_nm: float
) -> list[NavWarning]:
    """In-force warnings with a charted position inside the corridor, nearest first."""
    kept: list[NavWarning] = []
    for raw in raws:
        w = _warning(raw)
        if w is None or not w.positions:
            continue
        nearest, closest = min(
            ((p, _route_min_distance(route, p.lat, p.lon)) for p in w.positions),
            key=lambda pd: pd[1],
        )
        if closest <= corridor_nm:
            kept.append(
                NavWarning(
                    w.area, w.number, w.year, w.subregion, w.issued,
                    w.authority, w.text, w.positions, round(closest, 1), nearest,
                )
            )

    _epoch0 = datetime(1970, 1, 1, tzinfo=UTC)

    def _key(w: NavWarning) -> tuple[float, float]:
        return (w.closest_nm, -((w.issued or _epoch0) - _epoch0).total_seconds())

    kept.sort(key=_key)
    return kept


async def _get(client: httpx.AsyncClient, params: dict[str, str]) -> list[dict[str, object]]:
    try:
        resp = await client.get(_BROADCAST_WARN_URL, params=params)
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        raise NoticeFetchError(f"NGA broadcast-warn: {exc}") from exc
    if resp.status_code != 200:
        raise NoticeFetchError(f"NGA broadcast-warn: HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise NoticeParseError("NGA broadcast-warn: not JSON") from exc
    items = payload.get("broadcast-warn") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise NoticeParseError("NGA broadcast-warn: unexpected shape")
    return [w for w in items if isinstance(w, dict)]


async def fetch_nav_warnings(
    route: Route,
    *,
    corridor_nm: float = 75.0,
    areas: tuple[str, ...] = _DEFAULT_AREAS,
    max_warnings: int = 25,
    timeout_s: float = 20.0,
    client: httpx.AsyncClient | None = None,
) -> NoticeReport:
    own = client is None
    http = client or httpx.AsyncClient(timeout=timeout_s)
    try:
        resp = await _get(http, {"status": "active", "output": "json"})
    finally:
        if own:
            await http.aclose()

    in_area = [w for w in resp if str(w.get("navArea", "")) in areas]
    kept = screen_warnings(in_area, route, corridor_nm)
    return NoticeReport(
        route_name=route.name,
        generated=datetime.now(UTC),
        corridor_nm=corridor_nm,
        areas_checked=tuple(_AREA_LABEL.get(a, a) for a in areas),
        warnings=tuple(kept[:max_warnings]),
        total_in_force=len(in_area),
    )


def fetch_nav_warnings_sync(route: Route, **kwargs: object) -> NoticeReport:
    return asyncio.run(fetch_nav_warnings(route, **kwargs))  # type: ignore[arg-type]


def render_notices_markdown(report: NoticeReport) -> str:
    areas = ", ".join(report.areas_checked)
    if report.unreachable:
        return (
            f"> **PENDING** — NGA Maritime Safety Information ({areas}) was unreachable. "
            "Check active broadcast warnings at msi.nga.mil before departure."
        )
    hazards, sar = report.hazards, report.sar
    lines: list[str] = []
    if not hazards:
        lines += [
            f"> **No navigational warnings on the route.** {report.total_in_force} warnings "
            f"in force across {areas}; none with a charted position within "
            f"{report.corridor_nm:.0f} NM of the track.",
            "",
        ]
    else:
        noun = "warning" if len(hazards) == 1 else "warnings"
        lines += [
            f"> ⚠️ **{len(hazards)} navigational {noun} within "
            f"{report.corridor_nm:.0f} NM of the route** (of {report.total_in_force} in force "
            f"across {areas}). Positions given are the nearest charted point.",
            "",
        ]
    for w in hazards:
        issued = f"{w.issued:%Y-%m-%d}" if w.issued else "date n/a"
        where = f" near {_fmt_pos(w.nearest_position)}" if w.nearest_position else ""
        lines.append(
            f"- **{w.ident}** · {w.closest_nm:.0f} NM off track{where} · issued {issued}  \n"
            f"  {w.headline}"
        )
    if sar:
        idents = ", ".join(w.ident for w in sar)
        lines += [
            "",
            f"*{len(sar)} search-and-rescue broadcast(s) in the corridor ({idents}). NGA "
            "leaves these active long after the case closes. Monitor 16/DSC underway; not "
            "for route planning.*",
        ]
    if hazards:
        lines.append("")
        lines.append(
            "*Full text and warnings outside the corridor: msi.nga.mil (Broadcast Warnings).*"
        )
    lines += ["", _LNM_POINTER]
    return "\n".join(lines)
