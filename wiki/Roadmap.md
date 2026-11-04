<!--
Purpose: current build status - what's done, what's open, what's explicitly
    deferred, for passage-planner.
Version: v12 (2026-10-18) - stale-record pass: polar owner = Ship's
    Automation (D-125) in Re-point 1 and the non-goal; dead Influx read
    marked done 10-07 (SignalK half still open); step 7 reads the history
    API; sweep = 42 solves; vessel-name item closed (numeral 2 in
    vessel.toml); Starlink item narrowed to the registration.
    v11 (2026-10-07) - rebuild-from-spec pass: new "Re-point to Ship's
    Automation" open block (the InfluxDB polar read is dead since the Pi was
    rebuilt 2026-10-06; step 7 must use the history API); stale open items
    closed (mx-inv proxy PUT/DELETE is in the live branch, Documents/Comms
    forms built, research proven and its try-list exercised, Example Buddy
    paste-in exists); Docker readiness and the sweep's real default size
    (21 x 2) recorded; polar non-goal marked open again. v10 (2026-09-22):
    fleet follow-ups done. v9: weather-routing steps 1-6 shipped.
Scope: PRODUCTION - engineering wiki, source of truth for *current* status
    (Design-Decisions.md holds the why, history/ the chronological detail).
Architecture: passage-planner subsystem documentation. See wiki/Home.md for
    the page index.
Invocation: n/a (reference doc)
-->

# Roadmap

Status as of **2026-10-07**. The planner is complete end to end and in use (FLD -> T&C briefing v2,
Gulf shakedown legs). Weather-routing steps 1-6 shipped 2026-09-22; `solve` and `sweep` are
CLI-only and EXPERIMENTAL; step 7 waits on shakedown data. The boat's automation Pi was rebuilt as
Ship's Automation on 2026-10-06 and the InfluxDB this project read the polar from went with the old
stack, so briefings currently use the PROVISIONAL polar - see "Re-point to Ship's Automation" below.
No code changes since 2026-09-24 (Docker readiness) apart from a naming sweep on 2026-10-07.

## Done

- **Briefing text trimmed to terse facts, no commentary** (2026-09-22): standing style
  rule, applied across `briefing.py`, `comms.py`, `consular.py`, `notices.py`,
  `weather/compare.py`, `weather/tropical.py`. Same pass added a per-country **Calendar
  check** to comms section 7d (suspend Starlink yes/no + date, from billing-cycle timing
  and whether Maya eSIM covers that country). See
  [Design Decisions](Design-Decisions.md#briefing-text-is-terse-facts-no-commentary).
- **Core CLI pipeline** (2026-09-08, 90 tests at the time): config loading,
  GPX ingest, great-circle passage timing, Open-Meteo weather fetch, NHC
  tropical screening, polar loader/interpolator, weather-interpolated leg
  analyzer, Markdown briefing assembly, Markdown→HTML/docx export.
- **Papers-of-record readiness** (`papers/`): filename scan + config
  overrides + per-trip-type required-document matrix, briefing §6
  sub-section.
- **Hydro/depth-clearance** (`hydro/`): bathymetry triage (GEBCO),
  best-effort ENC soundings (US waters), NOAA CO-OPS tide curve
  reconstruction, operator-supplied depth overrides
  (`waypoint_depths.toml`), combined into a per-waypoint go/no-go +
  tide-gate report, briefing §5.
- **CIQ arrival dossier** (`ciq/`): 7-country hand-verified registry,
  briefing §6.
- **Shore comms/logistics advisories** (`comms.py`, `logistics.py`):
  Starlink billing-cycle pacing, per-country connectivity notes, offline
  shore-logistics templating with a coverage-gap checker.
- **USPS Float Plan auto-fill** (`floatplan.py`): fills the real 346-field
  AcroForm PDF from config + the parsed route/crew.
- **Forecast-model comparison** (`weather/compare.py`): GFS-vs-ECMWF
  divergence classification, `--compare` flag, briefing §3c.
- **Web GUI v1** (2026-09-17): standalone FastAPI backend (`backend/main.py`,
  port 8100) + React/Vite frontend (`frontend/`, port 5174 dev), both
  calling the newly-extracted `service.run_brief()` — the same generation
  code the CLI uses, not a second implementation. Core-fields-only form
  (route GPX, depart, speed, country, crew, watch hours) plus a collapsible
  shore-logistics template-and-edit section, added the same session.
  Verified live end-to-end in a browser (real GPX upload, real API
  response, all three download links working, no console errors).
- **`service.py` refactor**: `cli.py`'s `_cmd_brief` extracted into
  `service.run_brief()`, verified byte-for-byte parity (all 237 tests
  green, ruff clean, `mypy --strict` clean).
- **`polar_baseline` wired from InfluxDB** (2026-09-18; **superseded 2026-10-06** - that InfluxDB
  was retired with the old automation stack, see Open "Re-point"): `polar_source =
  "derived"`; `InfluxReader.polar_baseline()` + `polar.polar_from_grid()`
  read the builder's live measurement, soft-falling-back to `config/baseline.pol`
  exactly like every other optional source. Live-verified against the real
  server. See [Operations](Operations.md#polar-current-state-and-how-to-change-it).
- **Reorg path bug fixed** (2026-09-18): `[infra.ops_panel].db_path` →
  `"../mx-inv/vessel_local.db"`, `[infra.papers].records_dir`/`extra_dirs`
  → `"../../Papers of Record"` / `["../../Insurance"]`. Verified against
  the real filesystem. See
  [Operations](Operations.md#history-note).
- **Real crew roster + float plan wired into the web GUI** (2026-09-18):
  new `GET /api/crew` (reads `config/crew.toml`); the form's old freeform
  "Crew (comma-separated)" text box (default `"Skipper,Mate"`, never
  matched the roster) is replaced with roster checkboxes + a guest-adder
  (name/phone/gender/DOB), plus a "Generate USPS float plan" checkbox +
  food-days field. Fixed a second reorg-path bug found along the way:
  `run_brief()`'s float-plan-template default resolved one level short
  *and* the real PDF turned out to have been swept into
  `Papers of Record/Beneteau/` by the directory reorg (flagged to the owner, not
  moved). Live-verified end-to-end: unchecked a roster member, added a
  guest, generated a real float-plan PDF, and confirmed by reading the
  filled AcroForm fields back that the roster member kept full data
  (age/phone/PLB UIN) while the guest correctly got name/phone only, no
  fabricated PLB.

- **`polar_baseline` brought fully LIVE** (2026-09-18, later): the config
  flip that was previously the "open" item below is done —
  `[infra.influx].enabled = true`, `HYLAS_INFLUX_TOKEN` is a persistent
  Windows user env var. §2's Polar table row now shows what was *actually*
  resolved (`PassageAnalysis.polar_source`), not just the config's declared
  mode — a real, separate bug from just mislabeling: `_load_polar()`'s
  fallback wasn't catching `MissingSecretError`, so a token that's set at
  the registry level but not yet visible to a given running process would
  400 the whole briefing instead of falling back gracefully. Fixed, with a
  regression test. Live-verified: the same test route went from 0% sail
  (PROVISIONAL) to 51% sail (the builder's real polar) — see
  [Operations](Operations.md#polar-current-state-and-how-to-change-it).

- **Submit guard for unresearched shore logistics** (2026-09-21): the web
  form warns before generating a briefing whose §7b still has a port section
  that is mostly an untouched template (the 2026-09-18 incident). Fails open;
  `POST /api/logistics-coverage`. Phase 0 of the
  [logistics research design](Logistics-Research-Design.md#phase-0-a-submit-guard-done-2026-09-21).
- **Fleet list** (2026-09-21): one saved list of boats that may travel with us, edited in the form's
  "Travelling with" screen (no file to edit) and ticked per passage. Per boat: hull (monohull /
  catamaran / trimaran), make and model, keel-down and keel-up draft, air draft, optional LOA, beam
  and displacement, notes, "usual companion". Arrival, departure and anchoring use the keel-UP draft.
  A multihull in company flags upwind legs (TWA under 60 degrees) once in the executive summary and
  per leg. The choice feeds section 2, section 5, the float plan's "in company with", the depth check
  and the research prompt (with a beam warning for multihulls). `fleet.py`, `config/fleet.json`,
  `/api/fleet`, `FleetPanel.tsx`. Make/model and dimensions are captured now so a production boat's
  characteristics can seed a generic polar later (see Open).
- **Depth check screen** (2026-09-21): the form lists only the waypoints that need a chart depth,
  takes feet and a source, and remembers them by position (`hydro/soundings.py`,
  `config/depth_soundings.json`), applied automatically to every later briefing. No TOML for the
  crew. See [Data Flows](Logic-Flows.md#5-depth-clearance-and-tide-hydroassesspy).
- **State Department advisories and embassies in the arrival dossier** (2026-09-21):
  `consular.py`, deterministic from the department's public JSON feeds; level, date, reason, link,
  embassy contact, Level 3/4 warnings in the banner. Covers the whole Caribbean scope. See
  [Data Flows](Data-Flows-and-Interfaces.md#3-inbound-external-public-apis).
- **Arrival pick list + picker** (2026-09-21, data researched: 310 places, 34 countries; awaiting the skipper's review): `arrival.py` (schema,
  validator, nearest lookup), `/api/arrival-places`, the form's "Arrival & stops" picker,
  security-incident field in shore-logistics research. [Design](Arrival-Places.md).
- **Small code items closed** (2026-09-21): the web form's Advanced options
  (skip switches, `--compare`, the four override files); `--reload` on the 8100
  backend (in mx-inv's `run-live.bat`); "Wrong place? Research as..." on a
  finished research port; README refreshed. Remaining form gaps are only
  `--step-nm`, `--stem` and the float-plan file paths, all CLI-only.
- **Automated shore-logistics research ("Research with Claude")** (2026-09-21):
  `research.py` (headless Claude per port, live-verified: two West End runs,
  ~90 s and ~$0.40 API-equivalent each), `research_jobs.py` (background job
  registry, cancel, ok ports saved to `var/logistics/`), the
  `/api/logistics-research` endpoints and the `ResearchPanel` in the web form
  (consent on every run, progress, cancel, never overwrites hand edits).
  [Design and evidence](Logistics-Research-Design.md); how to run it in
  [Operations](Operations.md#research-with-claude).
- **Served behind `mx-inv`'s `:8000` at `/routing`** (2026-09-17): reverse
  proxy to `127.0.0.1:8100`, started by `mx-inv`'s `run-live.bat`. Closes
  the old "go-live script" and "LAN/Tailscale exposure" open items. See
  [Design Decisions](Design-Decisions.md#served-behind-mx-invs-8000-by-reverse-proxy-not-a-code-merge).
- **Real shore-logistics research for a live briefing** (2026-09-18): the
  FLD→T&C run's §7b (Spanish Wells) and `--to "turks and caicos"` (§6 CIQ,
  §7d comms) were regenerated with real content after the first run shipped
  with a blank template. The research file lives in `var/logistics/`
  (gitignored); the automation of this step is the top open item below.

- **Fleet follow-ups** (2026-09-22): the Fleet form's "Look up this boat" button
  (`boat_lookup.py`, `POST /api/fleet/lookup`) parses pasted MarineTraffic Overview text (MMSI, call
  sign, LOA, beam) and/or ORC Sailboat Data certificate text (LOA/beam/draft/displacement, plus a real
  per-boat polar from the VPP speed table when present) into editable form fields for a person to
  confirm before Save — not a scrape or a fetch, see
  [Design Decisions](Design-Decisions.md#a-buddy-boats-real-polar-comes-from-a-paste-never-a-scrape-or-a-fetch)
  for why. A buddy boat's real polar (`BuddyBoat.polar_file`) is checked on every leg against the
  fleet target speed (`analysis.BUDDY_BELOW_TARGET`); the old fixed-60°-TWA multihull heuristic
  (`MULTIHULL_UPWIND`) still applies only to a buddy with no real polar on file. Example Buddy's MMSI
  is now on file too (`config/vessel.toml`, from MarineTraffic).

- **Documents and Comms forms** (2026-09-22): `DocumentsPanel.tsx` / `CommsPanel.tsx` write
  `config/papers.toml` / `config/comms.toml` directly through `GET|POST /api/papers` and
  `/api/comms`; the Advanced-options uploads stay as a one-off override. See
  [Design Decisions](Design-Decisions.md#documents-and-comms-forms-write-paperstomlcommstoml-directly---a-machine-written-file-not-hand-curated-prose).
- **Research with Claude proven and its try-list exercised** (2026-09-21/22): real runs through the
  panel and the live API (about 2.5-4 min, $0.50-0.90 per port); submit guard, ambiguous name, cancel,
  hand-edit protection, the Advanced-options switches and uploads all checked live; one bug found and
  fixed (orphaned section after a rename now warns, with Remove).
- **mx-inv proxy forwards PUT and DELETE** (mx-inv commit `b54d8c5`, 2026-09-21): in mx-inv's live
  branch, so the Depth check's PUT/DELETE work through `:8000`; the POST twins stay.
- **Docker readiness** (2026-09-24, mx-inv Docker spec v5): `GET /api/health`, `HYLAS_INFLUX_URL`
  override, `Dockerfile` + allowlist `.dockerignore`, CI image job (build, run as UID 2300, probe
  health). Nothing pushed to a registry; not deployed.

The full chronological build history is in `history/` (per-session detail,
grep it, don't read it whole); `CHECKPOINT.md` is the short start-here file;
this page tracks *current* status.

## Open / not yet done

Roughly in priority order - not a commitment, just where things stand:

- **Re-point to Ship's Automation** (new 2026-10-07; config half done 10-07, no code re-pointed). the builder's
  stack and its InfluxDB are retired; the dead read is switched off. See
  [Data Flows (1)](Data-Flows-and-Interfaces.md#1-boat-data-ships-automation-current-and-the-retired-influxdb-read).
  1. **Polar source - owner decided (the owner 2026-10-07, Ship's Automation D-125):** Ship's Automation
     owns the measured polar - it builds it from its history + the mx-inv sail set and publishes a
     `.pol`; routing reads that file (`polar_source` back to a live source then) and keeps
     `config/baseline.pol` as the fallback. Open: the publish path/format, and whether to recover the
     15 x 11 derived grid from the builder's archived data.
  2. ~~**Stop the dead read.**~~ **Done 2026-10-07:** `config/vessel.toml` has `polar_source =
     "baseline"` and `[infra.influx] enabled = false` (suite back to ~14 s). Still open: point the
     unused `[infra.signalk]` block at `http://192.168.1.10:3000` (its URL is the builder's external name)
     and settle its credentials.
  3. **Step 7 data source.** Rebuild the SOG/STW read for calibration on the history API
     (`http://192.168.1.10:8090/api/series`); the signal paths to use are **ASSUMED - ask the owner** /
     to be confirmed against the Ship's Automation signal catalogue.
  4. Whether a briefing or planned route should be published to Ship's Automation (the old
     `route_plan` / `route_forecast` write-back idea) - **ASSUMED - ask the owner**; nothing built.
  5. The `export-polar` and `influx` CLI commands depend on InfluxDB; retire or re-point them with (1).
- **Weather routing by wind and current** (scoped 2026-09-21; step 1 done 2026-09-22). The real
  requirement is a route/departure optimizer that uses the boat's polar and ocean current, not an
  animated weather map (the owner already uses Windy Premium to look at weather). Order of work, each
  step shippable:
  1. ~~`passage-planner export-polar`~~ **Done (2026-09-22).** Writes the live derived polar
     (Influx `polar_baseline`) as a standard `.pol` (qtVlm/OpenCPN/Windy polar plugin format);
     `polar.write_pol()`, `service.export_polar()` (no soft fallback — raises rather than
     silently exporting the provisional file), CLI `passage-planner export-polar [--out PATH]`
     (default `var/polar_baseline.pol`, gitignored). Live-verified against the real boat config:
     wrote a real 15 TWA x 11 TWS grid from the builder's InfluxDB measurement.
  2. ~~Currents in the existing fixed-route analysis~~ **Done (2026-09-22).**
     `weather/openmeteo.py` fetches `ocean_current_velocity`/`ocean_current_direction` on the
     existing marine call (SMOC, ~8 km; `wind_speed_unit=kn` converts it too, live-verified, no new
     unit code); `ForecastPoint`/`LegConditions` carry `current_kn` + `current_to_deg` (named for
     the oceanographic "toward" convention, distinct from `wind_from_deg`'s meteorological "from").
     `analysis.py`'s `projected_speed_kn` is now speed-over-ground — boat speed plus
     `geo.current_component_kn()`'s along-course projection, not a full set-and-drift
     steering-triangle solve (deliberately out of scope; see
     [Design Decisions](Design-Decisions.md#currents-correct-leg-speed-along-the-drawn-course-not-a-steering-triangle)).
     A `FOUL_CURRENT` WARNING guards the case where the adverse component would exceed boat speed
     (flags instead of dividing by it); a new `WIND_AGAINST_CURRENT` CAUTION fires on opposed
     wind/current beyond configurable thresholds, `[safety.current]` in `config/vessel.toml`
     (defaults 15 kn TWS / 1.5 kn current / 120° opposed — PROVISIONAL, seamanship rule-of-thumb,
     not sourced). Briefing section 4 gained a Current column and the speed column is now labeled
     "SOG kn". `refine()`'s convergence loop needed no change — SOG-vs-STW is transparent to it.
     454 tests, ruff/mypy clean on both platforms. Live-verified against the real FLD→T&C route and
     a real departure: real Gulf Stream push on the early Florida Straits legs (+0.6 to +0.8 kn),
     small foul current on two later legs. Deferred to step 7 as already planned: calibrating
     forecast current against the boat's own SOG-minus-STW post-shakedown (read from Ship's
     Automation's history API since 2026-10-07, not InfluxDB - see step 7).
  3. ~~Gridded field provider~~ **Spike done, interface built (2026-09-22) — no GRIB or
     Copernicus needed for v1.** `weather/field.py`: `corridor_grid()` builds along-track samples
     offset cross-track (a band around the drawn route, not a bounding box — a bbox grid over the
     real 583 NM route tripped Open-Meteo's rate limit in live testing, see Design Decisions);
     `Field.at(time, lat, lon)` does nearest-point/nearest-time lookup over a fetched grid;
     `fetch_field()` reuses `OpenMeteoClient.fetch()` unchanged and caches results under
     `var/cache/` (JSON, gitignored). 465 tests, ruff/mypy clean on both platforms. Live-verified:
     a 63-point corridor grid over the real FLD→T&C route at 3 time slices fetched in 1.4s with
     real current data, a `.at()` lookup ~18 NM off-track returned a real point, and a repeat
     fetch hit the cache in 0.01s. See
     [Design Decisions](Design-Decisions.md#step-3s-gridded-field-provider-does-not-need-grib--confirmed-not-just-hoped)
     for what the spike actually found, including the rate-limit's real shape (a burst-rate limit,
     not a fixed per-minute budget — matters for step 5's departure-window sweep). Copernicus
     Marine account stays registered as the fallback path, not built against; GRIB decoding stays
     deferred. **Not built**: the isochrone solver itself (step 4) that would call `Field.at()`.
  4. ~~Corridor isochrone solver~~ **Built (2026-09-22).** `isochrone.py`, written in-house because
     libweatherrouting has no currents: solves each leg of the drawn route independently over a
     corridor grid (start and end waypoints forced as single-cell rows - the "anchored at start,
     end and any must-pass waypoints" requirement, since every original route waypoint becomes a
     leg boundary), a per-row dynamic program (the grid is a DAG, so a single forward pass finds
     the minimum-time path exactly, using the real conditions at the exact time each hop is
     attempted). Reuses `analysis.py`'s `decide_mode`/`mode_speed`/`burn_gph` (made public for
     this) - a solved passage and a fixed-route briefing can never disagree about what mode a given
     wind/current/bearing implies. A hop the current would make infeasible (SOG <= 0) is skipped,
     not clamped, unlike the fixed-route analyzer's guard - the solver has an alternative; the
     fixed-route analyzer doesn't. Falls back to the direct rhumb-line hop (with a warning) only if
     every corridor path into a leg's endpoint is infeasible. 476 tests, ruff/mypy clean on both
     platforms; confirmed it exactly reproduces the fixed-route analyzer's leg duration when
     confined to the on-track lane, and that it actually detours toward a strong-enough favorable
     current and never picks an infeasible hop when an alternative exists. Live-verified against
     the real FLD→T&C route with a real fetched field (48 points, 3 time slices): solved all 12
     legs, no fallbacks, 90.2h/583 NM/46 gal, 62% sail. **A real, informative finding, not a bug**:
     that live run's corridor duration (90.2h) came out *slower* than the fixed-route analyzer's
     figure for the same drawn track (87.7h) - both saw the identical 583 NM distance (the corridor
     found no lateral detour worth taking at this field resolution), but the solver's per-hop
     `Field.at()` lookups (3 time slices, 40 NM spacing) are coarser than the fixed-route
     analyzer's per-leg aggregate of ~39 finely-sampled forecast points, so its mode/speed calls are
     less accurate at this economical fetch size. See
     [Design Decisions](Design-Decisions.md#the-solvers-accuracy-is-bounded-by-the-fields-resolution-not-just-its-existence).
     It never routes through shoals (the Bahamas Banks are below the resolution of global depth
     data) and its output is a suggestion to check in Navionics, not a safety claim - unchanged from
     the original plan. `isochrone.to_gpx()` exports the solved hop-by-hop path as a clean GPX 1.1
     `<rte>` for that Navionics check. **CLI-surfaced (2026-09-22):**
     `passage-planner solve ROUTE.gpx --depart ... [--out FILE.gpx]` fetches its own field (a separate,
     deliberately coarser grid than the solver's internal one - see `--help`) and writes the solved
     path as GPX; marked EXPERIMENTAL in its own help text. No web endpoint yet.
  5. ~~Departure-window sweep~~ **Built (2026-09-22), tabulation only - no plot.** `sweep.py`'s
     `sweep_departures()` solves the drawn route (the fixed-route `analyze()`, not the corridor
     solver - see below for why) at every candidate departure in a window, on one or more forecast
     models, and `render_sweep_markdown()` tabulates ETA/fuel/peak wind/sail fraction with the best
     (fastest, no fuel shortfall) departure flagged. A fetch failure for one candidate/model is
     recorded and skipped, not fatal to the rest of the sweep. 491 tests, ruff/mypy clean on both
     platforms. Live-verified against the real FLD→T&C route: a 3-candidate, 1-model sweep (0h/6h/
     12h) solved cleanly in 1.2s with sane, differentiated results per departure.
     **Deliberately uses `analyze()`, not `isochrone.solve_passage()`**, and has **not been tried
     at its full stated scale** (every 6h for 5 days, inclusive, on two models = 21 x 2 = 42 solves) - both are the same
     underlying concern, the step-3 rate-limit finding, see
     [Design Decisions](Design-Decisions.md#the-sweep-uses-the-cheap-fixed-route-analyzer-not-the-corridor-solver-and-has-not-been-tried-at-full-scale).
     **GPX export**: built as `isochrone.to_gpx()` (see step 4, above) rather than inside `sweep.py`
     - it exports a *solved* path's hops, and the sweep itself only ever calls the fixed-route
     analyzer, which has no hop list of its own to export (its "path" is just the drawn route).
     **Not built**: the route-comparison plot. No plotting library is a dependency today
     (`matplotlib` or similar would be a real new dependency, not a redeploy of something already
     in the tree) and the terse Markdown table already answers the sweep's actual question - which
     departure is best - without one. Revisit only if the owner specifically wants a visual, not by
     default. **CLI-surfaced (2026-09-22):** `passage-planner sweep ROUTE.gpx --depart ... [--out FILE.md]`
     — marked EXPERIMENTAL in its own `--help` text (the full 21-candidate/2-model = 42-solve scale is still
     untried live). No web endpoint yet.
  6. ~~Optional Professional-forecast block~~ **Built (2026-09-22).** `marine_forecaster.py` parses a
     pasted MWXC "Wx Update" email into sections (TROPICS/ASCAT/BUOYS/IMAGERY/SYNOPSIS/OUTLOOK/
     SUGGEST/PRECIP/WIND/SWELLS) and extracts the highest knot figure mentioned in the WIND section
     — designed against two real archived MWXC emails (not a guessed format; the fixture used for
     tests is an original structurally-faithful synthetic sample, not republished subscriber
     content, see the module docstring). Compares that figure against
     `PassageAnalysis.peak_wind_kn` (a new property, also now shared with `sweep.py`) and flags a
     disagreement past a configurable threshold (default 8 kn, PROVISIONAL). Briefing section 3d
     renders only when text was actually pasted — **absent means omitted, no PENDING block** (the owner
     is not subscribed most of the time; this is normal steady state, not a missing feed) — matches
     the roadmap's original spec exactly. CLI: `brief --parker-file FILE`. 518 tests, ruff/mypy
     clean on both platforms. Live-verified: a real briefing run with a structurally-real sample
     correctly extracted a 27 kn peak, compared it against a 13 kn model peak, and rendered the
     disagreement callout. Parker is not subscribed now and can be turned on when needed; 200
     archived updates (2022-2024) in the owner's email are the test corpus (used for design, not
     committed to the repo — see above).
  7. After the shakedown passage: calibrate forecast current against the boat's own SOG minus STW
     from Ship's Automation's history API (`http://192.168.1.10:8090/api/series`).
  Paid data was considered (2026-09-21): currents and winds are free and good enough; money is best
  spent on a human forecaster and possibly a short PredictWind term as a solver benchmark. Not a
  substitute for professional routing; decision support next to Windy and the NWS text forecasts.
  **Step 7 data source changed (2026-10-07):** the SOG and STW history now comes from Ship's
  Automation's history API, not InfluxDB (see "Re-point" above). **Sweep scale:** the default window
  is 21 candidates x 2 models = 42 solves (an older note said "40"); only a 3-candidate sweep has run live.
  **Not built:** web endpoints for `solve` and `sweep`, a a marine forecaster paste box in the web form,
  wiring the sweep's winner into the corridor solver.
- **Review the FLD -> T&C rerun briefing (the owner, in progress)**: Old Bahama Bay operating status (phone);
  re-run the T&C Security line (CSSN does cover the Turks and Caicos; about $0.60); the research
  never tells the fuel plan (7a) that every stop's fuel dock is too shallow for 10 ft; whether to cut
  the router-failover blockquote in 7d. WP1 sits on a 1 ft shoal (route start mis-drawn - the owner to nudge
  it into the inlet).
- **Arrival places: review and corrections** by the skipper (Word review sheet, 32 entries marked "not
  confirmed"), and the US departure/re-entry ports (Florida, Gulf). Unlisted-place tracking was
  dropped by the owner 2026-09-22.
- **Garmin MFD GPX import test (aboard)**: does the MFD import a `<rte>` as a linked route or loose
  points? Leading theory: a bare `<rte>` lacks Garmin's `gpxx:RouteExtension`. the owner hand-keys routes
  meanwhile (a workaround, not a decision).
- **Documents (the owner, at the boat)**: scan the 2026 DTOPS receipt and sticker and point the decal's
  `path` in `config/papers.toml` at it (calendar-year: renew before lapse). NVDC is correcting the COD
  name typo (emailed 2026-09-11). Nothing is blocking in `papers --trip international`.
- **Starlink Personal Maritime registration**: parked until nearer departure; needs the COD (whose
  name misprint "YOUR VESSEL NAME" is with NVDC since 2026-09-11). `config/comms.toml` is done:
  `billing_cycle_day = 24` confirmed 2026-09-10, `priority_gb` unset on purpose.
- **Example Buddy polar**: the paste-in path exists (Fleet form "Look up this boat"); no ORC certificate
  for her has been pasted - she may not have one.
- **Container deployment**: the image is ready; where it runs (Pi or Ship's PC) depends on where
  mx-inv runs - follows mx-inv's hosting decision.
- **USCG Local Notice to Mariners**: deferred permanently (PDF-only, no feed); `notices.py` points at
  it.

## Explicitly decided NOT to build (don't re-propose without new information)

- **This project deriving/refining its own polar** - decided while the builder's stack derived it, and
  re-confirmed after that stack was retired: Ship's Automation owns the measured polar (the owner
  2026-10-07, its D-125) and publishes a `.pol`; routing consumes a polar file and does not fit one
  from telemetry. See
  [Design Decisions](Design-Decisions.md#pure-consumer-of-the-builders-polar-never-a-deriver).
- **A general PDF-rendering pipeline** — `.docx`/`.html` export only;
  PDF is a manual export step from Word/Acrobat. See
  [Design Decisions](Design-Decisions.md#no-pdf-generation-library).
- **A router or client-state library in the frontend** — one form, one
  result view; not worth the dependency at this scope. See
  [Design Decisions](Design-Decisions.md#frontend-has-no-router-or-client-state-library).
- **Live weather/notice caching or a background refresh job** — every
  fetch is on-demand, per briefing run; there's no daemon, no scheduled
  job. The only cross-run caches are NOAA CO-OPS's 30-day station list, the State
  Department feed (24 h) and the solver's corridor fields (`var/cache/`, development aid).
  Matches the project's "one passage, one briefing run" shape.
