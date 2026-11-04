/**
 * Purpose: Pure helpers for reading and replacing one port's `### <port>`
 *   section inside the shore-logistics Markdown textarea.
 * Version: v4 (2026-09-22) - adds removeSection(), for the orphaned-section
 *   warning App.tsx shows after a "Wrong place? Research as..." rename on a
 *   hand-edited port (the old heading is deliberately left in place rather
 *   than silently renamed/merged - see renamePort's comment - so it needs a
 *   visible way to clean it up). v3 added splitPorts() (same rule as the
 *   backend); v2 had a singlePortName() that is no longer needed; v1 was the
 *   section helpers.
 * Scope: PRODUCTION - passage-planner web UI.
 * Architecture: Used by App.tsx to merge "Research with Claude" results into
 *   the textarea without clobbering the skipper's own edits. No React, no
 *   fetches, so it can be exercised directly with `node` (see the checks in
 *   frontend/checks/logisticsSections.check.ts). Mirrors the `### ` heading rule the backend's
 *   logistics.py uses to split sections.
 */

const HEADING_RE = /^###\s+(.+?)\s*$/

interface Span {
  port: string
  start: number // index of the heading line
  end: number // index one past the section's last line
}

function spans(lines: string[]): Span[] {
  const heads: { port: string; at: number }[] = []
  lines.forEach((ln, i) => {
    const m = HEADING_RE.exec(ln)
    if (m) heads.push({ port: m[1], at: i })
  })
  return heads.map((h, i) => ({
    port: h.port,
    start: h.at,
    end: i + 1 < heads.length ? heads[i + 1].at : lines.length,
  }))
}

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase()

/** The text of `port`'s section (heading included, trailing blank lines
 *  dropped), or `null` if the Markdown has no such section. */
export function sectionFor(markdown: string, port: string): string | null {
  const lines = markdown.split('\n')
  const s = spans(lines).find((x) => same(x.port, port))
  return s ? lines.slice(s.start, s.end).join('\n').trimEnd() : null
}

/** Every `### ` section keyed by its heading, e.g. to remember what a freshly
 *  generated template looked like. */
export function allSections(markdown: string): Record<string, string> {
  const lines = markdown.split('\n')
  return Object.fromEntries(
    spans(lines).map((s) => [s.port, lines.slice(s.start, s.end).join('\n').trimEnd()]),
  )
}

/** `markdown` with `port`'s section replaced by `section`, or `section`
 *  appended if the port has none. Everything else is left byte-for-byte. */
export function replaceOrAppend(markdown: string, port: string, section: string): string {
  const body = section.trimEnd()
  const lines = markdown.split('\n')
  const s = spans(lines).find((x) => same(x.port, port))
  if (!s) {
    const base = markdown.trimEnd()
    return (base ? `${base}\n\n` : '') + body + '\n'
  }
  // The span runs to the next heading, so it already swallowed the blank line(s)
  // that separated the two sections; put exactly one back.
  const after = lines.slice(s.end)
  const rest = after.length > 0 ? ['', ...after] : ['']
  return [...lines.slice(0, s.start), ...body.split('\n'), ...rest].join('\n')
}

/** `markdown` with `port`'s section (heading and body) removed entirely, one
 *  blank line kept between whatever now ends up adjacent. A no-op (returns
 *  `markdown` unchanged) if the port has no section. For orphaned sections a
 *  rename left behind - see App.tsx's `renamePort` - never called silently. */
export function removeSection(markdown: string, port: string): string {
  const lines = markdown.split('\n')
  const s = spans(lines).find((x) => same(x.port, port))
  if (!s) return markdown
  const before = lines.slice(0, s.start)
  const after = lines.slice(s.end)
  while (before.length > 0 && before[before.length - 1].trim() === '') before.pop()
  while (after.length > 0 && after[0].trim() === '') after.shift()
  if (before.length === 0) return after.join('\n')
  if (after.length === 0) return before.join('\n').trimEnd() + '\n'
  return [...before, '', ...after].join('\n')
}

/** Port names from a posted or typed list, blanks and repeats dropped. Newlines or
 *  semicolons separate ports when either is present (so a picked name such as
 *  "Tyrell Bay, Carriacou (Grenada)" survives); otherwise commas do. Same rule as
 *  the backend's logistics.split_ports. */
export function splitPorts(raw: string): string[] {
  const parts = /[\n;]/.test(raw) ? raw.split(/[\n;]/) : raw.split(',')
  return [...new Set(parts.map((p) => p.trim()).filter(Boolean))]
}
