"""Tests for passage_planner.boat_lookup (paste-in parsing of MarineTraffic /
ORC Sailboat Data text for the Fleet form's "Look up this boat" button)."""

from __future__ import annotations

from passage_planner.boat_lookup import merge, parse_marinetraffic, parse_orc

# A trimmed version of the text get_page_text would return from a real
# MarineTraffic vessel Overview tab (see the Example Buddy lookup, 2026-09-22).
_MARINETRAFFIC_TEXT = """
EXAMPLE BUDDY
Pleasure Craft  MMSI: 000000001
Pleasure Craft EXAMPLE BUDDY is currently located in the Gulf of Mexico
Her length overall (LOA) is 19 meters and her width is 6 meters.
General
Name EXAMPLE BUDDY
Flag
UNITED STATES OF AMERICA
IMO -
MMSI 000000001
Call sign XYZ0000
AIS transponder class Class B
"""

# A synthetic ORC-style certificate excerpt: dimension labels plus a VPP
# speed table (TWS header row, TWA rows). Real certificate wording varies;
# this exercises the label and table shapes the parser looks for.
_ORC_TEXT = """
Yacht Data
LOA[m]     12.50
BMAX[m]    3.90
Draft[m]   2.20
DSPL[kg]   7200

VPP Speed Table (kn)
twa/tws   6      8      10     12     14     16     20
52        5.8    6.9    7.4    7.7    7.9    8.0    8.1
60        6.1    7.2    7.8    8.2    8.5    8.7    8.9
75        6.3    7.5    8.1    8.6    9.0    9.3    9.7
90        6.2    7.6    8.4    9.0    9.5    9.9    10.4
120       5.4    7.0    8.2    9.2    10.0   10.6    11.5
135       4.4    5.9    7.2    8.3    9.2    9.9    11.0
150       3.4    4.7    5.9    6.9    7.8    8.6    9.9
"""


def test_marinetraffic_parses_identity_and_dimensions() -> None:
    result = parse_marinetraffic(_MARINETRAFFIC_TEXT)
    assert result.mmsi == "000000001"
    assert result.call_sign == "XYZ0000"
    assert result.flag == "UNITED STATES OF AMERICA"
    assert result.loa_ft is not None and round(result.loa_ft, 1) == 62.3
    assert result.beam_ft is not None and round(result.beam_ft, 1) == 19.7


def test_marinetraffic_empty_text_reports_nothing_found() -> None:
    result = parse_marinetraffic("")
    assert not result.found
    assert result.mmsi == ""


def test_marinetraffic_missing_fields_are_reported() -> None:
    result = parse_marinetraffic("Name EXAMPLE BUDDY\nMMSI 000000001\n")
    assert result.mmsi == "000000001"
    assert any("call sign" in m for m in result.messages)
    assert any("LOA" in m for m in result.messages)


def test_orc_parses_dimensions_with_units() -> None:
    specs, polar = parse_orc(_ORC_TEXT)
    assert specs.loa_ft is not None and round(specs.loa_ft, 1) == 41.0
    assert specs.beam_ft is not None and round(specs.beam_ft, 1) == 12.8
    assert specs.draft_ft is not None and round(specs.draft_ft, 1) == 7.2
    assert specs.displacement_lb is not None and round(specs.displacement_lb, -1) == 15870.0
    assert polar is not None


def test_orc_vpp_table_becomes_a_real_polar() -> None:
    _, polar = parse_orc(_ORC_TEXT)
    assert polar is not None
    assert polar.provisional is False
    assert polar.tws_axis == (6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0)
    assert polar.twa_axis == (52.0, 60.0, 75.0, 90.0, 120.0, 135.0, 150.0)
    assert polar.speed(10.0, 60.0) == 7.8


def test_orc_unlabelled_displacement_is_left_for_manual_entry() -> None:
    text = "LOA[m] 12.5\nBMAX[m] 3.9\nDraft[m] 2.2\nDSPL 7200\n"
    specs, _ = parse_orc(text)
    assert specs.displacement_lb is None
    assert any("no kg/lb unit" in m for m in specs.messages)


def test_orc_text_with_no_table_yields_no_polar() -> None:
    specs, polar = parse_orc("LOA[m] 12.5\nBMAX[m] 3.9\n")
    assert polar is None
    assert any("no VPP speed table" in m or "displacement" in m for m in specs.messages)


def test_merge_prefers_orc_for_dimensions_and_marinetraffic_for_identity() -> None:
    mt = parse_marinetraffic(_MARINETRAFFIC_TEXT)
    orc, _ = parse_orc(_ORC_TEXT)
    combined = merge(mt, orc)
    assert combined.mmsi == "000000001"
    assert combined.call_sign == "XYZ0000"
    assert combined.draft_ft == orc.draft_ft
    assert combined.displacement_lb == orc.displacement_lb
    assert combined.loa_ft == orc.loa_ft  # ORC wins over MarineTraffic when both present


def test_merge_falls_back_to_marinetraffic_dimensions_without_orc() -> None:
    mt = parse_marinetraffic(_MARINETRAFFIC_TEXT)
    empty_orc = parse_orc("")[0]
    combined = merge(mt, empty_orc)
    assert combined.loa_ft == mt.loa_ft
    assert combined.draft_ft is None
