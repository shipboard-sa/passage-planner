<!--
Purpose: The non-obvious logic in passage-planner - passage timing, sail/motor decisions, current,
    flags, the refine loop, fleet rules, depth/tide, papers, CIQ, maintenance, comms, the corridor
    solver, the sweep, a marine forecaster, research jobs - with thresholds and the why.
Version: v2 (2026-10-07) - rebuild-from-spec pass: added current correction and every flag with its
    threshold, fleet rules, maintenance-due classification, comms Calendar check, corridor solver,
    departure sweep, a marine forecaster comparison, research job flow and the depth-check screen; fixed
    the hydro section (saved depths by position replaced waypoint_depths.toml as the normal path).
    v1 (2026-09-17..21, no header).
Scope: PRODUCTION - engineering wiki for passage-planner (
Architecture: passage-planner documentation. Linked from Home.md. Constants named here live in the
    module named beside them; config values in config/vessel.toml (see Data-Model.md).
Invocation: n/a (reference doc)
-->

# Logic Flows

The parts a new reader cannot get from module names. Thresholds come from `config/vessel.toml`
unless a module constant is named.

## 1. GPX -> timed passage -> leg analysis -> refine

**Stage 1, `route.parse_gpx()`**: XML -> `Route` of `Waypoint`s and `Leg`s (great-circle distance,
initial bearing, cumulative distance). Points closer than 0.02 NM merge. `<rte>` preferred, else
`<trk>` segments concatenated. Bad content raises `RouteContentError`.

**Stage 2, `passage.PassagePlan`**: route + tz-aware departure + a nominal speed -> evenly spaced
`TimedFix`es (`sample_fixes(step_nm)`, default 15 NM) with distance, position, ETA and leg index.
A bare departure (no zone) is local time at WP1 (`service.parse_depart`). The nominal speed is only
the first guess; real speeds come from stage 3.

**Stage 3, `analysis.analyze()`** (pure). Per leg:

1. Aggregate the forecast points on the leg: mean wind speed, circular-mean wind and wave direction,
   max gust, max wave height, mean pressure, mean current speed and direction.
2. TWA = angle between the leg bearing and the wind's "from" direction. `is_beat` = TWA <
   `beat_twa_min_deg` (45).
3. Polar speed at (TWS, TWA), capped at `hull_max_speed_kn` (9.0).
4. **Mode** (`decide_mode`, shared with the solver):
   - beat: motorsail if TWS >= 8 kn, else motor (the owner: "just motor into it if it makes sense"; no
     pure-sail upwind option);
   - polar speed >= `light_air_speed_kn` (4.5): sail;
   - TWS >= 4 kn and polar speed >= 2 kn: motorsail;
   - otherwise motor.
5. **Speed through water** (`mode_speed`): sail = polar; motor = cruise (7.0); motorsail = cruise +
   0.5 kn flat assist; all capped at hull max. The motorsail assist is a stated approximation, not a
   physics model.
6. **Current**: `geo.current_component_kn()` projects the current onto the leg course; SOG = STW +
   that component. No set-and-drift steering triangle (Design Decisions explains why). If SOG <= 0
   the leg gets a `FOUL_CURRENT` WARNING and timing uses 0.1 kn (no division by zero).
7. Duration = distance / SOG; fuel = mode burn (0 / `burn_gph` 2.25 / `motorsail_burn_gph` 1.6) x
   duration.
8. Flags (below).

Passage totals: distance by mode, sailing fraction, fuel, `usable_fuel_gal` (capacity less
`fuel_reserve_frac`), `fuel_margin_gal`, `fuel_shortfall`, `peak_wind_kn`, and `polar_source`
(what was actually used, printed in section 2).

**Stage 4, `analysis.refine()`**: analyze -> re-time -> re-fetch, at most 4 iterations, stop when the
ETA moves less than 30 minutes. Iteration 1 samples at the planning speed; each later iteration
samples with the previous iteration's per-leg speeds, so a given hour of weather lands where the boat
will actually be. Not converging is not an error; the last result is returned with its iteration
count.

### Leg flags

| Flag | Severity | Rule | Source of threshold |
|---|---|---|---|
| Seas over limit | WARNING | Hs > `wave_hs_max_m` (3.0 m) | vessel.toml |
| Seas approaching limit | CAUTION | 0.7 x limit < Hs <= limit | code |
| Sustained wind | WARNING | TWS > `wind_sustained_max_kn` (25) | vessel.toml |
| Gusts | WARNING | gust > `wind_gust_max_kn` (35, PROVISIONAL) | vessel.toml |
| Head seas | CAUTION | beat and Hs >= 1.5 m (`_HEAD_SEA_HS_M`) | code |
| `WIND_AGAINST_CURRENT` | CAUTION | TWS >= 15 kn and current >= 1.5 kn and the wind's push direction is >= 120 deg from the current's set | `[safety.current]`, PROVISIONAL |
| `FOUL_CURRENT` | WARNING | adverse current >= STW | code |
| `BELOW_FLEET_TARGET` | CAUTION | sailing and SOG < `fleet_target_avg_kn` (7.0): engine needed to hold schedule | vessel.toml |
| `BUDDY_BELOW_TARGET` | CAUTION | a buddy boat with its own polar makes < fleet target at this TWS/TWA | vessel.toml + buddy polar |
| `MULTIHULL_UPWIND` | CAUTION | a buddy multihull with no polar on file and TWA < max(45, 60) | code (`MULTIHULL_UPWIND_TWA_DEG`) |

Flags appear per leg in section 4 and collected in the executive summary.

## 2. Fleet rules

`fleet.apply_fleet()` replaces `VesselConfig.buddy_boats` with the boats chosen for this passage:
`--solo` = none, `--fleet id,id` = those, default = boats marked "usual companion" (or
`vessel.toml`'s `[[buddy_boat]]` when `fleet.json` does not exist). Then:

- **Shared-stop draft** = the deepest of this boat's planning draft and each buddy's
  `effective_shallow_draft_ft` (keel-up for a lifting keel with a known up-draft, else keel-down).
  Keel-down (Example Buddy 14 ft) is open water only. Used by sections 2 and 5 and the depth check.
- **Air draft** = the tallest in company.
- A buddy's real polar (`config/polars/<id>.pol`, from a pasted ORC certificate) drives
  `BUDDY_BELOW_TARGET`; without one, a multihull triggers `MULTIHULL_UPWIND`.
- The fleet also flows to the float plan's "in company with", the research prompt (with a beam
  warning for a multihull) and section 1.

## 3. `service.run_brief()` and the PENDING pattern

Each optional section is wrapped in `try/except` against its own error type (never a bare
`Exception`, so a real bug still shows a traceback) and degraded through `warn()`. `briefing.py`
checks each input and, if absent, writes `> PENDING` naming what is missing and the manual check
("check nhc.noaa.gov directly", "run with `--compare`"). A briefing with every optional source off is
still complete, just with more PENDING blocks. The a marine forecaster block (3d) is the one exception:
absent means omitted. Level 3/4 State Department advisories also add a line to the warnings banner.

## 4. Papers readiness: scan -> override -> assess

1. **`papers/scan.py`** classifies filenames in `Papers of Record/` and `Insurance/` by regex into
   DocKinds, after filtering obsolete files (COVID documents, the prior boat's files, blank
   templates). "Current" = parent folder in a known set (`purchase`, `concept 2025`, `fcc`, `ids`) or
   mtime on/after `acquired_after` (2025-01-01). Several matches: current beats legacy, then newest.
2. **`papers/overrides.py`** loads `config/papers.toml`; an override always wins (the expiry is often
   not in a filename).
3. **`papers/checklist.py` `assess_papers()`**: a required/optional matrix per trip type (9 required
   domestic, 12 international - passport, CBP decal, radio licence and EPIRB become required), a
   renewal cycle per kind (for `STALE` when no expiry is known), and a status per kind: `PRESENT`,
   `STALE`, `EXPIRING` (before passage end + 30 days), `EXPIRED`, `MISSING`, `LEGACY_ONLY`.
   `blocking` = required and missing/expired/legacy-only; the rest are warnings.

## 5. Depth, clearance and tide (`hydro/assess.py`)

Per waypoint:

1. Required charted depth = shared-stop draft + `ukc_margin_ft` (2 ft); 10 ft for this boat alone.
2. GEBCO triage (opentopodata): deeper than 40 ft (`_DEEP_WATER_FT`) = clear, no further lookup.
3. Otherwise the depth source, first that applies: an explicit `--depths-file` entry by waypoint name
   (CLI only, takes precedence); a **saved chart depth** within 0.5 NM (shallowest wins); the
   nearest NOAA ENC sounding (US waters).
4. Nearest CO-OPS tide station; tide height at ETA by cosine interpolation between predicted highs
   and lows (exact at the turns).
5. Marginal if charted depth + tide < required; then search +/- 6 h around ETA for a tide gate (a
   contiguous window where it clears).

Each sub-fetch fails to `None` independently, so a Bahamas waypoint without ENC still gets a tide- or
saved-depth assessment.

**Depth check screen** (`POST /api/depth-check`): runs the same assessment at planning speed and
returns only waypoints with no usable depth that are not deep water, or that are too shallow with the
tide, plus any already covered by a saved depth. The person reads the chart (Navionics, Explorer
chartbooks) and saves feet + source. A waypoint on a mapped shoal (the FLD route's WP1 on a 1 ft
shoal) is reported as "may need moving", not only "needs a depth".

## 6. CIQ dossier and State Department block

`ciq.render_dossier(countries)` looks each destination up in the 7-country `REGISTRY` (hand-verified,
dated); an unknown country gets a fill-in skeleton naming what to research. Every dossier ends with
the Q-flag-at-12-NM paragraph. Destination countries come from the picked stops (web) or `--to`
(CLI). `consular.py` adds advisory level, date, reason, link and embassy for every country in the
arrival list; U.S. soil (USA, Puerto Rico, USVI) says "no advisory"; territories without an embassy
point at the sovereign's.

## 7. Maintenance due (`ops.py`)

From mx-inv's recurring tasks: `days` interval -> next due = last completed + period; status
`OVERDUE` (before departure), `DUE` (within the passage window), `SOON` (within `soon_buffer_days`,
10, after arrival); `use` interval -> `PER_PASSAGE` (do before/after every outing); `hrs` interval ->
`HOURS`, listed with a caveat because routing does not know engine hours. The mx-inv seed date
2026-06-06 is an import placeholder, not a real service date, so early `OVERDUE` lines may be
artefacts.

## 8. Comms configuration (section 7d)

Per destination country, from the `comms.py` registry: is Starlink licensed there, T-Mobile roaming
quality, whether Maya eSIM covers it; a recommendation which to lean on. Starlink Personal Maritime is
unlimited within 12 NM of a coast and metered "Ocean Mode" beyond (`ocean_mode_usd_per_gb`);
`coastal.py` estimates offshore distance against the bundled coastline. **Calendar check**: suspend
Starlink yes/no and the date, from `billing_cycle_day` and whether Maya covers the country.

## 9. Local time

Every timestamp is labelled in the civil zone of its own position (`clock.Clock` with
`timezonefinder`, lazy-loaded). Departure in the departure zone, ETA in the arrival zone, each leg in
its midpoint's zone; open water resolves to the nearest civil zone; `[infra].timezone` is the last
fallback.

## 10. Corridor solver (`isochrone.py`, `passage-planner solve`, EXPERIMENTAL)

Each leg of the drawn route is solved separately (every original waypoint is a must-pass point):

1. Rows of candidate points along the leg every `--along-step-nm` (15), each offset across track by
   `--cross-offsets-nm` (-20,-10,0,10,20); the first and last rows are the single waypoint.
2. Forward dynamic program over the rows (a DAG): for each reachable point, try every point in the
   next row; hop time from `Field.at()` conditions at the moment the hop starts, the same
   `decide_mode` / `mode_speed` / `burn_gph` as the fixed-route analyzer, plus current. Keep the
   earliest arrival per point and its predecessor.
3. A hop with SOG <= 0.05 kn is skipped (the solver has alternatives; the analyzer does not). If no
   path reaches the leg end, use the direct rhumb hop and warn.
4. Back-track the best path; legs chain (each leg starts when the previous one arrives).

The CLI fetches its own coarser field (`--field-along-step-nm` 40, `--field-cross-offsets-nm`
-20,0,20, `--field-hours` 0,24,48; cached in `var/cache/` unless `--no-cache`) and writes the path as
GPX (`--out`). **Known limits**: accuracy is bounded by the field's resolution - on the real FLD->T&C
route it came out slower (90.2 h) than the fixed-route analyzer (87.7 h) on the identical 583 NM
because nearest-point lookups at 3 time slices are coarser than the analyzer's per-leg aggregate of
about 39 points. It knows nothing about depth (Bahamas banks are below global bathymetry resolution);
its route is a suggestion to check in Navionics, never a safety claim.

## 11. Departure sweep (`sweep.py`, `passage-planner sweep`, EXPERIMENTAL)

Candidate departures from `--depart` to `--depart + --window-days` (5) every `--step-hours` (6),
endpoints included = 21 candidates; each on every `--models` (default `gfs_seamless,ecmwf_ifs025`).
Each candidate is one fixed-route `analyze()` pass at the fleet target speed (not `refine()`, not
the solver - request volume). Requests run sequentially; a failed candidate/model is recorded and
skipped. Output table: departure, model, ETA, duration, fuel, peak wind, sail fraction, warnings; the
best = fastest with no fuel shortfall. **The full default (21 x 2 = 42 solves) has not been run
live** - only a 3-candidate sweep; Open-Meteo's burst limit is the risk. No plot by decision.

## 12. a marine forecaster comparison (section 3d)

`marine_forecaster.py` splits a pasted MWXC email into its sections (TROPICS, ASCAT, BUOYS, IMAGERY,
SYNOPSIS, OUTLOOK, SUGGEST, PRECIP, WIND, SWELLS), takes the highest `NNk` figure in WIND, and flags
a disagreement when it differs from the model's `peak_wind_kn` by more than 8 kn
(`DEFAULT_DISAGREEMENT_THRESHOLD_KN`, PROVISIONAL). The section quotes the relevant text and the
comparison; nothing else from the email is interpreted.

## 13. Research with Claude (job flow)

1. `GET /status`: CLI found (`HYLAS_CLAUDE_EXE`, else `claude` on PATH, else the newest `claude.exe`
   bundled with the desktop app in either the virtualized or real package path) and
   `CLAUDE_CODE_OAUTH_TOKEN` set.
2. `POST` starts a job (409 if one is running): ports run one after another on a background thread.
3. Per port: build the prompt (port, stop role instructions, up to 20 route points, boat and fleet
   specs, crew count, the 15 logistics fields), run `claude -p` (budget cap $5, 15 min timeout,
   cancellable every 0.5 s), validate the JSON reply into a `### <port>` section (fields, gaps,
   "Resolved as", sources), save ok results to `var/logistics/`.
4. The form polls every 4 s and merges finished sections into the textarea **only where the section
   is still template text**; a hand-edited section is never overwritten. A renamed hand-edited port
   leaves an orphan section, shown with a warning and a Remove button.
5. Submit guard: before generating, the form calls `/api/logistics-coverage` and asks if any section
   is mostly template (fails open).

Measured: about 2.5-4 min and $0.50-0.90 API-equivalent per port on the subscription; results vary
run to run, so review is mandatory ([Logistics research design](Logistics-Research-Design.md)).
