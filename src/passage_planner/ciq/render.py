"""Render the arrival dossier and the verification template."""

from __future__ import annotations

from .data import resolve
from .model import CountryCIQ

_Q_FLAG = (
    "At the 12 NM territorial limit hoist the **yellow Q flag** on the starboard "
    "spreader; keep all crew aboard until Customs & Immigration grant Pratique; then "
    "drop the Q and hoist the host-nation courtesy flag."
)


def render_dossier(countries: list[str]) -> str:
    """Markdown for section 6, one block per country. Unknown countries get a
    'verify from official sources' skeleton naming them."""
    blocks: list[str] = []
    for raw in countries:
        entry = resolve(raw)
        blocks.append(_known(entry) if entry else _unknown(raw))
    return ("\n\n".join(blocks) + "\n\n" + _Q_FLAG) if blocks else _Q_FLAG


def _known(c: CountryCIQ) -> str:
    lines = [
        f"### {c.name}",
        "",
        f"- **Ports of entry:** {c.ports_of_entry}",
        f"- **Pre-arrival:** {c.pre_arrival}",
        f"- **On arrival:** {c.on_arrival}",
        f"- **Documents:** {c.documents}",
    ]
    if c.fees:
        lines.append(f"- **Fees:** {c.fees}")
    if c.offices:
        lines.append(f"- **Where / who:** {c.offices}")
    lines.append("- **Primary sources:** " + " · ".join(c.official_urls))
    if c.secondary_urls:
        lines.append(
            "- `⚠️ SECONDARY` cross-check: " + " · ".join(c.secondary_urls)
        )
    if c.notes:
        lines.append(f"- **Notes:** {c.notes}")
    lines.append("")
    if c.verified:
        lines.append(
            f"*Checked against the primary sources on {c.verified}. Fees and pre-arrival "
            "apps change between seasons — re-check the URLs above before departure.*"
        )
    else:
        lines.append(
            "*Not yet verified — confirm every fee, port and pre-arrival step against the "
            "primary sources before departure.*"
        )
    return "\n".join(lines)


def _unknown(name: str) -> str:
    return "\n".join([
        f"### {name}",
        "",
        f"> Not in the built-in registry. Fill from official sources for **{name}**:",
        "",
        "- Legal ports of entry nearest the arrival waypoint",
        "- Mandatory pre-arrival declaration (system + submission window)",
        "- Arrival VHF channel(s), Q-flag and anchoring-pending-clearance rules",
        "- Fees (customs, immigration, cruising permit, overtime) and whether an agent "
        "is required",
        "- Documents: prior clearance (Zarpe), passports (>6 mo), registration, insurance, "
        "crew list ×5",
    ])


def ciq_template(countries: list[str]) -> str:
    """A ``ciq.md`` skeleton to complete, one section per country, with the
    official URLs to verify against."""
    out = [
        "# Arrival & Immigration Dossier — draft",
        "",
        "Complete each section from the primary URLs, then pass with `brief --ciq-file`.",
        "Or hand this file to a Claude conversation and have the official pages fetched "
        "and transcribed.",
        "",
    ]
    for raw in countries:
        entry = resolve(raw)
        if entry is not None:
            out.append(_known(entry))
        else:
            out.append(_unknown(raw))
        out.append("")
    return "\n".join(out)
