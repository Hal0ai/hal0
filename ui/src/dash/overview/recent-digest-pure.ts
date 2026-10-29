// hal0 Overview v2 — Recent digest (pure).
//
// Turns the newest-first activity records into a handful of display rows,
// folding the slot state flaps (ready → serving → ready …) a busy slot emits
// into one "served N requests" row. Error records are never folded.

import type { ActivityRecord } from '@/api/hooks/useActivity'

export interface DigestRow {
  key: string
  /** HH:MM, or the weekday short name once the record is more than 24 h old. */
  ts: string
  category: string
  tone: 'info' | 'warn' | 'err' | 'update'
  /** Already escaped; only `<b>` around the target is markup. */
  html: string
}

export interface DigestOpts {
  /** Records of one slot closer than this to the fold's newest record fold together. */
  windowS?: number
  rows?: number
  /** Reference time (ms) for the 24 h weekday rule; defaults to `Date.now()`. */
  now?: number
}

const DAY_MS = 24 * 3600 * 1000
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

const pad2 = (n: number) => String(n).padStart(2, '0')
const hhmm = (d: Date) => `${pad2(d.getHours())}:${pad2(d.getMinutes())}`

function tsLabel(ms: number, now: number): string {
  if (Number.isNaN(ms)) return '—'
  const d = new Date(ms)
  return now - ms > DAY_MS ? WEEKDAYS[d.getDay()] : hhmm(d)
}

function transition(r: ActivityRecord): { from: string; to: string } | null {
  const a = r.after as { from?: unknown; to?: unknown } | null
  return a && typeof a.from === 'string' && typeof a.to === 'string' ? { from: a.from, to: a.to } : null
}

/** Fold candidates: slot state changes that did not fail. */
const foldable = (r: ActivityRecord) =>
  r.action === 'slot.state' && r.severity !== 'error' && !!r.target && !Number.isNaN(Date.parse(r.ts))

function passthroughHtml(r: ActivityRecord): string {
  let msg = r.message ?? ''
  const target = r.target
  if (target && msg.startsWith(`${target}: `)) msg = msg.slice(target.length + 2)
  return target ? `<b>${esc(target)}</b> ${esc(msg)}` : esc(msg)
}

function foldRow(fold: ActivityRecord[], now: number): DigestRow {
  const newest = fold[0]
  const oldest = fold[fold.length - 1]
  const served = fold.filter((r) => transition(r)?.to === 'serving').length
  const target = esc(newest.target as string)
  let html: string
  if (served > 0) {
    const a = hhmm(new Date(Date.parse(oldest.ts)))
    const b = hhmm(new Date(Date.parse(newest.ts)))
    html = `<b>${target}</b> served ${served} ${served === 1 ? 'request' : 'requests'} · ${a === b ? a : `${a}–${b}`}`
  } else {
    const t = transition(newest)
    html = t ? `<b>${target}</b> ${esc(t.from)} → ${esc(t.to)}` : passthroughHtml(newest)
  }
  return {
    key: String(newest.id ?? `${newest.ts}-${newest.action}`),
    ts: tsLabel(Date.parse(newest.ts), now),
    category: newest.category,
    tone: fold.some((r) => r.severity === 'warn') ? 'warn' : 'info',
    html,
  }
}

function passthroughRow(r: ActivityRecord, now: number): DigestRow {
  return {
    key: String(r.id ?? `${r.ts}-${r.action}`),
    ts: tsLabel(Date.parse(r.ts), now),
    category: r.category || r.kind,
    tone: r.severity === 'error' ? 'err' : r.severity === 'warn' ? 'warn' : r.category === 'update' ? 'update' : 'info',
    html: passthroughHtml(r),
  }
}

/** Newest-first records → at most `rows` display rows. Slot flaps fold per target: another slot's
 *  records between two of a target's do not break its fold, and rows stay ordered by their newest
 *  record. Any record that cannot fold (an error, another kind of event) closes every open fold. */
export function digest(records: ActivityRecord[], opts: DigestOpts = {}): DigestRow[] {
  const windowMs = (opts.windowS ?? 600) * 1000
  const cap = opts.rows ?? 5
  const now = opts.now ?? Date.now()
  const items: ({ fold: ActivityRecord[] } | { rec: ActivityRecord })[] = []
  const open = new Map<string, ActivityRecord[]>()
  for (const r of records) {
    if (!foldable(r)) {
      items.push({ rec: r })
      open.clear()
      continue
    }
    const target = r.target as string
    const fold = open.get(target)
    if (fold && Date.parse(fold[0].ts) - Date.parse(r.ts) <= windowMs) {
      fold.push(r)
    } else {
      const next = [r]
      open.set(target, next)
      items.push({ fold: next })
    }
  }
  return items.slice(0, cap).map((it) => ('fold' in it ? foldRow(it.fold, now) : passthroughRow(it.rec, now)))
}
