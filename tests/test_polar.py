"""Tests for passage_planner.polar."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from passage_planner import load_polar
from passage_planner.errors import PolarError
from passage_planner.polar import polar_from_grid, write_pol

REPO_POLAR = Path(__file__).resolve().parents[1] / "config" / "baseline.pol"


def test_shipped_baseline_loads_and_is_provisional() -> None:
    pol = load_polar(REPO_POLAR)
    assert pol.provisional is True
    assert pol.tws_axis[0] == 4.0
    assert pol.tws_axis[-1] == 25.0
    assert pol.twa_axis[0] == 0.0
    assert pol.twa_axis[-1] == 180.0
    assert len(pol.grid) == len(pol.twa_axis)
    assert all(len(row) == len(pol.tws_axis) for row in pol.grid)


def test_exact_grid_point() -> None:
    pol = load_polar(REPO_POLAR)
    # TWA 90, TWS 12 -> the value in the shipped file.
    assert pol.speed(12.0, 90.0) == pytest.approx(7.3)
    # In irons row is all zero.
    assert pol.speed(15.0, 0.0) == 0.0


def test_bilinear_midpoint() -> None:
    pol = load_polar(REPO_POLAR)
    # Halfway between TWS 12 and 14 at TWA 90: (7.3 + 7.7) / 2.
    assert pol.speed(13.0, 90.0) == pytest.approx(7.5)


def test_twa_folds_and_clamps() -> None:
    pol = load_polar(REPO_POLAR)
    assert pol.speed(12.0, 200.0) == pytest.approx(pol.speed(12.0, 160.0))
    assert pol.speed(12.0, -90.0) == pytest.approx(pol.speed(12.0, 90.0))
    # TWS above the axis clamps to the last column.
    assert pol.speed(60.0, 90.0) == pytest.approx(pol.speed(25.0, 90.0))
    # TWA below the first non-zero row clamps to row 0 (zero).
    assert pol.speed(12.0, 0.0) == 0.0


def test_best_vmg_angles_are_sane() -> None:
    pol = load_polar(REPO_POLAR)
    up_twa, up_vmg = pol.best_upwind(14.0)
    dn_twa, dn_vmg = pol.best_downwind(14.0)
    assert 40.0 <= up_twa <= 70.0
    assert 120.0 <= dn_twa <= 180.0
    assert up_vmg > 0 and dn_vmg > 0


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "t.pol"
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return p


def test_missing_file() -> None:
    with pytest.raises(PolarError):
        load_polar(REPO_POLAR.with_name("nope.pol"))


def test_ragged_row_rejected(tmp_path: Path) -> None:
    body = """
    twa/tws\t6\t10
    45\t4.0\t5.0
    90\t6.0
    """
    with pytest.raises(PolarError, match="expected 3 values"):
        load_polar(_write(tmp_path, body))


def test_non_ascending_tws_rejected(tmp_path: Path) -> None:
    body = """
    twa/tws\t10\t6
    45\t5.0\t4.0
    90\t6.0\t5.0
    """
    with pytest.raises(PolarError, match="ascending"):
        load_polar(_write(tmp_path, body))


def test_comma_separated_and_comment(tmp_path: Path) -> None:
    body = """
    # a comment
    twa/tws,6,10,14
    45,3.4,5.5,6.2
    90,4.8,6.8,7.7
    """
    pol = load_polar(_write(tmp_path, body))
    assert pol.tws_axis == (6.0, 10.0, 14.0)
    assert pol.speed(10.0, 90.0) == pytest.approx(6.8)


# -- polar_from_grid (the polar_baseline InfluxDB shape) --------------------


def test_polar_from_grid_builds_sorted_axes() -> None:
    cells = {
        (110.0, 10.0): 6.9, (110.0, 12.0): 7.5,
        (60.0, 10.0): 6.3, (60.0, 12.0): 6.8,
    }
    pol = polar_from_grid(cells, source="influxdb:boat/polar_baseline")
    assert pol.twa_axis == (60.0, 110.0)
    assert pol.tws_axis == (10.0, 12.0)
    assert pol.speed(10.0, 60.0) == pytest.approx(6.3)
    assert pol.provisional is False
    assert pol.source == "influxdb:boat/polar_baseline"


def test_polar_from_grid_rejects_ragged_grid() -> None:
    cells = {(60.0, 10.0): 6.3, (60.0, 12.0): 6.8, (90.0, 10.0): 6.8}  # missing (90, 12)
    with pytest.raises(PolarError, match="incomplete grid"):
        polar_from_grid(cells, source="influxdb:boat/polar_baseline")


def test_polar_from_grid_rejects_empty() -> None:
    with pytest.raises(PolarError, match="no polar samples"):
        polar_from_grid({}, source="influxdb:boat/polar_baseline")


def test_polar_from_grid_rejects_twa_out_of_range() -> None:
    cells = {(200.0, 10.0): 5.0}
    with pytest.raises(PolarError, match=r"0\.\.180"):
        polar_from_grid(cells, source="influxdb:boat/polar_baseline")


# -- write_pol (the export-polar CLI command's serializer) ------------------


def test_write_pol_round_trips_through_load_polar(tmp_path: Path) -> None:
    cells = {
        (60.0, 10.0): 6.3, (60.0, 12.0): 6.8,
        (110.0, 10.0): 6.9, (110.0, 12.0): 7.5,
    }
    original = polar_from_grid(cells, source="influxdb:boat/polar_baseline")
    out = tmp_path / "exported.pol"
    write_pol(original, out)
    reloaded = load_polar(out)
    assert reloaded.twa_axis == original.twa_axis
    assert reloaded.tws_axis == original.tws_axis
    assert reloaded.grid == original.grid
    assert reloaded.provisional is False  # not auto-flagged; original was live data


def test_write_pol_marks_provisional_polars(tmp_path: Path) -> None:
    provisional = load_polar(REPO_POLAR)
    assert provisional.provisional is True
    out = tmp_path / "provisional.pol"
    write_pol(provisional, out)
    assert "# PROVISIONAL" in out.read_text(encoding="utf-8")
    assert load_polar(out).provisional is True


def test_write_pol_includes_header_comment(tmp_path: Path) -> None:
    cells = {(60.0, 10.0): 6.3, (60.0, 12.0): 6.8}
    pol = polar_from_grid(cells, source="influxdb:boat/polar_baseline")
    out = tmp_path / "with_header.pol"
    write_pol(pol, out, header="exported 2026-09-22")
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# exported 2026-09-22\n")
    # A comment line doesn't break parsing.
    assert load_polar(out).speed(10.0, 60.0) == pytest.approx(6.3)
