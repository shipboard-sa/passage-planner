"""Departure-window sweep (weather-routing step 5).

Purpose: solve the drawn route's fixed-route analysis at every candidate
    departure time in a window, on one or more forecast models, and
    tabulate ETA/fuel/peak wind/sail fraction so the skipper can compare
    departure times instead of planning against just one.
Version: v1 (2026-09-22)
Scope: PRODUCTION - passage-planner subsystem, not yet wired into the CLI or
    web backend (no caller invokes this yet).
Architecture: passage-planner subsystem. Each candidate departure/model pair
    is one `analysis.analyze()` pass over a fresh `OpenMeteoClient.fetch()` -
    the same building blocks `service.run_brief()` already uses for a single
    briefing, called in a loop instead of once.

**Why a single `analyze()` pass per candidate, not `refine()`'s full
convergence loop**: `refine()` re-fetches the forecast up to
`max_iterations` times per call to converge the ETA against the corrected
leg speeds. A sweep of the roadmap's stated shape (every 6 h for 5 days, on
two models) is already 40 candidate solves; multiplying that by up to 4
re-fetches each would be 160+ forecast fetches, a burst far beyond what the
step-3 spike found Open-Meteo's real rate limit tolerates. A single pass at
the vessel's fleet-target cruising speed is a reasonable planning-speed
starting point (the same speed the fleet list already uses as its
`sample_fixes` seed) and gives an ETA close enough for comparing departure
times against each other - the whole point of a sweep is relative ranking,
not the same per-departure precision a single briefing's `refine()` gives.

**Why this has not been live-tested at its full stated scale**: the
smallest live test run against the real route below (3 candidates x 1
model, 6 requests) succeeded cleanly, but a real 40-row sweep (80 requests)
has not been tried. Given the step-3 finding that ~52 requests in under 4
seconds tripped a 429, a caller running a full sweep should watch for
errors on the first attempt rather than assume it will complete - this
module does not yet add any pacing/throttling of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from .analysis import PassageAnalysis, Severity, analyze
from .config import VesselConfig
from .errors import WeatherFetchError, WeatherParseError
from .passage import PassagePlan
from .polar import Polar
from .route import Route
from .weather.openmeteo import OpenMeteoClient, OpenMeteoConfig

DEFAULT_MODELS: tuple[str, ...] = ("gfs_seamless", "ecmwf_ifs025")


@dataclass(frozen=True, slots=True)
class SweepRow:
    """One candidate departure/model's result."""

    depart: datetime
    model: str
    eta: datetime
    duration_h: float
    total_nm: float
    fuel_gal: float
    fuel_shortfall: bool
    peak_wind_kn: float
    sailing_fraction: float
    warning_count: int


@dataclass(frozen=True, slots=True)
class SweepFailure:
    """A candidate departure/model whose forecast fetch or analysis failed -
    kept separate from :class:`SweepRow` so the row fields never need to be
    optional."""

    depart: datetime
    model: str
    error: str


@dataclass(frozen=True, slots=True)
class SweepResult:
    route_name: str
    window_start: datetime
    window_end: datetime
    rows: tuple[SweepRow, ...]
    failures: tuple[SweepFailure, ...] = ()

    def best(self, *, prefer: str = "duration_h") -> SweepRow | None:
        """The best row by ``prefer`` (ascending - lower is better for every
        current field: duration_h, fuel_gal, peak_wind_kn, warning_count).
        Rows with a fuel shortfall are excluded unless every row has one."""
        candidates = [r for r in self.rows if not r.fuel_shortfall] or list(self.rows)
        if not candidates:
            return None
        return min(candidates, key=lambda r: _field(r, prefer))


def _field(row: SweepRow, name: str) -> Any:
    return getattr(row, name)


def candidate_departures(
    start: datetime, *, window_days: float = 5.0, step_hours: float = 6.0
) -> list[datetime]:
    """Departure times from ``start`` to ``start + window_days``, every
    ``step_hours`` (endpoints included)."""
    if window_days <= 0.0:
        raise ValueError("window_days must be > 0")
    if step_hours <= 0.0:
        raise ValueError("step_hours must be > 0")
    end = start + timedelta(days=window_days)
    out: list[datetime] = []
    t = start
    while t <= end:
        out.append(t)
        t += timedelta(hours=step_hours)
    return out


def _warning_count(analysis: PassageAnalysis) -> int:
    return sum(1 for h in analysis.hazards if h.severity is Severity.WARNING)


async def sweep_departures(
    vessel: VesselConfig,
    polar: Polar,
    route: Route,
    start: datetime,
    *,
    window_days: float = 5.0,
    step_hours: float = 6.0,
    models: tuple[str, ...] = DEFAULT_MODELS,
    sample_step_nm: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> SweepResult:
    """Solve ``route`` at every candidate departure in the window, on every
    model, sequentially (not concurrently - see module docstring on request
    pacing). A candidate's fetch failure is recorded as a
    :class:`SweepFailure` rather than aborting the rest of the sweep."""
    if not models:
        raise ValueError("models must not be empty")
    times = candidate_departures(start, window_days=window_days, step_hours=step_hours)

    own_client = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    rows: list[SweepRow] = []
    failures: list[SweepFailure] = []
    try:
        for depart in times:
            plan = PassagePlan(
                route=route, depart_utc=depart,
                planning_speed_kn=vessel.performance.fleet_target_avg_kn,
            )
            fixes = plan.sample_fixes(sample_step_nm)
            for model in models:
                cfg = OpenMeteoConfig(model=model, marine_model=model)
                try:
                    forecast = await OpenMeteoClient(config=cfg).fetch(
                        fixes, route.name, client=http
                    )
                except (WeatherFetchError, WeatherParseError) as exc:
                    failures.append(SweepFailure(depart=depart, model=model, error=str(exc)))
                    continue
                result = analyze(vessel, plan, forecast, polar)
                rows.append(SweepRow(
                    depart=depart, model=model, eta=result.eta, duration_h=result.duration_h,
                    total_nm=result.total_nm, fuel_gal=result.total_fuel_gal,
                    fuel_shortfall=result.fuel_shortfall, peak_wind_kn=result.peak_wind_kn,
                    sailing_fraction=result.sailing_fraction,
                    warning_count=_warning_count(result),
                ))
    finally:
        if own_client:
            await http.aclose()

    return SweepResult(
        route_name=route.name, window_start=times[0], window_end=times[-1],
        rows=tuple(rows), failures=tuple(failures),
    )


def render_sweep_markdown(result: SweepResult) -> str:
    """A terse Markdown table of the sweep, best-first flagged, for a
    briefing appendix or a standalone report - not yet wired into
    `briefing.py`."""
    best = result.best()
    lines = [
        f"# Departure-window sweep — {result.route_name}",
        "",
        f"Window: {result.window_start:%Y-%m-%d %H:%M} to {result.window_end:%Y-%m-%d %H:%M} UTC "
        f"— {len(result.rows)} solved, {len(result.failures)} failed.",
        "",
        "| Depart (UTC) | Model | ETA (UTC) | Duration | Fuel gal | Peak wind kn | Sail % | "
        "Warnings |",
        "|---|---|---|--:|--:|--:|--:|--:|",
    ]
    for row in sorted(result.rows, key=lambda r: (r.depart, r.model)):
        flags = []
        if row.fuel_shortfall:
            flags.append("⚠️ fuel shortfall")
        if best is not None and row is best:
            flags.append("**best**")
        flag_text = f" {' '.join(flags)}" if flags else ""
        lines.append(
            f"| {row.depart:%Y-%m-%d %H:%M} | {row.model} | {row.eta:%Y-%m-%d %H:%M} | "
            f"{row.duration_h:.1f} h | {row.fuel_gal:.0f} | {row.peak_wind_kn:.0f} | "
            f"{row.sailing_fraction * 100:.0f} | {row.warning_count}{flag_text} |"
        )
    if result.failures:
        lines += ["", "**Failed:**"]
        for f in sorted(result.failures, key=lambda f: (f.depart, f.model)):
            lines.append(f"- {f.depart:%Y-%m-%d %H:%M} ({f.model}): {f.error}")
    return "\n".join(lines)
