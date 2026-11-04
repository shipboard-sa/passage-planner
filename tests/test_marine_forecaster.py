"""Tests for passage_planner.marine_forecaster (weather-routing step 6).

The fixture `parker_sample.txt` is an original, structurally-faithful
synthetic MWXC-style update (same section headers and wind notation the
real emails use), not a republished subscriber excerpt - see the module
docstring's note on why real archived text isn't committed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from passage_planner.analysis import PassageAnalysis
from passage_planner.marine_forecaster import (
    DEFAULT_DISAGREEMENT_THRESHOLD_KN,
    ParkerForecast,
    compare_with_model,
    parse,
    render_parker_markdown,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE = (FIXTURES / "parker_sample.txt").read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class _FakeAnalysis:
    peak_wind_kn: float


def _analysis(peak_wind_kn: float) -> PassageAnalysis:
    return _FakeAnalysis(peak_wind_kn=peak_wind_kn)  # type: ignore[return-value]


# -- parse() ----------------------------------------------------------------


def test_parse_extracts_header() -> None:
    p = parse(SAMPLE)
    assert p.header == "Wx Update, E Caribbean, Mon15, 8am"


def test_parse_splits_known_sections() -> None:
    p = parse(SAMPLE)
    for name in ("TROPICS", "ASCAT", "BUOYS", "IMAGERY", "SYNOPSIS", "OUTLOOK",
                 "SUGGEST", "PRECIP", "WIND", "SWELLS"):
        assert name in p.sections, name
    assert "trough near 55W" in p.sections["TROPICS"]
    assert "Trinidad-StVincent" in p.sections["SUGGEST"]


def test_parse_section_bodies_do_not_bleed_into_each_other() -> None:
    p = parse(SAMPLE)
    assert "OUTLOOK" not in p.sections["SYNOPSIS"]
    assert "SUGGEST" not in p.sections["OUTLOOK"]


def test_parse_peak_wind_from_wind_section() -> None:
    p = parse(SAMPLE)
    # WIND section's highest g<n>k figure is 27 (E-SE@14-22g27k/4-6' Tue16).
    assert p.peak_wind_kn == pytest.approx(27.0)


def test_parse_ignores_precip_coverage_numbers() -> None:
    # PRECIP's "30k"/"35k" are coverage figures, not wind - must not leak
    # into the wind figure when a WIND section is present.
    p = parse(SAMPLE)
    assert p.peak_wind_kn is not None
    assert p.peak_wind_kn < 35.0 or p.peak_wind_kn == pytest.approx(27.0)


def test_parse_handles_unrecognized_text_without_raising() -> None:
    p = parse("Just some plain text with no MWXC structure at all.")
    assert p.sections == {}
    assert p.peak_wind_kn is None
    assert p.raw_text.startswith("Just some plain text")


def test_parse_handles_empty_string() -> None:
    p = parse("")
    assert p.header == ""
    assert p.sections == {}
    assert p.peak_wind_kn is None


def test_parse_finds_a_wind_figure_even_without_a_wind_header() -> None:
    p = parse("Blowing hard out there, 25-30g35k expected offshore tonight.")
    assert p.peak_wind_kn == pytest.approx(35.0)


# -- compare_with_model() ----------------------------------------------------


def test_compare_flags_a_large_disagreement() -> None:
    parker = parse(SAMPLE)  # peak 27 kn
    cmp = compare_with_model(parker, _analysis(peak_wind_kn=14.0))
    assert cmp.parker_peak_wind_kn == pytest.approx(27.0)
    assert cmp.model_peak_wind_kn == pytest.approx(14.0)
    assert cmp.delta_kn == pytest.approx(13.0)
    assert cmp.disagreement is True


def test_compare_does_not_flag_a_close_match() -> None:
    parker = parse(SAMPLE)  # peak 27 kn
    cmp = compare_with_model(parker, _analysis(peak_wind_kn=24.0))
    assert cmp.delta_kn == pytest.approx(3.0)
    assert cmp.disagreement is False


def test_compare_threshold_is_configurable() -> None:
    parker = parse(SAMPLE)  # peak 27 kn
    cmp = compare_with_model(parker, _analysis(peak_wind_kn=24.0), disagreement_threshold_kn=2.0)
    assert cmp.disagreement is True


def test_compare_exactly_at_threshold_counts_as_disagreement() -> None:
    parker = ParkerForecast(raw_text="", header="", sections={}, peak_wind_kn=22.0)
    cmp = compare_with_model(
        parker, _analysis(peak_wind_kn=14.0),
        disagreement_threshold_kn=DEFAULT_DISAGREEMENT_THRESHOLD_KN,
    )
    assert cmp.delta_kn == pytest.approx(8.0)
    assert cmp.disagreement is True


def test_compare_with_no_parker_wind_figure_never_disagrees() -> None:
    parker = ParkerForecast(raw_text="no numbers here", header="h", sections={}, peak_wind_kn=None)
    cmp = compare_with_model(parker, _analysis(peak_wind_kn=40.0))
    assert cmp.parker_peak_wind_kn is None
    assert cmp.delta_kn is None
    assert cmp.disagreement is False


# -- render_parker_markdown() ------------------------------------------------


def test_render_includes_provenance_tag_and_header() -> None:
    parker = parse(SAMPLE)
    cmp = compare_with_model(parker, _analysis(peak_wind_kn=14.0))
    md = render_parker_markdown(parker, cmp)
    assert "[Professional Forecast: Marine Weather Center]" in md
    assert "Wx Update, E Caribbean, Mon15, 8am" in md


def test_render_shows_the_comparison_line() -> None:
    parker = parse(SAMPLE)
    cmp = compare_with_model(parker, _analysis(peak_wind_kn=14.0))
    md = render_parker_markdown(parker, cmp)
    assert "27 kn" in md
    assert "14 kn" in md
    assert "+13 kn" in md
    assert "disagree" in md.lower()


def test_render_omits_disagreement_callout_on_agreement() -> None:
    parker = parse(SAMPLE)
    cmp = compare_with_model(parker, _analysis(peak_wind_kn=25.0))
    md = render_parker_markdown(parker, cmp)
    assert "disagree" not in md.lower()


def test_render_includes_known_sections() -> None:
    parker = parse(SAMPLE)
    cmp = compare_with_model(parker, _analysis(peak_wind_kn=14.0))
    md = render_parker_markdown(parker, cmp)
    assert "*Synopsis:*" in md
    assert "*Suggest:*" in md


def test_render_falls_back_to_raw_text_when_unrecognized() -> None:
    parker = parse("No structure here, just a note.")
    cmp = compare_with_model(parker, _analysis(peak_wind_kn=10.0))
    md = render_parker_markdown(parker, cmp)
    assert "No structure here, just a note." in md
