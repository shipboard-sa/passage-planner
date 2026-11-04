"""Parse pasted MarineTraffic / ORC Sailboat Data text into buddy-boat specs.

Purpose: turn text a person pastes from MarineTraffic's vessel-details page
    and/or an ORC Sailboat Data certificate (orc.org, mirrored by
    github.com/jieter/orc-data) into BuddyBoat spec fields and, when the ORC
    text includes its VPP speed table, a real per-boat :class:`Polar`.
Version: v1 (2026-09-22)
Scope: PRODUCTION - feeds the Fleet form's "Look up this boat" paste box
    (backend/main.py's POST /api/fleet/lookup, frontend/src/FleetPanel.tsx).
Architecture: passage-planner support module. Pure text parsing, no network
    I/O - neither source documents a stable public API (orc-data's own
    README gives no endpoint/format guarantee), so the fetch is done by a
    person's own browser (the owner's, or Claude's) and pasted in, mirroring the
    a marine forecaster paste-in pattern (see wiki/Design-Decisions.md). Every
    parsed field lands in an editable form field for a person to confirm
    before Save writes anything - this module never blocks or fails loudly,
    it just reports what it could and couldn't find.

MarineTraffic's Overview tab (no login needed) gives MMSI, call sign, flag,
and LOA/beam in meters. ORC certificates give LOA/BMAX/draft/displacement
(various label spellings and units) plus a VPP speed table (TWS columns,
TWA rows) - a real, measured polar for that specific boat, not a generic
estimate. When both are pasted, ORC wins for physical dimensions (more
precise, measured to class-rule tolerances) and MarineTraffic wins for
identity fields (MMSI/call sign/flag) that ORC certificates don't carry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import PolarError
from .polar import Polar, polar_from_grid

_M_TO_FT = 3.280839895
_KG_TO_LB = 2.2046226218


@dataclass
class ParsedBoat:
    mmsi: str = ""
    call_sign: str = ""
    flag: str = ""
    loa_ft: float | None = None
    beam_ft: float | None = None
    draft_ft: float | None = None
    displacement_lb: float | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return any([
            self.mmsi, self.call_sign, self.flag, self.loa_ft, self.beam_ft,
            self.draft_ft, self.displacement_lb,
        ])


def _first(pattern: str, text: str, *, flags: int = re.IGNORECASE) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def parse_marinetraffic(text: str) -> ParsedBoat:
    """Parse a MarineTraffic vessel Overview page's text (as pasted, or from
    get_page_text). No login/API - only what the public Overview tab shows."""
    out = ParsedBoat()
    if not text.strip():
        return out

    mmsi = _first(r"\bMMSI\s*[:\s]\s*(\d{9})\b", text)
    if mmsi:
        out.mmsi = mmsi
    else:
        out.messages.append("MarineTraffic: no 9-digit MMSI found")

    call_sign = _first(r"\bCall sign\s*[:\s]+([A-Z0-9]{4,8})\b", text)
    if call_sign:
        out.call_sign = call_sign.upper()
    else:
        out.messages.append("MarineTraffic: no call sign found")

    flag = _first(r"\bFlag\s*[:\s]*\n?\s*([A-Z][A-Za-z .'-]{2,40})", text)
    if flag:
        out.flag = " ".join(flag.split())

    loa_m = _first(r"length overall\s*\(LOA\)\s*is\s*([\d.]+)\s*met", text) \
        or _first(r"\bLOA\b\s*[:\s]+([\d.]+)\s*m\b", text)
    if loa_m:
        out.loa_ft = round(float(loa_m) * _M_TO_FT, 1)
    else:
        out.messages.append("MarineTraffic: no LOA found")

    beam_m = _first(r"(?:her\s+)?width\s+is\s+([\d.]+)\s*met", text) \
        or _first(r"\bbeam\b\s*[:\s]+([\d.]+)\s*m\b", text)
    if beam_m:
        out.beam_ft = round(float(beam_m) * _M_TO_FT, 1)
    else:
        out.messages.append("MarineTraffic: no beam found")

    return out


# ORC label -> ParsedBoat field, and the unit each label is normally given in.
_ORC_DIM_LABELS: tuple[tuple[str, str], ...] = (
    (r"\bLOA\b", "loa_ft"),
    (r"\bBMAX\b|\bBeam\b", "beam_ft"),
    (r"\bDraft\b", "draft_ft"),
)
_ORC_DISPL_LABELS = r"\bDSPL\b|\bDisplacement\b"


def _dim_with_unit(label_pat: str, text: str, *, units: str = "m|ft") -> tuple[float, str] | None:
    """A value right after ``label_pat``, plus the unit token found next to it
    (one of ``units``, or ``""`` if none was given)."""
    m = re.search(
        rf"(?:{label_pat})\s*(?:\[?\s*({units})\s*\]?)?\s*[:=]?\s*([\d.]+)\s*({units})?\b",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    unit = (m.group(1) or m.group(3) or "").lower()
    return float(m.group(2)), unit


def parse_orc(text: str) -> tuple[ParsedBoat, Polar | None]:
    """Parse an ORC certificate's dimensions and (when present) its VPP speed
    table into a real per-boat :class:`Polar`. Returns ``(specs, polar)`` -
    ``polar`` is ``None`` when no usable TWA x TWS grid was found; that is
    reported in ``specs.messages``, not raised, since the dimensions alone are
    still useful to fill in."""
    out = ParsedBoat()
    if not text.strip():
        return out, None

    for label_pat, field_name in _ORC_DIM_LABELS:
        found = _dim_with_unit(label_pat, text)
        if found is None:
            out.messages.append(f"ORC: no {field_name.replace('_ft', '')} found")
            continue
        value, unit = found
        if unit == "ft":
            ft = value
        elif unit == "m":
            ft = value * _M_TO_FT
        else:
            # No explicit unit: sailboat LOA/beam/draft in meters is almost
            # always under 25; feet, almost always over.
            ft = value if value >= 25 else value * _M_TO_FT
        setattr(out, field_name, round(ft, 2))

    displ = _dim_with_unit(_ORC_DISPL_LABELS, text, units="kg|lb")
    if displ is None:
        out.messages.append("ORC: no displacement found")
    else:
        value, unit = displ
        if unit == "":
            # kg and lb ranges overlap heavily for a cruising boat (roughly
            # 2,000-40,000 kg vs 5,000-90,000 lb) - guessing wrong here would
            # silently corrupt the fuel/speed math downstream, so an
            # unlabelled figure is left for a person to enter by hand instead.
            out.messages.append(
                f"ORC: displacement {value:g} has no kg/lb unit - enter it by hand"
            )
        else:
            lb = value * _KG_TO_LB if unit == "kg" else value
            out.displacement_lb = round(lb, 0)

    polar = _parse_orc_vpp_table(text, out.messages)
    return out, polar


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_orc_vpp_table(text: str, messages: list[str]) -> Polar | None:
    """Find a TWS header row (several ascending numbers, 3-35, on one line)
    followed by TWA rows (first number 0-180, then one speed per TWS column)
    and build a :class:`Polar` from them. Rows that don't fit that shape
    (VMG/beat-angle/gybe-angle rows, headers) are skipped, not guessed at."""
    lines = text.splitlines()
    cells: dict[tuple[float, float], float] = {}
    tws_axis: tuple[float, ...] = ()

    for line in lines:
        nums = [float(x) for x in _NUM.findall(line)]
        if tws_axis == () and 2 <= len(nums) <= 12 and all(3.0 <= n <= 35.0 for n in nums):
            candidate = tuple(sorted(nums))
            if candidate == tuple(nums) and len(set(candidate)) == len(candidate):
                tws_axis = candidate
            continue
        if tws_axis and len(nums) == len(tws_axis) + 1:
            twa, *speeds = nums
            if 0.0 <= twa <= 180.0 and all(0.0 <= s <= 40.0 for s in speeds):
                for tws, speed in zip(tws_axis, speeds, strict=True):
                    cells[(twa, tws)] = speed

    if not cells:
        messages.append("ORC: no VPP speed table recognised - no polar built")
        return None
    twa_rows = len({twa for twa, _ in cells})
    if twa_rows < 2 or len(tws_axis) < 2:
        messages.append("ORC: VPP table too sparse to build a polar")
        return None
    try:
        polar = polar_from_grid(cells, source="ORC certificate (pasted)", provisional=False)
    except PolarError:
        messages.append("ORC: VPP table rows don't share the same TWS columns - no polar built")
        return None
    messages.append(
        f"ORC: built a real polar from the VPP table ({twa_rows} TWA rows x "
        f"{len(tws_axis)} TWS columns)"
    )
    return polar


def merge(marinetraffic: ParsedBoat, orc: ParsedBoat) -> ParsedBoat:
    """Combine both sources: ORC's measured dimensions win when present (draft
    and displacement only ever come from ORC); MarineTraffic wins for
    identity fields ORC certificates don't carry (MMSI, call sign, flag)."""
    return ParsedBoat(
        mmsi=marinetraffic.mmsi,
        call_sign=marinetraffic.call_sign,
        flag=marinetraffic.flag,
        loa_ft=orc.loa_ft if orc.loa_ft is not None else marinetraffic.loa_ft,
        beam_ft=orc.beam_ft if orc.beam_ft is not None else marinetraffic.beam_ft,
        draft_ft=orc.draft_ft,
        displacement_lb=orc.displacement_lb,
        messages=[*marinetraffic.messages, *orc.messages],
    )
