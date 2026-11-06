"""The Caribbean arrival-places pick list: stable identity data, nothing volatile.

Purpose: Load and validate ``data/arrival_places.toml`` (the normal arrival
    harbours and anchorages by country and island) and serve it to the web form's
    picker, so an arrival is chosen from a list instead of typed.
Version: v1 (2026-09-21) - schema, loader, validator, nearest-place lookup.
Scope: PRODUCTION - data layer for the arrival picker in the web GUI (planned
    consumers: /api/arrival-places, the form's stops picker, research context).
Architecture: passage-planner support module. Pure: reads one TOML file, no
    network. The data file is maintained by Claude sessions (researched from
    government and cruising sources, each entry carrying its sources and a
    verified date) and never shown to the crew, who only see the picker. Holds
    identity only (name, island, country, position, clearance kind). Anything that
    changes (fees, hours, pre-arrival systems, depths, phone numbers) stays
    just-in-time research. ``ciq/`` and ``logistics.py`` are separate and unchanged.

Entry shape (``[[place]]`` tables)::

    id        "gd-tyrell-bay"    <iso2 lower>-<slug>, unique; the prefix must be
                                 a country in COUNTRIES and match ``country``
    name      "Tyrell Bay"
    island    "Carriacou"        island, or the coast/region for a mainland place
    country   "Grenada"          canonical name from COUNTRIES
    lat, lon  12.4667, -61.4833  decimal degrees, the anchorage/harbour itself
    kind      "port_of_entry"    port_of_entry | harbour | anchorage
    clearance "..."              one line: what clearing in here involves
    also_known_as  ["Tyrrel Bay"]   optional alternate spellings
    region    "Windward Islands"     optional grouping label
    sources   ["https://..."]    at least one
    verified  "2026-09-21"       ISO date the entry was last checked

``port_of_entry`` means customs and immigration clear a yacht at this place;
``harbour`` is a normal stop where you clear elsewhere; ``anchorage`` is a
named anchorage with no facilities to clear.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .errors import ArrivalPlaceError
from .geo import great_circle_nm

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "arrival_places.toml"

# ISO-3166 alpha-2 (lower case) -> canonical display name. Covers the whole
# planned cruising range, so adding a tranche never needs a code change. Cuba,
# Haiti and Venezuela are deliberately absent (out of scope for now, 2026-09-21).
COUNTRIES: dict[str, str] = {
    "us": "United States",
    "bs": "Bahamas",
    "tc": "Turks and Caicos",
    "do": "Dominican Republic",
    "pr": "Puerto Rico",
    "vi": "US Virgin Islands",
    "vg": "British Virgin Islands",
    "ai": "Anguilla",
    "sx": "Sint Maarten",
    "mf": "Saint-Martin",
    "bl": "Saint-Barthelemy",
    "kn": "St Kitts and Nevis",
    "ag": "Antigua and Barbuda",
    "ms": "Montserrat",
    "gp": "Guadeloupe",
    "dm": "Dominica",
    "mq": "Martinique",
    "lc": "St Lucia",
    "vc": "St Vincent and the Grenadines",
    "bb": "Barbados",
    "gd": "Grenada",
    "tt": "Trinidad and Tobago",
    "cw": "Curacao",
    "aw": "Aruba",
    "bq": "Bonaire",
    "co": "Colombia",
    "pa": "Panama",
    "cr": "Costa Rica",
    "ni": "Nicaragua",
    "hn": "Honduras",
    "bz": "Belize",
    "gt": "Guatemala",
    "mx": "Mexico",
    "jm": "Jamaica",
    "ky": "Cayman Islands",
}

# Coarse (lat_min, lat_max, lon_min, lon_max) box per country, generous enough to hold
# every real place (including outlying islands and, for mainland countries, the
# Caribbean coast) but tight enough to catch a wrong sign, a swapped digit or a place
# filed under the wrong country. Mainland boxes are deliberately loose.
COUNTRY_BOXES: dict[str, tuple[float, float, float, float]] = {
    "us": (24.0, 31.0, -98.0, -79.0),
    "bs": (20.8, 27.4, -79.6, -72.6),
    "tc": (21.1, 22.1, -72.6, -71.0),
    "do": (17.4, 20.1, -72.1, -68.2),
    "pr": (17.8, 18.6, -67.4, -65.0),
    "vi": (17.6, 18.5, -65.2, -64.5),
    "vg": (18.3, 18.8, -64.9, -64.25),
    "ai": (18.1, 18.3, -63.2, -62.9),
    "sx": (18.0, 18.1, -63.2, -63.0),
    "mf": (18.0, 18.15, -63.2, -62.95),
    "bl": (17.85, 17.95, -62.95, -62.75),
    "kn": (17.05, 17.5, -63.0, -62.4),
    "ag": (16.9, 17.8, -62.4, -61.6),
    "ms": (16.6, 16.85, -62.3, -62.1),
    "gp": (15.8, 16.6, -61.9, -60.9),
    "dm": (15.15, 15.7, -61.5, -61.2),
    "mq": (14.3, 14.95, -61.3, -60.8),
    "lc": (13.65, 14.15, -61.15, -60.85),
    "vc": (12.5, 13.4, -61.5, -61.1),
    "bb": (13.0, 13.35, -59.7, -59.4),
    "gd": (11.95, 12.6, -61.85, -61.3),
    "tt": (10.0, 11.4, -61.95, -60.45),
    "cw": (12.0, 12.45, -69.2, -68.7),
    "aw": (12.35, 12.65, -70.1, -69.8),
    "bq": (12.0, 12.35, -68.5, -68.15),
    "co": (7.9, 13.5, -82.0, -71.0),
    "pa": (7.2, 9.7, -83.2, -77.0),
    "cr": (9.5, 11.3, -85.9, -82.5),
    "ni": (10.7, 15.1, -83.9, -82.5),
    "hn": (14.9, 16.6, -88.5, -83.1),
    "bz": (15.85, 18.55, -89.3, -87.3),
    "gt": (15.3, 16.0, -89.4, -88.5),
    "mx": (17.7, 21.7, -88.5, -86.4),
    "jm": (17.7, 18.6, -78.5, -76.1),
    "ky": (19.2, 19.8, -81.5, -79.7),
}

KINDS = ("port_of_entry", "harbour", "anchorage")

_ID_RE = re.compile(r"^[a-z]{2}-[a-z0-9]+(-[a-z0-9]+)*$")
_REQUIRED = ("id", "name", "country", "lat", "lon", "kind", "clearance", "sources", "verified")


@dataclass(frozen=True, slots=True)
class ArrivalPlace:
    id: str
    name: str
    country: str
    island: str
    lat: float
    lon: float
    kind: str
    clearance: str
    sources: tuple[str, ...]
    verified: str
    also_known_as: tuple[str, ...] = ()
    region: str = ""

    @property
    def label(self) -> str:
        """The name shown in the picker and used as the port name in the briefing,
        e.g. ``Tyrell Bay, Carriacou (Grenada)``."""
        where = self.island.strip()
        # The island adds nothing when it repeats the name or the country
        # ("Crocus Bay, Anguilla (Anguilla)").
        skip = not where or where.lower() in (self.name.lower(), self.country.lower())
        head = self.name if skip else f"{self.name}, {where}"
        return f"{head} ({self.country})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "name": self.name,
            "island": self.island,
            "country": self.country,
            "region": self.region,
            "lat": self.lat,
            "lon": self.lon,
            "kind": self.kind,
            "clearance": self.clearance,
            "also_known_as": list(self.also_known_as),
            "verified": self.verified,
        }


def _fail(where: str, msg: str) -> ArrivalPlaceError:
    return ArrivalPlaceError(f"{where}: {msg}")


def _place(entry: dict[str, Any], index: int) -> ArrivalPlace:
    where = f"place #{index + 1} ({entry.get('id', '?')})"
    for key in _REQUIRED:
        if key not in entry or entry[key] in ("", [], None):
            raise _fail(where, f"missing '{key}'")
    pid = str(entry["id"])
    if not _ID_RE.match(pid):
        raise _fail(where, "id must look like 'gd-tyrell-bay' (lower case, iso2 prefix)")
    prefix = pid[:2]
    if prefix not in COUNTRIES:
        raise _fail(where, f"id prefix '{prefix}' is not a known country code")
    if entry["country"] != COUNTRIES[prefix]:
        raise _fail(where, f"country must be '{COUNTRIES[prefix]}' for id prefix '{prefix}'")
    if entry["kind"] not in KINDS:
        raise _fail(where, f"kind must be one of {', '.join(KINDS)}")
    try:
        lat, lon = float(entry["lat"]), float(entry["lon"])
    except (TypeError, ValueError) as exc:
        raise _fail(where, "lat/lon must be numbers") from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise _fail(where, "lat/lon out of range")
    if not (5 <= lat <= 35 and -100 <= lon <= -55):
        raise _fail(where, "position is outside the Caribbean/Gulf box (5-35 N, 100-55 W)")
    lat_min, lat_max, lon_min, lon_max = COUNTRY_BOXES[prefix]
    if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
        raise _fail(
            where,
            f"position {lat}, {lon} is outside the box for {COUNTRIES[prefix]} "
            f"({lat_min}-{lat_max} N, {lon_min}-{lon_max} E): wrong sign, wrong digits, "
            "or filed under the wrong country?",
        )
    sources = entry["sources"]
    if not isinstance(sources, list) or not all(
        isinstance(s, str) and s.startswith("http") for s in sources
    ):
        raise _fail(where, "sources must be a list of http(s) URLs")
    try:
        date.fromisoformat(str(entry["verified"]))
    except ValueError as exc:
        raise _fail(where, "verified must be an ISO date (YYYY-MM-DD)") from exc
    aka = entry.get("also_known_as", [])
    if not isinstance(aka, list) or not all(isinstance(a, str) for a in aka):
        raise _fail(where, "also_known_as must be a list of strings")
    return ArrivalPlace(
        id=pid,
        name=str(entry["name"]).strip(),
        country=str(entry["country"]),
        island=str(entry.get("island", "")).strip(),
        lat=lat,
        lon=lon,
        kind=str(entry["kind"]),
        clearance=str(entry["clearance"]).strip(),
        sources=tuple(sources),
        verified=str(entry["verified"]),
        also_known_as=tuple(aka),
        region=str(entry.get("region", "")).strip(),
    )


def parse_places(text: str, *, source: str = "arrival_places.toml") -> list[ArrivalPlace]:
    """Parse and validate the TOML text. Raises :class:`ArrivalPlaceError` on the
    first problem, naming the entry, and on duplicate ids or duplicate labels."""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ArrivalPlaceError(f"{source}: not valid TOML ({exc})") from exc
    raw = doc.get("place", [])
    if not isinstance(raw, list):
        raise ArrivalPlaceError(f"{source}: 'place' must be an array of tables ([[place]])")
    places = [_place(e, i) for i, e in enumerate(raw)]
    seen_ids: set[str] = set()
    seen_labels: set[str] = set()
    for p in places:
        if p.id in seen_ids:
            raise ArrivalPlaceError(f"{source}: duplicate id '{p.id}'")
        if p.label.lower() in seen_labels:
            raise ArrivalPlaceError(f"{source}: duplicate label '{p.label}' ({p.id})")
        seen_ids.add(p.id)
        seen_labels.add(p.label.lower())
    return places


def load_places(path: Path | None = None) -> list[ArrivalPlace]:
    """The validated pick list, sorted by country, island, name. An absent file is
    an empty list (the picker then offers only 'Other')."""
    p = path or DEFAULT_PATH
    if not p.is_file():
        return []
    places = parse_places(p.read_text(encoding="utf-8"), source=p.name)
    return sorted(places, key=lambda x: (x.country, x.island, x.name))


def _q(value: str) -> str:
    """A TOML basic string (JSON string escapes are valid TOML)."""
    return json.dumps(value, ensure_ascii=False)


def dump_places(places: list[ArrivalPlace], *, header: str = "") -> str:
    """The canonical TOML text for ``places``: sorted by country, island, name, one
    ``[[place]]`` per entry, fixed field order. ``parse_places(dump_places(x))``
    gives ``x`` back."""
    out: list[str] = [ln if ln.startswith("#") else f"# {ln}" for ln in header.splitlines()]
    if out:
        out.append("")
    for p in sorted(places, key=lambda x: (x.country, x.island, x.name)):
        out += [
            "[[place]]",
            f"id = {_q(p.id)}",
            f"name = {_q(p.name)}",
            f"island = {_q(p.island)}",
            f"country = {_q(p.country)}",
        ]
        if p.region:
            out.append(f"region = {_q(p.region)}")
        out += [
            f"lat = {p.lat:g}",
            f"lon = {p.lon:g}",
            f"kind = {_q(p.kind)}",
            f"clearance = {_q(p.clearance)}",
        ]
        if p.also_known_as:
            out.append("also_known_as = [" + ", ".join(_q(a) for a in p.also_known_as) + "]")
        out.append("sources = [" + ", ".join(_q(u) for u in p.sources) + "]")
        out += [f"verified = {_q(p.verified)}", ""]
    return "\n".join(out)


def close_pairs(
    places: list[ArrivalPlace], max_nm: float = 0.3
) -> list[tuple[ArrivalPlace, ArrivalPlace, float]]:
    """Pairs of different places closer than ``max_nm``: usually one place entered
    twice under two names."""
    pairs: list[tuple[ArrivalPlace, ArrivalPlace, float]] = []
    for i, a in enumerate(places):
        for b in places[i + 1 :]:
            d = great_circle_nm(a.lat, a.lon, b.lat, b.lon)
            if d < max_nm:
                pairs.append((a, b, d))
    return pairs


def find(places: list[ArrivalPlace], ident: str) -> ArrivalPlace | None:
    return next((p for p in places if p.id == ident), None)


def nearest(
    places: list[ArrivalPlace], lat: float, lon: float, *, n: int = 5,
    max_nm: float | None = None,
) -> list[tuple[ArrivalPlace, float]]:
    """The ``n`` places closest to a point, with distance in NM, nearest first."""
    ranked = sorted(
        ((p, great_circle_nm(lat, lon, p.lat, p.lon)) for p in places), key=lambda t: t[1]
    )
    if max_nm is not None:
        ranked = [t for t in ranked if t[1] <= max_nm]
    return ranked[:n]


def _main(argv: list[str]) -> int:
    """``python -m passage_planner.arrival FILE ...`` validates files and prints a
    per-country count. ``python -m passage_planner.arrival merge OUT FILE ...``
    validates all the FILEs together (duplicates between files are errors), warns
    about places closer than 0.3 NM, and writes the canonical merged file to OUT."""
    if not argv:
        print("usage: python -m passage_planner.arrival [merge OUT] FILE.toml [FILE.toml ...]")
        return 2
    merging = argv[0] == "merge"
    if merging:
        if len(argv) < 3:
            print("usage: python -m passage_planner.arrival merge OUT FILE.toml [FILE.toml ...]")
            return 2
        out_path, files = Path(argv[1]), argv[2:]
    else:
        out_path, files = None, argv
    combined: list[str] = []
    for name in files:
        path = Path(name)
        try:
            text = path.read_text(encoding="utf-8")
            places = parse_places(text, source=path.name)
        except (OSError, ArrivalPlaceError) as exc:
            print(f"INVALID {name}: {exc}")
            return 1
        combined.append(text)
        counts: dict[str, int] = {}
        for p in places:
            counts[p.country] = counts.get(p.country, 0) + 1
        summary = ", ".join(f"{c} {n}" for c, n in sorted(counts.items()))
        print(f"OK {name}: {len(places)} places ({summary})")
    if out_path is None:
        return 0
    try:
        merged = parse_places("\n\n".join(combined), source="merged")
    except ArrivalPlaceError as exc:
        print(f"INVALID merge: {exc}")
        return 1
    for a, b, d in close_pairs(merged):
        print(f"WARNING {d:.2f} NM apart: {a.id} / {b.id} (one place entered twice?)")
    header = (
        "Purpose: Caribbean arrival-places pick list (identity data only) for the web form's\n"
        "  Arrival & stops picker. Written by Claude sessions, never by the crew.\n"
        "Version: v1 (regenerated by `python -m passage_planner.arrival merge`)\n"
        "Scope: PRODUCTION - read by src/passage_planner/arrival.py,"
        " served at /api/arrival-places.\n"
        "Architecture: see wiki/Arrival-Places.md. Do not hand-edit casually: entries carry\n"
        "  their sources and a verified date; re-run the merge to regenerate this file."
    )
    out_path.write_text(dump_places(merged, header=header), encoding="utf-8")
    print(f"wrote {out_path} ({len(merged)} places)")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
