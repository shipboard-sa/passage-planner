"""a marine forecaster (MWXC) professional forecast text: parse it, compare its
peak wind figure against the model picture, render a briefing section
(weather-routing step 6).

Purpose: turn a pasted MWXC "Wx Update" email into sections plus a peak
    wind figure, and compare that figure against the model forecast this
    tool already computed for the same passage.
Version: v1 (2026-09-22)
Scope: PRODUCTION - passage-planner subsystem. `parse()`/`compare_with_model()`
    are pure; not yet wired into `briefing.py`, `service.py` or the CLI.
Architecture: passage-planner subsystem. Consumes `analysis.PassageAnalysis`
    (specifically its `peak_wind_kn` property, shared with `sweep.py`).
    Designed against two real MWXC emails (2024-09-20 and 2024-10-30, from
    the owner's email archive - the "200 archived updates" the roadmap names as
    the test corpus), not a guessed format.

**Always optional, absent means omitted**: this project's other
not-yet-wired data feeds (NHC tropical, ENC depth, the CIQ portals) render
a **PENDING** block explaining what's missing - see `briefing.py`'s module
docstring. a marine forecaster is different on purpose: the owner is not subscribed most
of the time (see `config/sources.toml`'s `mwxc` entry, `enabled = false`),
so no text pasted is the normal, expected steady state, not a gap to call
out. When ``parker_text`` is absent, the caller renders nothing for this
section, not a PENDING callout.

**Why a peak-wind comparison, not a full numeric parse of every area**:
MWXC's free text names a different, inconsistently-worded set of areas in
every update (the two real samples this module was built against already
differ), and its wind figures are ranges across multiple time windows per
area. Reliably mapping "this route, this leg, this time" onto "the right
number in this paragraph" is not a tractable regex problem. Extracting the
single highest knot figure mentioned in the WIND section is: MWXC's own
notation always writes every sustained/gust figure as ``<number>k``, so a
maximum over all of them is a real, defensible "worst wind Parker is
describing for the region this update covers" - not a precise per-leg
figure, but exactly the kind of high-level cross-check ("does the
professional's read of this region look a lot worse than the models?") the
roadmap's "flag disagreements" is asking for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .analysis import PassageAnalysis

# MWXC's section headers, in the order real samples use them. Not every
# update has every section (ASCAT is sometimes absent); order is used only
# to make section extraction robust to headers embedded in body text.
_KNOWN_SECTIONS: tuple[str, ...] = (
    "TROPICS", "ASCAT", "BUOYS", "IMAGERY", "SYNOPSIS", "OUTLOOK", "SUGGEST",
    "PRECIP", "WIND", "SWELLS",
)
_SECTION_RE = re.compile(
    # A known section name at line-start, followed by anything up to the
    # first colon on that line (real headers carry trailing detail, e.g.
    # "ASCAT 10PM Sun14:" or "PRECIP (Coverage is highest...):").
    r"^(" + "|".join(_KNOWN_SECTIONS) + r")[^:\n]*:\s*", re.MULTILINE,
)
_KNOT_RE = re.compile(r"(\d{1,3})k\b")

# Default: a professional peak-wind figure this far above the model's peak
# (in either direction) is worth a callout. Not sourced/calibrated against
# real disagreements yet - PROVISIONAL, tune once a few real comparisons
# have been seen.
DEFAULT_DISAGREEMENT_THRESHOLD_KN = 8.0


@dataclass(frozen=True, slots=True)
class ParkerForecast:
    raw_text: str
    header: str                  # first non-blank line, e.g. "Wx Update, E Caribbean, Fri20, 8am"
    sections: dict[str, str]     # section name -> body text, only sections actually present
    peak_wind_kn: float | None   # max knot figure in the WIND section (whole text if none present)


def parse(raw_text: str) -> ParkerForecast:
    """Parse a pasted MWXC email body. Never raises on unexpected text - a
    format this doesn't recognize still yields a :class:`ParkerForecast`
    with an empty ``sections`` dict and the raw text preserved, so a caller
    can always render *something* rather than reject the paste."""
    text = raw_text.strip()
    lines = text.splitlines()
    header = lines[0].strip() if lines else ""

    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()

    wind_text = sections.get("WIND", text)
    peak = _peak_knots(wind_text)
    if peak is None and "WIND" in sections:
        # The WIND section had no parseable figures; fall back to the whole text.
        peak = _peak_knots(text)

    return ParkerForecast(raw_text=raw_text, header=header, sections=sections, peak_wind_kn=peak)


def _peak_knots(text: str) -> float | None:
    values = [float(v) for v in _KNOT_RE.findall(text)]
    return max(values) if values else None


@dataclass(frozen=True, slots=True)
class ParkerComparison:
    parker_peak_wind_kn: float | None
    model_peak_wind_kn: float
    delta_kn: float | None   # parker - model; None if Parker's text had no wind figure
    disagreement: bool


def compare_with_model(
    parker: ParkerForecast,
    analysis: PassageAnalysis,
    *,
    disagreement_threshold_kn: float = DEFAULT_DISAGREEMENT_THRESHOLD_KN,
) -> ParkerComparison:
    """Compare Parker's peak wind figure against ``analysis.peak_wind_kn``
    (the model picture this tool already computed for the same passage)."""
    model_peak = analysis.peak_wind_kn
    if parker.peak_wind_kn is None:
        return ParkerComparison(
            parker_peak_wind_kn=None, model_peak_wind_kn=model_peak,
            delta_kn=None, disagreement=False,
        )
    delta = parker.peak_wind_kn - model_peak
    return ParkerComparison(
        parker_peak_wind_kn=parker.peak_wind_kn, model_peak_wind_kn=model_peak,
        delta_kn=delta, disagreement=abs(delta) >= disagreement_threshold_kn,
    )


def render_parker_markdown(parker: ParkerForecast, comparison: ParkerComparison) -> str:
    """Terse-facts Markdown for a briefing's weather section. Caller decides
    whether to include this at all - see the module docstring's "absent
    means omitted" note; this function assumes the caller already has text
    to render."""
    lines = [f"**[Professional Forecast: Marine Forecast Service]** {parker.header}".rstrip()]
    delta = comparison.delta_kn
    if comparison.parker_peak_wind_kn is not None and delta is not None:
        lines.append(
            f"- Peak wind mentioned: {comparison.parker_peak_wind_kn:.0f} kn "
            f"vs model peak {comparison.model_peak_wind_kn:.0f} kn "
            f"({delta:+.0f} kn)"
        )
        if comparison.disagreement:
            lines.append(
                "- ⚠️ Professional and model peak winds disagree by "
                f"{abs(delta):.0f} kn - read the full text below before relying on "
                "either alone."
            )
    else:
        lines.append(
            f"- No wind figure could be extracted from the pasted text for comparison against "
            f"the model peak ({comparison.model_peak_wind_kn:.0f} kn)."
        )
    for name in _KNOWN_SECTIONS:
        body = parker.sections.get(name)
        if body:
            lines += ["", f"*{name.title()}:* {body}"]
    if not parker.sections:
        lines += ["", parker.raw_text.strip()]
    return "\n".join(lines)
