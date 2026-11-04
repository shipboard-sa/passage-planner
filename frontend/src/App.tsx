/**
 * Purpose: Passage briefing request form - collects route file + trip
 *   inputs, submits to the backend, shows/downloads the generated files.
 * Version: v11 (2026-09-22) - added the Documents (DocumentsPanel) and Comms
 *   plan (CommsPanel) sections - form screens for config/papers.toml's
 *   per-DocKind overrides and config/comms.toml, replacing the papers_file/
 *   comms_config Advanced-options uploads for the common case (they stay as
 *   an escape hatch for a one-off override). v10: orphaned-section warning: a "Wrong place?
 *   Research as..." rename on a hand-edited port leaves the old `### ` section
 *   in place (renamePort's design, unchanged) rather than guessing how to
 *   merge it; this now surfaces it with a Remove button (logisticsSections.ts's
 *   new removeSection()) instead of letting it silently ride into the
 *   generated briefing - found via live testing 2026-09-22, see
 *   wiki/Design-Decisions.md. v9: adds the "Travelling with" section (FleetPanel) and sends the
 *   chosen boats as `fleet`. v8 added the Depth check section (DepthCheckPanel). v7: the destination country and shore-logistics ports come
 *   from an arrival pick list (ArrivalPicker) instead of two typed fields. v6 added
 *   the Advanced options section (the CLI's skip
 *   switches, --compare and four override-file uploads). v5 hosts the "Research
 *   with Claude" panel and merges its results into the shore-logistics textarea
 *   (never over hand edits); v4 added the submit guard for untouched
 *   shore-logistics templates.
 * Scope: PRODUCTION - passage-planner web UI.
 * Architecture: passage-planner subsystem. Calls backend/main.py's API via
 *   api/types.ts; the CLI (src/passage_planner/cli.py) is the other,
 *   independent way to generate the same briefings. API calls go through
 *   import.meta.env.BASE_URL so they still work proxied under mx-inv's
 *   /routing (see vite.config.ts and mx-inv's backend/main.py). Crew is
 *   collected against the real config/crew.toml roster (GET /api/crew),
 *   not freeform text, so the float plan's persons-on-board data
 *   (phone/DOB/PLB) is never silently lost on a name mismatch.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import ArrivalPicker from './ArrivalPicker'
import CommsPanel from './CommsPanel'
import DepthCheckPanel from './DepthCheckPanel'
import DocumentsPanel from './DocumentsPanel'
import FleetPanel from './FleetPanel'
import ResearchPanel from './ResearchPanel'
import { allSections, removeSection, replaceOrAppend, sectionFor } from './logisticsSections'
import type {
  BriefFormValues,
  BriefResponse,
  CrewRosterMember,
  Guest,
  LogisticsCoverageResponse,
  Stop,
  UnresearchedPort,
} from './api/types'

// BASE_URL is '/' in dev, '/routing/' in the production build (see
// vite.config.ts) - API calls must go through it so they resolve correctly
// whether the page is served standalone (:8100 dev) or proxied under
// mx-inv's /routing (:8000 prod, see backend/main.py's routing_proxy).
const API_BASE = `${import.meta.env.BASE_URL}api`

const FILE_LABELS: Record<string, string> = {
  markdown: 'Markdown',
  html: 'HTML',
  docx: 'Word (.docx)',
  float_plan: 'Float Plan (PDF)',
}

function defaultDepart(): string {
  const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000)
  tomorrow.setMinutes(0, 0, 0)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${tomorrow.getFullYear()}-${pad(tomorrow.getMonth() + 1)}-${pad(tomorrow.getDate())}T${pad(tomorrow.getHours())}:00`
}

const EMPTY_GUEST: Guest = { name: '', phone: '', gender: '', dob: '' }

// Ports in the shore-logistics text that still look like an untouched template,
// shown in the submit-guard banner. `checked` = fields per section.
interface LogisticsGuard {
  ports: UnresearchedPort[]
  checked: number
}

function displayLabel(m: CrewRosterMember): string {
  return m.key.charAt(0).toUpperCase() + m.key.slice(1)
}

export default function App() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const logisticsRef = useRef<HTMLTextAreaElement>(null)
  // Optional override files (uncontrolled inputs, read at submit like the route).
  const overrideRefs = {
    papers_file: useRef<HTMLInputElement>(null),
    depths_file: useRef<HTMLInputElement>(null),
    ciq_file: useRef<HTMLInputElement>(null),
    comms_config: useRef<HTMLInputElement>(null),
  }
  const [values, setValues] = useState<BriefFormValues>({
    depart: defaultDepart(),
    speed: '6.5',
    watch_hours: '6',
    logisticsMarkdown: '',
    floatPlan: false,
    foodDays: '',
    compare: false,
    noTropical: false,
    noTides: false,
    noNotices: false,
    noAdvisories: false,
    noMaintenance: false,
    noPapers: false,
  })
  const [roster, setRoster] = useState<CrewRosterMember[]>([])
  const [crewSelection, setCrewSelection] = useState<Record<string, boolean>>({})
  const [guests, setGuests] = useState<Guest[]>([])
  const [status, setStatus] = useState<'idle' | 'loading' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<BriefResponse | null>(null)
  const [templateStatus, setTemplateStatus] = useState<'idle' | 'loading' | 'error'>('idle')
  const [logisticsOpen, setLogisticsOpen] = useState(false)
  const [guard, setGuard] = useState<LogisticsGuard | null>(null)
  // Synchronous mirror of the textarea, so several research results arriving in
  // one poll each see the previous one's edit. `pristine` holds, per port, the
  // last text WE wrote (template skeleton or research): a section still equal to
  // it has not been touched by hand and may be overwritten.
  const markdownRef = useRef('')
  // The arrival and stops, picked from the arrival list. The destination country
  // and the shore-logistics port names are derived from them (nobody types either).
  const [stops, setStops] = useState<Stop[]>([])
  // The boats travelling with us: null until the fleet list has loaded (then the usual
  // companions), an empty list means travelling alone.
  const [fleetIds, setFleetIds] = useState<string[] | null>(null)
  // Bumped when the route file changes, so the picker can suggest nearby places.
  const [routeVersion, setRouteVersion] = useState(0)
  const pristine = useRef<Record<string, string>>({})

  useEffect(() => {
    fetch(`${API_BASE}/crew`)
      .then((res) => (res.ok ? res.json() : []))
      .then((data: CrewRosterMember[]) => {
        setRoster(data)
        setCrewSelection(Object.fromEntries(data.map((m) => [m.key, true])))
      })
      .catch(() => {
        // no roster yet (config/crew.toml missing) - guests-only still works
      })
  }, [])

  function updateField<K extends keyof BriefFormValues>(key: K, value: BriefFormValues[K]) {
    setValues((v) => ({ ...v, [key]: value }))
  }

  const setLogistics = useCallback((md: string) => {
    markdownRef.current = md
    setValues((v) => ({ ...v, logisticsMarkdown: md }))
    setGuard(null)
  }, [])

  // Research result -> textarea. Only over an untouched section (or none at all)
  // unless `force`; returns whether it was written.
  const applyResearch = useCallback(
    (port: string, section: string, force: boolean): boolean => {
      const current = sectionFor(markdownRef.current, port)
      const mine = pristine.current[port]
      const untouched =
        current === null || (mine !== undefined && current.trim() === mine.trim())
      if (!untouched && !force) return false
      setLogistics(replaceOrAppend(markdownRef.current, port, section))
      pristine.current[port] = section
      return true
    },
    [setLogistics],
  )

  // Newline-terminated so one picked stop with a comma in its label ("Salt Pond
  // (Thompson Bay), Long Island (Bahamas)") is not read as a comma-separated list.
  const portsText = stops.map((x) => `${x.label}\n`).join('')
  const stopRoles = Object.fromEntries(
    stops.filter((x) => x.role).map((x) => [x.label, x.role as string]),
  )
  const countryText = [...new Set(stops.map((x) => x.country))].join(', ')

  // "Research as ..." on a finished port: swap the stop (same country, no longer
  // "from the list"), and the textarea section too if it is still the text we
  // wrote (a hand-edited section is left alone; the new name then arrives as a
  // new section).
  const renamePort = useCallback(
    (port: string, next: string) => {
      setStops((list) =>
        list.map((x) =>
          x.label.toLowerCase() === port.toLowerCase()
            ? { label: next, country: x.country, role: x.role }
            : x,
        ),
      )
      const current = sectionFor(markdownRef.current, port)
      const mine = pristine.current[port]
      if (current !== null && mine !== undefined && current.trim() === mine.trim()) {
        const renamed = current.replace(/^###[^\n]*/, `### ${next}`)
        setLogistics(replaceOrAppend(markdownRef.current, port, renamed))
        pristine.current[next] = renamed
        delete pristine.current[port]
      }
    },
    [setLogistics],
  )

  // Sections left behind by a "Wrong place? Research as..." rename on a hand-edited
  // port (renamePort deliberately leaves the old heading in place rather than
  // guessing how to merge it - see its comment above) - a stop no longer in the
  // list has no way to remove its old section short of hand-editing the raw
  // Markdown, so this surfaces it instead of letting it silently ride into the
  // generated briefing.
  const orphanedSections = useMemo(() => {
    const current = new Set(stops.map((x) => x.label.trim().toLowerCase()))
    return Object.keys(allSections(values.logisticsMarkdown)).filter(
      (heading) => !current.has(heading.trim().toLowerCase()),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values.logisticsMarkdown, stops])

  function removeOrphanedSection(heading: string) {
    setLogistics(removeSection(markdownRef.current, heading))
  }

  function toggleCrew(key: string) {
    setCrewSelection((s) => ({ ...s, [key]: !s[key] }))
  }

  function updateGuest(i: number, field: keyof Guest, value: string) {
    setGuests((gs) => gs.map((g, idx) => (idx === i ? { ...g, [field]: value } : g)))
  }

  function addGuest() {
    setGuests((gs) => [...gs, { ...EMPTY_GUEST }])
  }

  function removeGuest(i: number) {
    setGuests((gs) => gs.filter((_, idx) => idx !== i))
  }

  async function handleGenerateTemplate() {
    if (stops.length === 0) {
      setError('Pick the arrival and stops first.')
      setStatus('error')
      return
    }
    setTemplateStatus('loading')
    setError(null)
    setGuard(null)
    try {
      const params = new URLSearchParams({ ports: portsText })
      const res = await fetch(`${API_BASE}/logistics-template?${params}`)
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.detail ?? `Request failed (${res.status})`)
      }
      const data: { markdown: string } = await res.json()
      pristine.current = allSections(data.markdown)
      setLogistics(data.markdown)
      setTemplateStatus('idle')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setStatus('error')
      setTemplateStatus('error')
    }
  }

  // Ask the backend which port sections still look like an untouched template.
  // Fails open: if the check itself errors, the briefing must not be blocked.
  async function checkLogisticsCoverage(): Promise<LogisticsGuard | null> {
    try {
      const res = await fetch(`${API_BASE}/logistics-coverage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ markdown: values.logisticsMarkdown }),
      })
      if (!res.ok) return null
      const data: LogisticsCoverageResponse = await res.json()
      return data.unresearched.length > 0
        ? { ports: data.unresearched, checked: data.checked }
        : null
    } catch {
      return null
    }
  }

  function backToLogistics() {
    setGuard(null)
    setLogisticsOpen(true)
    requestAnimationFrame(() => logisticsRef.current?.focus())
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    void submitBrief(false)
  }

  async function submitBrief(skipGuard: boolean) {
    const file = fileInputRef.current?.files?.[0]
    if (!file) {
      setError('Choose a GPX route file first.')
      setStatus('error')
      return
    }

    const selectedRoster = roster.filter((m) => crewSelection[m.key])
    const validGuests = guests.filter((g) => g.name.trim())
    if (selectedRoster.length === 0 && validGuests.length === 0) {
      setError('Select at least one crew member or add a guest.')
      setStatus('error')
      return
    }

    if (!skipGuard && values.logisticsMarkdown.trim()) {
      setStatus('loading')
      setError(null)
      const found = await checkLogisticsCoverage()
      if (found) {
        setGuard(found)
        setStatus('idle')
        return
      }
    }
    setGuard(null)

    const form = new FormData()
    form.append('route', file)
    form.append('depart', values.depart)
    form.append('speed', values.speed)
    if (countryText) form.append('country', countryText)
    const displayNames = [
      ...selectedRoster.map(displayLabel),
      ...validGuests.map((g) => g.name.trim()),
    ]
    form.append('crew', displayNames.join(','))
    form.append('watch_hours', values.watch_hours)
    if (fleetIds !== null) form.append('fleet', JSON.stringify(fleetIds))
    if (values.logisticsMarkdown.trim()) {
      form.append('logistics_markdown', values.logisticsMarkdown)
    }
    if (values.floatPlan) {
      const floatPlanCrew = [
        ...selectedRoster.map((m) => m.key),
        ...validGuests.map((g) => `${g.name.trim()}:${g.phone.trim()}:${g.gender}:${g.dob}`),
      ]
      form.append('float_plan', 'true')
      form.append('float_plan_crew', floatPlanCrew.join(','))
      if (values.foodDays.trim()) form.append('food_days', values.foodDays)
    }

    const switches: [boolean, string][] = [
      [values.compare, 'compare'],
      [values.noTropical, 'no_tropical'],
      [values.noTides, 'no_tides'],
      [values.noNotices, 'no_notices'],
      [values.noAdvisories, 'no_advisories'],
      [values.noMaintenance, 'no_maintenance'],
      [values.noPapers, 'no_papers'],
    ]
    for (const [on, name] of switches) if (on) form.append(name, 'true')
    for (const [name, ref] of Object.entries(overrideRefs)) {
      const f = ref.current?.files?.[0]
      if (f) form.append(name, f)
    }

    setStatus('loading')
    setError(null)
    setResult(null)
    try {
      const res = await fetch(`${API_BASE}/brief`, { method: 'POST', body: form })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.detail ?? `Request failed (${res.status})`)
      }
      const data: BriefResponse = await res.json()
      setResult(data)
      setStatus('idle')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setStatus('error')
    }
  }

  return (
    <div className="page">
      <header>
        <h1>Passage Briefing</h1>
        <p className="subtitle">
          Feed the routing pipeline a GPX route and departure plan; get back a full briefing.
        </p>
      </header>

      <form className="brief-form" onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="route">Route (GPX)</label>
          <input
            id="route"
            type="file"
            accept=".gpx"
            ref={fileInputRef}
            required
            onChange={() => setRouteVersion((n) => n + 1)}
          />
        </div>

        <div className="field-row">
          <div className="field">
            <label htmlFor="depart">Departure</label>
            <input
              id="depart"
              type="datetime-local"
              value={values.depart}
              onChange={(e) => updateField('depart', e.target.value)}
              required
            />
            <span className="hint">No timezone shown = read as local time at the first waypoint.</span>
          </div>
          <div className="field">
            <label htmlFor="speed">Planning speed (kn)</label>
            <input
              id="speed"
              type="number"
              step="0.1"
              min="1"
              value={values.speed}
              onChange={(e) => updateField('speed', e.target.value)}
              required
            />
          </div>
        </div>

        <ArrivalPicker
          stops={stops}
          onChange={setStops}
          routeVersion={routeVersion}
          getRouteFile={() => fileInputRef.current?.files?.[0]}
        />

        <FleetPanel selected={fleetIds} onSelect={setFleetIds} />

        <div className="field-row">
          <div className="field">
            <label htmlFor="watch-hours">Watch length (h)</label>
            <input
              id="watch-hours"
              type="number"
              step="0.5"
              min="1"
              value={values.watch_hours}
              onChange={(e) => updateField('watch_hours', e.target.value)}
              required
            />
          </div>
        </div>

        <div className="field">
          <label>Crew</label>
          {roster.length > 0 && (
            <div className="crew-roster">
              {roster.map((m) => (
                <label key={m.key} className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={crewSelection[m.key] ?? false}
                    onChange={() => toggleCrew(m.key)}
                  />
                  {displayLabel(m)} <span className="hint">({m.name})</span>
                </label>
              ))}
            </div>
          )}
          {roster.length === 0 && (
            <span className="hint">
              No regular-crew roster found (config/crew.toml) — add guests below.
            </span>
          )}

          <div className="guest-list">
            {guests.map((g, i) => (
              <div key={i} className="guest-row">
                <input
                  type="text"
                  placeholder="Guest name"
                  value={g.name}
                  onChange={(e) => updateGuest(i, 'name', e.target.value)}
                />
                <input
                  type="text"
                  placeholder="Phone"
                  value={g.phone}
                  onChange={(e) => updateGuest(i, 'phone', e.target.value)}
                />
                <select value={g.gender} onChange={(e) => updateGuest(i, 'gender', e.target.value)}>
                  <option value="">—</option>
                  <option value="M">M</option>
                  <option value="F">F</option>
                </select>
                <input
                  type="date"
                  value={g.dob}
                  onChange={(e) => updateGuest(i, 'dob', e.target.value)}
                />
                <button type="button" className="secondary" onClick={() => removeGuest(i)}>
                  Remove
                </button>
              </div>
            ))}
            <button type="button" className="secondary" onClick={addGuest}>
              + Add guest
            </button>
          </div>
          <span className="hint">
            Phone/gender/DOB only matter for the float plan below — leave blank for a
            watch-schedule-only guest.
          </span>
        </div>

        <div className="field checkbox-field">
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={values.floatPlan}
              onChange={(e) => updateField('floatPlan', e.target.checked)}
            />
            Generate USPS float plan (PDF)
          </label>
          {values.floatPlan && (
            <div className="field">
              <label htmlFor="food-days">Food aboard (days)</label>
              <input
                id="food-days"
                type="number"
                min="0"
                step="1"
                value={values.foodDays}
                onChange={(e) => updateField('foodDays', e.target.value)}
              />
            </div>
          )}
        </div>

        <details className="advanced">
          <summary>Depth check (optional)</summary>
          <DepthCheckPanel
            getRouteFile={() => fileInputRef.current?.files?.[0]}
            depart={values.depart}
            speed={values.speed}
            fleetIds={fleetIds}
          />
        </details>

        <details className="advanced">
          <summary>Documents (optional)</summary>
          <DocumentsPanel />
        </details>

        <details className="advanced">
          <summary>Comms plan (optional)</summary>
          <CommsPanel />
        </details>

        <details className="advanced">
          <summary>Advanced options (optional)</summary>
          <div className="field checkbox-field">
            <label>Checks</label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={values.compare}
                onChange={(e) => updateField('compare', e.target.checked)}
              />
              Add the ECMWF-vs-GFS forecast confidence check (one extra weather pull)
            </label>
            {(
              [
                ['noTropical', 'Skip the NHC tropical check'],
                ['noTides', 'Skip the NOAA tide / depth assessment'],
                ['noNotices', 'Skip the NGA broadcast-warning check'],
                ['noAdvisories', 'Skip the U.S. State Department advisory / embassy lookup'],
                ['noMaintenance', 'Skip the maintenance-due lookup'],
                ['noPapers', 'Skip the document-readiness check'],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className="checkbox-row">
                <input
                  type="checkbox"
                  checked={values[key]}
                  onChange={(e) => updateField(key, e.target.checked)}
                />
                {label}
              </label>
            ))}
          </div>
          <div className="field">
            <label htmlFor="papers-file">Papers overrides (papers.toml)</label>
            <input id="papers-file" type="file" accept=".toml" ref={overrideRefs.papers_file} />
            <span className="hint">
              Document issue/expiry dates; default is config/papers.toml. The Documents screen
              above covers the usual case — use this only for a one-off override.
            </span>
          </div>
          <div className="field">
            <label htmlFor="depths-file">Waypoint depths (waypoint_depths.toml)</label>
            <input id="depths-file" type="file" accept=".toml" ref={overrideRefs.depths_file} />
            <span className="hint">Soundings you read off the chart, for the depth section.</span>
          </div>
          <div className="field">
            <label htmlFor="ciq-file">Arrival dossier (Markdown)</label>
            <input id="ciq-file" type="file" accept=".md,.markdown,.txt" ref={overrideRefs.ciq_file} />
            <span className="hint">Replaces the built-in customs/immigration text.</span>
          </div>
          <div className="field">
            <label htmlFor="comms-config">Comms plan (comms.toml)</label>
            <input id="comms-config" type="file" accept=".toml" ref={overrideRefs.comms_config} />
            <span className="hint">
              Default is config/comms.toml. The Comms plan screen above covers the usual case —
              use this only for a one-off override.
            </span>
          </div>
        </details>

        <details
          className="advanced"
          open={logisticsOpen}
          onToggle={(e) => setLogisticsOpen(e.currentTarget.open)}
        >
          <summary>Shore logistics (optional)</summary>
          <div className="field">
            <label>Ports for shore logistics</label>
            <div className="inline-field">
              <span className="hint">
                {stops.length > 0
                  ? stops.map((x) => x.label).join(' · ')
                  : 'Pick the arrival and stops above first.'}
              </span>
              <button
                type="button"
                className="secondary"
                onClick={handleGenerateTemplate}
                disabled={templateStatus === 'loading' || stops.length === 0}
              >
                {templateStatus === 'loading' ? 'Generating…' : 'Generate template'}
              </button>
            </div>
            <span className="hint">
              Generates a skeleton below (haul-out size, contacts, currency, security, …): fill it
              in, or let Claude research it, then submit with the briefing. Leave blank to skip
              this section.
            </span>
          </div>
          <ResearchPanel
            ports={portsText}
            roles={stopRoles}
            fleetIds={fleetIds}
            getRouteFile={() => fileInputRef.current?.files?.[0]}
            crewCount={
              roster.filter((m) => crewSelection[m.key]).length +
              guests.filter((g) => g.name.trim()).length
            }
            onApply={applyResearch}
            onRename={renamePort}
          />
          {orphanedSections.length > 0 && (
            <div className="banner warning">
              {orphanedSections.length === 1
                ? "1 section below doesn't match any current stop"
                : `${orphanedSections.length} sections below don't match any current stop`}{' '}
              — left behind by a "Wrong place? Research as…" rename on a hand-edited section, so it
              wasn't touched. It will still be included in the generated briefing unless you remove
              it or fold it into the current section by hand.
              <ul>
                {orphanedSections.map((heading) => (
                  <li key={heading}>
                    {heading}{' '}
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => removeOrphanedSection(heading)}
                    >
                      Remove this section
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="field">
            <label htmlFor="logistics-markdown">Shore logistics (Markdown)</label>
            <textarea
              id="logistics-markdown"
              ref={logisticsRef}
              rows={10}
              value={values.logisticsMarkdown}
              onChange={(e) => setLogistics(e.target.value)}
            />
          </div>
        </details>

        <button type="submit" disabled={status === 'loading'}>
          {status === 'loading' ? 'Generating…' : 'Generate briefing'}
        </button>
      </form>

      {guard && (
        <div className="banner warning guard" role="alert">
          <strong>Shore logistics (§7b) looks unresearched.</strong>
          <ul>
            {guard.ports.map((p) => (
              <li key={p.port}>
                {p.port}: {p.missing.length} of {guard.checked} fields still empty
              </li>
            ))}
          </ul>
          <p>Submitting now puts the blank template text into the briefing.</p>
          <div className="guard-actions">
            <button type="button" onClick={backToLogistics}>
              Go back and fill it in
            </button>
            <button type="button" className="secondary" onClick={() => void submitBrief(true)}>
              Generate anyway
            </button>
          </div>
        </div>
      )}

      {error && <div className="banner error">{error}</div>}

      {result && (
        <section className="result">
          <h2>{result.route_name}</h2>
          <div className="stats">
            <div><span className="stat-value">{result.total_nm.toFixed(0)}</span> NM</div>
            <div><span className="stat-value">{result.duration_h.toFixed(1)}</span> h</div>
            <div><span className="stat-value">{(result.sailing_fraction * 100).toFixed(0)}%</span> sail</div>
            <div><span className="stat-value">{result.total_fuel_gal.toFixed(0)}</span> gal fuel</div>
          </div>
          <p className="depart-eta">
            Depart <strong>{result.depart}</strong> &rarr; ETA <strong>{result.eta}</strong>
          </p>

          {result.warnings.length > 0 && (
            <div className="banner warning">
              <strong>Warnings:</strong>
              <ul>
                {result.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="downloads">
            {result.files.map((kind) => (
              <a
                key={kind}
                href={`${API_BASE}/brief/${result.id}/download/${kind}`}
                className="download-link"
              >
                Download {FILE_LABELS[kind] ?? kind}
              </a>
            ))}
          </div>

          <details className="markdown-preview">
            <summary>Preview Markdown</summary>
            <pre>{result.markdown}</pre>
          </details>
        </section>
      )}
    </div>
  )
}
