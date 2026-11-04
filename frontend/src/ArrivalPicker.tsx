/**
 * Purpose: "Arrival & stops" picker: choose where the passage arrives and any
 *   stops from the Caribbean arrival pick list instead of typing place names.
 *   Country first, then a place within it.
 * Version: v2 (2026-09-21) - country first: choose a country, then its places grouped
 *   by region or island; the name search across all countries stays as a shortcut.
 *   v1 was the single search box.
 * Scope: PRODUCTION - passage-planner web UI.
 * Architecture: Child of App.tsx. Reads GET /api/arrival-places (the stable
 *   identity list in data/arrival_places.toml, served by backend/main.py) and,
 *   once a route GPX is chosen, POST /api/arrival-places/suggest for the listed
 *   places nearest the route's end. App owns the chosen `stops`; it derives the
 *   destination country (CIQ section) and the shore-logistics port names from
 *   them, so nobody types either. A place that is not on the list can still be
 *   added ("Other"), with a country chosen from the list of countries, and is
 *   marked as not on the list.
 */
import { useEffect, useMemo, useState } from 'react'
import type { ArrivalPlace, ArrivalPlacesResponse, ArrivalSuggestResponse, Stop, StopRole } from './api/types'

const API_BASE = `${import.meta.env.BASE_URL}api`
const MAX_RESULTS = 8

const KIND_LABEL: Record<string, string> = {
  port_of_entry: 'port of entry',
  harbour: 'harbour',
  anchorage: 'anchorage',
}

const fold = (s: string) =>
  s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()

function haystack(p: ArrivalPlace): string {
  return fold([p.label, p.region, ...p.also_known_as].join(' '))
}

interface Props {
  stops: Stop[]
  onChange: (stops: Stop[]) => void
  /** Bumped by App whenever the route file input changes. */
  routeVersion: number
  getRouteFile: () => File | undefined
}

export default function ArrivalPicker({ stops, onChange, routeVersion, getRouteFile }: Props) {
  const [places, setPlaces] = useState<ArrivalPlace[]>([])
  const [countries, setCountries] = useState<string[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [country, setCountry] = useState('')
  const [filter, setFilter] = useState('')
  const [suggest, setSuggest] = useState<ArrivalSuggestResponse | null>(null)
  const [otherOpen, setOtherOpen] = useState(false)
  const [otherName, setOtherName] = useState('')
  const [otherCountry, setOtherCountry] = useState('')

  useEffect(() => {
    fetch(`${API_BASE}/arrival-places`)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => null)
          throw new Error(body?.detail ?? `Request failed (${res.status})`)
        }
        return res.json() as Promise<ArrivalPlacesResponse>
      })
      .then((data) => {
        setPlaces(data.places)
        setCountries(data.countries)
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)))
  }, [])

  // Offer the listed places nearest where the chosen route ends.
  useEffect(() => {
    const file = getRouteFile()
    if (!file) {
      setSuggest(null)
      return
    }
    const form = new FormData()
    form.append('route', file)
    fetch(`${API_BASE}/arrival-places/suggest`, { method: 'POST', body: form })
      .then((res) => (res.ok ? (res.json() as Promise<ArrivalSuggestResponse>) : null))
      .then(setSuggest)
      .catch(() => setSuggest(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the file changes
  }, [routeVersion])

  const chosen = useMemo(() => new Set(stops.map((s) => s.label.toLowerCase())), [stops])

  // Only countries that actually have places, with how many.
  const countryOptions = useMemo(() => {
    const counts = new Map<string, number>()
    for (const p of places) counts.set(p.country, (counts.get(p.country) ?? 0) + 1)
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [places])

  // The chosen country's places, grouped by region or island, in list order.
  const groups = useMemo(() => {
    if (!country) return []
    const words = fold(filter).split(/\s+/).filter(Boolean)
    // Group by region when the country has several (the Bahamas: Abacos, Exumas, ...);
    // when every place shares one region (Grenada: "Windward Islands") that says nothing,
    // so group by island instead (Carriacou, Grenada, Petite Martinique).
    const regions = new Set(places.filter((p) => p.country === country && p.region).map((p) => p.region))
    const useRegion = regions.size >= 2
    const out: { title: string; items: ArrivalPlace[] }[] = []
    for (const p of places) {
      if (p.country !== country) continue
      if (words.length > 0) {
        const h = haystack(p)
        if (!words.every((w) => h.includes(w))) continue
      }
      const title = useRegion ? p.region || p.island || country : p.island || p.region || country
      const last = out[out.length - 1]
      if (last && last.title === title) last.items.push(p)
      else out.push({ title, items: [p] })
    }
    // list order is by island within a country, so a region can recur: merge them
    const merged = new Map<string, ArrivalPlace[]>()
    for (const g of out) merged.set(g.title, [...(merged.get(g.title) ?? []), ...g.items])
    return [...merged.entries()]
      .map(([title, items]) => ({ title, items }))
      .sort((a, b) => a.title.localeCompare(b.title))
  }, [places, country, filter])

  const inCountry = useMemo(
    () => places.filter((p) => p.country === country).length,
    [places, country],
  )

  const results = useMemo(() => {
    const words = fold(query).split(/\s+/).filter(Boolean)
    if (words.length === 0) return []
    return places
      .filter((p) => !chosen.has(p.label.toLowerCase()))
      .filter((p) => {
        const h = haystack(p)
        return words.every((w) => h.includes(w))
      })
      .slice(0, MAX_RESULTS)
  }, [places, query, chosen])

  function add(p: ArrivalPlace) {
    if (chosen.has(p.label.toLowerCase())) return
    onChange([...stops, { label: p.label, country: p.country, id: p.id, kind: p.kind }])
    setQuery('')
  }

  function addOther() {
    const name = otherName.trim().replace(/\s+/g, ' ')
    if (!name || !otherCountry || chosen.has(name.toLowerCase())) return
    onChange([...stops, { label: name, country: otherCountry }])
    setOtherName('')
    setOtherOpen(false)
  }

  function remove(label: string) {
    onChange(stops.filter((s) => s.label !== label))
  }

  function setRole(label: string, role: string) {
    onChange(
      stops.map((s) =>
        s.label === label ? { ...s, role: role ? (role as StopRole) : undefined } : s,
      ),
    )
  }

  const suggestions = (suggest?.end ?? []).filter((p) => !chosen.has(p.label.toLowerCase()))

  return (
    <div className="field arrival-picker">
      <label>Arrival &amp; stops</label>

      {stops.length > 0 && (
        <ul className="stop-chips">
          {stops.map((s) => (
            <li key={s.label} className={`chip ${s.id ? '' : 'unlisted'}`}>
              <span>{s.label}</span>
              {s.kind && <span className="chip-kind">{KIND_LABEL[s.kind] ?? s.kind}</span>}
              {!s.id && <span className="chip-kind">not on the list</span>}
              <select
                className="chip-role"
                aria-label={`Why the boat stops at ${s.label}`}
                value={s.role ?? ''}
                onChange={(e) => setRole(s.label, e.target.value)}
              >
                <option value="">Stop</option>
                <option value="check-in">Check in</option>
                <option value="check-out">Check out</option>
                <option value="fuel">Fuel</option>
              </select>
              <button
                type="button"
                className="chip-remove"
                aria-label={`Remove ${s.label}`}
                onClick={() => remove(s.label)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="country-pick">
        <label htmlFor="arrival-country" className="sub-label">
          1. Country
        </label>
        <select
          id="arrival-country"
          value={country}
          onChange={(e) => {
            setCountry(e.target.value)
            setFilter('')
          }}
        >
          <option value="">Choose a country…</option>
          {countryOptions.map(([c, n]) => (
            <option key={c} value={c}>
              {c} ({n})
            </option>
          ))}
        </select>
      </div>

      {country && (
        <div className="place-pick">
          <span className="sub-label">2. Place in {country}</span>
          {inCountry > 12 && (
            <input
              type="text"
              autoComplete="off"
              aria-label={`Filter places in ${country}`}
              placeholder={`Filter ${country}'s places`}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          )}
          {groups.length === 0 && <div className="hint">No place matches that filter.</div>}
          {groups.map((g) => (
            <div key={g.title} className="place-group">
              <div className="place-group-title">{g.title}</div>
              <ul className="place-list">
                {g.items.map((p) => {
                  const picked = chosen.has(p.label.toLowerCase())
                  return (
                    <li key={p.id}>
                      <button
                        type="button"
                        className={`place-row ${picked ? 'picked' : ''}`}
                        disabled={picked}
                        onClick={() => add(p)}
                        title={p.clearance}
                      >
                        <span>
                          {p.name}
                          {p.island && p.island !== g.title && p.island !== p.name && (
                            <span className="place-island"> · {p.island}</span>
                          )}
                        </span>
                        <span className="chip-kind">
                          {picked ? 'added' : (KIND_LABEL[p.kind] ?? p.kind)}
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </div>
      )}

      <div className="picker-box">
        <label htmlFor="arrival-search" className="sub-label">
          or search every country by name
        </label>
        <input
          id="arrival-search"
          type="text"
          autoComplete="off"
          placeholder={
            places.length > 0
              ? 'Search a harbour, island or country (e.g. tyrell, carriacou)'
              : 'The arrival list is empty'
          }
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              if (results[0]) add(results[0])
            }
          }}
        />
        {results.length > 0 && (
          <ul className="picker-results" role="listbox">
            {results.map((p) => (
              <li key={p.id}>
                <button type="button" role="option" onClick={() => add(p)}>
                  <span>{p.label}</span>
                  <span className="chip-kind">{KIND_LABEL[p.kind] ?? p.kind}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {query.trim() && results.length === 0 && places.length > 0 && (
          <div className="hint">
            Nothing on the list matches &ldquo;{query.trim()}&rdquo;. Use &ldquo;Other&rdquo;
            below to add it.
          </div>
        )}
      </div>

      {suggestions.length > 0 && (
        <div className="suggestions">
          <span className="hint">Your route ends near:</span>
          {suggestions.map((p) => (
            <button key={p.id} type="button" className="secondary" onClick={() => add(p)}>
              {p.label} · {p.distance_nm} NM
            </button>
          ))}
        </div>
      )}

      {loadError && <div className="banner error">Arrival list: {loadError}</div>}

      {otherOpen ? (
        <div className="other-form">
          <input
            type="text"
            aria-label="Place name"
            placeholder="Place name (as you would say it)"
            value={otherName}
            onChange={(e) => setOtherName(e.target.value)}
          />
          <select
            aria-label="Country"
            value={otherCountry}
            onChange={(e) => setOtherCountry(e.target.value)}
          >
            <option value="">Country…</option>
            {countries.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <button type="button" onClick={addOther} disabled={!otherName.trim() || !otherCountry}>
            Add
          </button>
          <button type="button" className="secondary" onClick={() => setOtherOpen(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div>
          <button type="button" className="secondary" onClick={() => setOtherOpen(true)}>
            Other (not on the list)…
          </button>
        </div>
      )}

      <span className="hint">
        Choose a country, then the arrival and any stops on the way (repeat for each country you
        stop in). The destination country and the shore-logistics ports come from these.
      </span>
    </div>
  )
}
