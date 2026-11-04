"""Operator-supplied charted depths, and the template that asks for them.

Where NOAA ENC has no coverage (the Bahamas and most of the Caribbean), the
skipper reads the controlling depth off Navionics / Explorer Charts — by hand,
or by pasting a chart screenshot into a Claude conversation to have the
soundings transcribed — and drops it into ``waypoint_depths.toml``:

    [waypoints."Whale Cay Passage"]
    controlling_ft = 6.0
    datum          = "MLLW"
    source         = "Explorer Chartbook Abaco 2024, sand bar mid-channel"
    notes          = ""

Names match the route waypoints case-insensitively.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from ..errors import HydroError
from .model import ChartedDepth


def load_waypoint_depths(path: str | Path) -> dict[str, ChartedDepth]:
    p = Path(path)
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HydroError(f"waypoint depths file not found: {p}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise HydroError(f"{p}: {exc}") from exc

    table = raw.get("waypoints", {})
    if not isinstance(table, dict):
        raise HydroError(f"{p}: [waypoints] must be a table")

    out: dict[str, ChartedDepth] = {}
    for name, entry in table.items():
        if not isinstance(entry, dict):
            raise HydroError(f"{p}: [waypoints.{name}] must be a table")
        if "controlling_ft" not in entry:
            raise HydroError(f"{p}: [waypoints.{name}] is missing 'controlling_ft'")
        try:
            depth = float(entry["controlling_ft"])
        except (TypeError, ValueError) as exc:
            raise HydroError(f"{p}: [waypoints.{name}] controlling_ft is not a number") from exc
        out[name.strip().lower()] = ChartedDepth(
            depth_ft=depth,
            datum=str(entry.get("datum", "MLLW")),
            source="operator: " + str(entry.get("source", "hand-entered")),
            is_operator_supplied=True,
            notes=str(entry.get("notes", "")),
        )
    return out


def depths_template(
    waypoints: list[tuple[str, float, float]],
    *,
    enc_prefill: dict[str, ChartedDepth] | None = None,
) -> str:
    """A ``waypoint_depths.toml`` skeleton for the skipper to complete.

    ``waypoints`` is (name, lat, lon) for each waypoint that needs a depth.
    ``enc_prefill`` supplies a best-guess where NOAA ENC returned one.
    """
    prefill = enc_prefill or {}
    lines = [
        "# waypoint_depths.toml  --  fill controlling_ft for each waypoint below.",
        "# Read it off Navionics / Explorer Charts, or paste a chart screenshot",
        "# into a Claude conversation and have the soundings transcribed.",
        "# Datum is almost always MLLW (US) / LAT (elsewhere).",
        "",
    ]
    for name, lat, lon in waypoints:
        pre = prefill.get(name.strip().lower())
        lines.append(f'[waypoints."{name}"]')
        lines.append(f"# position: {lat:.5f}, {lon:.5f}")
        if pre is not None:
            lines.append(
                f"controlling_ft = {pre.depth_ft:.1f}  "
                f"# {pre.source} ({pre.survey_date or 'survey date n/a'}) - VERIFY"
            )
            lines.append(f'source = "{pre.source}"')
        else:
            lines.append("controlling_ft = 0.0   # <-- screenshot needed")
            lines.append('source = ""')
        lines.append('datum = "MLLW"')
        lines.append('notes = ""')
        lines.append("")
    return "\n".join(lines)
