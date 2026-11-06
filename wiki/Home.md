<!--
Purpose: Front page of the passage-planner engineering wiki - what the system is, page index, reading
    order for a rebuild, facts at a glance.
Version: v2 (2026-10-07) - rebuild-from-spec pass: reframed from the the builder / your-pi-hostname boundary to
    the current boat (Ship's Automation on boat-automation); added Requirements and Data Model pages;
    at-a-glance refreshed (561 tests, CI, interim host, polar fallback); history paragraph brought to
    date. v1 (2026-09-17..22, no header).
Scope: PRODUCTION - engineering wiki for passage-planner ( pushed to GitHub with the
    code (private repo).
Architecture: passage-planner documentation entry point. Current state and open items:
    ../CHECKPOINT.md. Every wiki page is linked from here.
Invocation: n/a (reference doc)
-->

# Hylas Routing - Wiki

Engineering documentation for `passage_planner`, the pre-departure passage planner for your vessel
Swallow 2 (your vessel): GPX route in, a weather-informed, boat-specific briefing out - leg-by-leg
sail/motor plan, fuel, depth and tide, warnings, arrival paperwork, shore logistics, maintenance,
comms, watch and float plan - from a CLI or a web form served behind mx-inv. This wiki is meant to
be enough, with the code, to rebuild the subsystem to the same behaviour.

**Audience**: whoever maintains it next - the owner, Claude in a later session, or another engineer. The
web GUI's counterpart is mx-inv (` same machine); the boat's automation is Ship's
Automation on the Pi `boat-automation` (192.168.1.10), which this project does **not** read yet
(see [Data Flows](Data-Flows-and-Interfaces.md)).

## Pages (suggested reading order for a rebuild)

1. **[Requirements](Requirements.md)** - purpose, users, functional requirements, the owner's standing
   rules, vessel planning figures, non-goals.
2. **[Architecture](Architecture.md)** - processes, module map, how CLI and web share
   `service.run_brief()`, frontend, CI, deployment topology.
3. **[Data Model](Data-Model.md)** - every config, data, cache and output file with fields and units;
   the briefing's section format.
4. **[Data Flows & Interfaces](Data-Flows-and-Interfaces.md)** - every boundary: boat data (Ship's
   Automation, the retired InfluxDB read), external APIs, pasted inputs, mx-inv's database and
   cockpit, the papers folders, the full web API table, the Claude research subprocess.
5. **[Logic Flows](Logic-Flows.md)** - timing and the refine loop, sail/motor decision, current,
   every flag and threshold, fleet rules, papers, depth/tide, CIQ, maintenance, comms, the corridor
   solver, the sweep, a marine forecaster, research jobs.
6. **[Operations](Operations.md)** - build from a blank machine, environment variables, running,
   the polar today, testing, backup/restore, failure modes.
7. **[Design Decisions](Design-Decisions.md)** - the why, so a settled question is not re-litigated.
8. **[Roadmap](Roadmap.md)** - done, open (including the re-point to Ship's Automation and step 7),
   decided-not-to-build.
9. **[Arrival places](Arrival-Places.md)** - the Caribbean arrival pick list (310 places, 34
   countries), the picker, maintenance of the data file.
10. **[Logistics research design](Logistics-Research-Design.md)** - "Research with Claude": design
    record, decisions, live results.
11. **[ActiveCaptain Integration](ActiveCaptain-Integration.md)** - Garmin's crowd-sourced marina,
    fuel, bridge, hazard and anchorage data: API workflow, tile-based SQLite export model, data
    schema, briefing wiring, configuration, limitations.

## At a glance

| | |
|---|---|
| **Stack** | Python 3.11+ library + CLI (`argparse`); FastAPI backend + React 19 / TypeScript / Vite frontend (optional `web` extra) |
| **Repo** | ` its own git repo, GitHub `shipboard-sa/passage-planner` (private), branch `main` |
| **Run (CLI)** | `passage-planner brief ROUTE.gpx --depart 2026-10-15T07:00 --to bahamas` -> `var/briefings/` |
| **Run (web)** | ` -> `http://<host>:8000/routing` (mx-inv proxies to `127.0.0.1:8100`) |
| **Host today** | the owner's laptop (interim; the Ship's PC is away for repair), beside mx-inv |
| **Output** | `.md` / `.html` / `.docx` briefing (sections 1-9), optional filled USPS Float Plan PDF, `solve` GPX, `sweep` table |
| **Tests** | 561 passing (`pytest -q`, 2026-10-07), ruff clean, `mypy --strict`; GitHub Actions CI (quality + Docker image job) |
| **Auth** | none; LAN / tailnet trust boundary, exposure = mx-inv's `:8000` |
| **External data** | keyless public APIs (Open-Meteo, NHC, NGA, NOAA, opentopodata, State Dept); Garmin ActiveCaptain (free API key, tile-based SQLite); Claude Code CLI on the owner's subscription for research |
| **Polar** | PROVISIONAL `config/baseline.pol` (fallback) - the InfluxDB `polar_baseline` it used to read was retired 2026-10-06; re-point open |
| **Status** | complete and in use; `solve`/`sweep` EXPERIMENTAL; step 7 (current calibration) waits on the shakedown passage |

## History in one paragraph

Built 2026-09-08 as a CLI (config, GPX, passage timing, Open-Meteo, NHC, polar, leg analyzer,
Markdown/HTML/docx export). Then: papers readiness, hydro (GEBCO/ENC/tides), the CIQ registry, comms
and logistics advisories, the USPS float plan. 2026-09-17: `service.run_brief()` extracted and the
FastAPI + React web form built, served behind mx-inv's `:8000/routing`. 2026-09-18: the live polar
read from the builder's InfluxDB (`polar_baseline`) and the crew roster. 2026-09-21: arrival pick list (310
places), State Department block, depth check, fleet list, Research with Claude. 2026-09-22:
weather-routing steps 1-6 (export-polar, currents, corridor field, isochrone solver, departure sweep,
a marine forecaster), fleet boat lookup, Documents and Comms forms, terse-facts briefing style.
2026-09-24: Docker readiness. 2026-10-06: the boat's Pi was rebuilt as Ship's Automation and the old
InfluxDB went away, leaving the polar on its fallback. Detail: `CHECKPOINT.md` (current) and
`history/CHECKPOINT_ARCHIVE_2026-09-21.md` (grep only).
