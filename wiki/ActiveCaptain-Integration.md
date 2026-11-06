<!--
Purpose: How hylas-routing integrates Garmin's ActiveCaptain Community API for marina, fuel, bridge,
    hazard and anchorage data along a passage route.
Version: v1 (2026-11-06) - initial build: tile-based SQLite export model, data schema, briefing
    wiring (sections 5c and 7a-ii/7b), configuration, API workflow, limitations.
Scope: PRODUCTION - engineering wiki for hylas-routing (D:/boat/routing/).
Architecture: hylas-routing documentation. Linked from Home.md.
Invocation: n/a (reference doc)
-->

# ActiveCaptain Community Integration

The [ActiveCaptain Community](https://activecaptain.garmin.com) is Garmin's crowd-sourced marine
database: boaters add and update markers for marinas, fuel docks, bridges, hazards, anchorages,
boat ramps, inlets, locks and more. The data is the same content visible in the free ActiveCaptain
web and mobile apps and on Garmin chartplotters.

hylas-routing uses the **ActiveCaptain Community third-party API** to download tile-based SQLite
exports covering the route corridor, then queries them locally for POIs near each waypoint. The
results feed into briefing sections 5 (bridge clearances, hazards) and 7 (fuel stops, transient
dockage). No Garmin device or subscription is required to use the API.

## API access

The API is free. Registration is through the
[Garmin Developer Portal](https://developer.garmin.com/active-captain/overview/): request access,
provide a privacy policy URL, and receive API keys. Stage keys are approved immediately; production
keys require Garmin review.

- **SDK:** [ActiveCaptainCommunitySDK-common](https://github.com/garmin/ActiveCaptainCommunitySDK-common)
  (Apache-2.0), published by Garmin. Contains the C++ reference implementation, the SQLite schema,
  and the API contracts our Python client was built from.
- **API key:** runtime env var `ACTIVECAPTAIN_API_KEY`, never stored in config files or committed.
- **Stage vs production:** `[infra.activecaptain].env` in `vessel.toml` (`"stage"` or
  `"production"`). Stage uses `activecaptain-stage.garmin.com`; production uses
  `activecaptain.garmin.com`. The stage data set may be smaller or synthetic.

No rate limits, usage caps, or commercial-use restrictions are documented on the developer portal
pages as of 2026-11-06. The general Garmin Terms of Use apply.

## API workflow

The API is **not** a per-query REST service. It uses a tile-based export model designed for
offline-capable applications:

```
 route waypoints
       |
       v
 1. Compute bounding box (route ± search_radius_nm)
       |
       v
 2. POST bounding box -> /api/v2/points-of-interest/tiles
       |                  returns list of (tileX, tileY) coordinates
       v
 3. POST tile coordinates -> /api/v2/points-of-interest/export
       |                      returns per-tile download metadata:
       |                      signed Azure Blob Storage URL, MD5, file size
       v
 4. Download gzipped SQLite files (one per tile)
       |  - MD5 verified against the API's reported hash
       |  - decompressed and validated (markers table must exist)
       |  - cached under var/cache/activecaptain/tile_<x>_<y>.db
       |  - 7-day TTL (configurable); fresh tiles skip download
       v
 5. Query each tile's SQLite with R-tree spatial index
       |  - rIndex table stores coordinates as Garmin semicircles (int32)
       |  - conversion: degrees * (2^31 / 180)
       |  - filter by POI type bitmask and bounding box
       v
 6. Build structured dataclasses, compute distance to nearest waypoint
       |
       v
 7. Sort by distance, return list[RoutePoi]
```

Auth is a single `apikey` HTTP header on steps 2 and 3. The tile downloads (step 4) use signed
URLs that require no additional auth.

## SQLite tile schema

Each tile is a standalone SQLite database. The tables queried by `activecaptain/query.py`:

| Table | Key columns | Notes |
|---|---|---|
| `markers` | `id`, `poi_type`, `name`, `searchFilter` | `poi_type` is a bitmask (see below); `searchFilter` encodes service flags |
| `rIndex` | `id`, `minLat`, `maxLat`, `minLon`, `maxLon` | R-tree spatial index; coordinates in Garmin semicircles |
| `navigation` | `id`, `labeled` (JSON), `sectionNote` (JSON), `distanceUnit` | depth, tide, current, bridge type/height, restrictions |
| `contact` | `id`, `phone`, `vhfChannel` | |
| `fuel` | `id`, `dieselPrice`, `gasPrice`, `currency`, `volumeUnit`, `yesNo` (JSON), `labeled` (JSON), `sectionNote` (JSON), `distanceUnit` | |
| `dockage` | `id`, `labeled` (JSON), `sectionNote` (JSON), `distanceUnit` | total/transient slips, max LOA/beam |
| `reviews` | `markerId`, `rating` | aggregated for average rating and count |
| `translations` | `id`, `langId`, `translation` | resolves `fieldTextHandle` IDs in JSON fields; `langId=1` = English |

### POI type bitmask

```python
MARINA     =    1
HAZARD     =    2
ANCHORAGE  =    4
BOAT_RAMP  =    8
BUSINESS   =   16
COMMUNITY  =   32
INLET      =   64
LOCK       =  128
DAM        =  256
BRIDGE     =  512
FERRY      = 1024
```

### JSON field encoding

The `labeled`, `yesNo`, and `sectionNote` columns store JSON. Structured fields use a
`fieldTextHandle` integer that resolves through the `translations` table:

```json
[{"fieldTextHandle": 42, "value": "3.5"}, ...]
```

The resolved text gives the field name (e.g. "Depth at Approach", "Bridge Type", "Diesel"). Values
may be numeric strings, text handles themselves (`valueTextHandle`), or plain text.

### Unit handling

`distanceUnit` in `navigation`, `fuel`, and `dockage` tables: `1` = feet, `2` = meters, `0` =
unset (treated as meters, since the data set is metric-first). All depth/height/clearance values
are converted to feet in `query.py` before reaching the briefing.

## Data model (`activecaptain/model.py`)

All frozen dataclasses with `slots=True`:

| Class | Fields | Used for |
|---|---|---|
| `RoutePoi` | `poi` (one of the below), `distance_nm`, `nearest_wp_index` | the container returned by `near_route()` |
| `MarinaInfo` | `marker_id`, `name`, `lat`, `lon`, `poi_type`, `contact`, `fuel`, `dockage`, `navigation`, `services`, `rating`, `review_count` | marinas, fuel docks, moorings |
| `BridgeInfo` | `marker_id`, `name`, `lat`, `lon`, `navigation` | bridge clearances |
| `HazardInfo` | `marker_id`, `name`, `lat`, `lon`, `navigation` | navigational hazards |
| `AnchorageInfo` | `marker_id`, `name`, `lat`, `lon`, `navigation`, `rating`, `review_count` | anchorages |
| `ContactInfo` | `phone`, `vhf_channel` | |
| `FuelInfo` | `has_diesel`, `has_gas`, `diesel_price`, `gas_price`, `currency`, `fuel_depth_ft`, `notes` | |
| `DockageInfo` | `total_slips`, `transient_slips`, `loa_max_ft`, `beam_max_ft`, `notes` | |
| `NavigationInfo` | `depth_low_water_ft`, `tide`, `current`, `bridge_type`, `bridge_closed_height_ft`, `restriction_notes`, `navigational_notes` | |
| `ServiceFlags` | `fuel_station`, `repair`, `grocery`, `restaurant`, `retail`, `restrooms`, `taxi_launch`, `dealer`, `boat_ramp` | decoded from `searchFilter` bitmask |

## Module layout (`src/hylas_routing/activecaptain/`)

| File | Role |
|---|---|
| `__init__.py` | Public exports: `ActiveCaptainClient` and all model types |
| `client.py` | API client: `get_tiles()`, `get_export_urls()`, `ensure_tiles()` (download/cache), `near_route()` (main entry point) |
| `query.py` | SQLite queries against tile databases using the R-tree index; semicircle conversion, unit conversion, JSON field parsing, translation resolution |
| `model.py` | Frozen dataclasses (above) |

## Configuration (`vessel.toml`)

```toml
[infra.activecaptain]
token_env = "ACTIVECAPTAIN_API_KEY"   # env var name, not the key itself
env = "stage"                         # "stage" or "production"
cache_dir = "var/cache"               # tiles cached under <cache_dir>/activecaptain/
tile_max_age_days = 7                 # re-download tiles older than this
search_radius_nm = 10.0               # corridor width around each waypoint
timeout_s = 60.0                      # HTTP timeout for API calls and downloads
enabled = true
```

Parsed by `config.py` into `ActiveCaptainConfig` (frozen dataclass). The `token()` method reads the
env var and raises `MissingSecretError` if `enabled = true` but the key is not set.

## Briefing integration

### How the data flows in (`service.py`)

`run_brief()` loads the AC config, builds an `ActiveCaptainClient`, calls `near_route()` with the
route's waypoints, and passes the result as `activecaptain_pois` on `BriefingInputs`. Failure
(missing key, network error, bad tile data) is caught and degraded to a warning — the briefing
renders without AC data, same as before the integration existed.

### What renders (`briefing.py`)

**Section 5c — Bridge clearances & hazards (ActiveCaptain)**

A table of bridges near the route with closed height compared against the fleet's required
clearance (air draft + buffer). Each bridge shows ✅ or ⚠️ with the margin or shortfall in feet,
plus bridge type (bascule, swing, fixed), restriction notes, and distance from the route. Hazards
are listed below with depth and navigational notes.

**Section 7a-ii — Diesel stops near route (ActiveCaptain)**

A table of marinas with diesel, sorted by distance from the route. Shows price per gallon,
approach depth, VHF channel, phone, and notes. Up to 15 entries.

**Section 7b — Transient dockage (ActiveCaptain)**

A list of marinas offering transient slips (excluding those already shown in the fuel table),
with slip count, contact info, and service flags (grocery, repair, fuel).

### What does NOT render (yet)

- Anchorage data (available in the model but not wired to a briefing section)
- Inlets, locks, dams, boat ramps, ferries (parsed by the POI type enum but not queried by default)
- Review text (only the aggregate rating and count are pulled)
- Photos, hours of operation, website URLs (not in the tile export schema)

## Limitations

1. **Crowd-sourced data.** Accuracy depends on what boaters have reported. Prices go stale, closed
   facilities may still be listed, depths may not reflect recent dredging. The briefing should
   treat AC data as a research starting point, not as a substitute for official publications.

2. **No real-time queries.** The tile export is a point-in-time snapshot. Changes to the community
   database after the tile was downloaded are not visible until the tile's cache TTL expires and a
   fresh export is fetched.

3. **Stage vs production data.** The stage environment may serve a reduced or synthetic data set.
   Full coverage requires a production API key.

4. **Coverage gaps.** ActiveCaptain coverage is strongest in US waters and popular cruising
   grounds. Remote or less-trafficked areas may have few or no markers.

5. **No write-back.** This integration is read-only. We do not contribute data back to the
   ActiveCaptain Community through the API (the third-party API supports writes, but we have no
   use case for automated contributions).

## Decision record

See [Design Decisions](Design-Decisions.md) RT-D-1: "ActiveCaptain Community as the structured
marina / bridge / hazard data source."
