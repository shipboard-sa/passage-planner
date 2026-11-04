<!--
Purpose: the why behind passage-planner's major architectural choices.
Version: v11 (2026-10-18) - polar-consumer status: owner = Ship's
    Automation (the owner 2026-10-07, its D-125), ASSUMED removed.
    v10 (2026-10-07) - rebuild-from-spec pass: status notes on the
    three decisions whose counterparty was the builder's stack (polar consumer,
    write broker, local_buffer config) now that it is retired; the
    provenance-tier entry corrected to what the code does (tiers validated,
    registry not consulted, only the Parker block prints a tag); the proxy
    entry notes the Docker image. v9 (2026-09-22) - added the Documents/Comms forms decision (write
    papers.toml/comms.toml directly, not a JSON overlay - and the
    multi-line-note bug that was fixed before it shipped). v8: added the
    orphaned-research-section finding and fix from live testing of the
    shore-logistics research try-list (removeSection() + App.tsx's orphan
    warning). v7: added the fleet-follow-up
    decision: a buddy boat's real polar comes from a pasted MarineTraffic/ORC
    paste-in, never a scrape or fetch (boat_lookup.py), and how that relates
    to the "pure consumer, never a deriver" rule above. v6: added the current-correction
    model, the step-3 GRIB-vs-Open-Meteo-grid spike's actual findings, the
    step-4 corridor isochrone solver's design and its live-found
    field-resolution accuracy tradeoff, step 5's choice of the cheap
    fixed-route analyzer over the corridor solver for the departure-window
    sweep, then step 6's a marine forecaster design and the CLI-before-web wiring
    decision for solve/sweep.
Scope: PRODUCTION - engineering wiki, read before touching an area whose
    design was already deliberately settled.
Architecture: passage-planner subsystem documentation. See wiki/Home.md for
    the page index; Roadmap.md tracks current status, this page the why.
Invocation: n/a (reference doc)
-->

# Design Decisions

Why the system is shaped the way it is — written so a future change doesn't
accidentally re-litigate a question that was already deliberately settled.

## Pure consumer of the builder's polar, never a deriver

**Decision**: this project loads a polar — the static `config/baseline.pol`,
or (as of 2026-09-18, `polar_source = "derived"`) the live `polar_baseline`
InfluxDB measurement — and interpolates against it. It does not fit,
refine, or recompute a polar from telemetry anywhere in the codebase.

**Why**: polar derivation from real sailing data is the builder's job, on
`your-pi-hostname`, from telemetry his stack already ingests. Duplicating that
logic here would mean two systems independently deriving performance
curves from overlapping (or diverging) data — a coordination hazard with no
upside, since this project has no telemetry pipeline of its own to derive
from anyway. `config/vessel.toml` names this split directly: the *derivation*
pipeline belongs to "the builder's empirical polar-derivation pipeline," and this
project's own `polar_source` config option only ever has two values —
`baseline` (the static file) and `derived` (read the builder's `polar_baseline`
measurement verbatim — wired 2026-09-18). Neither value means "compute one
here."

**What this actually means in code**: `polar.py` has no fit/regression
logic, no telemetry ingestion, no write-back — including in
`polar_from_grid()`, the new (2026-09-18) builder for the InfluxDB shape:
it assembles exactly the cells the builder published into a grid and rejects an
incomplete one rather than filling the gap by interpolation or estimation.
`Polar.provisional` is a one-way flag (this file hasn't been replaced by
measured data yet — always `False` for an Influx-sourced polar) — never a
trigger for this codebase to try replacing it itself.

**Note on sourcing**: no single sentence in the code states this rule in so
many words — it's the consistent shape of everything `polar.py` and
`config.py`'s `polar_source` option do (and don't do). If a future change
ever adds telemetry-driven polar fitting to this project, that would be a
deliberate reversal of a load-bearing assumption across `analysis.py`,
`briefing.py`, and the the builder coordination story — flag it explicitly rather
than adding it quietly.

**Status 2026-10-07**: the counterparty is gone. the builder's stack, including the InfluxDB that
published `polar_baseline`, was retired when the Pi was rebuilt as Ship's Automation (2026-10-06).
The code is unchanged; since 2026-10-07 `config/vessel.toml` says `polar_source = "baseline"`, so
every run uses the PROVISIONAL `config/baseline.pol`.
**Owner decided (the owner 2026-10-07, Ship's Automation D-125):** Ship's Automation owns the measured
polar - it builds it from its history + the mx-inv sail set and publishes a `.pol`; routing reads
that file and keeps `baseline.pol` as the fallback. The rule "routing never fits a polar from
telemetry" stands, with Ship's Automation as the new counterparty; the re-point is tracked in
[Roadmap](Roadmap.md) "Re-point to Ship's Automation".

## CLI and web share one pipeline via `service.py`

**Decision**: `cli.py`'s `_cmd_brief` body was extracted into
`service.run_brief(args) -> BriefResult`, which both the CLI and
`backend/main.py` call with an equivalent `argparse.Namespace`/
`SimpleNamespace`.

**Why**: a passage briefing is expensive to get subtly wrong twice (weather
fetch, refine-loop timing, section assembly) — two independent
implementations would drift the moment either one got a bugfix the other
didn't. The refactor (2026-09-17) was verified byte-for-byte: all 237 tests
stayed green with only two tests' monkeypatch targets moving from
`cli.fetch_route_forecast`/`fetch_nav_warnings_sync` to the equivalent
`service.*` names, since that's genuinely where the calls now live — not a
behavior change.

**What this means going forward**: the web form's "core fields only" scope
(route, depart, speed, country, crew, watch hours) is a UI decision, not a
pipeline limitation — every flag the CLI supports (skip-toggles, file
overrides, float-plan generation) is already reachable through
`service.run_brief()`'s namespace; exposing more of them in the form is
additive work on `backend/main.py`/`App.tsx` only, never a `service.py`
change. (Done 2026-09-21 for the skip switches, `--compare` and the four
override files: an "Advanced options" section in the form, no `service.py`
change, as predicted.)

## No auth on the web form

**Decision**: no login, no CORS opened up for cross-origin access, no
per-user permissions on `backend/main.py`.

**Why**: same trust boundary `mx-inv` uses — LAN/Tailscale-only
reachability is assumed, not enforced by the application. Unlike `mx-inv`,
this backend doesn't even set `allow_origins=["*"]`; it relies on the dev
proxy and same-origin static serving instead, which is arguably a *smaller*
exposure than `mx-inv`'s wide-open CORS, but the underlying assumption is
identical: anyone who can reach the port can generate briefings and read
their contents. Since 2026-09-17 the app is reachable over LAN/Tailscale,
but only through `mx-inv`'s `:8000` (see the next section) — 8100 itself is
bound to `127.0.0.1` and has no firewall rule — so the exposure and trust
boundary are exactly `mx-inv`'s.

**Revisit if**: `mx-inv` ever gains auth (then the proxy should forward it),
or this backend is ever bound to a non-loopback address.

## Served behind `mx-inv`'s `:8000` by reverse proxy, not a code merge

**Decision** (2026-09-17, the owner's call): the routing GUI is reached at
`http://<host>:8000/routing`. `mx-inv/backend/main.py` has a `/routing/{path}`
catch-all that forwards over HTTP to this project's own uvicorn process on
`127.0.0.1:8100`; `mx-inv`'s `run-live.bat` builds this `frontend/` and starts
this backend (in a separate minimized window) before starting itself.

**Why a proxy, not an import merge**: this is a genuinely separate repo
(`passage-planner`, own venv, own CI gate) with heavier dependencies (weather
fetch, GPX, docx/pdf). The proxy gives one origin and one firewall rule
without merging codebases, venvs or CI. It is also the shape a future Docker
split would take (`mx-inv` the exposed gateway, routing internal-network
only), so it does not foreclose that; size it against real `your-pi-hostname`
headroom (`docker stats`) when that migration is real.

**Costs**: two processes to keep alive; a routing-side change needs a
routing rebuild (run-live does it). The launcher is `mx-inv`'s, so a bug
there takes both apps down: on 2026-09-18 a nested `if/else` in
`run-live.bat` died at batch parse time and left both 8000 and 8100 dead
(rewritten with `goto`/labels; see `mx-inv`'s checkpoint).

**Revisit if**: a landing page that launches each module (mx-inv desktop /
cockpit / routing) gets built (logged in `mx-inv`'s Roadmap), or routing
moves to a container.

**Update 2026-09-24**: a container image exists (`Dockerfile`, mx-inv Docker spec v5) with the
same shape - mx-inv the exposed gateway, routing internal on 8100, `config/` and `var/` mounted.
Not deployed; the laptop/Ship's PC launcher arrangement is still production.

## The PENDING-block pattern for unwired or missing data

**Decision**: every optional briefing section checks whether its input data
is present and, if not, renders an explicit `> PENDING` block naming what's
missing and what manual check to run instead — never a silently-omitted
section, never a placeholder that looks like real data.

**Why**: a passage briefing is a safety document. A missing tropical
outlook that just isn't mentioned at all is a worse failure mode than one
that says "PENDING — check nhc.noaa.gov directly," because the reader can't
tell the difference between "checked, nothing to report" and "never
checked" without the explicit marker. This pattern is why `briefing.py`
never raises just because InfluxDB is unreachable, the papers directory is
missing, or a weather API call fails — every failure mode this project
anticipates degrades to a labeled gap in the document, not a crashed run or
a silent one.

## Provenance tiers on external data sources

**Decision**: `sources.toml`'s `weather_source[]`/`data_source[]` entries
each carry a `tier`: `primary`, `professional`, or `secondary`. At least one
enabled primary-tier weather source is required at config load, or
`ConfigValidationError` is raised.

**Why**: not every weather/data source deserves equal trust in a go/no-go
decision. GFS/ECMWF via Open-Meteo are primary (free, keyless, always on);
some sources are marked `professional` (a paid or higher-fidelity feed,
e.g. a a marine forecaster email/IMAP source, currently disabled) or `secondary`
(supplementary, not load-bearing on their own). The tier isn't just
metadata — it's what a human reading the briefing should weight when a
`--compare` run shows GFS and ECMWF diverging: primary-tier disagreement is
the confidence signal `weather/compare.py` actually screens for.

**Correction 2026-10-07 (checked against the code)**: the tiers are enforced only at config load
(at least one enabled primary weather source). No fetcher reads `sources.toml` - every URL is a
module constant - and the briefing does not print `[Primary Source: ...]` tags; the only tier tag
emitted is `[Professional Forecast: Marine Forecast Service]` on the a marine forecaster block. The GRIB,
NWS-text, NDBC and IMAP entries describe sources that were planned, not built. Treat the file as a
registry of intent until a fetcher is wired to it.

## `local_buffer_path` and `route_forecast` config exist before the code that needs them

**Decision**: `InfluxConfig` already carries `write_timeout_s`,
`local_buffer_path` (`var/influx_buffer.jsonl`), and
`route_forecast_measurement` fields, none of which any code in `influx.py`
currently reads or writes.

**Why**: this is deliberate forward-shaped config, not dead weight left
over from a removed feature — it names the intended shape of a future
write-back path (store briefing output locally first, forward it to
InfluxDB once a write-scoped token exists, so a LAN outage mid-passage
doesn't lose a planned write) before the code exists to use it. The
`vessel.toml` comment states plainly that this is "aspirational" and needs
either a separate write-scoped token or the builder widening the existing one —
not yet requested (see [Roadmap](Roadmap.md)). Worth knowing this config
exists before assuming a write path is further along than it is, and worth
keeping if/when the write-back is actually built, since the store-and-
forward shape was chosen up front rather than bolted on later.

**Status 2026-10-07**: the InfluxDB these fields target no longer exists; the fields are dead
config until the re-point decides whether any write-back is wanted.

## Let the builder broker InfluxDB writes (inherited from `mx-inv`'s rule)

**Decision**: this project holds a read-only InfluxDB token and has no
write path at all — not even a disabled one, unlike `mx-inv`'s
`vessel_log.py`/`sail_config.py` brokers, which at least exist in code
(disabled by default) pending the builder's endpoint. This project doesn't have
broker code yet either, because the write-back feature itself hasn't been
built (see [Roadmap](Roadmap.md)).

**Why**: consistent with the same rule `mx-inv`'s wiki documents — a single
owner for the InfluxDB schema/write path (the builder's `your-pi-hostname` stack)
rather than multiple systems writing overlapping measurements
independently. When `route_plan`/`route_forecast` write-back is eventually
built, the expected shape (per the config fields already in place) is a
separate write-scoped token, following the same broker-or-scoped-token
pattern rather than widening the existing read-only token's own scope.

**Status 2026-10-07**: moot as written - the InfluxDB and its broker are retired. Ship's
Automation's own integration pattern applies to anything new (live state from SignalK, history
from its history API, events pushed to the owning app's API). Whether routing should publish a
planned route there at all is open (Roadmap).

## No PDF generation library

**Decision**: `export.py` produces `.md` and `.html` (via `markdown-it-py`)
and `.docx` (via a hand-walked token stream against `python-docx`) — no
PDF output.

**Why**: both the HTML and docx outputs are explicitly styled to "open
clean in Word and Acrobat" and be exported to PDF from there manually,
rather than pulling in a headless PDF rendering dependency (wkhtmltopdf,
WeasyPrint, a browser-automation print-to-PDF, etc.) for a document that's
generated once per passage, not at scale. The float-plan PDF is a separate,
narrower case — filling an existing AcroForm template (`floatplan.py`,
`pypdf`) is a much smaller problem than general Markdown-to-PDF rendering.

## Frontend has no router or client state library

**Decision**: `App.tsx` is one form and one result view with plain
`useState`, no React Router, no React Query, no Redux/Zustand.

**Why**: the whole app is one screen with one request/response cycle — a
router or a server-state library would be solving a problem this scope
doesn't have. Mirrors `mx-inv`'s own "don't add a dependency before a
second real route/screen needs it" posture (see `mx-inv`'s Design
Decisions on its `/cockpit` router choice), applied here from day one
instead of retrofitted.

## Shore-logistics research runs headless Claude on the subscription, one port at a time

**Decision** (2026-09-21): the "Research with Claude" button shells out to the
Claude Code CLI (`claude -p`) rather than calling the Anthropic API. One
subprocess per port, sequential; read-only web tools only; consent on every
run; the result is a draft for the skipper to review, never trusted.

**Why**:
- **Billing:** the owner's constraint is his own subscription, not a second metered
  key. The CLI with `CLAUDE_CODE_OAUTH_TOKEN` does that; `--bare` is never used
  (it ignores OAuth), and `ANTHROPIC_API_KEY` is stripped from the child so a
  stray key cannot turn a run into API billing.
- **One port per subprocess** makes partial failure structural: a timeout, the
  usage cap or a bad reply loses one port and keeps the rest. Sequential because
  four concurrent web-heavy sessions is the worst case for a weekly cap.
- **Read-only tools and a temp working directory** so a hostile web page cannot
  make the run do anything; the worst case is misleading text, which is why
  the review step is mandatory and the panel never overwrites a hand-edited
  section.
- **Assume and flag** for ambiguous names: a headless run cannot ask, so it
  picks the settlement nearest the route and says so in a "Resolved as" line.
- **Failure is never silent:** a failed port becomes the template plus a
  RESEARCH INCOMPLETE note, which the submit guard and section 7b's coverage
  note both catch.
- **The job registry is in `src/`, not `backend/`,** so the pytest and mypy
  gate covers the state machine (backend/ is outside it).
- **Not extended to CIQ, papers or comms:** those are pre-verified registries
  by design; only shore logistics is genuine per-passage lookup.

Evidence and the full reasoning: [Logistics research design](Logistics-Research-Design.md).

## One fleet list, chosen per passage, keel-up draft governs stops

the owner may travel with different boats on different passages, or alone, so the boats live in one
saved list (`config/fleet.json`, edited only through the form) and each passage ticks who is coming.
The web crew never edits a file (same rule as the crew roster and the saved depths). The two facts
that matter most are the water and air draft and whether a boat is a monohull or a multihull, which
changes upwind performance dramatically. A lifting-keel boat's keel-down draft is for open water only;
arrival, departure and anchoring use the keel-up draft (the owner, 2026-09-21). Make and model are stored
for production boats because their characteristics are the natural starting point for a generic polar
later; a converted racer such as Example Buddy is the exception and needs measured data. A multihull's
effect is only flagged for now (a generic upwind rule, not a catamaran polar), and the form uses POST
rather than PUT/DELETE because mx-inv's `/routing` proxy forwards only GET and POST.

## Briefing text is terse facts, no commentary

**Decision** (2026-09-22): every user-facing string the briefing renders — bullets, table cells,
PENDING blocks, registry entries — states a fact or a limitation flatly. It does not narrate,
hedge, or justify. A limitation reads as a labeled callout ("⚠️ Not licensed"), not a sentence
explaining the limitation ("service may not be available here, so confirm before relying on it").
Numbers, thresholds, source citations, and what-to-check instructions are facts and stay; phrases
like "no evidence found," "not independently confirmed," "confirm before relying on this," and
multi-clause justification get cut.

**Why**: the owner reads these briefings operationally — underway or prepping to cast off — not as
research memos. Dense narrative paragraphs slow that down. First applied to `comms.py`'s
per-country registry (the Antilles entries had grown multi-sentence licensing histories) and
`briefing.py`'s section text, then swept into `consular.py`, `notices.py`,
`weather/compare.py`, `weather/tropical.py`. `ops.py` was found to have no rendering code (the
maintenance section's text lives in `briefing.py`), so it needed no change.

**What this actually means in code**: apply this to any new or edited user-facing string in these
modules going forward — don't wait to be told per-section. Line count doesn't necessarily drop
(most trims shorten a sentence on an existing line rather than remove the line), so don't judge
the pass by `wc -l`; judge it by whether a sentence explains a limitation or just states it.

## Currents correct leg speed along the drawn course, not a steering triangle

**Decision** (scoped and built 2026-09-22, weather-routing step 2): a leg's current correction is
`sog_kn = stw_kn + current_kn * cos(current_to_deg - course_deg)` — the current vector's
component projected onto the leg's course line, added to (or subtracted from) the polar/mode
speed already computed. This is *not* a traditional set-and-drift triangle (solving for the
helm correction needed to make good the drawn course through a cross-current) — it only
corrects the along-course speed, hence duration, ETA and fuel.

**Why**: `analysis.py`'s own module docstring states the philosophy this has to fit — "analyses
a *given* route... does not solve for an optimal one," intentionally simple and inspectable, a
beat the skipper drew stays a beat rather than being silently re-routed. A full steering-triangle
solve is itself a small optimizer (find the heading that achieves a target course-over-ground)
and doesn't belong in a leg analyzer whose job is reporting on a route as drawn, not correcting
how to steer it. The along-course-component model is also exactly what
[Roadmap.md](Roadmap.md)'s weather-routing plan already promised ("correct leg speed over
ground"), so this is a clarification of an existing commitment, not a new one. A real
set-and-drift steering correction is a piloting/nav concern for the person at the helm with a
chart, not a pre-departure planning output.

**Consequence — a foul-current guard is required**: if the adverse component is large enough
that `sog_kn <= 0` (current exceeds boat speed dead against the course), the leg cannot be timed
by dividing distance by speed. Flag it (WARNING, "current exceeds boat speed on this leg — not
makeable as drawn at this departure time") instead of computing a negative or near-infinite
duration.

**Data**: Open-Meteo's marine API already serves `ocean_current_velocity`/`ocean_current_direction`
at any point (confirmed live 2026-09-22, MeteoFrance SMOC, ~8 km, hourly) — the same endpoint
`weather/openmeteo.py` already calls for wave data, no new source. Two conventions to keep
straight: `ocean_current_direction` is the direction the current flows **toward** (oceanographic
convention), the opposite of `wind_from_deg`'s meteorological "from" — name the field
`current_to_deg`, never `current_from_deg`, so the two can't be silently confused. `wind_speed_unit=kn`
(already sent on every request) converts `ocean_current_velocity` to knots too — verified live
against a real Gulf Stream-area point, no separate unit-conversion code needed.

**Wind-against-current flag** (the classic Gulf Stream steep-seas hazard): fires when the wind's
downwind direction and the current's set direction are opposed by more than a configurable angle
(default 120°) and both exceed configurable minimums (default TWS 15 kn, current 1.5 kn) — a new
`[safety.current]` config block. These three numbers are seamanship rule-of-thumb, not measured
or sourced from a specific reference — mark them PROVISIONAL/tunable in the config comment and
revisit if a real Gulf Stream crossing shows they're wrong in either direction.

**Deferred to step 7 (post-shakedown)**: calibrating the forecast current against the boat's own
SOG-minus-STW from InfluxDB. Nothing in step 2 blocks on that; it needs real sailing data that
doesn't exist yet.

## Step 3's gridded field provider does not need GRIB — confirmed, not just hoped

**Decision** (scoped 2026-09-22, spiked and built the same day): skip the GRIB-decoding path the
original weather-routing plan called for. `weather/field.py`'s `Field.at(time, lat, lon)` is
backed entirely by `OpenMeteoClient.fetch()` — the same production code path steps 1/2 already
use — over a **corridor-following grid**, not a bounding box. Fall back to raw GRIB (GFS/ECMWF
open data) or the Copernicus Marine client only if a real solver's request volume (step 4) turns
out to exceed what the pacing below allows, not from assuming it upfront.

**What the spike actually found (2026-09-22, three live runs against the real API)**:

1. **A bounding-box grid is the wrong shape and is genuinely dangerous.** `Route.bounding_box()`
   over the real 583 NM FLD→T&C route (a diagonal track) produced a rectangle covering roughly
   6.5° x 9.8° — 260 grid points at 0.5° spacing, mostly empty water and some land, nothing to do
   with the corridor a solver would actually search. Fetching that grid at 4 time slices (1,040
   fixes) tripped Open-Meteo's real per-minute rate limit (`HTTP 429: "Minutely API request limit
   exceeded"`) after only ~52 requests in 3.8 seconds. This is why `corridor_grid()` follows the
   route instead: along-track samples, each offset cross-track by a small set of distances (the
   same "band around the Navionics route" shape the roadmap's step 4 plan already called for) —
   sized for what a corridor-limited solver needs, not the route's full extent.
2. **The rate limit is about burst rate, not a fixed request budget.** A separate calibration
   (80 sequential single-point requests, ~2.5 req/s) completed with zero errors in 32 seconds — far
   more total requests than the 52 that tripped the limit above, at a slower pace. The chunked
   fetch loop in `openmeteo.py` has no inter-chunk delay, so a grid large enough to need many
   chunks fired back-to-back (~14 req/s in the failing run) is the actual risk, not total volume
   over a longer window. A future solver issuing a real departure-window sweep (step 5, many more
   fetches) should keep this in mind — pace chunks or lower `max_points_per_request`, don't assume
   today's small corridor grids generalize to that volume unchanged.
3. **A realistic corridor grid works cleanly.** `corridor_grid(route, along_step_nm=30, cross_offsets_nm=(-15, 0, 15))`
   over the same real route produced 63 points; fetched at 3 time slices (189 fixes) in 1.4 seconds
   with no errors, current data present at the sampled points, and a `.at()` lookup ~18 NM off the
   nearest grid cell returned real wind/wave/current — proving the interface actually answers
   queries away from the drawn track, which is the whole reason a `Field` exists rather than reusing
   the route-forecast path. The disk cache under `var/cache/` (gitignored) made a repeat fetch of
   the same grid+times near-instant (0.01s) with no network call.

**Consequence for the Copernicus Marine account** (registered 2026-09-21 under the owner's account):
keep it — it's the credible fallback path if a real solver's request volume outgrows what
Open-Meteo's burst pacing allows — but nothing is built against it. GRIB decoding for wind/wave
stays deferred the same way.

**Not built as part of this**: the isochrone solver itself (step 4) that would actually call
`Field.at()`. `field.py` is infrastructure step 4 needs, proven against real data, not a working
router.

## The corridor solver's accuracy is bounded by the field's resolution, not just its existence

**Found** (2026-09-22, live-verified, not a design choice to defend — a real limitation to know
about): a live run of `isochrone.py`'s `solve_passage()` against the real FLD→T&C route, using a
field fetched at an economical size (48 points, 3 time slices 24 h apart, chosen specifically to
stay well under the step-3 spike's rate-limit finding), produced a corridor-route duration of
90.2 h for the same 583 NM the fixed-route analyzer solves in 87.7 h for — both saw the *identical*
distance (the corridor never found a lateral detour worth the current available at that
resolution), yet the solver's own passage still came out slower.

**Why**: `isochrone.py`'s `_hop()` looks up conditions once per hop, via `Field.at()`'s
nearest-point/nearest-time match against whatever grid was fetched. `analysis.py`'s fixed-route
`analyze()` instead aggregates (mean/circular-mean) *every* forecast point that lands on a leg —
for the live run above, about 39 finely-sampled points along the exact drawn track, from one
purpose-built route-forecast fetch. A field fetched coarsely to stay well clear of Open-Meteo's
rate limit is, by construction, a worse approximation of the real wind at any given hop than a
forecast fetched densely along one specific track. This is not a bug in either path — each is
doing the right thing for what it was given — but it means **a solved passage is only as good as
the field it's handed**, and a caller economizing on fetch size (reasonable, given the rate-limit
finding above) is trading some of that accuracy away. Bump `fetch_field()`'s point count and time
slices for a more faithful solve; the trade against request budget is the real constraint, not
anything in the solver's own logic.

**Consequence for step 5** (the departure-window sweep): that step multiplies the number of times
a field has to be fetched (every 6 h for 5 days, per the original plan) — the resolution-vs-budget
tradeoff found here gets sharper, not smaller, and is worth designing around explicitly rather than
reusing whatever grid size happened to work for a single-departure test.

## The sweep uses the cheap fixed-route analyzer, not the corridor solver, and has not been tried at full scale

**Decision** (2026-09-22): `sweep.py`'s `sweep_departures()` solves each candidate departure with
`analysis.analyze()` (a single pass over one route-forecast fetch), not `analysis.refine()`'s
multi-iteration convergence and not `isochrone.solve_passage()`'s per-leg corridor search. The
roadmap's stated shape — every 6 h for 5 days, on two models — is 40 candidate solves before either
of those richer paths is even considered.

**Why**: `refine()` re-fetches up to `max_iterations` (default 4) times per call to converge the
ETA; multiplied across 40 candidates that is 160+ forecast fetches in one sweep, and
`solve_passage()` needs its own `Field` fetched per candidate (a `corridor_grid()` covering the
whole route, at however many time slices that candidate's corridor search needs) — both are a
different order of magnitude of request volume from anything tried live so far, working against
the exact rate limit the step-3 spike found (a burst of ~52 requests in under 4 seconds tripped a
429). A single `analyze()` pass per candidate keeps the cost to one fetch (two HTTP requests) per
departure/model pair — 40 candidates is 80 requests, the same order of magnitude as the 80-request
calibration that succeeded cleanly at a paced ~2.5 req/s in the step-3 spike, though not identical
conditions. A sweep's actual job is *relative* ranking across departures, not each candidate's
individual precision, so trading `refine()`'s convergence for one honest pass is the right cut for
v1, not a hidden shortcut.

**What has and hasn't been verified**: a 3-candidate, 1-model sweep (6 requests) ran live against
the real FLD→T&C route in 1.2 seconds with sane, differentiated results. The roadmap's full stated
scale — 40 candidates across two models, 80 requests — has deliberately **not** been tried live,
because the honest answer is "probably fine, given the calibration number above, but not the same
request shape and not confirmed." Whoever runs a full sweep for the first time should watch for
429s rather than assume it will complete, and this module still adds no pacing or throttling of its
own — that is the natural next hardening step if a full-scale run does trip the limit, not
something to pre-build speculatively before it's known to be needed.

**Why `isochrone.solve_passage()` isn't wired to anything yet, including the sweep**: it needs a
`Field`, and fetching a fresh field per sweep candidate is the request-volume problem above,
multiplied by whatever a corridor search needs internally. A sweep that ranks departures with the
cheap analyzer, then re-solves only the winning departure with the corridor solver for a real
route suggestion, is the natural next integration — not built now, flagged here so a future session
doesn't have to re-derive why the two are still separate.

## a marine forecaster's professional forecast: peak-wind comparison only, and always optional

**Decision** (2026-09-22, weather-routing step 6): `marine_forecaster.py` extracts a single number from
a pasted MWXC email — the highest knot figure in its WIND section — and compares that against
`PassageAnalysis.peak_wind_kn`. It does not attempt to parse per-area, per-time-window figures and
match them against specific legs. Briefing section 3d renders only when
`BriefingInputs.parker_text` is actually set; when it is `None` (the normal case — the owner is not
subscribed most of the time), the section is omitted entirely, not replaced with a **PENDING**
block the way every other not-yet-wired feed in this project is.

**Why a single peak figure, not per-area parsing**: two real archived MWXC emails (2024-09-20 and
2024-10-30, from the owner's email, the "200 archived updates" the roadmap names as the test corpus)
confirmed the section structure is consistent (TROPICS/ASCAT/BUOYS/IMAGERY/SYNOPSIS/OUTLOOK/
SUGGEST/PRECIP/WIND/SWELLS) but the *area names inside SUGGEST/PRECIP/WIND* are not — each update
free-hands a different partition of the Caribbean, worded differently. Reliably mapping "this leg,
this time window" onto "the right clause in this paragraph" is not a tractable regex problem, and
a wrong per-leg match would be worse than no match — it would look authoritative and be silently
wrong. MWXC's own notation always writes a wind figure as `<number>k`, so a maximum over the WIND
section's figures is a real, defensible "worst wind the professional is describing for the region
this update covers." That is a coarser signal than a per-leg figure, but it is an honest one, and
it is exactly the "does the professional's read of this region look a lot worse than the models?"
cross-check the roadmap actually asked for.

**Why "absent means omitted," breaking the project's own PENDING-block convention**: every other
deferred section (NHC tropical, ENC depth, the CIQ portals) is a feed this tool is *supposed* to
have and doesn't yet — a real gap worth calling out every time. a marine forecaster is different in kind:
it is a subscription the owner turns on and off around specific passages (see `config/sources.toml`'s
`mwxc` entry, `enabled = false` by default), so "nothing was pasted" is the expected steady state,
not a gap. A PENDING block here would nag on every briefing the owner generates while not subscribed,
which is most of them.

**Why the real archived emails aren't committed as test fixtures**: MWXC is a personal-use paid
subscription ("Subscriber content: personal use, check terms before republishing excerpts").
`tests/fixtures/parker_sample.txt` is an original synthetic sample
built to match the real structure and notation exactly (verified against the two real emails
above), not a republished excerpt — the parser is proven against the real *shape* of the data
without redistributing anyone's proprietary forecast text.

## The corridor solver and sweep get a CLI surface before a web one

**Decision** (2026-09-22): `passage-planner solve` and `passage-planner sweep` are real CLI
subcommands now, both marked **EXPERIMENTAL** in their own `--help` text. Neither gets a FastAPI
endpoint or a React panel in this pass.

**Why CLI first**: both features carry open, load-bearing caveats from their own build sessions —
the solver's field-resolution accuracy tradeoff, the sweep's untested-at-full-scale request volume
(see the two entries above). A web form invites exactly the request pattern those caveats warn
against (a person clicking "sweep" with the roadmap's full 40-candidate/2-model default, no sense
of the request-volume cost behind the button) before either has been proven at that scale from a
terminal, where the caller sees every warning and error directly. `--help` on both names the
EXPERIMENTAL status and points at the relevant Design Decisions entries; a web form has no
equivalent place to put that caveat where a user reliably reads it first.

**What "EXPERIMENTAL" actually means here**: not "untested" — both are unit-tested and
live-verified at small scale against the real FLD→T&C route. It means "the caller is expected to
have read the caveats," which a CLI `--help` can assume more safely than a web form can.

## A buddy boat's real polar comes from a paste, never a scrape or a fetch

**Decision** (2026-09-22): the Fleet form's "Look up this boat" button (`boat_lookup.py`,
`POST /api/fleet/lookup`) parses text a person pastes from MarineTraffic's vessel Overview page
and/or an ORC Sailboat Data certificate (orc.org, mirrored by github.com/jieter/orc-data). It never
fetches either URL itself. When the ORC text includes its VPP speed table, the parser builds a real
per-boat `Polar` from it — not a generic/PROVISIONAL estimate — which a buddy boat can then carry in
its own `polar_file`.

**Why paste, not fetch**: neither source documents a stable public API. `orc-data`'s own README
names no endpoint, no response shape, and no rate limit — it is a scraper against orc.org's pages,
not a contract. MarineTraffic's page structure is likewise undocumented and account-gated for
anything beyond the Overview tab. Scraping either from this app would be a standing maintenance
liability (any page redesign silently breaks the parser) for a feature used rarely — adding a new
buddy boat, or refreshing one's specs, is not a per-passage action. Pasting also solves a problem no
scrape could: **MarineTraffic has duplicate vessel names**, so picking the right one is a judgment
call only a person looking at the search results can make; this app is never in a position to
"search MarineTraffic for X" and get back the right boat unattended. ORC certificates are per boat
by construction, so this doesn't apply there, but the same paste-in mechanism serves both.

**Why this doesn't conflict with "pure consumer, never a deriver" above**: that decision is about
*this vessel's own* polar, and specifically about never fitting/regressing one from telemetry —
the builder's job. A buddy boat's ORC-certificate polar is neither: it's not this vessel's, and it isn't
derived from anything, it's *parsed verbatim* from a rated, measured VPP table someone else already
computed. `boat_lookup.py` has no fit/regression logic of its own, same spirit as `polar.py`.

**What this replaced**: before this, a buddy boat had no speed model at all — `analysis.py` only
ever flagged a multihull's upwind legs against a fixed 60° TWA heuristic
(`MULTIHULL_UPWIND_TWA_DEG`), regardless of how fast or slow she actually was, and only upwind. A
buddy boat with a real polar on file is now checked on **every** leg, any point of sail, against the
fleet target speed (`BUDDY_BELOW_TARGET`); the crude heuristic still fires, unchanged, for a buddy
boat with no polar on file. The two are mutually exclusive per boat, never both.

**Trust boundary**: every parsed field lands in an editable, already-populated form field — never
written to `config/fleet.json` directly. A person reviews (and can correct) what was found before
pressing Save, same as every other Fleet-form field. `fleet.py`'s `boat_from_dict` also refuses a
`polar_file` value that isn't a plain relative path (no `..`, no leading `/`, no absolute path) —
defense in depth, since `backend/main.py` never accepts a client-supplied `polar_file` in the first
place; it only ever accepts `polar_pol_text` and computes the filename itself from the boat's id.

## A renamed, hand-edited research section leaves an orphan - now visible, not silent

**Found** (2026-09-22, live browser testing of the shore-logistics research try-list): hand-edit a
researched port's section, then use "Wrong place? Research as…" to correct its resolved name (a
realistic sequence - e.g. correcting "Georgetown" to "George Town, Great Exuma, Bahamas" after
noticing the ambiguity). `App.tsx`'s `renamePort` deliberately leaves the old `### <old name>`
heading and body untouched when the section isn't pristine (see its own comment - this is right,
not a bug: guessing how to merge a hand edit into a renamed heading would risk mangling it). But the
subsequent research run for the new name then lands as a **second, new** `### <new name>` section,
because `applyResearch`'s pristine-comparison has nothing to compare the new heading against (it
never existed before) and so never triggers the hand-edit-protection path either. Net effect: two
headings for what is now one stop, the old one orphaned (not linked to anything in "Ports for shore
logistics"), and **no warning anywhere** - it would silently ride into the generated briefing.

**Fix**: `logisticsSections.ts` gained `removeSection()`, and `App.tsx` now computes
`orphanedSections` (every `### ` heading in the Markdown that doesn't match a current stop label) on
every render and shows a warning banner listing each one with a "Remove this section" button. This
doesn't change `renamePort`'s behavior at all - leaving a hand edit alone on rename is still correct
- it just makes the leftover visible and gives a one-click way to clean it up, instead of requiring
a scroll through raw Markdown to notice it.

**Why not detect and merge automatically**: the two sections could plausibly be reconciled (keep the
hand edit, drop the fresh research, or vice versa, or interleave both), but that is exactly the kind
of judgment call this project avoids automating on the skipper's behalf (see the shore-logistics
research design's "a person reviews before it's used" principle throughout). A visible orphan with a
one-click remove is enough; silent merging is not.

## Documents and Comms forms write papers.toml/comms.toml directly - a machine-written file, not hand-curated prose

**Decision** (2026-09-22): the new Documents and Comms screens (`DocumentsPanel.tsx`/`CommsPanel.tsx`,
`GET/POST /api/papers` and `/api/comms`) write `config/papers.toml`/`config/comms.toml` directly -
not a JSON overlay like `fleet.json`/`depth_soundings.json`. A save fully regenerates the file from
the current form state: the old free-text file header comment is replaced by a generic one, a
document's TOML table title becomes `KIND_LABEL[kind]` (never user-editable - `load_overrides` only
ever reads `kind`, so the title was already just a label, see the existing "one override per
DocKind" behavior), and DocKind rows come out in enum order rather than whatever order they were
originally typed in. None of that is data loss - every `path`/`issued`/`expires`/`note` value
round-trips byte-for-byte (verified live against the real `config/papers.toml`, restored after)
- it is the accepted cost of a form-owned file, the same way `fleet.json`'s save already discards
whatever hand-formatting `vessel.toml`'s seed data had.

**The one real bug this surfaced and fixed before shipping**: a naive single-line TOML string writer
flattens embedded newlines to spaces, so a hand-written multi-paragraph note (the FCC licence entry
has one with three `STEP N:` paragraphs) round-tripped as one run-on paragraph - functionally
harmless (all the words survive) but a real readability regression for anyone reading the raw file.
Fixed before this ever went live: `_toml_str()` (duplicated in `overrides.py` and `comms.py` - small
enough not to warrant a shared module) writes a TOML multi-line basic string (`"""..."""`) whenever
the value contains a newline, preserving paragraph breaks, and only falls back to the flattening
single-line form for the one case a multi-line string can't safely represent - a value that itself
contains a literal `"""`.

**Why write TOML directly instead of a JSON overlay**: CHECKPOINT.md named this design outcome before
any code existed ("documents and comms forms ... that write `papers.toml` / `comms.toml`"), and both
files are already meant to be hand-curated, occasionally Claude-populated data (see the papers.toml
module docstring, and `wiki/Design-Decisions.md`'s note that papers/comms/CIQ are "pre-verified
registries by design") - not per-passage state like the fleet list or saved depths. A form is just a
friendlier way to edit that same file; there is no need for the JSON-overlay-plus-seed-file pattern
`fleet.py` uses, since there's no "vessel.toml seed vs. saved override" distinction to preserve here
- the TOML file already *is* the one source of truth, before and after this change.

**Advanced-options uploads stay**: `papers_file`/`comms_config` in Advanced options are untouched -
they remain the escape hatch for a one-off override that shouldn't touch the real config file (e.g.
testing), which the form-based screens intentionally don't support.
