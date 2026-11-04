/**
 * Purpose: "Comms plan" screen: the vessel-level Starlink/cellular/Maya eSIM
 *   facts, edited by form instead of by hand-editing config/comms.toml or
 *   uploading an override file.
 * Version: v1 (2026-09-22)
 * Scope: PRODUCTION - passage-planner web UI.
 * Architecture: Child of App.tsx (its own collapsible section). GET /api/comms
 *   loads the current config/comms.toml (blank fields if the file doesn't
 *   exist yet); POST /api/comms saves the whole record in one call. Nobody
 *   needs to touch the TOML file or the Advanced options comms_config upload
 *   for the common case; that upload stays as an escape hatch for a one-off
 *   override this form doesn't cover.
 */
import { useEffect, useState } from 'react'
import type { CommsConfigData } from './api/types'

const API_BASE = `${import.meta.env.BASE_URL}api`

interface Form {
  starlink_plan: string
  starlink_billing_cycle_day: string
  starlink_priority_gb: string
  starlink_ocean_mode_usd_per_gb: string
  starlink_notes: string
  tmobile_plan: string
  tmobile_roaming_included: boolean
  tmobile_notes: string
  maya_esim_scope: string
  maya_esim_notes: string
  router_model: string
  router_notes: string
}

const num = (v: number | null): string => (v === null || v === undefined ? '' : String(v))
const toNum = (s: string): number | null => (s.trim() === '' ? null : Number(s))

function fromData(d: CommsConfigData): Form {
  return {
    starlink_plan: d.starlink_plan,
    starlink_billing_cycle_day: num(d.starlink_billing_cycle_day),
    starlink_priority_gb: num(d.starlink_priority_gb),
    starlink_ocean_mode_usd_per_gb: num(d.starlink_ocean_mode_usd_per_gb),
    starlink_notes: d.starlink_notes,
    tmobile_plan: d.tmobile_plan,
    tmobile_roaming_included: d.tmobile_roaming_included,
    tmobile_notes: d.tmobile_notes,
    maya_esim_scope: d.maya_esim_scope,
    maya_esim_notes: d.maya_esim_notes,
    router_model: d.router_model,
    router_notes: d.router_notes,
  }
}

const EMPTY: Form = fromData({
  starlink_plan: '', starlink_billing_cycle_day: null, starlink_priority_gb: null,
  starlink_ocean_mode_usd_per_gb: null, starlink_notes: '', tmobile_plan: '',
  tmobile_roaming_included: false, tmobile_notes: '', maya_esim_scope: '', maya_esim_notes: '',
  router_model: '', router_notes: '',
})

export default function CommsPanel() {
  const [form, setForm] = useState<Form>(EMPTY)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let alive = true
    fetch(`${API_BASE}/comms`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data: CommsConfigData) => {
        if (alive) setForm(fromData(data))
      })
      .catch((e: unknown) => {
        if (alive) setLoadError(e instanceof Error ? e.message : 'could not load the comms plan')
      })
    return () => {
      alive = false
    }
  }, [])

  function set<K extends keyof Form>(key: K, value: Form[K]) {
    setSaved(false)
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function save() {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const body = {
        starlink_plan: form.starlink_plan,
        starlink_billing_cycle_day: toNum(form.starlink_billing_cycle_day),
        starlink_priority_gb: toNum(form.starlink_priority_gb),
        starlink_ocean_mode_usd_per_gb: toNum(form.starlink_ocean_mode_usd_per_gb),
        starlink_notes: form.starlink_notes,
        tmobile_plan: form.tmobile_plan,
        tmobile_roaming_included: form.tmobile_roaming_included,
        tmobile_notes: form.tmobile_notes,
        maya_esim_scope: form.maya_esim_scope,
        maya_esim_notes: form.maya_esim_notes,
        router_model: form.router_model,
        router_notes: form.router_notes,
      }
      const res = await fetch(`${API_BASE}/comms`, {
        method: 'POST', // POST, not PUT: the /routing proxy only forwards GET and POST
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const respBody = await res.json().catch(() => null)
        const detail = respBody?.detail
        throw new Error(typeof detail === 'string' ? detail : `Request failed (${res.status})`)
      }
      const data: CommsConfigData = await res.json()
      setForm(fromData(data))
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the comms plan.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="field comms-panel">
      {loadError && <p className="hint error-text">Could not load the comms plan ({loadError}).</p>}
      <h4>Starlink</h4>
      <div className="field-row">
        <div className="field">
          <label htmlFor="cf-sl-plan">Plan</label>
          <input
            id="cf-sl-plan"
            value={form.starlink_plan}
            onChange={(e) => set('starlink_plan', e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="cf-sl-cycle">Billing cycle day (1-28)</label>
          <input
            id="cf-sl-cycle"
            inputMode="numeric"
            value={form.starlink_billing_cycle_day}
            onChange={(e) => set('starlink_billing_cycle_day', e.target.value)}
          />
        </div>
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="cf-sl-gb">Priority GB/mo (optional, uncapped if blank)</label>
          <input
            id="cf-sl-gb"
            inputMode="decimal"
            value={form.starlink_priority_gb}
            onChange={(e) => set('starlink_priority_gb', e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="cf-sl-rate">Ocean Mode rate ($/GB, optional)</label>
          <input
            id="cf-sl-rate"
            inputMode="decimal"
            value={form.starlink_ocean_mode_usd_per_gb}
            onChange={(e) => set('starlink_ocean_mode_usd_per_gb', e.target.value)}
          />
        </div>
      </div>
      <div className="field">
        <label htmlFor="cf-sl-notes">Notes</label>
        <input
          id="cf-sl-notes"
          value={form.starlink_notes}
          onChange={(e) => set('starlink_notes', e.target.value)}
        />
      </div>

      <h4>T-Mobile</h4>
      <div className="field-row">
        <div className="field">
          <label htmlFor="cf-tm-plan">Plan</label>
          <input
            id="cf-tm-plan"
            value={form.tmobile_plan}
            onChange={(e) => set('tmobile_plan', e.target.value)}
          />
        </div>
        <div className="field">
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={form.tmobile_roaming_included}
              onChange={(e) => set('tmobile_roaming_included', e.target.checked)}
            />{' '}
            Roaming included
          </label>
        </div>
      </div>
      <div className="field">
        <label htmlFor="cf-tm-notes">Notes</label>
        <input
          id="cf-tm-notes"
          value={form.tmobile_notes}
          onChange={(e) => set('tmobile_notes', e.target.value)}
        />
      </div>

      <h4>Maya eSIM</h4>
      <div className="field">
        <label htmlFor="cf-my-scope">Coverage scope</label>
        <input
          id="cf-my-scope"
          value={form.maya_esim_scope}
          onChange={(e) => set('maya_esim_scope', e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="cf-my-notes">Notes</label>
        <input
          id="cf-my-notes"
          value={form.maya_esim_notes}
          onChange={(e) => set('maya_esim_notes', e.target.value)}
        />
      </div>

      <h4>Router</h4>
      <div className="field">
        <label htmlFor="cf-rt-model">Model</label>
        <input
          id="cf-rt-model"
          value={form.router_model}
          onChange={(e) => set('router_model', e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="cf-rt-notes">Notes</label>
        <input
          id="cf-rt-notes"
          value={form.router_notes}
          onChange={(e) => set('router_notes', e.target.value)}
        />
      </div>

      {error && <p className="error-text">{error}</p>}
      <div className="field-row">
        <button type="button" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save comms plan'}
        </button>
        {saved && <span className="hint">Saved.</span>}
      </div>
    </div>
  )
}
