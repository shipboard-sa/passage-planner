<!--
Purpose: Front page of the passage-planner repo: what it does, how to run the CLI and the web GUI, where the detail lives.
Version: v3 (2026-10-07) - v3: polar read dead since the 10-06 Ship's Automation cut-over (PROVISIONAL fallback), 561 tests, backend in the test gate, the Colin/your-pi-hostname section replaced by the Ship's Automation boundary. v2 (2026-09-21) - web GUI, /routing proxy, Research with Claude; v1 was the 2026-09-10 CLI-only page.
Invocation: n/a (reference doc)
Scope: PRODUCTION - documentation for the passage-planning subsystem (D:/boat/routing/).
Architecture: Entry-level doc only. Current state and open items: CHECKPOINT.md. How things work: wiki/Home.md. Do not let this page grow into either.
-->

# passage-planner

[![CI](https://github.com/hutchel/passage-planner/actions/workflows/ci.yml/badge.svg)](https://github.com/hutchel/passage-planner/actions/workflows/ci.yml)

Route & arrival planning for the Your Boat Model *Your Vessel*. Manual-mode analysis
engine: weather routing, polar/fuel evaluation, customs/immigration arrival dossiers,
port logistics — output as a scannable Markdown briefing.

**Where to look:** [`CHECKPOINT.md`](CHECKPOINT.md) is the start-here file (current state,
run procedure, gotchas, open items). [`wiki/Home.md`](wiki/Home.md) documents how the
system is built. This page is the short version.

Cruising 2026–onward: NOLA → Pensacola → Tampa/St Pete shakedown (~Oct 2026),
then Florida → the Bahamas → down the island chain → Panama and the western Caribbean.

## Status — 2026-10-07

`ruff` + `mypy --strict` + `pytest` all green (561 tests). CI runs the same gate on
every push and pull request (see the badge above). Two ways in: the CLI below, or the
web form (next section), which calls the same generation code.

Give it a GPX route and a departure time; it fetches weather along the corridor at
future waypoint times, times the passage against a polar + the engine, and writes a
briefing (`.md` / styled `.html` / native `.docx`) on the phases of the vessel's
Departure/Arrival SOP:

| § | What it computes / pulls |
|---|---|
| 1 Executive | route, ETA, sail-vs-power split, fuel margin, critical hazards |
| 2 Assumptions | draft / air-draft / engine / polar, the fleet draft for shared stops (a lifting keel counts keel-up) |
| 3 Weather | NHC tropical (systems dead-reckoned, screened against the corridor) · along-route model summary · GFS-vs-ECMWF confidence (`--compare`) |
| 4 Routing | leg-by-leg: wind, seas, point of sail, sail/motor/motorsail, projected speed, ETA, flags |
| 5 Depth & notices | NOAA CO-OPS tides + best-effort ENC/GEBCO depth per waypoint, tide gates · **5d** NGA broadcast warnings (NAVAREA IV + HYDROLANT) within 75 NM, SAR broadcasts split out |
| 6 Arrival dossier | per-country CIQ — ports, pre-arrival system, fees, clearance offices, `verified` date, checked against official gov sources |
| 7 Logistics | fuel (computed) · fresh-water autonomy · per-port shore logistics (`--logistics-file`) · maintenance due this passage (from the vessel-ops-panel DB) · **7d** comms configuration (Starlink Personal Maritime/T-Mobile/Maya eSIM) per destination country, with Ocean Mode economics and billing-cycle date |
| 8 Watch plan | rotation from crew count + watch length, boat-time zone |
| 9 Float plan | auto-drafted summary for the shore contact + an optional auto-filled **USPS Float Plan PDF** (`--float-plan`) |

All briefing times render **local to the position they describe** (offline lat/lon →
IANA zone), so a passage that crosses a timezone line reads correctly on both sides.
**Polar: currently the PROVISIONAL fallback.** The config still points at the derived
polar in The InfluxDB (`192.168.1.21:8086`), which did not carry over when our own
Ship's Automation replaced his stack on 2026-10-06, so every briefing warns and uses the
hand-built `config/baseline.pol`; briefing §2 says which polar was used. Re-pointing to
Ship's Automation (and deciding who owns the polar) is CHECKPOINT Open 1.

`brief --float-plan` auto-fills the vessel's real USPS Float Plan PDF (346-field
AcroForm) from the same passage data — the whole itinerary block, fuel/water
autonomy, and the static vessel/operator/equipment facts in `config/float_plan.toml`.
The crew complement is a CLI-level choice per passage: `--float-plan-crew` looks
names up in `config/crew.toml` (the regulars) or takes an inline
`Name:phone:gender:YYYY-MM-DD` for a guest, independent of the briefing's own
`--crew` display names.

§7d recommends which of Starlink/T-Mobile/Maya eSIM to lean on at each destination
country, weighing local conditions (is Starlink even licensed there yet, T-Mobile
roaming speed, Maya coverage) against economics and calendar. The boat's confirmed plan
is Starlink **Personal Maritime** ($185/mo): unlimited data within 12 NM of any
coastline worldwide, metered "Ocean Mode" beyond that, and no 30-day-outside-home-region
cap (unlike Roam) — those provisions plus the monthly billing-cycle date
(`config/comms.toml`'s `starlink.billing_cycle_day`) are evergreen reminders, not
per-country facts.

### Web GUI

A form that collects the same inputs as `brief` and runs the same code in-process:
route GPX, departure, speed, destination country, crew (checkboxes from the
`config/crew.toml` roster plus one-off guests), watch length, an optional USPS float plan,
an Advanced section (the skip switches, `--compare` and the override files) and a
shore-logistics section. It is served behind the mx-inv app: launch
`D:/boat/hylas-mx-inv/run-live.bat` and open `http://localhost:8000/routing` (the routing backend
itself listens only on `127.0.0.1:8100`, with `--reload`, so backend edits go live without
a manual restart; frontend edits need the launcher's build step or `npm run build`).

The shore-logistics section has a **Research with Claude** button that researches each
listed port on the web (headless Claude Code, on your own subscription; asks before
every run) and fills the template for you to review. It needs `CLAUDE_CODE_OAUTH_TOKEN`;
setup and behavior are in [`wiki/Operations.md`](wiki/Operations.md).

### CLI

```
passage-planner brief ROUTE.gpx --depart 2026-10-15T07:00 --to bahamas [--compare]
    [--float-plan --float-plan-crew "lee,crew_member,Guest:555-1212:M:1990-06-15" --food-days 21]
passage-planner depths-template ROUTE.gpx        # waypoint_depths.toml skeleton
passage-planner ciq-template --to "usa,bahamas"  # arrival-dossier skeleton
passage-planner logistics-template --ports "West End,George Town"   # per-port shore-logistics skeleton
passage-planner papers --trip international       # document-readiness check
passage-planner influx ping|position|measurements
```

Run these from `D:/boat/routing/` with the project venv (`.venv\Scripts\python.exe -m
passage_planner.cli ...` if the `passage-planner` script is not on PATH). `--help` on any
subcommand lists every option.

### Dev

```
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
.venv\Scripts\ruff check src tests backend
.venv\Scripts\python -m mypy src
```

`backend/main.py` is covered by `tests/test_backend_*.py`; still verify UI changes live in the
browser. The frontend (`frontend/`, Vite + React) builds with `npm run build`; it has no test
runner, its pure helpers are checked with `node checks/logisticsSections.check.ts`.

## How this relates to Ship's Automation (the boat's own data)

Since 2026-10-06 the boat's data lives in our own **Ship's Automation** on the Pi
`boat-automation` (192.168.1.21): SignalK on :3000 and a history read API on :8090.
the previous automation partner's original stack (and its InfluxDB `boat` bucket) is retired. This repo is
entirely the skipper's, runs on the mx-inv host, and today reads **no** live boat data - it
fetches route-corridor forecasts at future waypoint times itself. Re-pointing the
polar and the step 7 SOG / STW calibration at the history API is open (CHECKPOINT
Open 1; the boundary is documented in `wiki/Data-Flows-and-Interfaces.md`).

The instrument suite on N2K (Airmar DST810 depth / STW / water temp, Garmin masthead
wind via a GND 10, MSC 10 sat-compass, Reactor 40 autopilot, A-100 AIS) meets the
preconditions for an empirical polar once enough sail time is logged; for the Oct
delivery, `baseline.pol` governs.

## Vessel planning figures

Engine Yanmar 4JH80 (80 hp) · fuel ~200 gal (uncalibrated) · cruise 7.0 kn @ ~2.25 gph
(provisional) · **planning draft 8'0" + 2' UKC** · **air draft 70' → 75' clearance
required (no ICW, outside routes only)** · fresh water 150 gal (cooking + hygiene;
drinking is bottled) · beam 14'9" · watermaker installed, not yet commissioned.
