"""Command-line entry point: turn a GPX route into a passage briefing.

Purpose: `passage-planner` CLI - GPX route + config in, briefing (.md/.html/
    .docx) out.
Version: v3 (2026-09-22) - added `export-polar`; `brief --parker-file`
    (weather-routing step 6); `solve` and `sweep` (weather-routing steps 4-5,
    marked EXPERIMENTAL - first CLI surface for the corridor solver and the
    departure-window sweep). See `passage_planner.__version__` for the
    installed package version.
Scope: PRODUCTION - passage-planner's primary entry point.
Architecture: passage-planner subsystem. Thin argparse wrapper over
    passage_planner.service / analysis / hydro / logistics / papers etc.;
    backend/main.py's web UI calls the same underlying service function
    rather than shelling out to this CLI.

    passage-planner brief ROUTE.gpx --depart 2026-10-15T12:00Z --to bahamas

Writes ``<stem>.md``, ``.html`` and ``.docx`` into the output directory and
prints a one-line summary plus any warning-level hazards.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__
from .analysis import PassageAnalysis, Severity
from .ciq import ciq_template
from .clock import Clock, timezonefinder_resolver
from .config import AppConfig, load
from .errors import InfluxError, RoutingError, WeatherFetchError, WeatherParseError
from .fleet import library_path, load_fleet
from .hydro import assess_route, depths_template
from .influx import InfluxReader
from .isochrone import solve_passage, to_gpx
from .logistics import logistics_template, split_ports
from .papers import check_papers
from .passage import PassagePlan
from .polar import write_pol
from .route import Waypoint, parse_gpx
from .service import export_polar, parse_depart, resolve_path, resolve_polar, run_brief
from .sweep import SweepResult, render_sweep_markdown, sweep_departures
from .weather.field import Field, corridor_grid, fetch_field


def _clock(app: AppConfig) -> Clock:
    """Boat clock: per-waypoint civil zone, falling back to the configured zone."""
    return Clock(app.vessel.infra.timezone, timezonefinder_resolver())


def _parse_depart(text: str, clock: Clock, start: Waypoint) -> datetime:
    """Parse ``--depart``; a value with no zone is read as local time at ``start``
    (prints a note to stderr in that case — see ``service.parse_depart``)."""
    try:
        return parse_depart(text, clock, start)
    except RoutingError as exc:
        raise SystemExit(f"--depart: {exc}") from exc


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="passage-planner")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("brief", help="generate a passage briefing from a GPX route")
    b.add_argument("route", type=Path, help="GPX route file (Navionics / OpenCPN export)")
    b.add_argument("--depart", required=True, help="ISO 8601 departure, e.g. 2026-10-15T12:00Z")
    b.add_argument("--speed", type=float, default=6.5, help="nominal planning speed, kn")
    b.add_argument("--to", dest="country", default=None, help="destination country (CIQ dossier)")
    b.add_argument("--crew", default="Skipper,Mate", help="comma-separated crew names")
    b.add_argument("--watch-hours", type=float, default=6.0, help="watch length, hours")
    b.add_argument("--step-nm", type=float, default=15.0, help="forecast sample spacing, NM")
    b.add_argument("--config", type=Path, default=Path("config"), help="config directory")
    b.add_argument("--out-dir", type=Path, default=Path("var/briefings"), help="output directory")
    b.add_argument("--stem", default=None, help="output filename stem (default: route name)")
    b.add_argument("--no-tropical", action="store_true", help="skip the NHC tropical check")
    b.add_argument("--compare", action="store_true",
                   help="add the ECMWF-vs-GFS forecast confidence check (one extra pull)")
    b.add_argument("--no-tides", action="store_true", help="skip the NOAA tide/depth assessment")
    b.add_argument("--no-notices", action="store_true",
                   help="skip the NGA broadcast-warning (NTM) corridor check")
    b.add_argument("--no-advisories", action="store_true",
                   help="skip the U.S. State Department advisory / embassy lookup")
    b.add_argument("--no-maintenance", action="store_true",
                   help="skip the ops-panel PM-due lookup")
    b.add_argument("--no-papers", action="store_true", help="skip the document-readiness check")
    b.add_argument("--papers-file", type=Path, default=None,
                   help="papers.toml with doc issue/expiry dates")
    b.add_argument("--depths-file", type=Path, default=None, help="operator waypoint_depths.toml")
    b.add_argument("--ciq-file", type=Path, default=None, help="Markdown for the arrival dossier")
    b.add_argument("--logistics-file", type=Path, default=None,
                   help="filled shore-logistics Markdown (see `logistics-template`)")
    b.add_argument("--parker-file", type=Path, default=None,
                   help="pasted a marine forecaster / MWXC forecast text (optional, section 3d; "
                        "compared against the model's peak wind)")
    b.add_argument("--comms-config", type=Path, default=None,
                   help="comms.toml (default: <config>/comms.toml)")
    b.add_argument("--float-plan", action="store_true",
                   help="also auto-fill the USPS Float Plan PDF for this passage")
    b.add_argument("--float-plan-crew", default=None,
                   help="crew complement for the float plan (comma-separated roster keys, or "
                        "'Name:phone:gender:YYYY-MM-DD' for a guest not in crew.toml); "
                        "defaults to --crew")
    b.add_argument("--food-days", type=float, default=None, help="days of food aboard")
    b.add_argument("--float-plan-config", type=Path, default=None,
                   help="float_plan.toml (default: <config>/float_plan.toml)")
    b.add_argument("--crew-file", type=Path, default=None,
                   help="crew.toml roster (default: <config>/crew.toml)")
    b.add_argument("--float-plan-template", type=Path, default=None,
                   help="blank USPS Float Plan PDF "
                        "(default: ../../Papers of Record/Beneteau/float plan template.pdf)")
    b.add_argument("--float-plan-out", type=Path, default=None,
                   help="output PDF path (default: <out-dir>/<stem>_float_plan.pdf)")
    b.add_argument("--fleet", default=None,
                   help="boats travelling with you: comma-separated fleet ids (see "
                        "`passage-planner fleet`). Default: the boats marked as usual companions")
    b.add_argument("--solo", action="store_true",
                   help="travelling alone: ignore the fleet list for this briefing")
    b.add_argument("--print", dest="to_stdout", action="store_true", help="also print the Markdown")

    fl = sub.add_parser("fleet", help="list the boats that may travel with you (fleet ids)")
    fl.add_argument("--config", type=Path, default=Path("config"), help="config directory")

    d = sub.add_parser(
        "depths-template", help="emit a waypoint_depths.toml skeleton for a route"
    )
    d.add_argument("route", type=Path)
    d.add_argument("--depart", required=True, help="ISO 8601 departure")
    d.add_argument("--speed", type=float, default=6.5)
    d.add_argument("--config", type=Path, default=Path("config"))
    d.add_argument("--out", type=Path, default=None, help="write here instead of stdout")

    pp = sub.add_parser("papers", help="document-readiness check against the papers directories")
    pp.add_argument("--trip", choices=["domestic", "international"], default="international")
    pp.add_argument("--config", type=Path, default=Path("config"))
    pp.add_argument("--papers-file", type=Path, default=None)
    pp.add_argument("--days", type=int, default=14, help="passage-end offset from today")

    q = sub.add_parser("ciq-template", help="emit an arrival-dossier skeleton for countries")
    q.add_argument("--to", dest="countries", required=True,
                   help="comma-separated countries the route touches")
    q.add_argument("--out", type=Path, default=None, help="write here instead of stdout")

    lg = sub.add_parser("logistics-template",
                        help="emit a shore-logistics skeleton for a passage's ports")
    lg.add_argument("--ports", required=True,
                    help="comma-separated ports / stops on this passage")
    lg.add_argument("--route", type=Path, default=None,
                    help="optional GPX, only used to name the passage in the header")
    lg.add_argument("--out", type=Path, default=None, help="write here instead of stdout")

    ep = sub.add_parser(
        "export-polar",
        help="write the builder's derived polar_baseline (InfluxDB) as a standard .pol file",
    )
    ep.add_argument("--config", type=Path, default=Path("config"), help="config directory")
    ep.add_argument("--out", type=Path, default=Path("var/polar_baseline.pol"),
                    help="output .pol path (default: var/polar_baseline.pol)")

    x = sub.add_parser("influx", help="diagnostics for the InfluxDB read connection")
    x.add_argument("action", choices=["ping", "measurements", "position", "latest"])
    x.add_argument("measurement", nargs="?", help="measurement name (for 'latest')")
    x.add_argument("--field", default="value")
    x.add_argument("--source", default=None, help="filter on the SignalK $source tag")
    x.add_argument("--grep", default=None, help="substring filter for 'measurements'")
    x.add_argument("--config", type=Path, default=Path("config"), help="config directory")

    sv = sub.add_parser(
        "solve", help="corridor-optimized route (weather-routing step 4) - EXPERIMENTAL"
    )
    sv.add_argument("route", type=Path, help="GPX route file")
    sv.add_argument("--depart", required=True, help="ISO 8601 departure, e.g. 2026-10-15T12:00Z")
    sv.add_argument("--config", type=Path, default=Path("config"), help="config directory")
    sv.add_argument("--along-step-nm", type=float, default=15.0,
                     help="solver's along-track hop spacing, NM")
    sv.add_argument("--cross-offsets-nm", default="-20,-10,0,10,20",
                     help="comma-separated cross-track offsets, NM (0 = on the drawn track)")
    sv.add_argument("--field-along-step-nm", type=float, default=40.0,
                     help="field FETCH grid spacing, NM - coarser than --along-step-nm on "
                          "purpose, see wiki/Design-Decisions.md on the rate-limit/accuracy "
                          "tradeoff")
    sv.add_argument("--field-cross-offsets-nm", default="-20,0,20",
                     help="comma-separated field fetch offsets, NM")
    sv.add_argument("--field-hours", default="0,24,48",
                     help="comma-separated hours-from-departure to fetch the field at")
    sv.add_argument("--out", type=Path, default=None,
                     help="write the solved path as GPX here (see also `--print`)")
    sv.add_argument("--print", dest="to_stdout", action="store_true",
                     help="also print the solved-path GPX")
    sv.add_argument("--no-cache", action="store_true", help="always fetch the field live")

    sw = sub.add_parser(
        "sweep", help="departure-window sweep (weather-routing step 5) - EXPERIMENTAL"
    )
    sw.add_argument("route", type=Path, help="GPX route file")
    sw.add_argument("--depart", required=True,
                     help="ISO 8601 start of the window, e.g. 2026-10-15T12:00Z")
    sw.add_argument("--config", type=Path, default=Path("config"), help="config directory")
    sw.add_argument("--window-days", type=float, default=5.0, help="sweep window, days")
    sw.add_argument("--step-hours", type=float, default=6.0, help="candidate spacing, hours")
    sw.add_argument("--models", default="gfs_seamless,ecmwf_ifs025",
                     help="comma-separated Open-Meteo model ids (see wiki/Design-Decisions.md - "
                          "the full window x model count has not been tried live at scale, "
                          "start small)")
    sw.add_argument("--out", type=Path, default=None, help="write the Markdown table here")
    return p


def _slug(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in name)
    return "_".join(keep.lower().split()) or "briefing"


def _cmd_brief(args: argparse.Namespace) -> int:
    try:
        datetime.fromisoformat(args.depart.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"--depart: not an ISO timestamp: {args.depart!r}") from exc
    result = run_brief(args)
    route = result.route
    _summarise(result.analysis, result.paths, result.clock, route.waypoints[0], route.waypoints[-1])
    if args.to_stdout:
        print()
        print(result.markdown)
    return 0


def _summarise(
    a: PassageAnalysis, paths: dict[str, Path], clock: Clock, start: Waypoint, end: Waypoint
) -> None:
    print(
        f"{a.route_name}: {a.total_nm:.0f} NM, "
        f"depart {clock.label(a.depart, start.lat, start.lon)} "
        f"-> ETA {clock.label(a.eta, end.lat, end.lon)} ({a.duration_h:.1f} h), "
        f"{a.sailing_fraction * 100:.0f}% sail, fuel {a.total_fuel_gal:.0f} gal"
    )
    warns = [h for h in a.hazards if h.severity is Severity.WARNING]
    for h in warns:
        print(f"  WARNING: {h.text}", file=sys.stderr)
    for kind, p in paths.items():
        print(f"  {kind:9s} {p}")


def _cmd_depths_template(args: argparse.Namespace) -> int:
    app = load(args.config)
    route = parse_gpx(args.route)
    depart = _parse_depart(args.depart, _clock(app), route.waypoints[0])
    plan = PassagePlan(route=route, depart_utc=depart, planning_speed_kn=args.speed)
    report = assess_route(app.vessel, plan)

    want = [w for w in report.waypoints if w.needs_screenshot or w.marginal]
    if not want:
        print("# No marginal or unknown-depth waypoints — nothing to fill in.")
        return 0
    prefill = {
        w.name.strip().lower(): w.charted
        for w in report.waypoints
        if w.charted is not None and not w.charted.is_operator_supplied
    }
    text = depths_template(
        [(w.name, w.lat, w.lon) for w in want], enc_prefill=prefill
    )
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(want)} waypoints)")
    else:
        print(text)
    return 0


def _cmd_papers(args: argparse.Namespace) -> int:
    from datetime import date, timedelta

    app = load(args.config)
    pp_cfg = app.vessel.infra.papers
    if pp_cfg is None:
        print("error: no [infra.papers] in the config", file=sys.stderr)
        return 2
    dirs = [resolve_path(args.config, pp_cfg.records_dir)] + [
        resolve_path(args.config, d) for d in pp_cfg.extra_dirs
    ]
    overrides = args.papers_file or resolve_path(args.config, pp_cfg.overrides_path)
    report = check_papers(
        dirs,
        trip=args.trip,
        passage_end=date.today() + timedelta(days=args.days),
        acquired_after=date.fromisoformat(pp_cfg.acquired_after),
        overrides_path=overrides if Path(overrides).is_file() else None,
    )
    for label, group in (("BLOCKING", report.blocking), ("CHECK/RENEW", report.warnings),
                         ("ON FILE", report.ok)):
        print(f"\n== {label} ==")
        for f in group:
            print(f"  {f.message}")
    if report.unclassified_current:
        kinds = sorted({d.kind.value for d in report.unclassified_current})
        print(f"\n(also found, not on the {args.trip} checklist: {', '.join(kinds)})")
    return 1 if report.blocking else 0


def _cmd_ciq_template(args: argparse.Namespace) -> int:
    countries = [c.strip() for c in args.countries.split(",") if c.strip()]
    text = ciq_template(countries)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(countries)} countries)")
    else:
        print(text)
    return 0


def _cmd_fleet(args: argparse.Namespace) -> int:
    app = load(args.config)
    boats = load_fleet(library_path(app.config_dir), app.vessel.buddy_boats)
    if not boats:
        print("no boats in the fleet list (add them in the web form's Travelling with screen)")
        return 0
    for b in boats:
        drafts = (
            f"draws {b.draft_up_ft:g} ft keel up / {b.draft_ft:g} ft down"
            if b.keel_up_known else f"draws {b.draft_ft:g} ft"
        )
        usual = "  [usual companion]" if b.usual_companion else ""
        print(f"{b.id}: {b.name} ({b.description}); {drafts}; air draft "
              f"{b.air_draft_ft:g} ft{usual}")
    return 0


def _cmd_logistics_template(args: argparse.Namespace) -> int:
    ports = split_ports(args.ports)
    if not ports:
        raise SystemExit("--ports: give at least one port")
    route_name = parse_gpx(args.route).name if args.route else None
    text = logistics_template(ports, route_name=route_name)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(ports)} ports)")
    else:
        print(text)
    return 0


def _cmd_export_polar(args: argparse.Namespace) -> int:
    app = load(args.config)
    try:
        polar = export_polar(app)
    except InfluxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_pol(polar, args.out, header=f"exported from InfluxDB polar_baseline: {polar.source}")
    print(f"wrote {args.out} ({len(polar.twa_axis)} TWA x {len(polar.tws_axis)} TWS cells)")
    return 0


def _cmd_solve(args: argparse.Namespace) -> int:
    app = load(args.config)
    route = parse_gpx(args.route)
    clock = _clock(app)
    depart = _parse_depart(args.depart, clock, route.waypoints[0])
    polar = resolve_polar(app, args.config, lambda msg: print(f"warning: {msg}", file=sys.stderr))

    field_grid = corridor_grid(
        route, along_step_nm=args.field_along_step_nm,
        cross_offsets_nm=tuple(float(x) for x in args.field_cross_offsets_nm.split(",")),
    )
    field_times = [depart + timedelta(hours=float(h)) for h in args.field_hours.split(",")]
    cache_dir = None if args.no_cache else Path("var/cache")

    async def fetch() -> Field:
        return await fetch_field(field_grid, field_times, route.name, cache_dir=cache_dir)

    try:
        field = asyncio.run(fetch())
    except (WeatherFetchError, WeatherParseError, RoutingError) as exc:
        print(f"error: field fetch failed: {exc}", file=sys.stderr)
        return 1

    result = solve_passage(
        app.vessel, polar, field, route, depart,
        along_step_nm=args.along_step_nm,
        cross_offsets_nm=tuple(float(x) for x in args.cross_offsets_nm.split(",")),
    )
    wp0, wp1 = route.waypoints[0], route.waypoints[-1]
    print(
        f"{result.route_name}: {result.total_nm:.0f} NM, "
        f"depart {clock.label(result.depart, wp0.lat, wp0.lon)} -> "
        f"ETA {clock.label(result.eta, wp1.lat, wp1.lon)} ({result.duration_h:.1f} h)"
    )
    print(
        f"  sail {result.sail_nm:.0f} NM / motor {result.motor_nm:.0f} NM / "
        f"motorsail {result.motorsail_nm:.0f} NM, fuel {result.total_fuel_gal:.0f} gal"
    )
    for w in result.warnings:
        print(f"  WARNING: {w}", file=sys.stderr)
    gpx = to_gpx(result)
    if args.out:
        args.out.write_text(gpx, encoding="utf-8")
        print(f"  wrote {args.out}")
    if args.to_stdout:
        print()
        print(gpx)
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    app = load(args.config)
    route = parse_gpx(args.route)
    clock = _clock(app)
    depart = _parse_depart(args.depart, clock, route.waypoints[0])
    polar = resolve_polar(app, args.config, lambda msg: print(f"warning: {msg}", file=sys.stderr))
    models = tuple(m.strip() for m in args.models.split(",") if m.strip())
    if not models:
        raise SystemExit("--models: give at least one model id")

    async def go() -> SweepResult:
        return await sweep_departures(
            app.vessel, polar, route, depart,
            window_days=args.window_days, step_hours=args.step_hours, models=models,
        )

    result = asyncio.run(go())
    md = render_sweep_markdown(result)
    if args.out:
        args.out.write_text(md, encoding="utf-8")
        print(f"wrote {args.out} ({len(result.rows)} solved, {len(result.failures)} failed)")
    else:
        print(md)
    return 0 if result.rows else 1


def _cmd_influx(args: argparse.Namespace) -> int:
    app = load(args.config)
    if not app.vessel.infra.influx.enabled:
        print(
            "note: [infra.influx].enabled is false — trying anyway "
            "(set HYLAS_INFLUX_TOKEN and flip enabled once verified)",
            file=sys.stderr,
        )
    with InfluxReader(app.vessel.infra.influx) as reader:
        try:
            if args.action == "ping":
                ok = reader.ping()
                print("influx: reachable and token accepted" if ok else "influx: NOT reachable")
                return 0 if ok else 1
            if args.action == "measurements":
                names = reader.measurements()
                if args.grep:
                    names = [n for n in names if args.grep.lower() in n.lower()]
                print(f"{len(names)} measurements:")
                for n in names:
                    print(f"  {n}")
                return 0
            if args.action == "position":
                fix = reader.latest_fix(source=args.source)
                if fix is None:
                    print("no recent GPS fix in InfluxDB")
                    return 1
                print(f"{fix.lat:.5f}, {fix.lon:.5f}  ({fix.time:%Y-%m-%d %H:%MZ}, age {fix.age})")
                return 0
            if args.action == "latest":
                if not args.measurement:
                    print("error: 'latest' needs a measurement name", file=sys.stderr)
                    return 2
                s = reader.latest(args.measurement, field=args.field, source=args.source)
                if s is None:
                    print(f"no recent value for {args.measurement}")
                    return 1
                print(f"{args.measurement} = {s.value}  ({s.time:%Y-%m-%d %H:%MZ})")
                return 0
        except InfluxError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 2


def main(argv: list[str] | None = None) -> int:
    # Briefings and templates contain en/em dashes etc.; the Windows console
    # defaults to cp1252 and would crash on print().
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")

    args = _build_parser().parse_args(argv)
    try:
        if args.command == "brief":
            return _cmd_brief(args)
        if args.command == "depths-template":
            return _cmd_depths_template(args)
        if args.command == "ciq-template":
            return _cmd_ciq_template(args)
        if args.command == "logistics-template":
            return _cmd_logistics_template(args)
        if args.command == "fleet":
            return _cmd_fleet(args)
        if args.command == "papers":
            return _cmd_papers(args)
        if args.command == "influx":
            return _cmd_influx(args)
        if args.command == "export-polar":
            return _cmd_export_polar(args)
        if args.command == "solve":
            return _cmd_solve(args)
        if args.command == "sweep":
            return _cmd_sweep(args)
    except (RoutingError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
