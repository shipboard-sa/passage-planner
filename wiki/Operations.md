<!--
Purpose: How to build passage-planner from a blank machine, run it (CLI and web), configure it, test
    it, keep it backed up, and recover from the failures seen so far.
Version: v2 (2026-10-07) - rebuild-from-spec pass: added build-from-blank, environment variables,
    testing, backup/restore and a failure-mode table; polar section rewritten (the InfluxDB it read is
    retired, briefings fall back to the PROVISIONAL file); config inventory moved to Data-Model.md;
    historical 2026-09-18 path-bug notes trimmed to one line. v1 (2026-09-17..22, no header).
Scope: PRODUCTION - engineering wiki for passage-planner (
Architecture: passage-planner documentation. Linked from Home.md. Day-to-day state and open items
    live in ../CHECKPOINT.md; this page is the procedures.
Invocation: n/a (reference doc)
-->

# Operations

## Build from a blank machine (Windows 11)

Prerequisites: Git, Python >= 3.11 (the live venv is 3.14.7; CI uses 3.13), Node.js (24.x in use;
the Docker build uses node:24), and - for Research with Claude - the Claude desktop app or the
Claude Code CLI. mx-inv must be checked out beside this repo (` if the web GUI is to
be reached through `:8000` and for the maintenance-due section.

```
cd D:\Documents\sailboat\code
git clone https://github.com/shipboard-sa/passage-planner.git routing     (private repo)
cd routing
python -m venv .venv
.venv\Scripts\pip install -e ".[dev,web]"
cd frontend && npm install && npm run build && cd ..
```

Then:

1. Set the persistent user environment variables below (`setx NAME value`, then start a **new**
   process tree - see the `setx` gotcha).
2. Check `config/vessel.toml` paths resolve: `[infra.ops_panel].db_path` ->
   ` `[infra.papers].records_dir` -> `D:\Documents\sailboat\Papers of
   Record` (`service.resolve_path(...).exists()`).
3. Smoke test: `.venv\Scripts\python.exe -m passage_planner.cli brief Navionics_archive_export.gpx
   --depart <tomorrow>T08:00 --to bahamas --no-papers` writes `var/briefings/fld_to_tc.*`.
4. Web: double-click ` from a normal window, open
   `http://localhost:8000/routing`.

`config/` and `data/` come with the clone (private repo). `var/` does not: briefings, research
results and caches are local (see Backup).

### Environment variables

| Variable | Needed for | Notes |
|---|---|---|
| `HYLAS_INFLUX_TOKEN` | the old InfluxDB polar read | still set on the laptop; the server is gone, so it no longer matters (see Polar) |
| `HYLAS_INFLUX_URL` | overrides `[infra.influx].url` | for the container plan; unset on the laptop |
| `CLAUDE_CODE_OAUTH_TOKEN` | Research with Claude | long-lived token from `claude setup-token`; persistent user variable |
| `HYLAS_CLAUDE_EXE` | optional | full path to `claude.exe` when discovery fails |
| `HYLAS_SIGNALK_TOKEN` | nothing yet | named in `[infra.signalk]`, unused |
| `VITE_API_TARGET` | frontend dev server | default `http://127.0.0.1:8100` |
| `MXINV_ROUTING_URL` | mx-inv side | `http://127.0.0.1:8100`; set by `run-live.bat`; without it mx-inv mounts no `/routing` |
| `MXINV_ROUTING_BRIEFINGS_DIR` | mx-inv side | where the cockpit looks for briefings; default ` |

Values never go in the repo, `config/`, or this wiki.

## Running the CLI

From ` (`.venv\Scripts\python.exe -m passage_planner.cli ...` or the `passage-planner`
script):

```
passage-planner brief ROUTE.gpx --depart 2026-10-05T07:00 --to bahamas --speed 6.5 \
    --crew "skipper,mate" --watch-hours 6 [--compare] [--float-plan --float-plan-crew "skipper,mate"]
    [--fleet id,id | --solo] [--logistics-file F.md] [--parker-file F.txt] [--no-tropical ...]
passage-planner solve ROUTE.gpx --depart ... --out var/solved.gpx        # EXPERIMENTAL
passage-planner sweep ROUTE.gpx --depart ... --out var/sweep.md          # EXPERIMENTAL
passage-planner export-polar [--out var/polar_baseline.pol]
passage-planner fleet | papers --trip international | depths-template ROUTE.gpx
passage-planner ciq-template --to "usa,bahamas" | logistics-template --ports "West End;George Town"
passage-planner influx ping|measurements|position|latest
python -m passage_planner.arrival [merge] data/arrival_places.toml [FILE.toml ...]
```

`brief` writes `var/briefings/<stem>.md|.html|.docx` (stem = route name in the GPX). A bare
`--depart` is local time at WP1. Skip flags: `--no-tropical`, `--no-tides`, `--no-notices`,
`--no-advisories`, `--no-maintenance`, `--no-papers`. `--help` on any subcommand is the full list.
**The cockpit tablet shows the newest CLI briefing**, so a briefing meant for the watch should be
generated with the CLI (or copied into `var/briefings/`).

## Running the web GUI

**Production**: double-click ` from a normal window (not from inside a
Claude session - a process started there inherits a stale environment). It builds both frontends,
starts this backend on `127.0.0.1:8100` in a minimized window titled "Routing GUI backend (internal,
port 8100)" with `--reload --reload-dir backend --reload-dir src`, then mx-inv on `0.0.0.0:8000`.
Python edits go live by themselves; frontend edits need `npm run build` (or a relaunch). If 8100 is
already listening, the launcher leaves it alone - kill that process to pick up new environment
variables.

**Headless through the live API**: `POST /routing/api/logistics-research` (`ports`
newline-separated and newline-terminated, `roles` JSON, `route`, `fleet`), poll `GET
/routing/api/logistics-research/{id}`, then `POST /routing/api/brief` with `logistics_markdown`. Do
not edit `src/` or `backend/` while a job runs (the reload kills it).

**Scratch backend without touching production**: `uvicorn backend.main:app --host 127.0.0.1 --port
8101` and, in `frontend/`, `VITE_API_TARGET=http://127.0.0.1:8101 npm run dev -- --port 5176
--strictPort`. Do not stop 8100/8000 - they are the live stack.

**Dev**: `uvicorn backend.main:app --reload --port 8100` + `npm run dev` (5174). `.claude/launch.json`
(at `D:\Documents\sailboat\`) has `routing-backend` and `routing-frontend` via `dev.cmd` wrappers.

## Research with Claude

Enabled when the CLI is found and `CLAUDE_CODE_OAUTH_TOKEN` is visible to the 8100 process
(`GET /api/logistics-research/status` says why not). Every run asks for consent (it uses the
subscription's weekly allotment; about 2.5-4 min and $0.50-0.90 API-equivalent per port). Ports run
one at a time; cancel kills the running process; finished ports are saved to
`var/logistics/research_<jobid>_<port>.md`. **Review before submitting**: numbers and prices vary
between runs. Qualify ambiguous names with brackets, e.g. `Spanish Wells (Eleuthera)`.

## Polar: current state and how to change it

- `[performance].polar_source = "derived"` and `[infra.influx].enabled = true` are still set, but the
  InfluxDB at `192.168.1.10:8086` was retired with the old automation stack (2026-10-06). Every
  briefing now warns *"could not read polar_baseline from InfluxDB ...; using baseline.pol"* and uses
  the hand-built **PROVISIONAL** `config/baseline.pol`; section 2 shows the file path.
- The derived 15 x 11 grid used 2026-09-18..22 was never exported to this repo. On the NOLA ->
  Gulfport test route it gave 51% sail against 0% for the PROVISIONAL file.
- To use a better polar now: drop a `.pol` file (format in [Data Model](Data-Model.md#polar-files-pol))
  in `config/`, point `polar_file` at it and set `polar_source = "baseline"`. Where the boat's real
  polar should come from from now on is open (Roadmap).
- Each attempt waits for the dead endpoint before falling back (connect/read timeout,
  `read_timeout_s = 10`); setting `enabled = false` removes the wait and the warning.

## Testing

| Check | Command (from ` | What it pins |
|---|---|---|
| Unit + integration tests | `.venv\Scripts\python.exe -m pytest -q` | 561 tests (2026-10-07), 38 files under `tests/`: every module, the backend endpoints through FastAPI's TestClient (`test_backend_*.py`), CLI wiring (`test_cli.py`), the shipped `data/arrival_places.toml` (validator, boxes, duplicates) |
| Lint | `-m ruff check src tests backend` | rules E, F, W, I, UP, B, SIM, RUF; line length 100 |
| Types | `-m mypy src` (strict) | run for both `--platform win32` and `--platform linux` (CI is Linux) |
| Frontend helpers | `node checks/logisticsSections.check.ts` (from `frontend/`) | section read/replace/remove |
| Frontend build | `npm run build` (`tsc -b && vite build`) | type-checks the TSX |
| UI | browser, through `http://localhost:8000/routing` | no frontend test runner; inject a GPX via `DataTransfer`, set React inputs with the native value setter |

Rules: tests use fakes for every HTTP API (no live network by design) and a scratch copy of `config/`
for anything that writes (see `tests/test_backend_fleet.py`); a test that writes the real `config/`
is a bug. Tests that assume a secret is unset must `monkeypatch.delenv` it.

**Observed 2026-10-07:** the suite took about 4 min (it was about 8 s on 2026-09-18); the slowest
tests are all in `test_cli.py` (15-59 s each). Probable cause, not verified: those tests load the
real `config/`, where InfluxDB is still enabled, and wait on the dead `192.168.1.10:8086` endpoint.
Disabling the Influx read (or making the CLI tests use a scratch config) should restore the speed.

## Backup and restore

| What | Where it lives | Backed up by |
|---|---|---|
| Code, `config/`, `data/`, wiki | git, pushed to GitHub `shipboard-sa/passage-planner` (private) | GitHub; the laptop's `D:\Documents` copy |
| `var/briefings/`, `var/web_briefings/` | local only | regenerable from route + time (but the forecast changes) |
| `var/logistics/` (researched and hand-edited port logistics) | local only, gitignored | **ASSUMED - ask the owner** whether `D:\Documents\sailboat` is in a backup set |
| `var/cache/` | local | regenerable |
| Environment variables | Windows user registry | re-create from their sources (`claude setup-token`) |

Restore = clone, build (above), copy back `var/logistics/` if it was saved. Nothing else is
stateful: `_RUNS` and research jobs are in memory by design.

## Known failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `/routing` returns 502 "Routing GUI backend isn't running" | the 8100 process is not running | look for its minimized window; relaunch `run-live.bat` |
| `/routing` 404 | mx-inv started without `MXINV_ROUTING_URL` | start mx-inv through `run-live.bat` |
| Research button disabled, "token not set" | the 8100 process predates the env var (`setx` reaches only new process trees); or it was started from inside a Claude session | close the 8100 window and relaunch from a normal window (or fully quit and reopen the desktop app) |
| Research job vanished mid-run | `--reload` restarted the backend after a code edit | re-run; finished ports are in `var/logistics/` |
| Briefing warns "could not read polar_baseline" | InfluxDB retired (expected until re-pointed) | see Polar |
| Section 7c PENDING | mx-inv DB path wrong, or mx-inv renamed `tasks` columns | check `[infra.ops_panel].db_path`; compare the query in Data Flows (5) |
| Section 6 Document readiness PENDING | papers directories not found | check `[infra.papers]` paths (two levels up from ` |
| `--compare` or `sweep` fails "not JSON" / "unreachable" | Open-Meteo burst throttling | wait, retry, or drop `--compare` |
| State Department block "older copy" | cadataapi 429 or old edition served | expected; it retries several times and keeps the newest |
| Web build assets 404 at `:8100/` | production build uses `base: '/routing/'` | use `:8000/routing` or the dev server |
| A port list split wrongly | a picked label contains a comma | send ports newline-terminated (`logistics.split_ports`) |
| Stray `config/depth_soundings.json` after a manual proxy test | a test wrote the real config | delete it; use a scratch config |
| CIQ fee wrong | registries drift | re-verify `ciq/data.py` each season (BVI changed 2026-08-01) |

Other gotchas: `InfluxConfig.token()` returns the env value even when disabled (the `influx`
subcommands need it) - do not "fix" it. The Claude desktop app is an MSIX package with a virtualized
`%APPDATA%`; `research.find_claude_exe()` searches both paths, so a check from inside a session
proves nothing about what the backend sees. Government lookups (FCC ULS, USCG PSIX) lag overnight.
Another tool (Claude Cowork) may also edit this tree: check `git status` before committing.

## History note

On 2026-09-18 three config paths were one level short after the `code/` reorg (`../Python/...`,
`../Papers of Record`); fixed to `../mx-inv/vessel_local.db`, `../../Papers of Record`,
`../../Insurance`. Any new default path should be checked with `service.resolve_path(...).exists()`.
