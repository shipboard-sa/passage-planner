export interface BriefResponse {
  id: string
  route_name: string
  total_nm: number
  duration_h: number
  sailing_fraction: number
  total_fuel_gal: number
  depart: string
  eta: string
  warnings: string[]
  markdown: string
  files: string[]
}

// POST /api/logistics-coverage: port sections still looking like an untouched
// template (submit guard). `checked` is the number of fields per section.
export interface UnresearchedPort {
  port: string
  missing: string[]
}

export interface LogisticsCoverageResponse {
  checked: number
  unresearched: UnresearchedPort[]
}

export interface BriefFormValues {
  depart: string
  speed: string
  watch_hours: string
  logisticsMarkdown: string
  floatPlan: boolean
  foodDays: string
  // Advanced options (the CLI's --compare and --no-* switches)
  compare: boolean
  noTropical: boolean
  noTides: boolean
  noNotices: boolean
  noAdvisories: boolean
  noMaintenance: boolean
  noPapers: boolean
}

// The regular-crew roster (config/crew.toml) — the float plan's real
// persons-on-board data, as opposed to the freeform display-name field.
export interface CrewRosterMember {
  key: string
  name: string
  phone: string
  gender: string
  dob: string | null
  plb_uin: string
}

// A one-off crew member not in the roster, added for a single passage.
export interface Guest {
  name: string
  phone: string
  gender: string
  dob: string
}

// GET /api/logistics-research/status: can the Research button work here?
export interface ResearchStatusResponse {
  available: boolean
  reason: string
}

export type ResearchPortStatus = 'pending' | 'running' | 'ok' | 'failed' | 'cancelled'

// One port inside GET /api/logistics-research/{id}.
export interface ResearchPortState {
  name: string
  status: ResearchPortStatus
  markdown: string
  gaps: string[]
  resolved_as: string | null
  error: string | null
  warnings: string[]
  seconds: number
  cost_usd: number
  turns: number | null
  saved_as: string | null
}

export interface ResearchJobState {
  id: string
  status: 'running' | 'done' | 'cancelled' | 'error'
  error: string | null
  total_cost_usd: number
  total_seconds: number
  ports: ResearchPortState[]
}

// One place from the arrival pick list (GET /api/arrival-places).
export interface ArrivalPlace {
  id: string
  label: string
  name: string
  island: string
  country: string
  region: string
  lat: number
  lon: number
  kind: string // port_of_entry | harbour | anchorage
  clearance: string
  also_known_as: string[]
  verified: string
}

export interface ArrivalPlacesResponse {
  places: ArrivalPlace[]
  countries: string[]
}

export interface ArrivalSuggestion extends ArrivalPlace {
  distance_nm: number
}

// POST /api/arrival-places/suggest: the listed places nearest the route's ends.
export interface ArrivalSuggestResponse {
  end: ArrivalSuggestion[]
  start: ArrivalSuggestion[]
}

// A boat that may travel with the vessel (config/fleet.json, edited in FleetPanel).
export type Hull = 'monohull' | 'catamaran' | 'trimaran'

export interface FleetBoat {
  id: string
  name: string
  hull: Hull
  make: string
  model: string
  draft_ft: number
  lifting_keel: boolean
  draft_up_ft: number | null
  air_draft_ft: number
  loa_ft: number | null
  beam_ft: number | null
  displacement_lb: number | null
  mmsi: string
  call_sign: string
  notes: string
  telemetry: boolean
  usual_companion: boolean
  polar_file: string
}

// POST /api/fleet/lookup: parsed from pasted MarineTraffic / ORC text.
export interface FleetLookupResult {
  mmsi: string
  call_sign: string
  flag: string
  loa_ft: number | null
  beam_ft: number | null
  draft_ft: number | null
  displacement_lb: number | null
  messages: string[]
  polar_pol_text: string | null
}

// GET/PUT/DELETE /api/fleet: the list, the hull choices and the usual companions' ids.
export interface FleetResponse {
  boats: FleetBoat[]
  hulls: Hull[]
  usual: string[]
}

// Why the boat stops at a place; steers what the research looks hardest for.
export type StopRole = 'check-in' | 'check-out' | 'fuel'

// A chosen arrival or stop. `id` is set when it came from the pick list.
export interface Stop {
  label: string
  country: string
  id?: string
  kind?: string
  role?: StopRole
}

// POST /api/depth-check: waypoints that need a chart depth (or are tight at the tide),
// plus any already covered by a saved depth.
export interface DepthRow {
  name: string
  lat: number
  lon: number
  status: 'needs_depth' | 'tight' | 'saved'
  automatic: string
  tide_ft: number | null
  required_ft: number
  saved: { id: string; depth_ft: number; source: string; notes: string; recorded: string } | null
}

export interface DepthCheckResponse {
  route_name: string
  required_ft: number
  draft_ft: number
  margin_ft: number
  rows: DepthRow[]
}

// GET/PUT/POST /api/papers: config/papers.toml's [documents] overrides, one
// row per DocKind (a fixed, known set - no add/remove/rename).
export interface PaperDocEntry {
  kind: string
  label: string
  path: string
  issued: string
  expires: string
  note: string
}

export interface PapersResponse {
  documents: PaperDocEntry[]
}

// GET/PUT/POST /api/comms: config/comms.toml, one flat record (no list).
export interface CommsConfigData {
  starlink_plan: string
  starlink_billing_cycle_day: number | null
  starlink_priority_gb: number | null
  starlink_ocean_mode_usd_per_gb: number | null
  starlink_notes: string
  tmobile_plan: string
  tmobile_roaming_included: boolean
  tmobile_notes: string
  maya_esim_scope: string
  maya_esim_notes: string
  router_model: string
  router_notes: string
}
