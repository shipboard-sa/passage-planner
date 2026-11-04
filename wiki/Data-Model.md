<!--
Purpose: Every file passage-planner reads or writes - config, reference data, saved libraries,
    caches, outputs - with field meanings, units, invariants, and the briefing's output format.
Version: v1 (2026-10-07) - new page for the rebuild-from-spec wiki pass; replaces the short
    config inventory that was in Operations.md (now a pointer here). Verified against the code
    (config.py, fleet.py, hydro/soundings.py, arrival.py, polar.py, briefing.py) on 2026-10-07.
Scope: PRODUCTION - engineering wiki for passage-planner (
Architecture: passage-planner documentation. Linked from Home.md. Module-level detail is in each
    file's own header; this page is the cross-file view.
Invocation: n/a (reference doc)
-->

# Data model

passage-planner has **no database of its own**. State lives in small files: hand- or form-maintained
config under `config/`, Claude-maintained reference data under `data/` and in code registries,
caches and outputs under `var/`. Paths in config resolve from the repo root (` unless
absolute (`service.resolve_path`). Secrets are never in any of these files: a field ending in `_env`
names an environment variable.

## Files at a glance

| Path | In git | Written by | Read by |
|---|---|---|---|
| `config/vessel.toml` | yes | hand | everything (`config.load`) |
| `config/sources.toml` | yes | hand | `config.load` (validation only, see below) |
| `config/baseline.pol` | yes | hand | `polar.load_polar` (current polar, see Operations) |
| `config/crew.toml` | yes | hand | float plan, `/api/crew` |
| `config/float_plan.toml` | yes | hand | `floatplan.py` |
| `config/papers.toml` | yes | Documents form (`/api/papers`) or hand | `papers/overrides.py` |
| `config/comms.toml` | yes | Comms form (`/api/comms`) or hand | `comms.py` |
| `config/fleet.json` | yes when it exists | Fleet form only (`/api/fleet`) | `fleet.py` |
| `config/polars/<boat-id>.pol` | yes when it exists | Fleet form's "Look up this boat" | `service.resolve_buddy_polars` |
| `config/depth_soundings.json` | yes when it exists | Depth check form only | `hydro/soundings.py` |
| `waypoint_depths.toml` (repo root) | yes | hand (legacy, CLI `--depths-file`) | `hydro/depths.py` |
| `data/arrival_places.toml` | yes | Claude sessions via `python -m passage_planner.arrival merge` | `arrival.py`, `/api/arrival-places` |
| `data/coastline_caribbean.geojson` | yes | one-off (Natural Earth) | `coastal.py` |
| `var/briefings/<stem>.*` | no | CLI `brief` | the owner; mx-inv cockpit (newest `.md`) |
| `var/web_briefings/<run-id>/` | no | web `/api/brief` | download endpoint |
| `var/web_uploads/` | no | web, per request, deleted in `finally` | the run |
| `var/logistics/` | no | research jobs; hand-researched files | the owner, `--logistics-file` |
| `var/cache/coops_stations.json` | no | `hydro/tides.py` (30 days) | tides |
| `var/cache/state_dept/state_dept.json` | no | `consular.py` (24 h) | section 6 |
| `var/cache/<key>.json` | no | `weather/field.py` (corridor fields) | `solve` |
| `var/polar_baseline.pol` | no | `export-polar` | qtVlm / OpenCPN / Windy |
| `var/influx_buffer.jsonl` | no | nothing yet (reserved for write-back) | - |

`config/fleet.json`, `config/polars/` and `config/depth_soundings.json` did not exist on disk on
2026-10-07: the fleet is still `vessel.toml`'s `[[buddy_boat]]`, and no depth has been saved.

## `config/vessel.toml` (`config_version = 1`)

| Table | Key fields (units) | Notes |
|---|---|---|
| `[vessel]` | `name`, `model`, `designer`, `rig`, `year`, `mmsi`, `loa_ft`, `beam_ft`, `displacement_lb`, `influx_vessel_tag` | identity; `name` is printed in the briefing |
| `[vessel.draft]` | `static_ft`, `actual_min_ft`, `actual_max_ft`, `planning_ft` (8.0), `ukc_margin_ft` (2.0) | **only `planning_ft` + `ukc_margin_ft` drive depth math** |
| `[vessel.air_draft]` | `masthead_measured_ft`, `antenna_ft`, `freeboard_ft`, `planning_ft` (70), `clearance_buffer_ft` (5) | required clearance = planning + buffer |
| `[vessel.water]` | `tank_gal`, `per_person_gpd`, `watermaker_installed`, `watermaker_commissioned`, `watermaker_gph` | water autonomy in 7b |
| `[propulsion]` | `engine`, `rated_hp`, `fuel_capacity_gal`, `fuel_reserve_frac` | |
| `[propulsion.cruise]` | `speed_kn`, `burn_gph`, `motorsail_burn_gph` | motor / motorsail speed and burn |
| `[performance]` | `polar_source` (`baseline` or `derived`), `polar_file`, `fleet_target_avg_kn`, `light_air_speed_kn`, `hull_max_speed_kn`, `reef_heel_deg` | see Logic Flows for how each is used |
| `[safety.thresholds]` | `wave_hs_max_m`, `wind_sustained_max_kn`, `wind_gust_max_kn`, `beat_twa_min_deg` | leg WARNING flags |
| `[safety.current]` | `wind_against_current_tws_min_kn`, `_current_min_kn`, `_angle_deg` | required section; PROVISIONAL rule-of-thumb values |
| `[safety.watch_alert]` | wind change, wind shift, 3 h pressure drop, CPA | printed verbatim in the watch orders |
| `[safety.depth]` | `tide_gate_enabled`, `live_sounder_available`, `sounder_depth_path` | |
| `[[buddy_boat]]` | `name`, `mmsi`, `draft_ft` (keel down), `lifting_keel`, `draft_up_ft`, `air_draft_ft`, `telemetry`, `notes` | the fleet until `fleet.json` is first saved |
| `[shore_contact]` | `name`, `email` | float-plan summary (personal data - do not copy into docs) |
| `[infra]` | `timezone` | fallback zone only |
| `[infra.influx]` | `url`, `org`, `org_id`, `bucket`, `bucket_id`, `token_env`, `route_forecast_measurement`, `read_timeout_s`, `write_timeout_s`, `local_buffer_path`, `enabled` | env `HYLAS_INFLUX_URL` overrides `url`. **The server this points at no longer exists** - see [Data Flows](Data-Flows-and-Interfaces.md) |
| `[infra.signalk]` | `url`, `token_env`, `enabled` (false) | parsed, **not used by any code** |
| `[infra.ops_panel]` | `db_path` (`../mx-inv/vessel_local.db`), `soon_buffer_days`, `enabled` | maintenance-due read |
| `[infra.papers]` | `records_dir`, `extra_dirs`, `acquired_after`, `overrides_path`, `enabled` | papers scan |

Values marked `# PROVISIONAL` in the file are estimates awaiting measurement. The loader
(`config.py`) builds frozen dataclasses and raises `ConfigValidationError` on a missing required
table, a wrong type, or a bad value.

## `config/sources.toml` (`config_version = 1`)

`[[weather_source]]` and `[[data_source]]` entries: `id`, `kind` (`grib`, `text`, `tropical`,
`email_text`, `json`, `chart`, `observations`, `tides`), `tier` (`primary` / `professional` /
`secondary`), `enabled`, `provider`, `endpoint`, plus kind-specific options. The loader requires at
least one enabled primary weather source.

**Finding (2026-10-07): the registry is validated but not consulted by any fetcher.** Every fetch URL
is a constant in its module (Open-Meteo in `weather/openmeteo.py`, NHC in `weather/tropical.py`, NGA
in `notices.py`, CO-OPS / ENC / GEBCO in `hydro/`, State Department in `consular.py`). The GRIB
entries (NOMADS GFS, GFS-Wave, HRRR, RTOFS, ECMWF open data), the NWS text products, NDBC and the
`mwxc` IMAP ingest are **declared but not built**; the models are reached through Open-Meteo instead
(see [Data Flows](Data-Flows-and-Interfaces.md)). Changing an endpoint in this file changes nothing.

## Polar files (`.pol`)

Tab-separated grid (the qtVlm / Expedition / OpenCPN format; Windy's polar plugin reads it). First
non-comment row: `twa/tws` then the TWS axis in knots. Each following row: a TWA in degrees, then
boat speed in knots per TWS column. `#` lines are comments; a `# PROVISIONAL` comment (or
"provisional" in the filename) sets `Polar.provisional`. Separators `,`, tab, `;` or space are all
accepted on read. `polar.py` interpolates bilinearly and caps every cell at `hull_max_speed_kn`.

`config/baseline.pol`: TWA 0-180 (16 rows), TWS 4-25 kn (10 columns), hand-estimated, PROVISIONAL.
TWA 0 and 30 are zero (no sailing that high).

## `config/fleet.json` and `config/polars/`

```json
{"boats": [ {"name": "SV Example Buddy", "hull": "monohull", "make": "", "model": "",
  "draft_ft": 14.0, "lifting_keel": true, "draft_up_ft": 8.0, "air_draft_ft": 90.0,
  "loa_ft": null, "beam_ft": null, "displacement_lb": null, "mmsi": "...", "call_sign": "",
  "notes": "", "telemetry": false, "usual_companion": true, "polar_file": ""} ]}
```

Fields are `config.BuddyBoat`; a boat's `id` is not stored, it is the slug of its name (so a rename
is a replace, `BoatSaveIn.replaces`). `hull` is one of monohull / catamaran / trimaran. Limits enforced by
`fleet.boat_from_dict`: draft <= 30 ft, air draft <= 200 ft, LOA <= 300 ft, beam <= 80 ft,
displacement <= 2,000,000 lb, text length caps. `polar_file` is set **server-side only** to
`polars/<id>.pol` when a parsed ORC polar is saved; a client-supplied path is never accepted (a path
traversal guard). Sorted by name, written atomically (temp file + replace).

## `config/depth_soundings.json`

```json
{"soundings": [ {"id": "...", "lat": 26.7, "lon": -79.0, "depth_ft": 14.5,
  "source": "Navionics", "notes": "", "label": "WP5", "recorded": "2026-09-21"} ]}
```

Depth in feet at chart datum as shown on the chart; 0 < depth <= 200 ft. Keyed by **position**: a
waypoint within 0.5 NM uses it (shallowest wins when several match); saving within 0.1 NM of an
existing sounding updates that one. Written atomically.

## `config/papers.toml`

One table per document: `[documents."<label>"]` with `kind` (a `papers.DocKind` value), `path` (file
under the papers directories), `issued`, `expires` (ISO dates), `note` (multi-line allowed). The 16
DocKinds: vessel_documentation, radio_licence, radio_operator, epirb_registration, insurance_policy,
insurance_proof, passport, boater_education, fishing_licence, marpol_placard, float_plan_template,
cbp_decal, survey, crew_id, dinghy_registration, other. An override always wins over the filename
scan. **No document numbers, dates of birth or account numbers** - validity dates and operational
notes only. The Documents form rewrites the whole `[documents]` table on save (multi-line notes kept
as TOML `"""` strings).

## `config/comms.toml`

`[starlink]` (`plan`, `billing_cycle_day` 1-31, `ocean_mode_usd_per_gb`, optional `priority_gb`,
`notes`), `[t_mobile]` (`plan`, `roaming_included`, `notes`), `[maya_esim]` (`scope`, `notes`),
`[router]` (`model`, `notes`). Vessel-level facts only; per-country connectivity is the `comms.py`
registry (21 countries). The Comms form rewrites all four tables.

## `config/crew.toml` and `config/float_plan.toml`

`[[crew]]`: `key` (lookup key, case-insensitive), `name`, `phone`, `gender`, `dob` (ISO), `plb_uin`.
A guest is given inline as `Name:phone:gender:YYYY-MM-DD` and gets no PLB.

`float_plan.toml`: `[vessel]` (hailing port, official number, HIN, type, hull material, colours,
features, anchor line ft), `[comms]` (call sign, phones, operator email, EPIRB UIN, tracking note),
`[equipment] aboard` (list), `[operator]`, `[contacts]` (rescue authority and phone). These fill the
static half of the 346-field USPS Float Plan AcroForm; the itinerary half (up to 21 rows: depart,
waypoints, arrive) comes from the passage.

Both files hold personal data and are committed to the **private** repo; never quote their values in
the wiki.

## `data/arrival_places.toml`

Schema, loader rules and maintenance: [Arrival places](Arrival-Places.md). 310 `[[place]]` entries,
34 countries (161 port_of_entry, 69 harbour, 80 anchorage) on 2026-10-07. Required per place: `id`,
`name`, `country`, `lat`, `lon`, `kind`, `clearance`, `sources`, `verified`.

## Code-resident reference registries

| Registry | Module | Content | Verified |
|---|---|---|---|
| CIQ dossiers | `ciq/data.py` `REGISTRY` | USA, Bahamas, Turks and Caicos, BVI, USVI, Dominican Republic, Puerto Rico (+ aliases) | 2026-09-09 (BVI fee change 2026-08-01 flagged) |
| Comms by country | `comms.py` `REGISTRY` | 21 countries: Starlink licensing, T-Mobile roaming, Maya eSIM | 2026-09-10 / 2026-09-14 (Antilles) |
| Arrival-place countries and boxes | `arrival.py` `COUNTRIES`, `COUNTRY_BOXES` | allowed ISO codes and a coarse lat/lon box per country | - |
| Logistics fields | `logistics.py` `LOGISTICS_FIELDS` | the 15 per-port fields (groceries ... security, notes) and their prompts | - |
| Stop roles | `research.py` `STOP_ROLES` | check-in / check-out / fuel instructions for the researcher | - |

Re-verify CIQ fees each season.

## Shore-logistics Markdown (`var/logistics/*.md`, the 7b textarea)

```
# Shore logistics — <route> — draft
### <Port label>
- **Groceries:** ...
- **Chandlery:** ...
... one bullet per LOGISTICS_FIELDS label ...
- **Notes:** ...
Sources: <urls>
```

A port section is `### <name>`; the coverage check counts a section as unresearched when at least
half of its checked fields are still template text (`_UNRESEARCHED_FRACTION = 0.5`). Research
output adds a "Resolved as" line for an ambiguous name.

## The briefing (output format)

One Markdown document (`briefing.build_briefing`), exported to `.md`, styled `.html`
(markdown-it-py, CommonMark + tables) and native `.docx` (python-docx, hand-walked tokens). Filename
stem = slug of the GPX route name unless `--stem`. Section order is fixed:

| Section | Content |
|---|---|
| 1. Executive Voyage Briefing | route, depart/ETA (local), distance, sail/motor split, fuel and margin, critical hazards, fleet notes |
| 2. Vessel & Planning Assumptions | drafts, air draft, engine, **polar actually used**, fleet draft for shared stops |
| 3. Weather Picture | 3a tropical outlook (NHC), 3b model summary along route, 3c forecast confidence (GFS vs ECMWF, `--compare`), 3d professional forecast (only when Parker text was given) |
| 4. Step-by-Step Weather Routing Analysis | per leg: local time, wind, TWA / point of sail, seas, current, mode, SOG, ETA, flags |
| 5. Depth, Clearance, Tide & Navigational Notices | per-waypoint depth / tide / gate; 5d NGA broadcast warnings, SAR split out |
| 6. Arrival & Immigration Dossier | per-country CIQ, State Department advisory + embassy, Document readiness |
| 7. Logistics & Support Matrix | 7a fuel (computed), 7b water / provisioning / dinghy + per-port logistics, 7c preventative maintenance, 7d communications configuration (per country, Calendar check) |
| 8. Watch Plan | rotation from crew count and watch length; watch-standing orders |
| 9. Float Plan | summary for the shore contact (+ optional USPS PDF beside it) |

Any feed that was skipped or failed renders `> PENDING ...` with the manual check instead. The mx-inv
cockpit parses the generated line, tropical headline and weather summary out of the newest `.md`, so
renaming these headings is a cross-repo change (mx-inv `backend/routers/cockpit.py`
`parse_briefing`).

Web run outputs: `var/web_briefings/<run-id>/<stem>.md|.html|.docx` and, with a float plan,
`<stem>_float_plan.pdf`. The run index (`_RUNS`, run id -> file kinds) is in memory only.
