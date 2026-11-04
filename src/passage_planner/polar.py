"""Sailing polar: load a ``.pol`` grid and interpolate target boat speed.

Purpose: represent a TWA x TWS boat-speed polar and interpolate/VMG-optimize
    against it, from either a `.pol` file or a `polar_baseline` InfluxDB grid.
Version: v4 (2026-09-22) - `write_pol()` split into `polar_to_pol_text()` (pure
    text, no filesystem write) + a thin `write_pol()` wrapper, so
    backend/main.py's fleet-lookup endpoint can hand a buddy boat's real
    polar to the frontend as text before anything is saved to disk.
Scope: PRODUCTION - passage-planner's polar model, used by `analysis.py`.
Architecture: passage-planner subsystem. Pure consumer, never derives/refines a
    polar itself - see `wiki/Design-Decisions.md`. `service.py` picks the
    vessel's own polar source (file vs `influx.InfluxReader.polar_baseline()`)
    per `[performance] polar_source`; a *buddy* boat's polar (see
    boat_lookup.py) is always a pasted-in ORC certificate, never derived.
    `write_pol()`/`polar_to_pol_text()` let a derived or pasted polar be
    handed to third-party tools (qtVlm/OpenCPN/Windy) as a standard file.

The ``.pol`` format is the tab-separated grid qtVlm / Expedition / OpenCPN use:
a header row of TWS values (the first cell is an ignored corner label), then one
row per TWA whose first cell is the angle and the rest are target boat speeds in
knots. ``#`` comment lines and blank lines are skipped. Whitespace, tab, comma,
and semicolon all work as separators.

:meth:`Polar.speed` does bilinear interpolation over the (TWA, TWS) grid,
clamping to the axis extremes. TWA is folded to 0..180 — the polar is assumed
port/starboard symmetric (see the boat's ``Polar-Methodology`` wiki page).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import PolarError

_SEP = re.compile(r"[,\t; ]+")


@dataclass(frozen=True, slots=True)
class Polar:
    twa_axis: tuple[float, ...]          # ascending, degrees
    tws_axis: tuple[float, ...]          # ascending, knots
    grid: tuple[tuple[float, ...], ...]  # grid[i_twa][j_tws] = boat speed, knots
    source: str                          # file path or description
    provisional: bool                    # True until replaced by measured data

    def speed(self, tws_kn: float, twa_deg: float) -> float:
        """Interpolated target boat speed (knots) for a wind speed and angle."""
        twa = abs(((twa_deg + 180.0) % 360.0) - 180.0)  # fold to 0..180
        i0, i1, fi = _bracket(self.twa_axis, twa)
        j0, j1, fj = _bracket(self.tws_axis, max(0.0, tws_kn))
        g = self.grid
        top = g[i0][j0] + fj * (g[i0][j1] - g[i0][j0])
        bot = g[i1][j0] + fj * (g[i1][j1] - g[i1][j0])
        return top + fi * (bot - top)

    def best_upwind(self, tws_kn: float) -> tuple[float, float]:
        """(TWA, VMG) that maximises upwind velocity-made-good at this TWS."""
        return self._best_vmg(tws_kn, upwind=True)

    def best_downwind(self, tws_kn: float) -> tuple[float, float]:
        """(TWA, VMG) that maximises downwind velocity-made-good at this TWS."""
        return self._best_vmg(tws_kn, upwind=False)

    def _best_vmg(self, tws_kn: float, *, upwind: bool) -> tuple[float, float]:
        best_twa = 0.0
        best_vmg = 0.0
        lo, hi = (30.0, 90.0) if upwind else (90.0, 180.0)
        twa = lo
        while twa <= hi + 1e-9:
            angle = twa if upwind else 180.0 - twa
            vmg = self.speed(tws_kn, twa) * math.cos(math.radians(angle))
            if vmg > best_vmg:
                best_vmg, best_twa = vmg, twa
            twa += 1.0
        return best_twa, best_vmg


def _bracket(axis: tuple[float, ...], value: float) -> tuple[int, int, float]:
    """Return (lower index, upper index, fraction) for ``value`` on ``axis``."""
    if value <= axis[0]:
        return 0, 0, 0.0
    if value >= axis[-1]:
        last = len(axis) - 1
        return last, last, 0.0
    for k in range(len(axis) - 1):
        if axis[k] <= value <= axis[k + 1]:
            span = axis[k + 1] - axis[k]
            frac = 0.0 if span <= 0.0 else (value - axis[k]) / span
            return k, k + 1, frac
    last = len(axis) - 1
    return last, last, 0.0


def _tokens(line: str) -> list[str]:
    return [t for t in _SEP.split(line.strip()) if t]


def load_polar(path: str | Path, *, provisional: bool | None = None) -> Polar:
    """Load a ``.pol`` file.

    ``provisional`` overrides the auto-detection (a leading ``# PROVISIONAL``
    comment, or the filename containing 'provisional').
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PolarError(f"polar file not found: {p}") from exc
    except OSError as exc:
        raise PolarError(f"cannot read {p}: {exc}") from exc

    auto_provisional = "provisional" in p.name.lower()
    data_lines: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            if "provisional" in s.lower():
                auto_provisional = True
            continue
        data_lines.append(line)

    if len(data_lines) < 2:
        raise PolarError(f"{p}: need a header row plus at least one TWA row")

    header = _tokens(data_lines[0])
    try:
        tws_axis = tuple(float(x) for x in header[1:])
    except ValueError as exc:
        raise PolarError(f"{p}: header TWS values are not all numeric: {header[1:]}") from exc
    if len(tws_axis) < 2:
        raise PolarError(f"{p}: need at least 2 TWS columns")
    if list(tws_axis) != sorted(tws_axis) or len(set(tws_axis)) != len(tws_axis):
        raise PolarError(f"{p}: TWS columns must be strictly ascending: {tws_axis}")

    twa_vals: list[float] = []
    grid: list[tuple[float, ...]] = []
    for lineno, line in enumerate(data_lines[1:], start=2):
        cells = _tokens(line)
        if len(cells) != len(tws_axis) + 1:
            raise PolarError(
                f"{p}:{lineno}: expected {len(tws_axis) + 1} values "
                f"(TWA + {len(tws_axis)} speeds), got {len(cells)}"
            )
        try:
            row = [float(c) for c in cells]
        except ValueError as exc:
            raise PolarError(f"{p}:{lineno}: non-numeric value in row") from exc
        twa_vals.append(row[0])
        speeds = tuple(row[1:])
        if any(s < 0.0 for s in speeds):
            raise PolarError(f"{p}:{lineno}: negative boat speed")
        grid.append(speeds)

    if list(twa_vals) != sorted(twa_vals) or len(set(twa_vals)) != len(twa_vals):
        raise PolarError(f"{p}: TWA rows must be strictly ascending: {twa_vals}")
    if not (twa_vals[0] >= 0.0 and twa_vals[-1] <= 180.0):
        raise PolarError(f"{p}: TWA rows must lie within 0..180, got {twa_vals[0]}..{twa_vals[-1]}")

    is_provisional = auto_provisional if provisional is None else provisional
    return Polar(
        twa_axis=tuple(twa_vals),
        tws_axis=tws_axis,
        grid=tuple(grid),
        source=str(p),
        provisional=is_provisional,
    )


def polar_to_pol_text(polar: Polar, *, header: str | None = None) -> str:
    """``polar`` as tab-separated ``.pol`` text (the inverse of :func:`load_polar`,
    minus the filesystem write - see :func:`write_pol`). ``header`` is an optional
    comment line (e.g. provenance/date); a ``# PROVISIONAL`` comment is added
    automatically when ``polar.provisional`` is true, matching
    :func:`load_polar`'s auto-detection."""
    lines: list[str] = []
    if header:
        lines.append(f"# {header}")
    if polar.provisional:
        lines.append("# PROVISIONAL")
    tws_cells = "\t".join(f"{tws:g}" for tws in polar.tws_axis)
    lines.append(f"twa/tws\t{tws_cells}")
    for twa, row in zip(polar.twa_axis, polar.grid, strict=True):
        speeds = "\t".join(f"{s:g}" for s in row)
        lines.append(f"{twa:g}\t{speeds}")
    return "\n".join(lines) + "\n"


def write_pol(polar: Polar, path: str | Path, *, header: str | None = None) -> None:
    """Write ``polar`` as a ``.pol`` file (see :func:`polar_to_pol_text`) — the
    qtVlm / Expedition / OpenCPN grid format, readable by Windy's polar plugin
    too."""
    Path(path).write_text(polar_to_pol_text(polar, header=header), encoding="utf-8")


def polar_from_grid(
    cells: Mapping[tuple[float, float], float], *, source: str, provisional: bool = False
) -> Polar:
    """Build a :class:`Polar` from a sparse ``(twa_deg, tws_kn) -> boat_speed_kn``
    mapping — the shape the builder's ``polar_baseline`` InfluxDB measurement comes in
    (tags ``twa_deg``/``tws_kn``, field ``boat_speed_kn``), as opposed to the
    rectangular ``.pol`` file :func:`load_polar` reads. Every TWA row must carry
    the same set of TWS columns; a partial grid is rejected rather than
    interpolated over silently.
    """
    if not cells:
        raise PolarError(f"{source}: no polar samples")
    twa_axis = tuple(sorted({twa for twa, _tws in cells}))
    tws_axis = tuple(sorted({tws for _twa, tws in cells}))
    grid: list[tuple[float, ...]] = []
    for twa in twa_axis:
        row: list[float] = []
        for tws in tws_axis:
            key = (twa, tws)
            if key not in cells:
                raise PolarError(f"{source}: incomplete grid — missing TWA={twa} TWS={tws}")
            row.append(cells[key])
        grid.append(tuple(row))
    if not (twa_axis[0] >= 0.0 and twa_axis[-1] <= 180.0):
        raise PolarError(
            f"{source}: TWA values must lie within 0..180, got {twa_axis[0]}..{twa_axis[-1]}"
        )
    return Polar(
        twa_axis=twa_axis,
        tws_axis=tws_axis,
        grid=tuple(grid),
        source=source,
        provisional=provisional,
    )
