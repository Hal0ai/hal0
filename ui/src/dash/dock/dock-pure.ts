// The status dock's event feed: the journal (in-RAM event lines) and the activity trail (operator
// and agent actions) merged into one timeline, plus the filters the dock's expanded pane applies
// and the small rules its status segment and export button need. Pure, so the merge order and the
// filters are tested without rendering (__tests__/dock-pure.test.ts). The dock components only
// draw the result.
//
// Only `kind === 'action'` activity records join the feed: `event` records duplicate journal lines.

import type { ActivityFilters, ActivityRecord } from '@/api/hooks/useActivity'
import type { JournalEntry } from '@/api/hooks/useLogs'
import type { StatusAction } from '@/dash/image/status-line-pure'
import { entryKey } from '@/api/hooks/logRing.js'

export type DockStream = 'j' | 'a'
export type DockLevel = 'ok' | 'info' | 'warn' | 'error'

export interface DockEvent {
  id: string
  ts: string
  /** `Date.parse(ts)`, or 0 when it does not parse. The sort key. */
  t: number
  stream: DockStream
  source: string
  level: DockLevel
  msg: string
  /** The originating activity record; null for journal lines. */
  record: ActivityRecord | null
  /** A journal line's parsed slot name, when the backend sent one. The slot filter reads it. */
  slot?: string | null
}

/** Rows the collapsed dock ticker shows. */
export const TICKER_ROWS = 2

function parseTs(ts: string | null | undefined): number {
  const t = Date.parse(ts ?? '')
  return Number.isNaN(t) ? 0 : t
}

export function journalToEvents(ring: readonly JournalEntry[]): DockEvent[] {
  // Keyed by content (the signature the ring dedups on), not `entry.id`: journal ids restart at 1
  // after an API restart, so a replayed row would collide with a row already in the ring.
  return ring.map((entry) => ({
    id: `j:${entryKey(entry)}`,
    ts: entry.ts,
    t: parseTs(entry.ts),
    stream: 'j' as const,
    source: entry.source || 'hal0',
    level: entry.level,
    msg: entry.msg,
    record: null,
    slot: entry.slot ?? null,
  }))
}

export function activityToEvents(records: readonly ActivityRecord[]): DockEvent[] {
  return records
    .filter((r) => r.kind === 'action')
    .map((r) => ({
      id: `a:${r.id}`,
      ts: r.ts,
      t: parseTs(r.ts),
      stream: 'a' as const,
      source: r.category,
      level: r.severity,
      msg: r.message,
      record: r,
    }))
}

/** The slot an event is about, or null. A journal line: its `slot` field, else the name after a
 *  `slot:` source. An activity action: its target up to the first `/`. */
export function slotOfEvent(e: DockEvent): string | null {
  if (e.record) {
    const target = e.record.target
    if (!target) return null
    const i = target.indexOf('/')
    return (i >= 0 ? target.slice(0, i) : target) || null
  }
  if (e.slot) return e.slot
  return e.source.startsWith('slot:') ? e.source.slice('slot:'.length) || null : null
}

/** Normalise an activity record's severity into one of the four row classes. */
export function sevOf(rec: { severity?: string | null }): DockLevel {
  const s = String(rec.severity || '').toLowerCase()
  if (s === 'ok' || s === 'warn' || s === 'error' || s === 'info') return s
  return 'info'
}

/** Short HH:MM:SS from an ISO/epoch ts; falls back to the raw string. */
export function shortTime(ts: string | null | undefined): string {
  if (!ts) return ''
  const d = new Date(ts)
  if (!Number.isNaN(d.getTime())) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }
  // Already-short or non-parseable: show the tail.
  return ts.length > 8 ? ts.slice(11, 19) || ts : ts
}

/** Compact actor label: "mcp:hermes" is "hermes (mcp)", an empty actor is "system". */
export function actorLabel(actor: string | null | undefined): string {
  if (!actor) return 'system'
  if (actor.startsWith('mcp:')) return `${actor.slice(4)} (mcp)`
  return actor
}

/** Ascending by time. Equal times keep input order, journal events before activity events. */
export function mergeFeeds(journal: readonly JournalEntry[], activity: readonly ActivityRecord[]): DockEvent[] {
  return [...journalToEvents(journal), ...activityToEvents(activity)].sort((a, b) => a.t - b.t)
}

/** Which streams have already delivered a batch. A stream's first batch is its backfill. */
export type DockReady = Record<DockStream, boolean>

/** The events that are news: not yet seen, and from a stream that had already delivered its first
 *  batch. The journal and the activity trail are separate connections, so whichever backfills
 *  second must not read as new. */
export function newRows(events: readonly DockEvent[], seen: ReadonlySet<string>, ready: DockReady): DockEvent[] {
  return events.filter((e) => ready[e.stream] && !seen.has(e.id))
}

export interface DockFilters {
  stream: 'all' | DockStream
  severity: 'all' | DockLevel
  /** A source group (`sourceGroup`), or 'all'. */
  source: string
  /** A slot name, or 'all'. */
  slot: string
  q: string
}

export const DOCK_FILTERS_DEFAULT: DockFilters = {
  stream: 'all',
  severity: 'all',
  source: 'all',
  slot: 'all',
  q: '',
}

/** Collapse a source to its group key: `slot:npu` is `slot`; an empty source is `hal0`. Same rule
 *  as `_srcGroup` in chrome.jsx. */
export function sourceGroup(source: string): string {
  const v = source || 'hal0'
  const i = v.indexOf(':')
  return i >= 0 ? v.slice(0, i) : v
}

export function sourceGroups(events: readonly DockEvent[]): string[] {
  const groups = new Set(events.map((e) => sourceGroup(e.source)))
  return ['all', ...[...groups].sort()]
}

function matchesSlot(e: DockEvent, slot: string): boolean {
  if (e.record) {
    const target = e.record.target
    return target != null && (target === slot || target.startsWith(`${slot}/`))
  }
  return e.source === `slot:${slot}` || e.slot === slot
}

export function filterEvents(events: readonly DockEvent[], f: DockFilters): DockEvent[] {
  const q = f.q.toLowerCase()
  return events.filter((e) => {
    if (f.stream !== 'all' && e.stream !== f.stream) return false
    if (f.severity !== 'all' && e.level !== f.severity) return false
    if (f.source !== 'all' && sourceGroup(e.source) !== f.source) return false
    if (f.slot !== 'all' && !matchesSlot(e, f.slot)) return false
    if (q && !e.msg.toLowerCase().includes(q) && !e.source.toLowerCase().includes(q)) return false
    return true
  })
}

/** The actions the dock's status segment can draw: the ones with nothing to call are dropped
 *  before the cap, so a missing editor link never costs the segment a real button. */
export function segmentActions(
  actions: readonly StatusAction[],
  ctx: { editorUrl?: string | null; onCancel?: (() => void) | null },
): StatusAction[] {
  return actions
    .filter((a) => {
      if (a === 'open_editor') return !!ctx.editorUrl
      if (a === 'cancel') return !!ctx.onCancel
      return true
    })
    .slice(0, 2)
}

const EXPORT_CATEGORIES = new Set(['slot', 'model', 'profile', 'capability', 'system'])

/** The activity export's filter set for the dock's filters. Only the keys it sets are present. */
export function exportFilters(f: DockFilters): ActivityFilters {
  const out: ActivityFilters = {}
  if (f.severity !== 'all') out.severity = f.severity
  if (EXPORT_CATEGORIES.has(f.source)) out.category = f.source
  if (f.slot !== 'all') out.search = f.slot
  return out
}
