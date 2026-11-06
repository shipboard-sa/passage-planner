"""Core "generate a briefing" logic, shared by the CLI and the web backend.

Purpose: build a passage briefing from a route + config, independent of
    whether the caller is `cli.py` or `backend/main.py`.
Version: v5 (2026-09-22) - added `resolve_buddy_polars()` (loads each fleet
    boat's own real polar, from a pasted ORC certificate - see
    boat_lookup.py - when one is on file) and wired it into `run_brief()`'s
    `refine()` call. v4: added `export_polar()` for the `export-polar` CLI
    command (fetches the derived polar with no soft fallback, unlike
    `resolve_polar`); wired `--parker-file` through to `BriefingInputs.parker_text`
    (weather-routing step 6).
Scope: PRODUCTION - passage-planner's shared brief-generation entry point.
Architecture: passage-planner subsystem. Extracted from `cli.py`'s `_cmd_brief`
    so the CLI and the FastAPI web backend hit identical logic; calls into
    analysis/briefing/hydro/notices/papers/ops/polar/route/weather.

:func:`run_brief` takes the same ``argparse.Namespace`` shape the ``brief``
subcommand parses (see ``cli.py``) and returns a :class:`BriefResult` instead
of printing to stdout — the caller (CLI or FastAPI route) decides how to
present it. Warnings that the CLI used to print straight to stderr are both
printed (so `passage-planner brief` keeps behaving exactly as before) and
collected, so a web caller can surface them in the response instead of a
terminal only it can see.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .activecaptain import ActiveCaptainClient, RoutePoi
from .analysis import PassageAnalysis, refine
from .briefing import BriefingInputs, build_briefing
from .clock import Clock, timezonefinder_resolver
from .comms import load_comms_config
from .config import AppConfig, VesselConfig, load
from .consular import ConsularReport, advisory_warnings, build_consular_report
from .errors import (
    ActiveCaptainError,
    CommsError,
    FloatPlanError,
    HydroError,
    InfluxError,
    MissingSecretError,
    NoticeError,
    OpsPanelError,
    PolarError,
    RoutingError,
)
from .export import write_briefing
from .fleet import apply_fleet, parse_ids
from .floatplan import (
    build_field_values,
    fill_float_plan_pdf,
    load_crew_roster,
    load_float_plan_config,
    resolve_crew,
)
from .hydro import assess_route, load_waypoint_depths
from .hydro.soundings import library_path, load_soundings, soundings_to_depths
from .influx import InfluxReader
from .notices import NoticeReport, fetch_nav_warnings_sync
from .ops import read_maintenance_due
from .papers import check_papers
from .passage import PassagePlan
from .polar import Polar, load_polar
from .route import Route, Waypoint, parse_gpx
from .weather import fetch_forecast_comparison, fetch_route_forecast, fetch_tropical_outlook_sync
from .weather.compare import ForecastComparison
from .weather.model import RouteForecast
from .weather.tropical import TropicalOutlook


@dataclass(frozen=True)
class BriefResult:
    route: Route
    analysis: PassageAnalysis
    clock: Clock
    markdown: str
    paths: dict[str, Path]
    warnings: list[str] = field(default_factory=list)


def clock_for(app: AppConfig) -> Clock:
    """Boat clock: per-waypoint civil zone, falling back to the configured zone."""
    return Clock(app.vessel.infra.timezone, timezonefinder_resolver())


def parse_depart(text: str, clock: Clock, start: Waypoint) -> datetime:
    """Parse a departure timestamp; a value with no zone is local time at ``start``."""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RoutingError(f"not an ISO timestamp: {text!r}") from exc
    if dt.tzinfo is None:
        dt = clock.localize(dt, start.lat, start.lon)
        print(
            f"note: departure read as local time at {start.name} "
            f"({clock.abbr(dt, start.lat, start.lon)})",
            file=sys.stderr,
        )
    return dt


def slug(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in name)
    return "_".join(keep.lower().split()) or "briefing"


def resolve_path(config_dir: Path, rel: str) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    return (config_dir.parent / rel) if config_dir.name == "config" else (config_dir / rel)


def resolve_polar(app: AppConfig, config_dir: Path, warn: Callable[[str], None]) -> Polar:
    """Load the boat's polar per ``[performance] polar_source``.

    ``"derived"`` means the builder's empirically-derived ``polar_baseline`` InfluxDB
    measurement — ground truth once it's there (this project never derives or
    refines a polar itself, see ``wiki/Design-Decisions.md``). Any failure to
    reach it (disabled, offline, no data yet) falls back to the static
    ``config/baseline.pol`` file, the same soft-failure shape every other
    optional data source in this pipeline uses — a polar source can never by
    itself block a briefing from generating.
    """
    perf = app.vessel.performance
    influx_cfg = app.vessel.infra.influx
    if perf.polar_source == "derived" and influx_cfg.enabled:
        try:
            with InfluxReader.from_app_config(app) as reader:
                derived = reader.polar_baseline()
            if derived is not None:
                return derived
            warn(
                "polar_source=derived but InfluxDB has no polar_baseline data yet; "
                "using baseline.pol"
            )
        except (InfluxError, MissingSecretError) as exc:
            warn(f"could not read polar_baseline from InfluxDB ({exc}); using baseline.pol")
    return load_polar(resolve_path(config_dir, perf.polar_file))


def resolve_buddy_polars(
    vessel: VesselConfig, config_dir: Path, warn: Callable[[str], None]
) -> dict[str, Polar]:
    """Each buddy boat's own real polar (from a pasted ORC certificate, see
    boat_lookup.py), keyed by boat id, for every boat in this passage's fleet
    that has one on file. A boat with none keeps analysis.py's generic
    multihull-upwind heuristic instead - a missing/unreadable polar file never
    blocks a briefing, same soft-failure shape as resolve_polar."""
    out: dict[str, Polar] = {}
    for buddy in vessel.buddy_boats:
        if not buddy.polar_file:
            continue
        try:
            out[buddy.id] = load_polar(resolve_path(config_dir, buddy.polar_file))
        except PolarError as exc:
            warn(f"{buddy.name}'s polar file could not be read ({exc}); using the "
                 "multihull heuristic instead")
    return out


def export_polar(app: AppConfig) -> Polar:
    """Fetch the builder's derived ``polar_baseline`` from InfluxDB for the
    ``export-polar`` CLI command. Unlike :func:`resolve_polar`, this never falls
    back to ``baseline.pol`` — exporting the provisional file as if it were the
    derived one would be misleading, so any failure (disabled, offline, no
    data yet) is raised to the caller instead of silently substituted.
    """
    influx_cfg = app.vessel.infra.influx
    if not influx_cfg.enabled:
        raise InfluxError("[infra.influx].enabled is false — nothing to export")
    with InfluxReader.from_app_config(app) as reader:
        derived = reader.polar_baseline()
    if derived is None:
        raise InfluxError("polar_baseline has no rows yet in InfluxDB")
    return derived


def _fleet_ids(args: argparse.Namespace) -> list[str] | None:
    """Which fleet boats this briefing travels with: ``--solo`` means none, ``--fleet``
    names them, otherwise the boats marked as usual companions."""
    if getattr(args, "solo", False):
        return []
    return parse_ids(getattr(args, "fleet", None))


def run_brief(args: argparse.Namespace) -> BriefResult:
    """Build a passage briefing. ``args`` has the same attributes as the
    parsed ``brief`` subcommand (see ``cli._build_parser``)."""
    warnings: list[str] = []

    def warn(message: str) -> None:
        warnings.append(message)
        print(f"warning: {message}", file=sys.stderr)

    app = apply_fleet(load(args.config), _fleet_ids(args))
    route = parse_gpx(args.route)
    clock = clock_for(app)
    depart = parse_depart(args.depart, clock, route.waypoints[0])
    plan = PassagePlan(route=route, depart_utc=depart, planning_speed_kn=args.speed)
    polar = resolve_polar(app, args.config, warn)
    buddy_polars = resolve_buddy_polars(app.vessel, args.config, warn)

    analysis = refine(
        app.vessel, plan, polar,
        lambda fixes: fetch_route_forecast(fixes, route.name),
        sample_step_nm=args.step_nm,
        buddy_polars=buddy_polars,
    )
    final_fixes = plan.sample_fixes(args.step_nm, analysis.leg_speeds)
    forecast: RouteForecast = fetch_route_forecast(final_fixes, route.name)

    comparison: ForecastComparison | None = None
    if getattr(args, "compare", False):
        try:
            comparison = fetch_forecast_comparison(final_fixes, route.name)
        except RoutingError as exc:
            warn(f"model comparison failed ({exc})")

    tropical: TropicalOutlook | None = None
    if not args.no_tropical:
        try:
            tropical = fetch_tropical_outlook_sync(route)
        except RoutingError as exc:
            warn(f"tropical check failed ({exc}); section left PENDING")

    hydro = None
    if not args.no_tides:
        try:
            # Depths read off a chart and saved through the web form apply by position;
            # an explicit --depths-file (by waypoint name) takes precedence over them.
            depths = soundings_to_depths(
                [(w.name, w.lat, w.lon) for w in route.waypoints],
                load_soundings(library_path(Path(args.config))),
            )
            if args.depths_file:
                depths.update(load_waypoint_depths(args.depths_file))
            hydro = assess_route(
                app.vessel, plan, leg_speeds=analysis.leg_speeds, waypoint_depths=depths
            )
        except HydroError as exc:
            warn(f"tide/depth assessment failed ({exc}); section left PENDING")

    notices = None
    if not args.no_notices:
        try:
            notices = fetch_nav_warnings_sync(route)
        except NoticeError as exc:
            warn(f"NGA notice check failed ({exc}); section left PENDING")
            notices = NoticeReport(
                route_name=route.name, generated=datetime.now(UTC), corridor_nm=75.0,
                areas_checked=("NAVAREA IV", "HYDROLANT"), warnings=(), total_in_force=0,
                unreachable=True,
            )

    consular: ConsularReport | None = None
    named_countries = [c.strip() for c in (args.country or "").split(",") if c.strip()]
    if named_countries and not getattr(args, "no_advisories", False):
        consular = build_consular_report(named_countries)
        if consular.unreachable:
            warn("State Department advisories unreachable; that block is left PENDING")
        else:
            for line in advisory_warnings(consular):
                warn(line)

    papers = None
    pp_cfg = app.vessel.infra.papers
    if not args.no_papers and pp_cfg is not None and pp_cfg.enabled:
        us_only = {"usa", "us", "united states", "usvi", "us virgin islands", "puerto rico", "pr"}
        named = [c.strip().lower() for c in (args.country or "").split(",") if c.strip()]
        trip = "international" if any(c not in us_only for c in named) else "domestic"
        overrides = args.papers_file or resolve_path(args.config, pp_cfg.overrides_path)
        try:
            from datetime import date

            dirs = [resolve_path(args.config, pp_cfg.records_dir)] + [
                resolve_path(args.config, d) for d in pp_cfg.extra_dirs
            ]
            papers = check_papers(
                dirs,
                trip=trip,
                passage_end=analysis.eta.date(),
                acquired_after=date.fromisoformat(pp_cfg.acquired_after),
                overrides_path=overrides if Path(overrides).is_file() else None,
            )
        except (RoutingError, ValueError) as exc:
            warn(f"document check failed ({exc})")

    maintenance = None
    op_cfg = app.vessel.infra.ops_panel
    if not args.no_maintenance and op_cfg is not None and op_cfg.enabled:
        try:
            maintenance = read_maintenance_due(
                resolve_path(args.config, op_cfg.db_path),
                analysis.depart.date(),
                analysis.eta.date(),
                soon_buffer_days=op_cfg.soon_buffer_days,
            )
        except OpsPanelError as exc:
            warn(f"maintenance lookup failed ({exc})")

    comms_config = None
    try:
        comms_config = load_comms_config(args.comms_config or (args.config / "comms.toml"))
    except CommsError as exc:
        warn(f"comms config failed ({exc}); section left generic")

    ac_pois: tuple[RoutePoi, ...] = ()
    ac_cfg = app.vessel.infra.activecaptain
    if ac_cfg is not None and ac_cfg.enabled:
        try:
            wps = [(w.lat, w.lon) for w in route.waypoints]
            client = ActiveCaptainClient(
                cache_dir=resolve_path(args.config, ac_cfg.cache_dir),
                api_key=ac_cfg.token(),
                env=ac_cfg.env,
                timeout_s=ac_cfg.timeout_s,
            )
            ac_pois = tuple(client.near_route(
                wps, radius_nm=ac_cfg.search_radius_nm,
            ))
        except (ActiveCaptainError, MissingSecretError) as exc:
            warn(f"ActiveCaptain lookup failed ({exc})")

    def read(path: Path | None) -> str | None:
        return path.read_text(encoding="utf-8") if path is not None else None

    inputs = BriefingInputs(
        vessel=app.vessel,
        plan=plan,
        analysis=analysis,
        forecast=forecast,
        crew=tuple(c.strip() for c in args.crew.split(",") if c.strip()),
        watch_hours=args.watch_hours,
        destination_country=args.country,
        hydro=hydro,
        notices=notices,
        consular=consular,
        maintenance=maintenance,
        papers=papers,
        comms_config=comms_config,
        forecast_comparison=comparison,
        tropical=tropical,
        ciq_markdown=read(args.ciq_file),
        logistics_markdown=read(args.logistics_file),
        clock=clock,
        parker_text=read(getattr(args, "parker_file", None)),
        activecaptain_pois=ac_pois,
    )
    markdown = build_briefing(inputs)

    stem = args.stem or slug(route.name)
    title = f"Passage Briefing - {route.name}"
    paths = write_briefing(markdown, args.out_dir, stem, title)

    if getattr(args, "float_plan", False):
        try:
            fp_path = args.float_plan_config or (args.config / "float_plan.toml")
            crew_path = args.crew_file or (args.config / "crew.toml")
            # NOTE: as of the code/ reorg this file actually lives under
            # Papers of Record/Beneteau/ (the old boat's folder) even though
            # it's a generic blank USPS form, not Beneteau-specific - flagged
            # to the owner, not moved without approval. See CHECKPOINT.md.
            template = args.float_plan_template or resolve_path(
                args.config, "../../Papers of Record/Beneteau/float plan template.pdf"
            )
            static = load_float_plan_config(fp_path)
            roster = load_crew_roster(crew_path)
            crew_arg = args.float_plan_crew or args.crew
            crew = resolve_crew(crew_arg.split(","), roster)
            values = build_field_values(
                vessel=app.vessel, plan=plan, analysis=analysis, clock=clock,
                static=static, crew=crew, food_days=args.food_days,
            )
            fp_out = args.float_plan_out or (args.out_dir / f"{stem}_float_plan.pdf")
            Path(fp_out).parent.mkdir(parents=True, exist_ok=True)
            written = fill_float_plan_pdf(template, fp_out, values)
            paths["float_plan"] = written
        except FloatPlanError as exc:
            warn(f"float-plan fill failed ({exc})")

    return BriefResult(
        route=route, analysis=analysis, clock=clock, markdown=markdown, paths=paths,
        warnings=warnings,
    )
