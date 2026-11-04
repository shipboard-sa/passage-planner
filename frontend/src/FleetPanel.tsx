/**
 * Purpose: "Travelling with" screen: tick which boats travel with you on this passage,
 *   and add, edit or remove boats in the saved fleet list.
 * Version: v2 (2026-09-22) - added call_sign and a "Look up this boat" paste box
 *   (POST /api/fleet/lookup, boat_lookup.py) that parses pasted MarineTraffic /
 *   ORC certificate text into spec fields and, when present, a real polar -
 *   sent as polar_pol_text on Save, never fetched or scraped by this app itself.
 * Scope: PRODUCTION - passage-planner web UI.
 * Architecture: Child of App.tsx, which owns the chosen ids (`selected`; null until the
 *   list has loaded, then the usual companions). GET /api/fleet lists the boats;
 *   POST /api/fleet saves one (config/fleet.json) and POST /api/fleet/{id}/remove removes
 *   one (POST, not PUT/DELETE: mx-inv's /routing proxy only forwards GET and POST).
 *   The choice is sent as the `fleet` field of the brief, depth-check and research
 *   requests. Nobody edits a file: this is the only place the fleet list is maintained.
 */
import { useEffect, useState } from 'react'
import type { FleetBoat, FleetLookupResult, FleetResponse, Hull } from './api/types'

const API_BASE = `${import.meta.env.BASE_URL}api`

interface Props {
  selected: string[] | null
  onSelect: (ids: string[]) => void
}

interface BoatForm {
  replaces: string | null
  name: string
  hull: Hull
  make: string
  model: string
  draft_ft: string
  lifting_keel: boolean
  draft_up_ft: string
  air_draft_ft: string
  loa_ft: string
  beam_ft: string
  displacement_lb: string
  mmsi: string
  call_sign: string
  notes: string
  usual_companion: boolean
}

const EMPTY: BoatForm = {
  replaces: null,
  name: '',
  hull: 'monohull',
  make: '',
  model: '',
  draft_ft: '',
  lifting_keel: false,
  draft_up_ft: '',
  air_draft_ft: '',
  loa_ft: '',
  beam_ft: '',
  displacement_lb: '',
  mmsi: '',
  call_sign: '',
  notes: '',
  usual_companion: false,
}

const num = (v: number | null): string => (v === null || v === undefined ? '' : String(v))
const toNum = (s: string): number | null => (s.trim() === '' ? null : Number(s))

function fromBoat(b: FleetBoat): BoatForm {
  return {
    replaces: b.id,
    name: b.name,
    hull: b.hull,
    make: b.make,
    model: b.model,
    draft_ft: num(b.draft_ft),
    lifting_keel: b.lifting_keel,
    draft_up_ft: num(b.draft_up_ft),
    air_draft_ft: num(b.air_draft_ft),
    loa_ft: num(b.loa_ft),
    beam_ft: num(b.beam_ft),
    displacement_lb: num(b.displacement_lb),
    mmsi: b.mmsi,
    call_sign: b.call_sign,
    notes: b.notes,
    usual_companion: b.usual_companion,
  }
}

function summary(b: FleetBoat): string {
  const made = [b.make, b.model].filter(Boolean).join(' ')
  const draft =
    b.lifting_keel && b.draft_up_ft
      ? `draws ${b.draft_up_ft} ft keel up (${b.draft_ft} ft down)`
      : `draws ${b.draft_ft} ft`
  const size = [b.loa_ft ? `${b.loa_ft} ft LOA` : '', b.beam_ft ? `beam ${b.beam_ft} ft` : '']
    .filter(Boolean)
    .join(', ')
  return [made, draft, `air draft ${b.air_draft_ft} ft`, size].filter(Boolean).join(' · ')
}

export default function FleetPanel({ selected, onSelect }: Props) {
  const [boats, setBoats] = useState<FleetBoat[]>([])
  const [hulls, setHulls] = useState<Hull[]>(['monohull', 'catamaran', 'trimaran'])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [form, setForm] = useState<BoatForm | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lookupOpen, setLookupOpen] = useState(false)
  const [marinetrafficText, setMarinetrafficText] = useState('')
  const [orcText, setOrcText] = useState('')
  const [lookingUp, setLookingUp] = useState(false)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const [lookupMessages, setLookupMessages] = useState<string[]>([])
  const [polarPolText, setPolarPolText] = useState<string | null>(null)
  const [polarSummary, setPolarSummary] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetch(`${API_BASE}/fleet`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data: FleetResponse) => {
        if (!alive) return
        setBoats(data.boats)
        setHulls(data.hulls)
        // first load: travel with the usual companions
        if (selected === null) onSelect(data.usual)
      })
      .catch((e: unknown) => {
        if (alive) setLoadError(e instanceof Error ? e.message : 'could not load the fleet list')
      })
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const chosen = selected ?? []

  function toggle(id: string) {
    onSelect(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id])
  }

  function set<K extends keyof BoatForm>(key: K, value: BoatForm[K]) {
    setForm((f) => (f ? { ...f, [key]: value } : f))
  }

  async function save() {
    if (!form) return
    setSaving(true)
    setError(null)
    try {
      const boat = {
        name: form.name,
        hull: form.hull,
        make: form.make,
        model: form.model,
        draft_ft: toNum(form.draft_ft),
        lifting_keel: form.lifting_keel,
        draft_up_ft: form.lifting_keel ? toNum(form.draft_up_ft) : null,
        air_draft_ft: toNum(form.air_draft_ft),
        loa_ft: toNum(form.loa_ft),
        beam_ft: toNum(form.beam_ft),
        displacement_lb: toNum(form.displacement_lb),
        mmsi: form.mmsi,
        call_sign: form.call_sign,
        notes: form.notes,
        usual_companion: form.usual_companion,
      }
      const res = await fetch(`${API_BASE}/fleet`, {
        method: 'POST', // POST, not PUT: the /routing proxy only forwards GET and POST
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ boat, replaces: form.replaces, polar_pol_text: polarPolText }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        const detail = body?.detail
        throw new Error(typeof detail === 'string' ? detail : `Request failed (${res.status})`)
      }
      const data: FleetResponse = await res.json()
      const saved = data.boats.find((b) => b.name.trim().toLowerCase() === form.name.trim().toLowerCase())
      setBoats(data.boats)
      if (saved) {
        // a new boat is ticked for this passage; a renamed one keeps its tick
        const without = chosen.filter((x) => x !== form.replaces && x !== saved.id)
        const wasTicked = form.replaces !== null && chosen.includes(form.replaces)
        onSelect(form.replaces === null || wasTicked ? [...without, saved.id] : without)
      }
      closeForm()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the boat.')
    } finally {
      setSaving(false)
    }
  }

  function resetLookup() {
    setLookupOpen(false)
    setMarinetrafficText('')
    setOrcText('')
    setLookupError(null)
    setLookupMessages([])
    setPolarPolText(null)
    setPolarSummary(null)
  }

  function openForm(initial: BoatForm) {
    resetLookup()
    setForm(initial)
  }

  function closeForm() {
    resetLookup()
    setForm(null)
  }

  async function runLookup() {
    setLookingUp(true)
    setLookupError(null)
    try {
      const res = await fetch(`${API_BASE}/fleet/lookup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ marinetraffic_text: marinetrafficText, orc_text: orcText }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.detail ?? `Request failed (${res.status})`)
      }
      const data: FleetLookupResult = await res.json()
      setLookupMessages(data.messages)
      setForm((f) =>
        f
          ? {
              ...f,
              mmsi: data.mmsi || f.mmsi,
              call_sign: data.call_sign || f.call_sign,
              loa_ft: data.loa_ft !== null ? String(data.loa_ft) : f.loa_ft,
              beam_ft: data.beam_ft !== null ? String(data.beam_ft) : f.beam_ft,
              draft_ft: data.draft_ft !== null ? String(data.draft_ft) : f.draft_ft,
              displacement_lb:
                data.displacement_lb !== null ? String(data.displacement_lb) : f.displacement_lb,
            }
          : f
      )
      if (data.polar_pol_text) {
        setPolarPolText(data.polar_pol_text)
        setPolarSummary(
          'A real polar was parsed from the ORC certificate - it will be saved with this boat.'
        )
      }
    } catch (e) {
      setLookupError(e instanceof Error ? e.message : 'Could not parse the pasted text.')
    } finally {
      setLookingUp(false)
    }
  }

  async function remove(b: FleetBoat) {
    if (!window.confirm(`Remove ${b.name} from the fleet list?`)) return
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/fleet/${encodeURIComponent(b.id)}/remove`, {
        method: 'POST',
      })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.detail ?? `Request failed (${res.status})`)
      }
      const data: FleetResponse = await res.json()
      setBoats(data.boats)
      onSelect(chosen.filter((x) => x !== b.id))
      closeForm()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not remove the boat.')
    }
  }

  return (
    <div className="field fleet-panel">
      <label>Travelling with</label>
      {loadError && <p className="hint error-text">Could not load the fleet list ({loadError}).</p>}
      {!loadError && boats.length === 0 && (
        <p className="hint">Travelling alone. Add a boat if you are in company.</p>
      )}
      {boats.length > 0 && (
        <ul className="fleet-list">
          {boats.map((b) => (
            <li key={b.id}>
              <label className="fleet-boat">
                <input
                  type="checkbox"
                  checked={chosen.includes(b.id)}
                  onChange={() => toggle(b.id)}
                />
                <span className="fleet-name">{b.name}</span>
                <span className="chip-kind">{b.hull}</span>
              </label>
              <span className="fleet-detail">{summary(b)}</span>
              {b.polar_file && <span className="chip-kind">real polar on file</span>}
              <button type="button" className="link-button" onClick={() => openForm(fromBoat(b))}>
                Edit
              </button>
            </li>
          ))}
        </ul>
      )}
      {boats.length > 0 && chosen.length === 0 && (
        <p className="hint">No boat ticked: this passage is planned as travelling alone.</p>
      )}
      {!form && (
        <button type="button" onClick={() => openForm({ ...EMPTY })}>
          Add a boat…
        </button>
      )}

      {form && (
        <div className="fleet-form">
          <h4>{form.replaces ? `Edit ${form.replaces.replace(/_/g, ' ')}` : 'Add a boat'}</h4>

          {!lookupOpen && (
            <button type="button" className="link-button" onClick={() => setLookupOpen(true)}>
              Look up this boat…
            </button>
          )}
          {lookupOpen && (
            <div className="fleet-lookup">
              <p className="hint">
                Neither site has a stable public API, so this only parses what you paste — find
                the boat yourself first (MarineTraffic has duplicate names; ORC certificates are
                per boat), then paste its page text below.
              </p>
              <div className="field">
                <label htmlFor="fb-lookup-mt">
                  MarineTraffic Overview page text (MMSI, call sign, LOA, beam)
                </label>
                <textarea
                  id="fb-lookup-mt"
                  rows={4}
                  value={marinetrafficText}
                  onChange={(e) => setMarinetrafficText(e.target.value)}
                  placeholder="Paste the vessel's MarineTraffic Overview page text here…"
                />
              </div>
              <div className="field">
                <label htmlFor="fb-lookup-orc">
                  ORC certificate text (LOA/beam/draft/displacement, and a VPP speed table for a
                  real polar)
                </label>
                <textarea
                  id="fb-lookup-orc"
                  rows={6}
                  value={orcText}
                  onChange={(e) => setOrcText(e.target.value)}
                  placeholder="Paste the ORC certificate's text here…"
                />
              </div>
              <div className="field-row">
                <button
                  type="button"
                  onClick={runLookup}
                  disabled={lookingUp || (!marinetrafficText.trim() && !orcText.trim())}
                >
                  {lookingUp ? 'Parsing…' : 'Parse pasted text'}
                </button>
                <button type="button" className="link-button" onClick={resetLookup}>
                  Close
                </button>
              </div>
              {lookupError && <p className="error-text">{lookupError}</p>}
              {lookupMessages.length > 0 && (
                <ul className="hint">
                  {lookupMessages.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
              )}
              {polarSummary && <p className="hint">{polarSummary}</p>}
              <p className="hint">
                Nothing is saved yet — review the fields below (they've been filled in where
                something was found) and press Save boat when they look right.
              </p>
            </div>
          )}

          <div className="field-row">
            <div className="field">
              <label htmlFor="fb-name">Name</label>
              <input id="fb-name" value={form.name} onChange={(e) => set('name', e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="fb-hull">Hull</label>
              <select
                id="fb-hull"
                value={form.hull}
                onChange={(e) => set('hull', e.target.value as Hull)}
              >
                {hulls.map((h) => (
                  <option key={h} value={h}>
                    {h}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="field-row">
            <div className="field">
              <label htmlFor="fb-make">Make</label>
              <input id="fb-make" value={form.make} onChange={(e) => set('make', e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="fb-model">Model</label>
              <input
                id="fb-model"
                value={form.model}
                onChange={(e) => set('model', e.target.value)}
              />
            </div>
          </div>
          <div className="field-row">
            <div className="field">
              <label htmlFor="fb-draft">Draft, keel down or fixed (ft)</label>
              <input
                id="fb-draft"
                inputMode="decimal"
                value={form.draft_ft}
                onChange={(e) => set('draft_ft', e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="fb-air">Air draft (ft)</label>
              <input
                id="fb-air"
                inputMode="decimal"
                value={form.air_draft_ft}
                onChange={(e) => set('air_draft_ft', e.target.value)}
              />
            </div>
          </div>
          <div className="field">
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={form.lifting_keel}
                onChange={(e) => set('lifting_keel', e.target.checked)}
              />{' '}
              Lifting keel or daggerboards
            </label>
            {form.lifting_keel && (
              <>
                <label htmlFor="fb-up">Draft with the keel up (ft)</label>
                <input
                  id="fb-up"
                  inputMode="decimal"
                  value={form.draft_up_ft}
                  onChange={(e) => set('draft_up_ft', e.target.value)}
                />
                <p className="hint">
                  Arrival, departure and anchoring are planned against the keel-up draft; the
                  keel-down draft is for open water only.
                </p>
              </>
            )}
          </div>
          <div className="field-row">
            <div className="field">
              <label htmlFor="fb-loa">Length overall (ft, optional)</label>
              <input
                id="fb-loa"
                inputMode="decimal"
                value={form.loa_ft}
                onChange={(e) => set('loa_ft', e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="fb-beam">Beam (ft, optional)</label>
              <input
                id="fb-beam"
                inputMode="decimal"
                value={form.beam_ft}
                onChange={(e) => set('beam_ft', e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="fb-disp">Displacement (lb, optional)</label>
              <input
                id="fb-disp"
                inputMode="decimal"
                value={form.displacement_lb}
                onChange={(e) => set('displacement_lb', e.target.value)}
              />
            </div>
          </div>
          {form.hull !== 'monohull' && (
            <p className="hint">
              For a multihull the beam is what usually fails at slips, channels and travelifts, so
              please fill it in.
            </p>
          )}
          <div className="field-row">
            <div className="field">
              <label htmlFor="fb-mmsi">MMSI (9 digits, optional)</label>
              <input
                id="fb-mmsi"
                inputMode="numeric"
                value={form.mmsi}
                onChange={(e) => set('mmsi', e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="fb-callsign">Call sign (optional)</label>
              <input
                id="fb-callsign"
                value={form.call_sign}
                onChange={(e) => set('call_sign', e.target.value)}
              />
            </div>
          </div>
          <div className="field">
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={form.usual_companion}
                onChange={(e) => set('usual_companion', e.target.checked)}
              />{' '}
              We usually travel together (tick by default)
            </label>
          </div>
          <div className="field">
            <label htmlFor="fb-notes">Notes (optional)</label>
            <textarea
              id="fb-notes"
              rows={2}
              value={form.notes}
              onChange={(e) => set('notes', e.target.value)}
            />
          </div>
          {error && <p className="error-text">{error}</p>}
          <div className="field-row">
            <button type="button" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save boat'}
            </button>
            <button type="button" className="link-button" onClick={closeForm}>
              Cancel
            </button>
            {form.replaces && (
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  const b = boats.find((x) => x.id === form.replaces)
                  if (b) void remove(b)
                }}
              >
                Remove from the list
              </button>
            )}
          </div>
        </div>
      )}
      {!form && error && <p className="error-text">{error}</p>}
    </div>
  )
}
