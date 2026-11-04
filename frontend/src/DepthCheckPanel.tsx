/**
 * Purpose: "Depth check" screen: lists the waypoints of the chosen route that need a
 *   depth read off a chart, takes the depths as plain numbers, and remembers them.
 * Version: v1 (2026-09-21)
 * Scope: PRODUCTION - passage-planner web UI.
 * Architecture: Child of App.tsx (its own collapsible section). POST /api/depth-check
 *   (route GPX + departure + speed) returns the rows; PUT /api/depth-soundings saves
 *   depths (config/depth_soundings.json, matched by POSITION within half a nautical mile,
 *   so they help every later route past the same spot); DELETE forgets one. Nothing here
 *   or in the briefing needs the crew to see or edit a file: the saved depths are applied
 *   automatically when a briefing is generated (service.run_brief).
 */
import { useState } from 'react'
import type { DepthCheckResponse, DepthRow } from './api/types'

const API_BASE = `${import.meta.env.BASE_URL}api`

interface Props {
  getRouteFile: () => File | undefined
  depart: string
  speed: string
  /** The boats travelling with us (their draft governs shared stops); null = default. */
  fleetIds: string[] | null
}

interface Edit {
  depth: string
  source: string
}

const STATUS_LABEL: Record<DepthRow['status'], string> = {
  needs_depth: 'needs a depth',
  tight: 'tight at the tide',
  saved: 'depth saved',
}

function pos(lat: number, lon: number): string {
  return `${Math.abs(lat).toFixed(3)}°${lat >= 0 ? 'N' : 'S'} ${Math.abs(lon).toFixed(3)}°${lon >= 0 ? 'E' : 'W'}`
}

export default function DepthCheckPanel({ getRouteFile, depart, speed, fleetIds }: Props) {
  const [data, setData] = useState<DepthCheckResponse | null>(null)
  const [edits, setEdits] = useState<Record<string, Edit>>({})
  const [busy, setBusy] = useState<'check' | 'save' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  async function check(keepNote = false) {
    const file = getRouteFile()
    if (!file) {
      setError('Choose a GPX route file first.')
      return
    }
    setBusy('check')
    setError(null)
    if (!keepNote) setNote(null)
    try {
      const form = new FormData()
      form.append('route', file)
      form.append('depart', depart)
      form.append('speed', speed)
      if (fleetIds !== null) form.append('fleet', JSON.stringify(fleetIds))
      const res = await fetch(`${API_BASE}/depth-check`, { method: 'POST', body: form })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.detail ?? `Request failed (${res.status})`)
      }
      const next: DepthCheckResponse = await res.json()
      setData(next)
      setEdits(
        Object.fromEntries(
          next.rows.map((r) => [
            r.name,
            {
              depth: r.saved ? String(r.saved.depth_ft) : '',
              source: r.saved?.source ?? '',
            },
          ]),
        ),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  function setEdit(name: string, patch: Partial<Edit>) {
    setEdits((e) => ({ ...e, [name]: { ...e[name], ...patch } }))
  }

  // Rows where the person typed a depth that differs from what is already saved.
  const changed = (data?.rows ?? []).filter((r) => {
    const e = edits[r.name]
    if (!e || e.depth.trim() === '') return false
    const n = Number(e.depth)
    if (!Number.isFinite(n)) return false
    return !r.saved || r.saved.depth_ft !== n || r.saved.source !== e.source.trim()
  })

  async function save() {
    setBusy('save')
    setError(null)
    try {
      const soundings = changed.map((r) => ({
        lat: r.lat,
        lon: r.lon,
        depth_ft: Number(edits[r.name].depth),
        source: edits[r.name].source.trim() || 'read from chart',
        label: r.name,
      }))
      const res = await fetch(`${API_BASE}/depth-soundings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ soundings }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.detail ?? `Request failed (${res.status})`)
      }
      setNote(
        `Saved ${soundings.length} ${soundings.length === 1 ? 'depth' : 'depths'}. They are used ` +
          'automatically in briefings, for this route and any other that passes within half a mile.',
      )
      await check(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }

  async function forget(id: string) {
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/depth-soundings/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`Request failed (${res.status})`)
      setNote('Forgotten.')
      await check(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="depth-panel">
      <p className="hint">
        Where the automatic chart data cannot judge a waypoint, look at the depth on your chart
        (Navionics, a chartbook, the plotter) and type it in. It is remembered for that spot, so
        you only do it once.
      </p>
      <div>
        <button type="button" onClick={() => void check()} disabled={busy !== null}>
          {busy === 'check' ? 'Checking…' : "Check this route's depths"}
        </button>
      </div>

      {error && <div className="banner error">{error}</div>}
      {note && <div className="banner warning">{note}</div>}

      {data && (
        <>
          <p className="hint">
            This boat needs at least <strong>{data.required_ft} ft</strong> of chart depth (
            {data.draft_ft} ft draft plus {data.margin_ft} ft of clearance), before any tide.
          </p>
          {data.rows.length === 0 ? (
            <div className="banner warning">
              Nothing needs your attention on {data.route_name}: every waypoint is in deep water
              or covered by the automatic chart data.
            </div>
          ) : (
            <ul className="depth-rows">
              {data.rows.map((r) => {
                const e = edits[r.name] ?? { depth: '', source: '' }
                return (
                  <li key={r.name} className={`depth-row ${r.status}`}>
                    <div className="depth-head">
                      <strong>{r.name}</strong>
                      <span className="hint">{pos(r.lat, r.lon)}</span>
                      <span className={`badge ${r.status === 'saved' ? '' : 'failed'}`}>
                        {STATUS_LABEL[r.status]}
                      </span>
                    </div>
                    <div className="hint">{r.automatic}</div>
                    {r.status === 'tight' && (
                      <div className="port-error">
                        Charted depth plus the tide at arrival is under the {r.required_ft} ft this
                        boat needs. Check the depth on your chart, and plan the tide. If the waypoint itself sits in
                        shallow water, it may need moving on the route.
                      </div>
                    )}
                    <div className="depth-inputs">
                      <label>
                        Depth on your chart (feet)
                        <input
                          type="number"
                          min="0"
                          step="0.1"
                          value={e.depth}
                          onChange={(ev) => setEdit(r.name, { depth: ev.target.value })}
                        />
                      </label>
                      <label>
                        Where you read it
                        <input
                          type="text"
                          placeholder="e.g. Navionics, Explorer chartbook"
                          value={e.source}
                          onChange={(ev) => setEdit(r.name, { source: ev.target.value })}
                        />
                      </label>
                      {r.saved && (
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => void forget(r.saved!.id)}
                        >
                          Forget
                        </button>
                      )}
                    </div>
                    {r.saved && (
                      <div className="hint">
                        Saved {r.saved.recorded}: {r.saved.depth_ft} ft
                        {r.saved.source ? ` (${r.saved.source})` : ''}
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
          {changed.length > 0 && (
            <div>
              <button type="button" onClick={() => void save()} disabled={busy !== null}>
                {busy === 'save'
                  ? 'Saving…'
                  : `Save ${changed.length} ${changed.length === 1 ? 'depth' : 'depths'}`}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
