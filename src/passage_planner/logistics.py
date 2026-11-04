"""Per-port shore-logistics skeleton for a single passage.

Purpose: Emit the per-port shore-logistics Markdown skeleton, measure how much
    of it has been filled in, and flag ports that still look unresearched.
Version: v5 (2026-09-21) - added strip_template_preamble() so the template's own
    heading + instructions no longer leak into briefing 7b; v4 added the checked
    "Security / recent incidents" field (situational awareness, CSSN); v3 added
    port_skeleton() (single-port section, used by research.py); v2 added
    unresearched_ports() for the web form's submit guard.
Scope: PRODUCTION - feeds briefing section 7b and the web form.
Architecture: passage-planner data/support module. Called by cli.py
    (logistics-template), briefing.py (coverage note) and backend/main.py
    (template + coverage endpoints). Pure functions, no I/O.

This voyage is one-way — down the island chain to Panama, no revisits — so there
is no cached registry. The workflow is on-demand:

1. ``passage-planner logistics-template --ports "West End,George Town"`` emits a
   Markdown skeleton, one section per port, with the fields to fill and a hint
   on where to look for each.
2. Fill it — from a cruising guide, or hand it to a session that walks each
   port (marina site, ActiveCaptain, Waterway Guide / Noonsite, local WhatsApp
   groups).
3. ``passage-planner brief ROUTE.gpx --logistics-file logistics.md`` drops the
   result into briefing section 7b, with a coverage note flagging any port
   section that is missing standard fields.

Nothing is kept between passages.
"""

from __future__ import annotations

import re

# (field label, what to research / where to look). Order is the skeleton order.
LOGISTICS_FIELDS: tuple[tuple[str, str], ...] = (
    ("Groceries", "nearest full grocery; walk / taxi / bus / bike; provisioning quality; "
                  "for out-islands, note the supply-boat day"),
    ("Chandlery", "name, how well stocked, whether they can order parts in and lead time"),
    ("Fuel dock", "diesel yes/no, hours, depth alongside (need >= 8 ft), price basis, "
                  "whether duty-free is available on outbound clearance"),
    ("Water", "at the fuel dock / on the slips / jerry-jug only; potable or treat first"),
    ("Pump-out", "available and where; fee"),
    ("Laundry", "self-serve, drop-off, or marina machines; rough cost"),
    ("Marine services", "diesel mechanic, rigger, electrician / electronics, sailmaker, "
                        "welder; HAUL-OUT: yard name, travelift width (need >= 14 ft 9 in "
                        "beam) and depth (need >= 8 ft), hard-stand storage"),
    ("Dinghy landing", "where to leave it, dock or beach, lock it or not, fee, how secure"),
    ("Trash / recycling", "where it goes, any fee, separation rules"),
    ("Money", "currency in use; is USD accepted and at what rate; cash-vs-card reality; "
              "where to change money (bank / casa de cambio); ATM and whether it pays "
              "local currency"),
    ("Marina", "VHF working channel, reservation lead time, nightly rate basis, max LOA / "
               "draft, shore power (voltage / amps / plug), wifi or Starlink"),
    ("Key contacts", "name + WhatsApp / phone for the marina, any clearing agent, the fuel "
                     "dock, a mechanic — Caribbean businesses run on WhatsApp"),
    ("Local info", "the WhatsApp / Facebook community group that is actually used; a net if "
                   "one still runs; the VHF hailing channel"),
    ("Security / recent incidents", "situational awareness, not a verdict: search the "
        "Caribbean Safety & Security Net (thecssn.org, incident search by country and "
        "keyword, plus its map) and Noonsite's piracy & security page for this port and "
        "its island or coast; give the number of reports in the last 12 months, the "
        "date of the most recent, and the usual type (dinghy / outboard theft, "
        "boarding, ...)"),
    ("Notes", "anything else — fleet-specific needs (Example Buddy carries propane), "
              "seasonal closures, officialdom quirks"),
)

_FIELD_LABELS = tuple(label for label, _ in LOGISTICS_FIELDS)


def split_ports(raw: str) -> list[str]:
    """Port names from a typed or posted list, blanks and repeats dropped.

    Newlines or semicolons separate ports when either is present, so a picked
    name such as ``Tyrell Bay, Carriacou (Grenada)`` survives intact; a plain
    comma list (``West End, George Town``, the CLI's format) still works.
    """
    parts = re.split(r"[\n;]", raw) if re.search(r"[\n;]", raw) else raw.split(",")
    return list(dict.fromkeys(p.strip() for p in parts if p.strip()))

# Fields coverage_gaps() checks: every standard field except the optional Notes.
CHECKED_FIELD_COUNT = len(LOGISTICS_FIELDS) - 1

# A port section counts as "unresearched" when MORE than this fraction of its
# checked fields is still empty. Smaller gaps (a few honest unknowns) are the
# briefing's own 7b coverage note's job; this threshold is only for catching a
# template submitted with the research step skipped.
_UNRESEARCHED_FRACTION = 0.5
_PORT_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_BULLET_LABEL_RE = re.compile(r"^-\s+\*\*(.+?):\*\*", re.MULTILINE)


_TEMPLATE_INSTRUCTIONS = (
    "Fill each port section from the sources noted in italics, then pass with "
    "`brief --logistics-file`. Delete the *(research: …)* hints as you go. Leave a "
    "field blank only if it genuinely does not apply; write `n/a` if you checked and "
    "there is nothing."
)
_TEMPLATE_HEADING_RE = re.compile(r"^# Shore logistics.*— draft[ \t]*$", re.MULTILINE)


def strip_template_preamble(markdown: str) -> str:
    """Drop the template's own heading and fill-in instructions, keep everything else.

    ``logistics_template`` opens with a ``# Shore logistics … — draft`` heading and an
    instruction paragraph meant for whoever fills it in. They are not briefing content,
    so they are removed before the text is folded into section 7b. A hand-written
    preamble (for example researched notes) is left alone.
    """
    out = _TEMPLATE_HEADING_RE.sub("", markdown).replace(_TEMPLATE_INSTRUCTIONS, "")
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def logistics_template(ports: list[str], *, route_name: str | None = None) -> str:
    """A ``logistics.md`` skeleton — one section per port, fields + research hints."""
    ctx = f" for {route_name}" if route_name else ""
    out = [
        f"# Shore logistics{ctx} — draft",
        "",
        _TEMPLATE_INSTRUCTIONS,
        "",
    ]
    for port in ports:
        out.append(port_skeleton(port))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def port_skeleton(port: str) -> str:
    """One port's ``### <port>`` section with every field as a research hint."""
    lines = [f"### {port.strip()}", ""]
    lines.extend(f"- **{label}:**  *(research: {hint})*" for label, hint in LOGISTICS_FIELDS)
    return "\n".join(lines)


def coverage_gaps(markdown: str) -> dict[str, list[str]]:
    """Per port section in ``markdown``, which standard fields carry no content.

    A field 'has content' when its ``- **Label:**`` bullet is present and is
    followed by text that is not just a leftover ``*(research: …)*`` hint.
    """
    gaps: dict[str, list[str]] = {}
    sections = _split_sections(markdown)
    for port, body in sections.items():
        present: set[str] = set()
        for line in body.splitlines():
            m = _BULLET_LABEL_RE.match(line.strip())
            if not m:
                continue
            label = m.group(1)
            rest = line.split(":**", 1)[1].strip() if ":**" in line else ""
            rest = re.sub(r"\*\(research:.*?\)\*", "", rest).strip()
            if rest:
                present.add(label)
        missing = [lbl for lbl in _FIELD_LABELS if lbl not in present and lbl != "Notes"]
        if missing:
            gaps[port] = missing
    return gaps


def unresearched_ports(markdown: str) -> dict[str, list[str]]:
    """Ports in ``markdown`` that look like an untouched template: more than half
    of their checked fields are still empty.

    Returns ``{port: [missing field labels]}`` (a subset of ``coverage_gaps``).
    Text with no ``### `` port headings yields ``{}`` - free-form input is the
    author's own business, not something to second-guess.
    """
    return {
        port: missing
        for port, missing in coverage_gaps(markdown).items()
        if len(missing) > CHECKED_FIELD_COUNT * _UNRESEARCHED_FRACTION
    }


def _split_sections(markdown: str) -> dict[str, str]:
    matches = list(_PORT_HEADING_RE.finditer(markdown))
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        out[m.group(1)] = markdown[start:end]
    return out
