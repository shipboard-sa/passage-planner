"""Data model for the depth / clearance / tide-gate assessment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

METERS_TO_FEET = 3.28084


@dataclass(frozen=True, slots=True)
class TideStation:
    station_id: str
    name: str
    lat: float
    lon: float
    distance_nm: float  # from the waypoint it was matched to

    @property
    def is_distant(self) -> bool:
        """A station this far off has a different range and phase — treat with care."""
        return self.distance_nm > 25.0


@dataclass(frozen=True, slots=True)
class TideExtreme:
    time: datetime          # UTC
    height_ft: float        # above MLLW
    kind: str               # "H" or "L"


@dataclass(frozen=True, slots=True)
class TideWindow:
    start: datetime
    end: datetime
    threshold_ft: float     # the height-of-tide the window satisfies


@dataclass(frozen=True, slots=True)
class ChartedDepth:
    """Best available charted depth near a waypoint, at chart datum (MLLW for US)."""

    depth_ft: float
    datum: str              # "MLLW", "LAT", …
    source: str             # "NOAA ENC US5MIACC.000", "operator: Navionics screenshot", …
    is_operator_supplied: bool
    survey_date: str = ""   # ENC SORDAT when known
    notes: str = ""


@dataclass(frozen=True, slots=True)
class WaypointHydro:
    name: str
    lat: float
    lon: float
    eta: datetime                       # UTC
    leg_index: int

    station: TideStation | None
    tide_at_eta_ft: float | None        # interpolated height of tide at the ETA
    extremes: tuple[TideExtreme, ...]   # the hi/lo bracketing the ETA (up to 4)

    charted: ChartedDepth | None
    ocean_depth_ft: float | None        # coarse GEBCO depth (positive ft), for triage only
    deep_water: bool                    # GEBCO says clearly deeper than any draft concern
    required_charted_ft: float          # fleet best-case draft (lifting keels up) + UKC
    required_keeldown_ft: float         # fleet with every lifting keel DOWN + UKC
    gate_window: TideWindow | None      # when the crossing clears, if a depth is known
    marginal: bool                      # charted + tide at ETA is below required_charted_ft

    @property
    def clearance_at_eta_ft(self) -> float | None:
        """Charted depth + height of tide at the ETA, minus what the fleet needs.

        Positive = clears with margin to spare. None if depth is unknown.
        """
        if self.charted is None or self.tide_at_eta_ft is None:
            return None
        return self.charted.depth_ft + self.tide_at_eta_ft - self.required_charted_ft

    @property
    def needs_screenshot(self) -> bool:
        """A precise depth is missing and GEBCO can't rule out a draft problem."""
        return self.charted is None and not self.deep_water


@dataclass(frozen=True, slots=True)
class HydroReport:
    generated: datetime
    required_charted_ft: float       # fleet best-case (lifting keels up) + UKC
    required_keeldown_ft: float      # fleet with lifting keels down + UKC
    required_clearance_ft: float
    waypoints: tuple[WaypointHydro, ...]

    @property
    def fleet_has_lifting_keel(self) -> bool:
        return self.required_keeldown_ft > self.required_charted_ft + 1e-6

    @property
    def any_marginal(self) -> bool:
        return any(w.marginal for w in self.waypoints)

    @property
    def missing_depth(self) -> tuple[WaypointHydro, ...]:
        return tuple(w for w in self.waypoints if w.needs_screenshot)
