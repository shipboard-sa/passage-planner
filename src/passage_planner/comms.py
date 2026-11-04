"""Per-passage communications-configuration guidance: which of Starlink,
cellular (T-Mobile roaming), and the Maya eSIM to lean on, and why.

Purpose: Vessel-level Starlink billing-cycle pacing plus a per-country
    Starlink/cellular/Maya registry, rendered as the briefing's Communications
    Configuration subsection (7d).
Version: v3 (2026-09-22) - added save_comms_config(), the atomic-write
    counterpart to load_comms_config(), for the web form's Comms screen
    (backend/main.py's /api/comms). v2: added per-country Calendar check
    (suspend/keep-on recommendation); trimmed registry entries and rendered
    text to terse facts, no commentary; removed the router-failover
    paragraph.
Scope: PRODUCTION - feeds briefing.py section 7d of passage-planner.
Architecture: passage-planner support module. render_comms() is called by
    briefing.py's _comms_block(); reads config/comms.toml via
    load_comms_config(). No network calls - REGISTRY is a static dict,
    re-verified by hand each season.

The three services split into two very different kinds of fact:

- **Starlink** is one subscription for the whole boat and its rules are
  plan-level, not port-level. The vessel is on **Personal Maritime** ($185/mo,
  confirmed 2026-09-10): unlimited high-speed data within 12 NM of any
  coastline worldwide, metered "Ocean Mode" data beyond that, and no
  30-day-outside-home-region cap (the reason it beats Roam for a one-way
  voyage). Those constraints, plus the monthly billing-cycle date, are
  computed once from ``config/comms.toml`` and a reference date — not per
  country.
- **Cellular roaming quality** and **Maya eSIM coverage** really are
  country-level facts, so they live in a small registry alongside the CIQ
  one (see :mod:`passage_planner.ciq`), covering the same countries.

Nothing here is cached per-port — the voyage doesn't revisit ports — but the
country registry IS reused the way the CIQ registry is, since the same
handful of countries/jurisdictions recur across many stops.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .errors import CommsError

# Used when config/comms.toml gives no Ocean Mode rate.
_DEFAULT_OCEAN_MODE_USD_PER_GB = 6.0


def _starlink_reminder(rate_usd_per_gb: float) -> str:
    """The one-line offshore reminder that replaced a long plan-terms paragraph."""
    return (
        f"Offshore reminder: beyond 12 NM Starlink Ocean Mode is metered at "
        f"**${rate_usd_per_gb:.0f}/GB** and stays on until switched off. Batch big "
        "downloads inside the 12 NM line."
    )


_VERIFIED = "2026-09-10"
_VERIFIED_ANTILLES = "2026-09-14"


@dataclass(frozen=True, slots=True)
class CommsConfig:
    """Static, vessel-level comms facts from ``config/comms.toml``."""

    starlink_plan: str = ""
    starlink_billing_cycle_day: int | None = None
    starlink_priority_gb: float | None = None
    starlink_ocean_mode_usd_per_gb: float | None = None
    starlink_notes: str = ""
    tmobile_plan: str = ""
    tmobile_roaming_included: bool = False
    tmobile_notes: str = ""
    maya_esim_scope: str = ""
    maya_esim_notes: str = ""
    router_model: str = ""
    router_notes: str = ""


def load_comms_config(path: str | Path) -> CommsConfig | None:
    """``config/comms.toml``. Returns ``None`` (not an error) if the file is
    simply absent — the section then renders generic, sourced guidance."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CommsError(f"{p}: {exc}") from exc

    sl = raw.get("starlink", {})
    tm = raw.get("t_mobile", {})
    my = raw.get("maya_esim", {})
    rt = raw.get("router", {})

    cycle_day_raw = sl.get("billing_cycle_day")
    cycle_day: int | None = None
    if cycle_day_raw is not None:
        try:
            cycle_day = int(cycle_day_raw)
        except (TypeError, ValueError) as exc:
            raise CommsError(f"{p}: starlink.billing_cycle_day must be an integer") from exc
        if not 1 <= cycle_day <= 28:
            raise CommsError(f"{p}: starlink.billing_cycle_day must be 1-28")

    gb_raw = sl.get("priority_gb")
    priority_gb = float(gb_raw) if gb_raw is not None else None

    ocean_raw = sl.get("ocean_mode_usd_per_gb")
    ocean_mode_usd_per_gb = float(ocean_raw) if ocean_raw is not None else None

    return CommsConfig(
        starlink_plan=str(sl.get("plan", "")),
        starlink_billing_cycle_day=cycle_day,
        starlink_priority_gb=priority_gb,
        starlink_ocean_mode_usd_per_gb=ocean_mode_usd_per_gb,
        starlink_notes=str(sl.get("notes", "")),
        tmobile_plan=str(tm.get("plan", "")),
        tmobile_roaming_included=bool(tm.get("roaming_included", False)),
        tmobile_notes=str(tm.get("notes", "")),
        maya_esim_scope=str(my.get("scope", "")),
        maya_esim_notes=str(my.get("notes", "")),
        router_model=str(rt.get("model", "")),
        router_notes=str(rt.get("notes", "")),
    )


def _toml_str(value: str) -> str:
    """A TOML string literal for ``value``: a multi-line basic string when it
    contains a newline (so a multi-paragraph note keeps its blank lines on
    disk), else a quoted single-line string. See papers/overrides.py's
    identical helper for the full rationale."""
    escaped = value.replace("\\", "\\\\")
    if "\n" in escaped and '"""' not in escaped:
        body = "".join(c if c == "\n" or c >= " " or c == "\t" else " " for c in escaped)
        return '"""\n' + body + '"""'
    single_line = "".join(c if c >= " " or c == "\t" else " " for c in escaped).replace(
        '"', '\\"'
    )
    return '"' + single_line + '"'


def save_comms_config(path: str | Path, config: CommsConfig) -> None:
    """Write ``config/comms.toml`` atomically from ``config``, for the web
    form's Comms screen (backend/main.py's /api/comms) - the counterpart to
    :func:`load_comms_config`. A table with nothing set is omitted entirely
    (matching ``load_comms_config``'s tolerance for a missing table)."""
    lines = [
        "# Vessel-level comms facts - see comms.py's module docstring for how",
        "# these are used. Written by the Comms form; a stray manual edit",
        "# here is still read normally.",
        "",
    ]
    has_starlink = (
        config.starlink_plan or config.starlink_billing_cycle_day is not None
        or config.starlink_notes
    )
    if has_starlink:
        if config.starlink_billing_cycle_day is not None and not (
            1 <= config.starlink_billing_cycle_day <= 28
        ):
            raise CommsError("starlink.billing_cycle_day must be 1-28")
        lines.append("[starlink]")
        if config.starlink_plan:
            lines.append(f"plan = {_toml_str(config.starlink_plan)}")
        if config.starlink_billing_cycle_day is not None:
            lines.append(f"billing_cycle_day = {config.starlink_billing_cycle_day}")
        if config.starlink_priority_gb is not None:
            lines.append(f"priority_gb = {config.starlink_priority_gb:g}")
        if config.starlink_ocean_mode_usd_per_gb is not None:
            lines.append(f"ocean_mode_usd_per_gb = {config.starlink_ocean_mode_usd_per_gb:g}")
        if config.starlink_notes:
            lines.append(f"notes = {_toml_str(config.starlink_notes)}")
        lines.append("")
    if config.tmobile_plan or config.tmobile_roaming_included or config.tmobile_notes:
        lines.append("[t_mobile]")
        if config.tmobile_plan:
            lines.append(f"plan = {_toml_str(config.tmobile_plan)}")
        lines.append(f"roaming_included = {'true' if config.tmobile_roaming_included else 'false'}")
        if config.tmobile_notes:
            lines.append(f"notes = {_toml_str(config.tmobile_notes)}")
        lines.append("")
    if config.maya_esim_scope or config.maya_esim_notes:
        lines.append("[maya_esim]")
        if config.maya_esim_scope:
            lines.append(f"scope = {_toml_str(config.maya_esim_scope)}")
        if config.maya_esim_notes:
            lines.append(f"notes = {_toml_str(config.maya_esim_notes)}")
        lines.append("")
    if config.router_model or config.router_notes:
        lines.append("[router]")
        if config.router_model:
            lines.append(f"model = {_toml_str(config.router_model)}")
        if config.router_notes:
            lines.append(f"notes = {_toml_str(config.router_notes)}")
        lines.append("")

    p = Path(path)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _cycle_status(cycle_day: int, today: date) -> tuple[int, int]:
    """(days since the last reset, days until the next reset) for a plan
    that resets on ``cycle_day`` of every month."""
    this_month_reset = today.replace(day=cycle_day)
    if today >= this_month_reset:
        last_reset = this_month_reset
        next_reset = (
            date(today.year + 1, 1, cycle_day)
            if today.month == 12
            else date(today.year, today.month + 1, cycle_day)
        )
    else:
        last_reset = (
            date(today.year - 1, 12, cycle_day)
            if today.month == 1
            else date(today.year, today.month - 1, cycle_day)
        )
        next_reset = this_month_reset
    return (today - last_reset).days, (next_reset - today).days


@dataclass(frozen=True, slots=True)
class CountryComms:
    key: str
    name: str
    starlink_status: str
    cellular_notes: str
    maya_esim_notes: str
    recommendation: str
    official_urls: tuple[str, ...] = ()
    verified: str = ""
    notes: str = ""


REGISTRY: dict[str, CountryComms] = {
    "usa": CountryComms(
        key="usa",
        name="United States",
        starlink_status="Full service, no restriction — this is the account's home region.",
        cellular_notes="T-Mobile home network — normal LTE/5G coverage along the Gulf/Atlantic "
        "coast; no roaming involved.",
        maya_esim_notes="Not applicable — Maya covers destinations outside the US.",
        recommendation="T-Mobile for everyday data (it's free, in-network); Starlink as the "
        "underway/offshore primary and the weather-data workhorse.",
        official_urls=("https://www.t-mobile.com/coverage",),
        verified=_VERIFIED,
    ),
    "bahamas": CountryComms(
        key="bahamas",
        name="The Bahamas",
        starlink_status="Operational Starlink service in-country — the plan-level 12 NM/Ocean "
        "Mode split above applies here.",
        cellular_notes="T-Mobile roaming is 2G-class (~256 kbps) unless a paid high-speed day "
        "pass is added — fine for messages/email, not for grib files.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle — a solid primary for "
        "phone/tablet data at anchor or in a marina.",
        recommendation="Maya eSIM for phones/day-to-day browsing; Starlink for weather/nav data "
        "and anything bandwidth-heavy; T-Mobile as a free background fallback.",
        official_urls=("https://www.mappr.co/starlink-availability-by-country/",),
        verified=_VERIFIED,
        notes="One of the more built-up cruising grounds — marina wifi is also a realistic "
        "free option at many stops.",
    ),
    "turks and caicos": CountryComms(
        key="turks and caicos",
        name="Turks & Caicos Islands",
        starlink_status="⚠️ Not yet licensed as of the verified date below — regulatory approval "
        "was finalized and service is expected to launch in 2026, but confirm it's actually "
        "live before relying on it here.",
        cellular_notes="T-Mobile roaming is 2G-class (~256 kbps) unless a paid high-speed pass "
        "is added.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Lead with the Maya eSIM here; don't assume Starlink works until you've "
        "confirmed local service has actually gone live.",
        official_urls=(
            "https://www.visittci.com/news/"
            "new-satellite-network-rules-pave-way-for-starlink-in-turks-and-caicos",
        ),
        verified=_VERIFIED,
    ),
    "british virgin islands": CountryComms(
        key="british virgin islands",
        name="British Virgin Islands",
        starlink_status="Operational in-country — the plan-level 12 NM/Ocean Mode split above "
        "applies here.",
        cellular_notes="T-Mobile roaming is 2G-class (~256 kbps) unless a paid high-speed pass "
        "is added; local inter-island cell coverage is otherwise solid.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Maya eSIM day-to-day; Starlink for weather/nav; T-Mobile as a free "
        "background fallback.",
        verified=_VERIFIED,
    ),
    "usvi": CountryComms(
        key="usvi",
        name="US Virgin Islands",
        starlink_status="US territory — within the account's home region; full service.",
        cellular_notes="T-Mobile — US territory, normal domestic-network coverage, no roaming.",
        maya_esim_notes="Not applicable — US territory.",
        recommendation="T-Mobile for everyday data; Starlink for offshore/weather.",
        verified=_VERIFIED,
    ),
    "dominican republic": CountryComms(
        key="dominican republic",
        name="Dominican Republic",
        starlink_status="Operational Starlink service in-country.",
        cellular_notes="T-Mobile roaming is 2G-class (~256 kbps) unless a paid high-speed pass "
        "is added.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Maya eSIM day-to-day (Luperón/Samaná marina wifi is inconsistent); "
        "Starlink for weather/nav and for the Mona Passage crossing itself.",
        verified=_VERIFIED,
    ),
    "puerto rico": CountryComms(
        key="puerto rico",
        name="Puerto Rico (US)",
        starlink_status="US territory — within the account's home region; full service.",
        cellular_notes="T-Mobile — US territory, normal domestic coverage, no roaming.",
        maya_esim_notes="Not applicable — US territory.",
        recommendation="T-Mobile for everyday data; Starlink for offshore/weather.",
        verified=_VERIFIED,
    ),
    # --- Eastern Caribbean / ABC extension (added 2026-09-14) ---
    # the projected path south of Puerto Rico: Leewards -> Windwards -> Grenada -> ABC
    # islands. Researched 2026-09-14 (background pass); see routing/CHECKPOINT.md.
    # Re-verify before actually transiting — several of these are mid-transition
    # (regulatory decisions were actively pending as of the research date).
    "anguilla": CountryComms(
        key="anguilla",
        name="Anguilla",
        starlink_status="⚠️ Not licensed. On Starlink's 2026 roadmap; not live.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Lead with Maya eSIM/T-Mobile until Starlink is confirmed live.",
        official_urls=("https://www.mappr.co/starlink-availability-by-country/",),
        verified=_VERIFIED_ANTILLES,
    ),
    "sint maarten": CountryComms(
        key="sint maarten",
        name="Sint Maarten (Dutch side)",
        starlink_status="⚠️ Unresolved. BTP (regulator) advisory, Aug 2024: not authorized on "
        "the Dutch side. One uncorroborated May 2025 report claims a license was later granted.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Lead with Maya eSIM/T-Mobile; confirm current BTP status before relying "
        "on Starlink.",
        official_urls=("https://btp.sx/btp-clarifies-misleading-statements-in-media-136.html",),
        verified=_VERIFIED_ANTILLES,
        notes="Distinct jurisdiction from French-side Saint Martin, same landmass.",
    ),
    "saint martin": CountryComms(
        key="saint martin",
        name="Saint Martin (French side)",
        starlink_status="Commercially available since 2022 under the same ARCEP (French "
        "national telecom regulator) frequency-authorization framework covering Guadeloupe/"
        "Martinique/Saint Barthélemy.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Starlink is a reasonable primary here; distinct from the Dutch side "
        "(Sint Maarten, above) despite being the same island.",
        official_urls=(
            "https://en.arcep.fr/news/press-releases/view/n/"
            "frequencies-french-overseas-territories-181022.html",
        ),
        verified=_VERIFIED_ANTILLES,
        notes="ARCEP authorization framework confirmed officially; local retailer/commercial "
        "sourcing corroborates live service since 2022.",
    ),
    "saint barthelemy": CountryComms(
        key="saint barthelemy",
        name="Saint Barthélemy",
        starlink_status="Commercially available and legal since October 2022 under the same "
        "ARCEP authorization covering the French Antilles.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Starlink is a reasonable primary here.",
        verified=_VERIFIED_ANTILLES,
        notes="Imported equipment subject to octroi de mer (local import tax) and VAT if buying "
        "hardware locally — not relevant to a vessel arriving with its own dish.",
    ),
    "antigua and barbuda": CountryComms(
        key="antigua and barbuda",
        name="Antigua and Barbuda",
        starlink_status="⚠️ Live, licensing unclear. Starlink advertises service (Aug 2025), but "
        "no formal ABTD license found; tax remittance dispute reported.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Works in practice; keep Maya eSIM/T-Mobile as fallback, re-check before "
        "arrival.",
        official_urls=("https://antiguaobserver.com/govt-in-talks-with-high-speed-internet-starlink/",),
        verified=_VERIFIED_ANTILLES,
    ),
    "guadeloupe": CountryComms(
        key="guadeloupe",
        name="Guadeloupe",
        starlink_status="Fully authorized by ARCEP; live since 2022-09-29 with authorized "
        "local resellers.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Starlink is a reasonable primary here.",
        verified=_VERIFIED_ANTILLES,
    ),
    "dominica": CountryComms(
        key="dominica",
        name="Dominica",
        starlink_status="Officially launched by Starlink in June 2025 (own announcement); "
        "listed as 'Available' on Starlink's coverage map. No licensing dispute found (unlike "
        "Antigua/Grenada), suggesting a normal license process, but no independent Dominican "
        "regulator statement was located confirming the license terms directly.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Starlink is a reasonable primary here.",
        official_urls=("https://www.caribjournal.com/2025/06/06/starlink-dominica-launch/",),
        verified=_VERIFIED_ANTILLES,
        notes="Not to be confused with the Dominican Republic, already in this registry.",
    ),
    "martinique": CountryComms(
        key="martinique",
        name="Martinique",
        starlink_status="Same ARCEP authorization framework as Guadeloupe; live across the "
        "full territory since September 2022.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Starlink is a reasonable primary here.",
        verified=_VERIFIED_ANTILLES,
    ),
    "saint lucia": CountryComms(
        key="saint lucia",
        name="Saint Lucia",
        starlink_status="Best-documented jurisdiction on this route for maritime use "
        "specifically: Saint Lucia issued a formal 'Telecommunications (Aeronautical and "
        "Maritime Mobile Satellite Service) (Exemption) (Starlink Internet Services Limited) "
        "Order,' Statutory Instrument 15/2023, explicitly clearing maritime/aeronautical "
        "mobile satellite use by Starlink.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Starlink Maritime is explicitly cleared here — the strongest legal "
        "footing of any stop on this route.",
        official_urls=("https://www.ntrcslu.lc/telecommunications/",),
        verified=_VERIFIED_ANTILLES,
    ),
    "saint vincent and the grenadines": CountryComms(
        key="saint vincent and the grenadines",
        name="Saint Vincent and the Grenadines",
        starlink_status="Confirmed named operating license: 'Starlink SVG Ltd' appears on the "
        "NTRC's public Licence Register with an effective date of 2025-04-01 (class license + "
        "spectrum authorization + annual fees specified). Government also invested over "
        "$200,000 in Starlink equipment for the Southern Grenadines.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Starlink is confirmed licensed and a reasonable primary here.",
        official_urls=("https://www.ntrc.vc/providers/licence-register/",),
        verified=_VERIFIED_ANTILLES,
    ),
    "grenada": CountryComms(
        key="grenada",
        name="Grenada",
        starlink_status="⚠️ Unlicensed/gray-zone. NTRC lists no Starlink entry; operates as "
        "'over-the-top' service. No official roaming-specific ban found (no ban on record, "
        "despite earlier belief); cruiser-forum reports describe uninterrupted service.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Keep Maya eSIM/T-Mobile as primary; treat Starlink as a bonus, not "
        "guaranteed.",
        official_urls=("https://ntrc.gd/licensing-fees/",),
        verified=_VERIFIED_ANTILLES,
    ),
    "aruba": CountryComms(
        key="aruba",
        name="Aruba",
        starlink_status="⚠️ NOT YET LICENSED (as of 2026-09-14) — on Starlink's own roadmap for "
        "'starting in 2026' but not launched. Aruba's regulator (Directie Telecommunicatie "
        "Zaken, DTZ) has no public application/approval news found, in contrast to Curaçao "
        "where the process is at least publicly underway.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Do not plan around Starlink here until confirmed live; lead with Maya "
        "eSIM/T-Mobile.",
        official_urls=("https://www.mappr.co/starlink-availability-by-country/",),
        verified=_VERIFIED_ANTILLES,
    ),
    "bonaire": CountryComms(
        key="bonaire",
        name="Bonaire",
        starlink_status="✅ Licensed and live. RDI (regulator) granted a 15-year concession "
        "Nov 2025 after an initial Feb 2025 rejection; 350-450 Mbps reported by users.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Confirmed licensed and live — safe to plan around it here.",
        official_urls=(
            "https://english.rijksdienstcn.com/latest/news/2025/february/11/"
            "starlink-application-for-satellite-communication-services-concession-in-"
            "bonaire-sint-eustatius-and-saba-rejected",
        ),
        verified=_VERIFIED_ANTILLES,
        notes="Caribbean Netherlands special municipality, not an independent country.",
    ),
    "curacao": CountryComms(
        key="curacao",
        name="Curaçao",
        starlink_status="⚠️ Not yet licensed. RAC (regulator) has the application; as of Aug "
        "2026 the process is 'in its final phase' but not granted. Use prohibited until then.",
        cellular_notes="Not researched — check T-Mobile roaming tier before arrival.",
        maya_esim_notes="Covered by the Caribbean regional eSIM bundle.",
        recommendation="Re-check RAC's status shortly before transiting.",
        official_urls=("https://rac.cw/",),
        verified=_VERIFIED_ANTILLES,
    ),
}

ALIASES: dict[str, str] = {
    "bvi": "british virgin islands",
    "tci": "turks and caicos",
    "turks & caicos": "turks and caicos",
    "turks and caicos islands": "turks and caicos",
    "united states": "usa",
    "us": "usa",
    "u.s.": "usa",
    "u.s. virgin islands": "usvi",
    "us virgin islands": "usvi",
    "the bahamas": "bahamas",
    "dr": "dominican republic",
    "pr": "puerto rico",
    "st martin": "saint martin",
    "st. martin": "saint martin",
    "st maarten": "sint maarten",
    "st. maarten": "sint maarten",
    "st barts": "saint barthelemy",
    "st. barts": "saint barthelemy",
    "st barths": "saint barthelemy",
    "saint barths": "saint barthelemy",
    "saint barthélemy": "saint barthelemy",
    "antigua": "antigua and barbuda",
    "st lucia": "saint lucia",
    "st. lucia": "saint lucia",
    "svg": "saint vincent and the grenadines",
    "st vincent": "saint vincent and the grenadines",
    "st. vincent": "saint vincent and the grenadines",
    "the grenadines": "saint vincent and the grenadines",
    "curaçao": "curacao",
}


def resolve(name: str) -> CountryComms | None:
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    return REGISTRY.get(key)


def _calendar_check(c: CountryComms, config: CommsConfig | None, arrival: date) -> str | None:
    """Yes/no + date: suspend Starlink for this stop, or keep it on."""
    if config is None or config.starlink_billing_cycle_day is None:
        return None
    _, until = _cycle_status(config.starlink_billing_cycle_day, arrival)
    next_reset = arrival + timedelta(days=until)
    maya_covers = "not applicable" not in c.maya_esim_notes.lower()
    starlink_unreliable = "⚠️" in c.starlink_status
    if maya_covers and starlink_unreliable:
        return f"Yes — turn off. Request suspension by **{next_reset:%Y-%m-%d}**."
    return "No — keep it on."


def _country_block(c: CountryComms, config: CommsConfig | None, arrival: date) -> str:
    lines = [
        f"### {c.name}",
        "",
    ]
    check = _calendar_check(c, config, arrival)
    if check:
        lines.append(f"- **Calendar check:** {check}")
    lines += [
        f"- **Starlink:** {c.starlink_status}",
        f"- **Cellular / T-Mobile roaming:** {c.cellular_notes}",
        f"- **Maya eSIM:** {c.maya_esim_notes}",
        f"- **Recommendation:** {c.recommendation}",
    ]
    if c.official_urls:
        lines.append("- **Sources:** " + " · ".join(c.official_urls))
    if c.notes:
        lines.append(f"- **Notes:** {c.notes}")
    if c.verified:
        lines.append("")
        lines.append(
            f"*Checked {c.verified}. Satellite/cellular terms and regional licensing change — "
            "re-check before departure.*"
        )
    return "\n".join(lines)


def _unknown_country(name: str) -> str:
    return "\n".join([
        f"### {name}",
        "",
        f"> Not in the built-in comms registry. Before arrival, check: Starlink service status "
        f"in **{name}** (mappr.co/starlink-availability-by-country), T-Mobile roaming tier at "
        "t-mobile.com/coverage/mobile-internet-coverage, and Maya eSIM coverage at "
        "maya.net/esim.",
    ])


def render_comms(
    countries: list[str],
    config: CommsConfig | None,
    today: date,
    arrival: date | None = None,
) -> str:
    """Markdown for the briefing's Communications Configuration subsection."""
    lines = ["**Starlink billing-cycle status**", ""]
    if config is None:
        lines.append(
            "> **PENDING** — no `config/comms.toml` found; add one (see the shipped example) "
            "to get calendar-based pacing advice, or pass `--comms-config`."
        )
    else:
        plan_line = f"- Current plan: **{config.starlink_plan or 'not set'}**"
        if config.starlink_notes:
            plan_line += f" — {config.starlink_notes}"
        lines.append(plan_line)

        if config.starlink_billing_cycle_day is not None:
            since, until = _cycle_status(config.starlink_billing_cycle_day, today)
            if config.starlink_priority_gb is not None:
                # a capped monthly high-speed allotment (e.g. Roam 100GB/300GB) — pacing
                # against the cap actually means something.
                cycle_len = since + until
                frac = since / cycle_len if cycle_len else 0.0
                pacing = (
                    "plenty of headroom left this cycle — fine to lean on Starlink for bulk "
                    "data here"
                    if frac < 0.6
                    else "late in the cycle — favor the Maya eSIM/T-Mobile for bulk data and "
                    "save Starlink priority GB for weather/nav and critical comms"
                )
                lines.append(
                    f"- Day **{since}** of the cycle (resets in **{until} day(s)**, on the "
                    f"{config.starlink_billing_cycle_day} of the month) as of {today:%Y-%m-%d} "
                    f"— {pacing}. *(Calendar-based estimate only — actual usage isn't metered "
                    "here.)*"
                )
            else:
                # unlimited-within-territorial-waters plan (Personal Maritime, Roam
                # Unlimited): no monthly GB cap to pace against — the cost driver is
                # Ocean Mode usage, not calendar position.
                lines.append(
                    f"- Day **{since}** of the cycle (resets in **{until} day(s)**, on the "
                    f"{config.starlink_billing_cycle_day} of the month) as of {today:%Y-%m-%d}. "
                    "No monthly high-speed cap on this plan — territorial-water use is "
                    "unlimited; track Ocean-Mode (offshore) spend instead of cycle position."
                )
            next_reset = today + timedelta(days=until)
            lines.append(
                f"- To suspend or resume the plan (a haul-out, a lay-up), request it before "
                f"**{next_reset:%Y-%m-%d}** — Starlink only suspends/resumes in full "
                "billing-cycle increments, with no mid-cycle proration."
            )
        else:
            lines.append(
                "- ⚠️ No billing-cycle day set — add `starlink.billing_cycle_day` to "
                "`config/comms.toml` for a billing-date reference."
            )
        if config.starlink_priority_gb is not None:
            lines.append(
                f"- Priority high-speed allotment: **{config.starlink_priority_gb:.0f} "
                "GB/cycle** (a planning ceiling, not a live usage meter)."
            )
        lines.append("")
        tm_line = f"- T-Mobile: **{config.tmobile_plan or 'not set'}**"
        tm_line += (
            " — includes some international data roaming."
            if config.tmobile_roaming_included
            else " — domestic-only plan."
        )
        if config.tmobile_notes:
            tm_line += f" {config.tmobile_notes}"
        lines.append(tm_line)

        maya_line = f"- Maya eSIM: **{config.maya_esim_scope or 'not set'}**"
        if config.maya_esim_notes:
            maya_line += f" — {config.maya_esim_notes}"
        lines.append(maya_line)

        if config.router_model:
            router_line = f"- Router: **{config.router_model}**"
            if config.router_notes:
                router_line += f" — {config.router_notes}"
            lines.append(router_line)

    rate = (
        config.starlink_ocean_mode_usd_per_gb
        if config is not None and config.starlink_ocean_mode_usd_per_gb is not None
        else _DEFAULT_OCEAN_MODE_USD_PER_GB
    )
    lines += ["", f"> {_starlink_reminder(rate)}"]

    if not countries:
        lines += ["", "> No destination given (`--to`) — add it for per-country comms notes."]
        return "\n".join(lines)

    lines.append("")
    for raw in countries:
        entry = resolve(raw)
        arr = arrival if arrival is not None else today
        lines.append(_country_block(entry, config, arr) if entry else _unknown_country(raw))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
