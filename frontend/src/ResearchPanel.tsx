/**
 * Purpose: "Research with Claude" panel for the shore-logistics section:
 *   starts a background research job for the listed ports, shows per-port
 *   progress, lets the skipper cancel, and hands finished sections to App.
 * Version: v2 (2026-09-21) - adds "wrong place? research as ..." per finished
 *   port; v1 was the button, consent, progress, cancel and reload re-attach.
 * Scope: PRODUCTION - passage-planner web UI.
 * Architecture: Child of App.tsx (inside the "Shore logistics" details).
 *   Talks to backend/main.py's /api/logistics-research endpoints (backed by
 *   src/passage_planner/research_jobs.py). Never edits the textarea itself: it
 *   calls `onApply`, and App decides whether the port's section is still
 *   untouched (auto-replace) or edited by hand (a per-port Replace button
 *   appears here instead). Always behind an explicit consent step, never
 *   triggered by Generate briefing. The running job id is kept in
 *   localStorage so a reload picks the job back up (the backend keeps it).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { splitPorts } from './logisticsSections'
import type {
  ResearchJobState,
  ResearchPortState,
  ResearchStatusResponse,
} from './api/types'

const API_BASE = `${import.meta.env.BASE_URL}api`
const POLL_MS = 4000
const JOB_KEY = 'hylas-research-job'

interface Props {
  /** The ports to research, one per line (from the picked stops). */
  ports: string
  /** Why the boat stops at each port (port name to role); omitted ports are ordinary stops. */
  roles: Record<string, string>
  /** The boats travelling with us (their draft and beam go into the prompt); null = default. */
  fleetIds: string[] | null
  /** The chosen route GPX, if any (lets the model resolve ambiguous names). */
  getRouteFile: () => File | undefined
  crewCount: number
  /** Put `section` into the textarea; `force` overwrites the skipper's edits.
   *  Returns false when the section was edited by hand and `force` was not set. */
  onApply: (port: string, section: string, force: boolean) => boolean
  /** The skipper re-researches `port` under a better name: App swaps the name in
   *  the ports field and in an untouched textarea section. */
  onRename: (port: string, next: string) => void
}

const remember = (id: string | null) => {
  try {
    if (id) localStorage.setItem(JOB_KEY, id)
    else localStorage.removeItem(JOB_KEY)
  } catch {
    // storage blocked: the panel just won't survive a reload
  }
}

const recall = (): string | null => {
  try {
    return localStorage.getItem(JOB_KEY)
  } catch {
    return null
  }
}

const BADGE: Record<ResearchPortState['status'], string> = {
  pending: 'waiting',
  running: 'researching…',
  ok: 'done',
  failed: 'failed',
  cancelled: 'cancelled',
}

function minutes(seconds: number): string {
  return seconds >= 90 ? `${(seconds / 60).toFixed(1)} min` : `${Math.round(seconds)} s`
}

export default function ResearchPanel({
  ports,
  roles,
  fleetIds,
  getRouteFile,
  crewCount,
  onApply,
  onRename,
}: Props) {
  const [availability, setAvailability] = useState<ResearchStatusResponse | null>(null)
  // The ports the consent box is asking about (null = not asking).
  const [confirming, setConfirming] = useState<string[] | null>(null)
  // The finished port whose "research as ..." field is open, and what is typed.
  const [rerun, setRerun] = useState<{ port: string; value: string } | null>(null)
  const [starting, setStarting] = useState(false)
  const [job, setJob] = useState<ResearchJobState | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Ports whose finished section could not be dropped in because the skipper had
  // edited theirs: they get a manual Replace button.
  const [needsReplace, setNeedsReplace] = useState<Record<string, boolean>>({})
  const handled = useRef<Set<string>>(new Set())

  useEffect(() => {
    fetch(`${API_BASE}/logistics-research/status`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
      .then((s: ResearchStatusResponse) => setAvailability(s))
      .catch(() =>
        setAvailability({ available: false, reason: 'Could not reach the research service.' }),
      )
  }, [])

  // Take each finished port's section exactly once, as it arrives.
  const absorb = useCallback(
    (snap: ResearchJobState) => {
      for (const p of snap.ports) {
        const key = `${snap.id}:${p.name}`
        const usable = p.status === 'ok' || (p.status === 'failed' && p.markdown)
        if (!usable || handled.current.has(key)) continue
        handled.current.add(key)
        if (!onApply(p.name, p.markdown, false)) {
          setNeedsReplace((n) => ({ ...n, [p.name]: true }))
        }
      }
    },
    [onApply],
  )

  const poll = useCallback(
    async (id: string) => {
      try {
        const res = await fetch(`${API_BASE}/logistics-research/${id}`)
        if (res.status === 404) {
          remember(null)
          setJob(null)
          return
        }
        if (!res.ok) throw new Error(`Request failed (${res.status})`)
        const snap: ResearchJobState = await res.json()
        setJob(snap)
        absorb(snap)
        if (snap.status !== 'running') remember(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    },
    [absorb],
  )

  // Pick a job back up after a reload.
  useEffect(() => {
    const id = recall()
    if (id) void poll(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on mount
  }, [])

  const jobId = job?.id
  const running = job?.status === 'running'
  useEffect(() => {
    if (!jobId || !running) return
    const t = setInterval(() => void poll(jobId), POLL_MS)
    return () => clearInterval(t)
  }, [jobId, running, poll])

  async function start(list: string[]) {
    setConfirming(null)
    setStarting(true)
    setError(null)
    setNeedsReplace({})
    try {
      const form = new FormData()
      // the trailing newline keeps a single name that contains a comma in one piece
      form.append('ports', `${list.join('\n')}\n`)
      const wanted = Object.fromEntries(
        Object.entries(roles).filter(([name]) => list.includes(name)),
      )
      if (Object.keys(wanted).length > 0) form.append('roles', JSON.stringify(wanted))
      if (fleetIds !== null) form.append('fleet', JSON.stringify(fleetIds))
      const gpx = getRouteFile()
      if (gpx) form.append('route', gpx)
      if (crewCount > 0) form.append('crew_count', String(crewCount))
      const res = await fetch(`${API_BASE}/logistics-research`, { method: 'POST', body: form })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(body?.detail ?? `Request failed (${res.status})`)
      }
      const { id }: { id: string } = await res.json()
      remember(id)
      await poll(id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setStarting(false)
    }
  }

  async function cancel() {
    if (!job) return
    try {
      await fetch(`${API_BASE}/logistics-research/${job.id}`, { method: 'DELETE' })
      await poll(job.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  function replaceNow(p: ResearchPortState) {
    onApply(p.name, p.markdown, true)
    setNeedsReplace((n) => ({ ...n, [p.name]: false }))
  }

  function submitRerun() {
    if (!rerun) return
    const next = rerun.value.trim().replace(/\s+/g, ' ')
    if (!next) return
    if (next !== rerun.port) onRename(rerun.port, next)
    setRerun(null)
    setConfirming([next])
  }

  const names = splitPorts(ports)
  const disabledReason = !availability
    ? 'Checking…'
    : !availability.available
      ? availability.reason
      : names.length === 0
        ? 'Pick the arrival and stops above first.'
        : running
          ? 'A research job is running.'
          : null

  return (
    <div className="research-panel">
      <div className="inline-field">
        <button
          type="button"
          onClick={() => setConfirming(names)}
          disabled={disabledReason !== null || starting || confirming !== null}
        >
          {starting ? 'Starting…' : 'Research with Claude'}
        </button>
        {disabledReason && <span className="hint">{disabledReason}</span>}
      </div>

      {confirming && (
        <div className="banner warning consent" role="alert">
          <strong>
            Research {confirming.length === 1 ? 'this port' : `these ${confirming.length} ports`}?
          </strong>
          <ul>
            {confirming.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
          <p>
            Claude searches the web for each port, one after another, and fills the template
            below for you to review. It runs on <strong>your Claude subscription</strong> and
            counts against your weekly allotment. Earlier runs took about 90 seconds and
            roughly $0.40 (API-equivalent) per port.
            {getRouteFile()
              ? ' The chosen route file is sent so ambiguous names resolve near your track.'
              : ' No route file is chosen, so ambiguous names are resolved without the track.'}
          </p>
          <div className="guard-actions">
            <button type="button" onClick={() => void start(confirming)}>
              Start research
            </button>
            <button type="button" className="secondary" onClick={() => setConfirming(null)}>
              Not now
            </button>
          </div>
        </div>
      )}

      {error && <div className="banner error">{error}</div>}

      {job && (
        <div className="research-job">
          <div className="research-head">
            <strong>
              {job.status === 'running' && 'Researching…'}
              {job.status === 'done' && 'Research finished'}
              {job.status === 'cancelled' && 'Research cancelled'}
              {job.status === 'error' && 'Research stopped'}
            </strong>
            {running && (
              <button type="button" className="secondary" onClick={() => void cancel()}>
                Cancel
              </button>
            )}
          </div>
          {job.error && <div className="banner error">{job.error}</div>}
          <ul className="research-ports">
            {job.ports.map((p) => (
              <li key={p.name} className={`port-row ${p.status}`}>
                <div className="port-line">
                  <span className="port-name">{p.name}</span>
                  <span className={`badge ${p.status}`}>{BADGE[p.status]}</span>
                  {(p.status === 'ok' || p.status === 'failed') && p.seconds > 0 && (
                    <span className="hint">
                      {minutes(p.seconds)}
                      {p.cost_usd > 0 && ` · $${p.cost_usd.toFixed(2)} API-equiv.`}
                    </span>
                  )}
                </div>
                {p.resolved_as && <div className="resolved">{p.resolved_as}</div>}
                {p.status === 'failed' && p.error && (
                  <div className="port-error">
                    {p.error} — the template was left in place with a RESEARCH INCOMPLETE note.
                  </div>
                )}
                {p.status === 'ok' && p.gaps.length > 0 && (
                  <div className="port-note">
                    Not found for {p.gaps.length} field{p.gaps.length === 1 ? '' : 's'}:{' '}
                    {p.gaps.join(', ')}
                  </div>
                )}
                {p.warnings.map((w) => (
                  <div key={w} className="port-error">
                    {w}
                  </div>
                ))}
                {!running && p.status === 'ok' && (
                  <div className="port-note">
                    {rerun?.port === p.name ? (
                      <span className="inline-field">
                        <input
                          type="text"
                          aria-label={`Research ${p.name} as`}
                          value={rerun.value}
                          onChange={(e) => setRerun({ port: p.name, value: e.target.value })}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault()
                              submitRerun()
                            }
                          }}
                        />
                        <button type="button" onClick={submitRerun}>
                          Research this instead
                        </button>
                        <button type="button" className="secondary" onClick={() => setRerun(null)}>
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="secondary"
                        disabled={confirming !== null}
                        onClick={() => setRerun({ port: p.name, value: p.name })}
                      >
                        Wrong place? Research as…
                      </button>
                    )}
                  </div>
                )}
                {needsReplace[p.name] && (
                  <div className="port-note">
                    You have edited this port&apos;s section, so it was not overwritten.{' '}
                    <button type="button" className="secondary" onClick={() => replaceNow(p)}>
                      Replace with the research
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
          {!running && job.ports.some((p) => p.status === 'ok') && (
            <p className="hint">
              Review before submitting: phone numbers and prices vary between runs, and each
              section should be checked against the operator&apos;s own site. If a port was
              resolved to the wrong place, use &ldquo;Wrong place? Research as…&rdquo; with a
              more specific name.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
