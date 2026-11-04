/**
 * Purpose: "Documents" screen: issue/expiry dates and notes for the fixed set of
 *   document kinds the readiness check looks for, edited by form instead of by
 *   hand-editing config/papers.toml or uploading an override file.
 * Version: v1 (2026-09-22)
 * Scope: PRODUCTION - passage-planner web UI.
 * Architecture: Child of App.tsx (its own collapsible section). GET /api/papers
 *   lists every DocKind with its label and current override fields (blank when
 *   unset); POST /api/papers saves every row in one call (config/papers.toml's
 *   whole [documents] table - there is no add/remove/rename, just filling in
 *   the fixed rows, since papers/overrides.py only ever honors one override
 *   per kind). Nobody needs to touch the TOML file or the Advanced options
 *   papers_file upload for the common case; that upload stays as an escape
 *   hatch for a one-off override this form doesn't cover.
 */
import { useEffect, useState } from 'react'
import type { PaperDocEntry, PapersResponse } from './api/types'

const API_BASE = `${import.meta.env.BASE_URL}api`

function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}

export default function DocumentsPanel() {
  const [docs, setDocs] = useState<PaperDocEntry[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let alive = true
    fetch(`${API_BASE}/papers`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data: PapersResponse) => {
        if (alive) setDocs(data.documents)
      })
      .catch((e: unknown) => {
        if (alive) setLoadError(e instanceof Error ? e.message : 'could not load documents')
      })
    return () => {
      alive = false
    }
  }, [])

  function set(kind: string, key: keyof PaperDocEntry, value: string) {
    setSaved(false)
    setDocs((list) => list.map((d) => (d.kind === kind ? { ...d, [key]: value } : d)))
  }

  async function save() {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const res = await fetch(`${API_BASE}/papers`, {
        method: 'POST', // POST, not PUT: the /routing proxy only forwards GET and POST
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ documents: docs }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        const detail = body?.detail
        throw new Error(typeof detail === 'string' ? detail : `Request failed (${res.status})`)
      }
      const data: PapersResponse = await res.json()
      setDocs(data.documents)
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save documents.')
    } finally {
      setSaving(false)
    }
  }

  const today = todayIso()

  return (
    <div className="field documents-panel">
      {loadError && <p className="hint error-text">Could not load documents ({loadError}).</p>}
      <p className="hint">
        Issue/expiry dates and notes for each document kind the readiness check looks for. Blank
        fields are just not on file yet. A document that's expired, missing or only in a legacy
        (pre-2025) copy blocks the briefing's readiness check; an approaching expiry is a caution.
      </p>
      {docs.length > 0 && (
        <ul className="documents-list">
          {docs.map((d) => {
            const expired = d.expires !== '' && d.expires < today
            return (
              <li key={d.kind}>
                <div className="documents-row-head">
                  <span className="documents-label">{d.label}</span>
                  {expired && <span className="chip-kind error-text">expired</span>}
                </div>
                <div className="field-row">
                  <div className="field">
                    <label htmlFor={`doc-path-${d.kind}`}>File path (optional)</label>
                    <input
                      id={`doc-path-${d.kind}`}
                      value={d.path}
                      onChange={(e) => set(d.kind, 'path', e.target.value)}
                      placeholder="../Papers of Record/..."
                    />
                  </div>
                  <div className="field">
                    <label htmlFor={`doc-issued-${d.kind}`}>Issued</label>
                    <input
                      id={`doc-issued-${d.kind}`}
                      type="date"
                      value={d.issued}
                      onChange={(e) => set(d.kind, 'issued', e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor={`doc-expires-${d.kind}`}>Expires</label>
                    <input
                      id={`doc-expires-${d.kind}`}
                      type="date"
                      value={d.expires}
                      onChange={(e) => set(d.kind, 'expires', e.target.value)}
                    />
                  </div>
                </div>
                <div className="field">
                  <label htmlFor={`doc-note-${d.kind}`}>Note (optional)</label>
                  <input
                    id={`doc-note-${d.kind}`}
                    value={d.note}
                    onChange={(e) => set(d.kind, 'note', e.target.value)}
                  />
                </div>
              </li>
            )
          })}
        </ul>
      )}
      {error && <p className="error-text">{error}</p>}
      <div className="field-row">
        <button type="button" onClick={save} disabled={saving || docs.length === 0}>
          {saving ? 'Saving…' : 'Save documents'}
        </button>
        {saved && <span className="hint">Saved.</span>}
      </div>
    </div>
  )
}
