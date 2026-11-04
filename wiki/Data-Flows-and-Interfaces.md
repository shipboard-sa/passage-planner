<!--
Purpose: Every boundary passage-planner crosses - boat data, external APIs, sibling repos, files,
    the AI research subprocess, and its own web API - with contracts, failure behaviour and the
    counterparty for each.
Version: v3 (2026-10-18) - boat-data status: Influx read switched off 10-07 (config), polar owner =
    Ship's Automation (D-125); the SignalK re-point is what remains. v2 (2026-10-07) - rewritten for the rebuild-from-spec pass: the boat-data boundary now
    describes Ship's Automation on boat-automation (the builder's stack and its InfluxDB are retired; the
    routing reads are not yet re-pointed - marked open), the web API is a complete endpoint table,
    stale path notes in sections 5/6 fixed, sources.toml's real role stated. v1 (2026-09-18..22, no
    header) was framed around the the builder / your-pi-hostname coordination boundary.
Scope: PRODUCTION - engineering wiki for passage-planner (
Architecture: passage-planner documentation. Linked from Home.md. Process layout: Architecture.md;
    what happens to data inside: Logic-Flows.md; file formats: Data-Model.md.
Invocation: n/a (reference doc)
-->

# Data Flows & Interfaces

Every place data crosses a boundary - into this project, out of it, or between its own frontend and
backend. [Architecture](Architecture.md) covers process layout; [Logic Flows](Logic-Flows.md) what
happens to data once it is inside; [Data Model](Data-Model.md) the file formats.

```
   boat-automation (Pi, 192.168.1.10)         external public APIs (keyless HTTPS)
   Ship's Automation: SignalK :3000,           Open-Meteo forecast + marine, NHC, NGA MSI,
   history API :8090, rules engine :8200       NOAA CO-OPS / ENC, opentopodata GEBCO,
        |   (1) NOT READ today - re-point open  State Dept cadataapi
        x                                           | (3)
                                                    v
 GPX file (2) ----------------------->  +-------------------------------+
 mx-inv vessel_local.db (5, sqlite RO) ->|        passage_planner          |--> var/briefings/*.md  --> mx-inv cockpit (8)
 Papers of Record/, Insurance/ (6) ---->|  CLI + FastAPI :8100 + React  |--> var/web_briefings/<run>/ --> browser (7)
 pasted text: Parker, MarineTraffic,   |                               |--> claude -p subprocess (9) --> web (read-only tools)
   ORC (4) ---------------------------->+-------------------------------+
                                                    ^
                         browser --> mx-inv :8000 /routing/** --proxy--> 127.0.0.1:8100 (7)
```

## (1) Boat data: Ship's Automation (current) and the retired InfluxDB read

### What the boat runs now

On 2026-10-06 the boat's automation Pi was rebuilt as **Ship's Automation** on host
**boat-automation** (192.168.1.10). Relevant services (Ship's Automation decision D-117 and its
deploy docs; not this repo's code):

| Service | Address | What it offers | Auth |
|---|---|---|---|
| SignalK | `http://192.168.1.10:3000` | live boat state (SignalK REST / websocket) | read-only access allowed; writes need a device token |
| History read API | `http://192.168.1.10:8090` | `/api/signals`, `/api/latest?path=`, `/api/at?path=&t=`, `/api/series?path=&from=&to=&step=` over the boat's history (PostgreSQL + TimescaleDB; raw 30 days, 1-min rollups kept) | none (SELECT only, LAN + tailnet) |
| Rules engine API | `http://192.168.1.10:8200` | alarms, modes (used by mx-inv's cockpit) | token for writes |

**The InfluxDB server this project used to read (`192.168.1.10:8086`, org `your-org`, bucket
`boat`) no longer exists** - it was part of the previous automation stack (the previous automation builder's
"your-pi-hostname") and was not carried into Ship's Automation.

### What passage-planner actually reads today (verified in code 2026-10-07)

| Code | Points at | State |
|---|---|---|
| `influx.py` `InfluxReader` (Flux, `influxdb-client`) used by `service.resolve_polar()` for `polar_source = "derived"` | `[infra.influx].url` = `http://192.168.1.10:8086` (env `HYLAS_INFLUX_URL` overrides), token from env `HYLAS_INFLUX_TOKEN` | **dead endpoint, switched off 2026-10-07:** `config/vessel.toml` has `polar_source = "baseline"` and `[infra.influx] enabled = false`, so briefings use the PROVISIONAL `config/baseline.pol` without trying it (before that every briefing tried it, failed with `InfluxUnavailableError`, warned and fell back to the same file). Section 2 prints the polar path. |
| `service.export_polar()` (`export-polar`) | same | fails by design (no fallback) |
| CLI `influx ping/measurements/position/latest` | same | fails; diagnostic only |
| `[infra.signalk]` (`config.SignalKConfig`) | `https://signalk.your-pi-hostname.local`, `enabled = false` | parsed, **no code uses it** |
| SignalK :3000 and history API :8090 on boat-automation | - | **not read by any routing code** |

Consequences (status in [Roadmap](Roadmap.md)):

1. **Polar.** Briefings since the cut-over use the hand-estimated PROVISIONAL polar. The live
   `polar_baseline` grid (15 TWA x 11 TWS, read 2026-09-18 to 2026-09-22) was never saved in this
   repo (`var/polar_baseline.pol` does not exist). On the same test route that grid gave 51% sail
   against 0% for the PROVISIONAL file, so the difference is material. **Owner decided (the owner
   2026-10-07, Ship's Automation D-125):** Ship's Automation builds the measured polar and publishes a
   `.pol`; routing reads it and keeps `baseline.pol` as the fallback. Not yet built either side.
2. **Step 7 calibration** (forecast current vs the boat's own SOG minus STW) was written against
   InfluxDB; it must be re-pointed to the history API (`/api/series` over the SOG and STW paths).
3. **Config clean-up - Influx half done 2026-10-07** (`[infra.influx] enabled = false`,
   `polar_source = "baseline"`). Still open: point `[infra.signalk]` at `http://192.168.1.10:3000`
   (it still names the builder's external URL) and settle its credentials.

### Contract the old read used (kept for the re-point and for reading old briefings)

- Flux queries built by pure functions; SignalK path = `_measurement`, field always `value`;
  `cerbo.*` rows carry `value` and `text_value`, so every query filters `_field == "value"`; every
  query bounded by `range()` (30 days default, 10 years for the polar snapshot).
- `polar_baseline`: tags `twa_deg`, `tws_kn`, field `boat_speed_kn`, one row per cell sharing one
  timestamp; `polar.polar_from_grid()` rejects a ragged grid rather than filling gaps.
- Failure: 401/403 -> `InfluxAuthError`; connect/timeout -> `InfluxUnavailableError`;
  enabled-but-no-token -> `MissingSecretError`. `resolve_polar` catches `InfluxError` and
  `MissingSecretError`, so a polar source can never block a briefing.
- **No write path exists.** `InfluxReader` only queries. `route_forecast_measurement`,
  `write_timeout_s` and `local_buffer_path` are reserved config for a write-back that was never
  built (the issued token was read-only). Whether briefing output should be published to Ship's
  Automation at all is open.

## (2) Inbound: GPX route file

File read (CLI path argument; web multipart upload saved to `var/web_uploads/`, deleted after the
run). GPX 1.0/1.1, any namespace: `route.py` matches local tag names, no GPX library. `<rte>`
preferred, else all `<trk>/<trkseg>` points concatenated (Navionics exports). Missing waypoint names
become `WP1..WPn`. Points closer than 0.02 NM are merged. Fewer than two points, or no route/track,
raises `RouteContentError` (HTTP 400 on the web).

Outbound GPX: `isochrone.to_gpx()` writes a solved path as a GPX 1.1 `<rte>` (`solve --out`).

## (3) Inbound: external public APIs

All keyless. Each degrades its own briefing section to PENDING (or one waypoint's value to `None`)
on failure. **The URLs are constants in the modules; `config/sources.toml` is validated but not used
by any fetcher** ([Data Model](Data-Model.md#configsourcestoml-config_version--1)).

| Source | Endpoint | Used for | Module | Behaviour |
|---|---|---|---|---|
| Open-Meteo forecast | `api.open-meteo.com/v1/forecast` | hourly 10 m wind speed/direction/gust, MSL pressure at each fix; `models=gfs_seamless,ecmwf_ifs025` for `--compare`, `sweep` | `weather/openmeteo.py`, `weather/compare.py` | knots via `wind_speed_unit=kn`; 16-day horizon; retry/backoff; **throttles bursts** (seen as "not JSON" / "unreachable") |
| Open-Meteo marine | `marine-api.open-meteo.com/v1/marine` | wave height/direction/period, swell, `ocean_current_velocity` / `ocean_current_direction` (SMOC, about 8 km) | `weather/openmeteo.py` | current direction is "toward" (oceanographic) |
| NHC | `nhc.noaa.gov/CurrentStorms.json`, `nhc.noaa.gov/xml/TWOAT.xml` | active systems (dead-reckoned forward, screened against the corridor) and the Atlantic outlook text | `weather/tropical.py` | retry/backoff |
| NGA MSI | `msi.nga.mil/api/publications/broadcast-warn` | NAVAREA IV + HYDROLANT warnings within 75 NM of the route, SAR split out | `notices.py` | regex position extraction |
| NOAA CO-OPS | station list + `api.tidesandcurrents.noaa.gov/api/prod/datagetter` | nearest tide station, high/low predictions | `hydro/tides.py` | station list cached 30 days in `var/cache/coops_stations.json` |
| NOAA ENC Direct | `gis.charttools.noaa.gov/arcgis/rest/services/encdirect` (harbour/approach/coastal/general bands) | charted soundings near a waypoint, US waters only | `hydro/enc.py` | best effort |
| opentopodata | `api.opentopodata.org/v1/gebco2020` | coarse depth triage (deep water or not) | `hydro/bathy.py` | <= 100 points/call, >= 1.1 s between calls (free tier) |
| U.S. State Department | `cadataapi.state.gov/api/TravelAdvisories`, `/api/CountryTravelInformation` | advisory level/date/reason/link, embassy contact | `consular.py` | cached 24 h in `var/cache/state_dept/`; Cloudflare 429s on bursts (spaced, retried); sometimes serves an older edition, so fetched several times and the newest record per country wins; stale cache used with a note |

**Paid data:** none is used. Considered 2026-09-21: free winds and currents are good enough; money is
better spent on a human forecaster (a marine forecaster, optional paste-in) and possibly a short
PredictWind term as a solver benchmark (Roadmap). A Copernicus Marine account exists as a fallback
current source; nothing is built against it. the owner's Starlink/Windy subscriptions are outside this
code.

## (4) Inbound: pasted text (no fetch, by design)

| Paste | Where | Parsed by | Result |
|---|---|---|---|
| a marine forecaster / MWXC "Wx Update" email | CLI `brief --parker-file FILE` (no web box yet) | `marine_forecaster.py` | sections (TROPICS ... SWELLS) and the WIND section's peak knots, compared against the model peak; briefing 3d |
| MarineTraffic vessel Overview page text | Fleet form "Look up this boat" -> `POST /api/fleet/lookup` | `boat_lookup.parse_marinetraffic` | MMSI, call sign, flag, LOA, beam |
| ORC Sailboat Data certificate text | same | `boat_lookup.parse_orc` | LOA, beam, draft, displacement, and a polar from the VPP table |

Neither MarineTraffic nor ORC documents a public API; the person pastes what their browser already
shows, and nothing is saved until the form's Save.

## (5) Inbound: maintenance due, from mx-inv's database

Read-only `sqlite3.connect("file:<path>?mode=ro", uri=True)`; no import of mx-inv code (schema
coupling only). Path `[infra.ops_panel].db_path` = `../mx-inv/vessel_local.db` (resolves to
` correct since 2026-09-18). One query:
`SELECT system, task_name, period, interval_type, last_completed_date FROM tasks WHERE task_kind =
'recurring'`. `interval_type` `days` -> dated; `use` -> "every passage"; `hrs` -> listed, not dated
(engine hours not known to routing). Failure -> `OpsPanelError` -> warning, section 7c PENDING.
**Coupling risk:** a rename of `tasks` or these columns in mx-inv breaks 7c silently into PENDING.
mx-inv's database lives on whichever machine runs mx-inv; routing must run on the same machine
(sibling folders) for this read to work.

## (6) Inbound: papers-of-record directories

Read-only filename scan of `../../Papers of Record` and `../../Insurance` (resolve to
`D:\Documents\sailboat\Papers of Record` and `...\Insurance`). `papers/scan.py` classifies by
filename regex (never reads PDF content), `config/papers.toml` overrides win, `papers/checklist.py`
scores against the trip-type matrix. See [Logic Flows](Logic-Flows.md).

## (7) The web app's own API (backend `:8100`, reached as `/routing/api/...` through mx-inv)

Transport: JSON or multipart over HTTP. In production the browser talks only to mx-inv `:8000`;
mx-inv forwards `/routing/{path}` (GET, POST, PUT, DELETE; 120 s timeout; hop-by-hop headers
dropped; 502 with a plain message when 8100 is down) to `MXINV_ROUTING_URL` =
`http://127.0.0.1:8100`. The proxy is only mounted when that variable is set (mx-inv
`run-live.bat` sets it). No CORS middleware: same origin through the proxy, Vite proxy in dev. No
auth. Errors: FastAPI `{"detail": "..."}` with the status below.

| Method + path | Request | Response | Errors |
|---|---|---|---|
| `GET /api/health` | - | `{"status":"ok"}` (touches nothing; container healthcheck) | - |
| `POST /api/brief` | multipart: `route` (GPX file), `depart` (required, ISO or local), `speed` (6.5), `country` (comma-joined), `crew` (comma list), `watch_hours` (6), `logistics_markdown`, `float_plan`, `float_plan_crew`, `food_days`, `fleet` (ids), `compare`, `no_tropical`, `no_tides`, `no_notices`, `no_maintenance`, `no_advisories`, `no_papers`; files `papers_file`, `depths_file`, `ciq_file`, `comms_config` | `{id, route_name, total_nm, duration_h, sailing_fraction, total_fuel_gal, depart, eta (localized labels), warnings[], markdown, files[]}` | 400 `RoutingError` (bad GPX, bad depart, config) |
| `GET /api/brief/{id}/download/{kind}` | kind from `files[]` (`md`, `html`, `docx`, float-plan) | the file | 404 unknown run/kind or file gone (run index is in memory) |
| `GET /api/crew` | - | `[{key, name, phone, gender, dob, plb_uin}]` from `crew.toml` | - |
| `GET /api/logistics-template?ports=&route_name=` | ports newline/semicolon/comma separated | `{markdown}` | 400 no ports |
| `POST /api/logistics-coverage` | `{markdown}` | `{checked, unresearched:[{port, missing}]}` (form fails open on error) | - |
| `GET /api/logistics-research/status` | - | `{available, reason}` (exe found and token set) | - |
| `POST /api/logistics-research` | multipart: `ports` (newline-separated, newline-terminated), optional `route` GPX, `crew_count`, `roles` (JSON `{port: "check-in"\|"check-out"\|"fuel"}`), `fleet` | `{id}` | 400 no ports / bad GPX / bad roles; 409 a job is running; 503 not set up |
| `GET /api/logistics-research/{id}` | - | `{status, ports:[{name, status, markdown, gaps, resolved_as, error, warnings, seconds, cost_usd, turns, saved_as}], total_cost_usd, ...}`; form polls every 4 s | 404 |
| `DELETE /api/logistics-research/{id}` | - | `{cancelling: true}` (kills the running port's process; finished ports kept) | 404 |
| `GET /api/arrival-places` | - | `{places:[{id, label, name, island, country, region, lat, lon, kind, clearance, also_known_as, verified}], countries:[...]}` | 500 bad data file |
| `POST /api/arrival-places/suggest` | multipart `route` GPX, `limit` (6, max 15) | `{end:[place + distance_nm], start:[...]}` within 60 NM | 400 bad GPX |
| `POST /api/depth-check` | multipart `route`, `depart`, `speed`, `fleet` | `{route_name, required_ft, draft_ft, margin_ft, rows:[...]}` (only waypoints needing attention, plus saved matches) | 400 |
| `GET /api/depth-soundings` | - | `{soundings:[...]}` | 500 |
| `PUT /api/depth-soundings` | `{soundings:[{lat, lon, depth_ft, source, notes, label}]}` | updated library | 400 invalid |
| `DELETE /api/depth-soundings/{id}` | - | updated library | 404 |
| `GET /api/fleet` | - | `{boats:[...], hulls:[...], usual:[ids]}` | 500 |
| `PUT\|POST /api/fleet` | `{boat:{...BoatIn}, replaces?: id, polar_pol_text?: str}` | fleet response | 400 invalid |
| `POST /api/fleet/lookup` | `{marinetraffic_text, orc_text}` | `{mmsi, call_sign, flag, loa_ft, beam_ft, draft_ft, displacement_lb, messages[], polar_pol_text\|null}` (saves nothing) | - |
| `DELETE /api/fleet/{id}` or `POST /api/fleet/{id}/remove` | - | fleet response | 404 |
| `GET /api/papers` | - | `{documents:[{kind, label, path, issued, expires, note}]}` (all 16 kinds) | 500 |
| `PUT\|POST /api/papers` | `{documents:[{kind, path, issued, expires, note}]}` | same as GET | 400 unknown kind / bad date |
| `GET /api/comms` | - | flat `{starlink_plan, starlink_billing_cycle_day, starlink_priority_gb, starlink_ocean_mode_usd_per_gb, starlink_notes, tmobile_*, maya_esim_*, router_*}` | 500 |
| `PUT\|POST /api/comms` | same flat shape | same | 400 |
| `GET /` (and assets) | - | `frontend/dist` (StaticFiles, html=True) when built | - |

Every write endpoint has a POST twin because the mx-inv proxy forwarded only GET/POST until
2026-09-21; the proxy now forwards PUT and DELETE as well (mx-inv commit `b54d8c5`, in the live
branch). Type contract: `frontend/src/api/types.ts` mirrors these shapes by hand (no generator).

## (8) Outbound: the newest CLI briefing, read by mx-inv's cockpit

mx-inv's cockpit watch-handover screen reads the newest `var/briefings/*.md` (by mtime) and parses
the generated line, tropical headline and weather summary (`mx-inv backend/routers/cockpit.py`,
`parse_briefing`; folder overridable with `MXINV_ROUTING_BRIEFINGS_DIR`; flags a briefing as old).
It never triggers a run. Web-generated briefings (`var/web_briefings/`) are **not** seen by it.
Contract: keep the section headings in [Data Model](Data-Model.md#the-briefing-output-format)
stable, or change both repos together.

## (9) Outbound: the headless Claude Code CLI (Research with Claude)

One `claude -p --output-format json --allowedTools WebSearch WebFetch --permission-mode dontAsk
--max-budget-usd 5` subprocess per port, sequential, from a temp directory outside any project,
15-minute timeout, cancellable. Authenticated by the subscription token `CLAUDE_CODE_OAUTH_TOKEN`;
the child environment is stripped of API keys, other `CLAUDE_CODE_*` / `CLAUDE_PREVIEW_*` state and
anything named like a token, secret, password or API key (the OAuth token is the one exception).
Sent: port name, stop role, up to 20 route points, the boat's and fleet's public specs, crew count.
Nothing personal. The reply is untrusted text, validated into a `### <port>` section and shown for
review; finished ports are saved to `var/logistics/research_<jobid>_<port>.md` (gitignored). This is
the only place the project sends data to an AI service. Design: [Logistics research
design](Logistics-Research-Design.md).

## Interfaces this project deliberately does not have

- **No write to boat systems** (SignalK, history, rules engine) - read-only by design; the old
  InfluxDB write-back was never built.
- **No re-fetch trigger from mx-inv**; the cockpit only reads the last file.
- **No PDF renderer**; `.docx`/`.html`, PDF by export. The float plan is an AcroForm fill (`pypdf`).
- **No public or internet-facing listener**: 8100 is loopback only; exposure is exactly mx-inv's
  `:8000` (LAN, and the tailnet).
- **No scraping** of MarineTraffic, ORC or Noonsite.
