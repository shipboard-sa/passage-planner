"""U.S. Department of State travel advisory and embassy details, per country.

Purpose: Look up each destination country's current State Department travel
    advisory (level, date, one-line reason, link) and the U.S. embassy contact
    block, and render them as a briefing block, so nobody has to research them.
Version: v2 (2026-09-22) - trimmed rendered text to terse facts, no commentary.
Scope: PRODUCTION - feeds the arrival dossier (briefing section 6) and the
    warnings banner of passage-planner.
Architecture: passage-planner support module, same shape as notices.py: keyless
    public JSON, cached, degrades to an explicit PENDING note. Called by
    service.run_brief() (once per briefing, for the destination countries) and
    rendered by briefing.py. No research run and no LLM: the State Department
    publishes both feeds as structured JSON at cadataapi.state.gov
    (``/api/TravelAdvisories`` and ``/api/CountryTravelInformation``, roughly
    6 MB for the second), so the feeds are reduced once a day into a small
    cache file under var/cache/state_dept/. The endpoint sometimes answers from an
    older edition, so each feed is fetched several times and the newest record per
    country wins (see ADVISORY_ATTEMPTS).

Matching is by country NAME: the two feeds spell some names differently from
each other ("The Bahamas" vs "Bahamas", "AntiguaandBarbuda") and from ours
("St Lucia" vs "Saint Lucia"), so every name is reduced to a lower-case
alphanumeric key first. Puerto Rico, the US Virgin Islands and the United States
have no State Department advisory (they are U.S. soil) and say so.

The advisory is the U.S. government's view for U.S. citizens; other nationalities
aboard should check their own government's advice. It changes without notice, so
the block says to re-check before departure.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Any, Literal

import httpx

from .errors import ConsularFetchError, ConsularParseError

ADVISORIES_URL = "https://cadataapi.state.gov/api/TravelAdvisories"
COUNTRY_INFO_URL = "https://cadataapi.state.gov/api/CountryTravelInformation"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "var" / "cache" / "state_dept"
CACHE_TTL = timedelta(hours=24)
# The endpoint sits behind several servers and now and then answers from an OLDER
# edition (observed 2026-09-21: 2 of 3 calls returned 229 records, 1 returned an
# older 218-record edition with 2025 dates and old-style links). So each feed is
# fetched a few times and, per country, the most recently dated record wins.
ADVISORY_ATTEMPTS = 3
INFO_ATTEMPTS = 2
# The endpoint is behind Cloudflare, which answers 429 to a burst (seen while
# testing). A briefing makes about five requests a day, so this only matters as
# politeness: pause between requests, and back off and retry on 429 / 5xx.
_PAUSE_S = 0.5
_RETRIES = 2
_BACKOFF_S = 2.0
_HEADERS = {"User-Agent": "passage-planner/1.0 (private cruising planner; contact via repo)"}
STEP_URL = "https://step.state.gov"

_US_SOIL = {"unitedstates", "usa", "us", "puertorico", "pr", "usvirginislands", "usvi",
            "unitedstatesvirginislands", "virginislandsus"}

# Our spelling -> the State Department's, after key reduction. Only where they differ.
_ALIASES = {
    "stlucia": "saintlucia",
    "stkittsandnevis": "saintkittsandnevis",
    "stvincentandthegrenadines": "saintvincentandthegrenadines",
    "stvincent": "saintvincentandthegrenadines",
    "saintbarthelemy": "saintbarthelemy",
    "stbarts": "saintbarthelemy",
    "turksandcaicos": "turksandcaicosislands",
    "tci": "turksandcaicosislands",
    "bvi": "britishvirginislands",
    "dr": "dominicanrepublic",
    "saintmartin": "sintmaarten",  # one island; State files it under Sint Maarten
    "trinidad": "trinidadandtobago",
}

_LEVEL_RE = re.compile(r"Level\s+(\d)")
_LABELS = ("Embassy Name", "Street Address", "Phone", "Emergency Phone", "Fax", "Email", "Web")


def country_key(name: str) -> str:
    """Reduce a country name to a comparable key: accents folded, lower case,
    letters and digits only, leading "the" dropped, "&" read as "and"."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", "and")
    s = re.sub(r"^\s*the\s+", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return _ALIASES.get(s, s)


def _text(html: str) -> str:
    """HTML to plain text. Inline formatting tags vanish without a trace (so
    ``<b>crime</b>.`` stays ``crime.``); block tags become line breaks; any other
    tag becomes a space. Entities are decoded and all whitespace, including the
    non-breaking and narrow spaces the feed uses, is collapsed."""
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>|</ul>", "\n", html)
    s = re.sub(r"(?i)</?(?:b|i|em|strong|a|span|u|font|sup|sub)(?:\s[^>]*)?>", "", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


_LEVEL_PHRASE_RE = re.compile(
    r"^(exercise normal precautions|exercise increased caution|reconsider travel|do not travel)",
    re.IGNORECASE,
)


def _headline(summary_text: str, level_text: str) -> str:
    """The advisory's own reason line: the first line that opens with the level
    phrase (some records start with an update note instead), cut before the
    "Read the entire Travel Advisory" pointer."""
    for line in summary_text.splitlines():
        if _LEVEL_PHRASE_RE.match(line):
            return re.split(r"\s+Read the entire", line)[0].strip()[:300]
    return level_text


@dataclass(frozen=True, slots=True)
class Advisory:
    country: str
    level: int
    level_text: str  # e.g. "Exercise Increased Caution"
    published: str  # ISO date
    link: str
    headline: str  # the advisory's own one-line reason


@dataclass(frozen=True, slots=True)
class Embassy:
    name: str
    address: str
    phone: str
    emergency_phone: str
    email: str
    web: str


@dataclass(frozen=True, slots=True)
class CountryConsular:
    requested: str
    status: Literal["found", "us_soil", "not_found"]
    name: str = ""
    advisory: Advisory | None = None
    embassy: Embassy | None = None
    info_updated: str = ""


@dataclass(frozen=True, slots=True)
class ConsularReport:
    countries: tuple[CountryConsular, ...]
    fetched: datetime
    stale: bool = False  # the feeds were unreachable; an older cache was used
    unreachable: bool = False  # nothing could be fetched and there was no cache

    @property
    def high_risk(self) -> list[CountryConsular]:
        return [c for c in self.countries if c.advisory is not None and c.advisory.level >= 3]


# --------------------------------------------------------------------------
# Parsing the two feeds (pure)
# --------------------------------------------------------------------------


def parse_advisories(payload: Any) -> dict[str, Advisory]:
    """Advisories keyed by ``country_key``. The feed lists some countries twice; the
    most recently published wins."""
    if not isinstance(payload, list):
        raise ConsularParseError("TravelAdvisories: expected a list")
    out: dict[str, Advisory] = {}
    for rec in payload:
        if not isinstance(rec, dict):
            continue
        title = str(rec.get("Title", ""))
        m = _LEVEL_RE.search(title)
        if not m:
            continue
        country = re.split(r"\s+-\s+Level", title)[0]
        country = re.sub(r"\s+Travel Advisory$", "", country).strip()
        level_text = title.split(":", 1)[1].strip() if ":" in title else ""
        headline = _headline(_text(str(rec.get("Summary", ""))), level_text)
        adv = Advisory(
            country=country,
            level=int(m.group(1)),
            level_text=level_text,
            published=str(rec.get("Published") or rec.get("Updated") or "")[:10],
            link=str(rec.get("Link", "")),
            headline=headline,
        )
        key = country_key(country)
        if key not in out or adv.published > out[key].published:
            out[key] = adv
    return out


def _embassy(html: str) -> Embassy | None:
    text = _text(html)
    if not text:
        return None
    pattern = "|".join(re.escape(label) for label in _LABELS)
    parts = re.split(rf"(?:^|\n)\s*({pattern}):", text)
    fields: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        fields[parts[i]] = " ".join(parts[i + 1].split("\n")).strip()
    if not fields.get("Embassy Name"):
        return None
    # The street address spans lines; re-read it with the line breaks kept as commas.
    addr_match = re.search(r"Street Address:(.*?)(?:\nPhone:|\nEmergency Phone:|$)", text, re.S)
    address = (
        ", ".join(ln.strip() for ln in addr_match.group(1).splitlines() if ln.strip())
        if addr_match else fields.get("Street Address", "")
    )
    return Embassy(
        name=fields.get("Embassy Name", ""),
        address=address,
        phone=fields.get("Phone", ""),
        emergency_phone=fields.get("Emergency Phone", ""),
        email=fields.get("Email", ""),
        web=fields.get("Web", ""),
    )


def parse_country_info(payload: Any) -> dict[str, dict[str, Any]]:
    """The embassy block and last-updated date per country, keyed by ``country_key``.
    Only those two fields are kept (the feed is ~6 MB, mostly long prose)."""
    if not isinstance(payload, list):
        raise ConsularParseError("CountryTravelInformation: expected a list")
    out: dict[str, dict[str, Any]] = {}
    for rec in payload:
        if not isinstance(rec, dict):
            continue
        area = str(rec.get("geopoliticalarea", "")).strip()
        if not area:
            continue
        emb = _embassy(str(rec.get("travel_embassyAndConsulate", "")))
        updated = re.sub(r"^\s*Last Updated:\s*", "", str(rec.get("last_update_date", ""))).strip()
        out[country_key(area)] = {
            "name": area,
            "embassy": None if emb is None else asdict(emb),
            "updated": updated,
        }
    return out


def lookup(
    requested: list[str],
    advisories: dict[str, Advisory],
    info: dict[str, dict[str, Any]],
) -> tuple[CountryConsular, ...]:
    """Match each requested country to its advisory and embassy block."""
    out: list[CountryConsular] = []
    for raw in requested:
        key = country_key(raw)
        if key in _US_SOIL:
            out.append(CountryConsular(requested=raw, status="us_soil", name=raw.strip()))
            continue
        adv = advisories.get(key)
        rec = info.get(key)
        if adv is None and rec is None:
            out.append(CountryConsular(requested=raw, status="not_found", name=raw.strip()))
            continue
        emb = None
        if rec and rec.get("embassy"):
            e = rec["embassy"]
            emb = Embassy(e["name"], e["address"], e["phone"], e["emergency_phone"],
                          e["email"], e["web"])
        out.append(CountryConsular(
            requested=raw,
            status="found",
            name=(adv.country if adv else str(rec["name"] if rec else raw)),
            advisory=adv,
            embassy=emb,
            info_updated=str(rec["updated"]) if rec else "",
        ))
    return tuple(out)


# --------------------------------------------------------------------------
# Fetching and caching the feeds
# --------------------------------------------------------------------------


def _info_date(rec: dict[str, Any]) -> str:
    """A country-information record's date as sortable ISO text ("" if unparseable)."""
    try:
        return datetime.strptime(str(rec.get("updated", "")), "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


def merge_advisories(maps: list[dict[str, Advisory]]) -> dict[str, Advisory]:
    """Per country, the advisory with the newest publication date across ``maps``."""
    out: dict[str, Advisory] = {}
    for m in maps:
        for key, adv in m.items():
            if key not in out or adv.published > out[key].published:
                out[key] = adv
    return out


def merge_info(maps: list[dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Per country, the information record with the newest last-updated date."""
    out: dict[str, dict[str, Any]] = {}
    for m in maps:
        for key, rec in m.items():
            if key not in out or _info_date(rec) > _info_date(out[key]):
                out[key] = rec
    return out


def _fetch_all(
    client: httpx.Client,
    url: str,
    attempts: int,
    parse: Callable[[Any], Any],
    sleep: Callable[[float], None] = time.sleep,
) -> list[Any]:
    """Fetch and parse ``url`` up to ``attempts`` times; the successful parses.
    Raises the last error only if every attempt failed."""
    ok: list[Any] = []
    last: Exception | None = None
    for i in range(attempts):
        if i:
            sleep(_PAUSE_S)
        try:
            ok.append(parse(_get_json(client, url, sleep)))
        except (ConsularFetchError, ConsularParseError) as exc:
            last = exc
    if not ok:
        raise last or ConsularFetchError(f"{url}: no attempts made")
    return ok


def _reduce(client: httpx.Client, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    advisories = merge_advisories(
        _fetch_all(client, ADVISORIES_URL, ADVISORY_ATTEMPTS, parse_advisories, sleep)
    )
    sleep(_PAUSE_S)
    info = merge_info(
        _fetch_all(client, COUNTRY_INFO_URL, INFO_ATTEMPTS, parse_country_info, sleep)
    )
    return {
        "advisories": {
            k: {"country": a.country, "level": a.level, "level_text": a.level_text,
                "published": a.published, "link": a.link, "headline": a.headline}
            for k, a in advisories.items()
        },
        "info": info,
    }


def _from_cache(data: dict[str, Any]) -> tuple[dict[str, Advisory], dict[str, dict[str, Any]]]:
    advisories = {k: Advisory(**v) for k, v in data["advisories"].items()}
    return advisories, data["info"]


def _get_json(client: httpx.Client, url: str, sleep: Callable[[float], None] = time.sleep) -> Any:
    """GET ``url`` as JSON, retrying a few times on 429 and 5xx with a growing pause."""
    last = "no attempt made"
    for attempt in range(_RETRIES + 1):
        if attempt:
            sleep(_BACKOFF_S * attempt)
        try:
            resp = client.get(url)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last = str(exc)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            last = f"HTTP {resp.status_code}"
            continue
        if resp.status_code != 200:
            raise ConsularFetchError(f"{url}: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ConsularParseError(f"{url}: not JSON") from exc
    raise ConsularFetchError(f"{url}: {last}")


def load_feeds(
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    now: Callable[[], datetime] | None = None,
    client: httpx.Client | None = None,
    ttl: timedelta = CACHE_TTL,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Advisory], dict[str, dict[str, Any]], datetime, bool]:
    """The reduced feeds, from the cache when it is fresh, else fetched and cached.
    Returns (advisories, info, fetched_at, stale). ``stale`` means the network
    failed and an older cache was used. Raises :class:`ConsularFetchError` when
    nothing can be had (no network and no cache)."""
    clock = now or (lambda: datetime.now(UTC))
    cache_file = cache_dir / "state_dept.json"
    cached: dict[str, Any] | None = None
    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(cached["fetched"])
    except (OSError, ValueError, KeyError):
        cached = None
        fetched = clock()
    if cached is not None and clock() - fetched < ttl:
        adv, info = _from_cache(cached)
        return adv, info, fetched, False

    own = client is None
    http = client or httpx.Client(timeout=30.0, follow_redirects=True, headers=_HEADERS)
    try:
        reduced = _reduce(http, sleep)
    except (ConsularFetchError, ConsularParseError):
        if cached is not None:
            adv, info = _from_cache(cached)
            return adv, info, fetched, True
        raise
    finally:
        if own:
            http.close()
    stamp = clock()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({"fetched": stamp.isoformat(), **reduced}), encoding="utf-8"
        )
    except OSError:
        pass  # a read-only disk must not lose the lookup itself
    adv, info = _from_cache(reduced)
    return adv, info, stamp, False


def build_consular_report(
    countries: list[str],
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    now: Callable[[], datetime] | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ConsularReport:
    """The State Department block data for ``countries`` (names as typed or
    picked). An unreachable service with no cache gives ``unreachable=True``
    rather than raising, so a briefing is never blocked by it."""
    clock = now or (lambda: datetime.now(UTC))
    try:
        adv, info, fetched, stale = load_feeds(
            cache_dir=cache_dir, now=clock, client=client, sleep=sleep
        )
    except (ConsularFetchError, ConsularParseError):
        return ConsularReport(
            countries=tuple(CountryConsular(requested=c, status="not_found", name=c)
                            for c in countries),
            fetched=clock(), unreachable=True,
        )
    return ConsularReport(
        countries=lookup(countries, adv, info), fetched=fetched, stale=stale
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def advisory_warnings(report: ConsularReport) -> list[str]:
    """One line per country at Level 3 or 4, for the briefing's warnings banner."""
    return [
        f"U.S. State Department advisory for {c.name}: Level {c.advisory.level} "
        f"({c.advisory.level_text}), issued {c.advisory.published}"
        for c in report.high_risk
        if c.advisory is not None
    ]


def _embassy_line(e: Embassy) -> str:
    bits = [e.name.strip()]
    if e.address:
        bits.append(e.address)
    if e.phone:
        bits.append(f"phone {e.phone}")
    if e.emergency_phone and e.emergency_phone != e.phone:
        bits.append(f"emergency {e.emergency_phone}")
    if e.email:
        bits.append(e.email)
    if e.web:
        bits.append(e.web)
    return "; ".join(bits)


def render_consular_markdown(report: ConsularReport) -> str:
    """The arrival-dossier block. Explicit about anything it could not find."""
    head = "### U.S. Department of State: travel advisory and embassy"
    if report.unreachable:
        return (
            f"{head}\n\n> **PENDING** — the State Department feeds (cadataapi.state.gov) were "
            "unreachable and no earlier copy is cached. Check travel.state.gov for each "
            "destination's advisory and embassy before departure."
        )
    note = (
        f"*From travel.state.gov, as of {report.fetched:%Y-%m-%d}"
        + (" (feeds unreachable; older copy)" if report.stale else "")
        + f". Advisories change without notice — re-check before departure. Enrol U.S. "
        f"crew in STEP ({STEP_URL}). Other nationalities: own government's advice and "
        "consulate.*"
    )
    lines = [head, "", note, ""]
    for c in report.countries:
        if c.status == "us_soil":
            lines += [
                f"**{c.name}:** U.S. territory — no State Department advisory/embassy. "
                "Arrival handled by U.S. Customs and Border Protection.",
                "",
            ]
            continue
        if c.status == "not_found" or (c.advisory is None and c.embassy is None):
            lines += [
                f"**{c.name}:** no State Department record for this name. Check "
                "travel.state.gov (Travel Advisories) and the local U.S. embassy or consulate.",
                "",
            ]
            continue
        if c.advisory is not None:
            a = c.advisory
            flag = "⚠️ " if a.level >= 3 else ""
            lines.append(f"**{flag}{c.name}: Level {a.level} — {a.level_text}** "
                         f"(issued {a.published})")
            if a.headline:
                lines.append(f"- Why: {a.headline}")
            lines.append(f"- Full advisory: {a.link}")
        else:
            lines.append(f"**{c.name}:** no current advisory in the feed.")
        if c.embassy is not None:
            lines.append(f"- U.S. embassy: {_embassy_line(c.embassy)}")
            lines.append("- Consulates and other offices: listed on the embassy's website.")
        else:
            lines.append(
                "- U.S. embassy: none listed — covered by the embassy of its sovereign "
                "country. See travel.state.gov."
            )
        if c.info_updated:
            lines.append(f"- Country information last updated by State: {c.info_updated}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
