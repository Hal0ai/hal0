// [memory.refresh] (ADR-0060) — the pure half of the Knowledge page refresh panel.
//
// The schedules are five-field cron expressions in UTC, read the way the server reads them
// (src/hal0/config/cron.py): numbers, `*`, ranges `a-b`, steps `/n` and comma lists, no names and
// no `@` shorthands. When both the day of month and the day of week are restricted a day matches
// either. The panel edits them as presets in the viewer's local time and always shows the UTC
// expression it will save.
//
// Local time is turned into UTC with the viewer's offset *now* (`offsetMinutes`, east of UTC
// positive, i.e. `-new Date().getTimezoneOffset()`). The saved cron is UTC and does not move, so
// after a daylight-saving change the same schedule reads an hour off in local time; the panel says
// so next to the time.

export type PagesMode = 'scheduled' | 'after_consolidation' | 'manual'
export type ConsolidationMode = 'continuous' | 'scheduled'

export const PAGES_MODES: { id: PagesMode; label: string }[] = [
  { id: 'scheduled', label: 'Scheduled' },
  { id: 'after_consolidation', label: 'After every consolidation' },
  { id: 'manual', label: 'Manual only' },
]

export const CONSOLIDATION_MODES: { id: ConsolidationMode; label: string }[] = [
  { id: 'continuous', label: 'Continuous' },
  { id: 'scheduled', label: 'Scheduled' },
]

/** The server's defaults (src/hal0/config/schema.py, MemoryRefreshConfig). */
export const REFRESH_DEFAULTS = {
  pages: 'scheduled' as PagesMode,
  schedule: '0 3 * * *',
  min_interval_s: 86400,
  consolidation: 'continuous' as ConsolidationMode,
  consolidation_schedule: '0 2 * * *',
}

/** `min_interval_s` bounds: 0 to 30 days. */
export const MAX_INTERVAL_S = 30 * 86400

// ── cron ──────────────────────────────────────────────────────────────────────

const FIELDS: [string, number, number][] = [
  ['minute', 0, 59],
  ['hour', 0, 23],
  ['day of month', 1, 31],
  ['month', 1, 12],
  ['day of week', 0, 7],
]

export interface Cron {
  expression: string
  minutes: Set<number>
  hours: Set<number>
  days: Set<number>
  months: Set<number>
  weekdays: Set<number>
  daysRestricted: boolean
  weekdaysRestricted: boolean
}

const DIGITS = /^\d+$/

function parseField(text: string, name: string, low: number, high: number): Set<number> {
  if (!text) throw new Error(`the ${name} field is empty`)
  const out = new Set<number>()
  for (const part of text.split(',')) {
    if (!part) throw new Error(`the ${name} field has an empty list entry`)
    let body = part
    let step = 1
    const slash = part.indexOf('/')
    if (slash !== -1) {
      body = part.slice(0, slash)
      const stepText = part.slice(slash + 1)
      if (!DIGITS.test(stepText) || Number(stepText) < 1) throw new Error(`the ${name} field has a bad step ${part}`)
      step = Number(stepText)
    }
    let start: number
    let end: number
    if (body === '*') {
      start = low
      end = high
    } else if (body.includes('-')) {
      const [a, b] = body.split('-', 2)
      if (!DIGITS.test(a) || !DIGITS.test(b)) throw new Error(`the ${name} field has a bad range ${part}`)
      start = Number(a)
      end = Number(b)
      if (start > end) throw new Error(`the ${name} field has a range that runs backwards ${part}`)
    } else if (DIGITS.test(body)) {
      start = Number(body)
      end = slash !== -1 ? high : start
    } else {
      throw new Error(`the ${name} field has ${part}, which is not a number`)
    }
    if (start < low || end > high) throw new Error(`the ${name} field is ${low}-${high}, and ${part} is outside it`)
    for (let v = start; v <= end; v += step) out.add(v)
  }
  return out
}

/** Parse a cron expression, or throw an Error that says what is wrong. */
export function parseCron(expression: string): Cron {
  const fields = String(expression ?? '').trim().split(/\s+/).filter(Boolean)
  if (fields.length !== 5) {
    throw new Error(`a schedule has five fields (minute hour day month weekday), this has ${fields.length}`)
  }
  const parsed = fields.map((text, i) => parseField(text, FIELDS[i][0], FIELDS[i][1], FIELDS[i][2]))
  const weekdays = new Set([...parsed[4]].map((d) => (d === 7 ? 0 : d)))
  const cron: Cron = {
    expression: fields.join(' '),
    minutes: parsed[0],
    hours: parsed[1],
    days: parsed[2],
    months: parsed[3],
    weekdays,
    daysRestricted: fields[2] !== '*',
    weekdaysRestricted: fields[4] !== '*',
  }
  if (nextFireOf(cron, new Date(Date.UTC(2000, 0, 1))) === null) throw new Error('this schedule names no day that exists')
  return cron
}

/** The error message for an invalid expression, or null when it is valid. */
export function cronError(expression: string): string | null {
  try {
    parseCron(expression)
    return null
  } catch (e) {
    return (e as Error).message
  }
}

function dayMatches(cron: Cron, d: Date): boolean {
  const dom = cron.days.has(d.getUTCDate())
  const dow = cron.weekdays.has(d.getUTCDay())
  if (cron.daysRestricted && cron.weekdaysRestricted) return dom || dow
  if (cron.daysRestricted) return dom
  if (cron.weekdaysRestricted) return dow
  return true
}

const SEARCH_DAYS = 4 * 366 + 1

function nextFireOf(cron: Cron, after: Date): Date | null {
  const start = new Date(after.getTime())
  start.setUTCSeconds(0, 0)
  start.setUTCMinutes(start.getUTCMinutes() + 1)
  const hours = [...cron.hours].sort((a, b) => a - b)
  const minutes = [...cron.minutes].sort((a, b) => a - b)
  const day = Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate())
  for (let offset = 0; offset < SEARCH_DAYS; offset += 1) {
    const current = new Date(day + offset * 86400000)
    if (!cron.months.has(current.getUTCMonth() + 1) || !dayMatches(cron, current)) continue
    for (const h of hours) {
      for (const m of minutes) {
        const candidate = new Date(current.getTime() + (h * 60 + m) * 60000)
        if (candidate.getTime() >= start.getTime()) return candidate
      }
    }
  }
  return null
}

/** The first time the schedule fires strictly after `after`, or null for an invalid expression. */
export function nextFire(expression: string, after: Date): Date | null {
  try {
    return nextFireOf(parseCron(expression), after)
  } catch {
    return null
  }
}

// ── presets in local time ─────────────────────────────────────────────────────

export const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
/** The "every N hours" choices: the divisors of 24, so every run is the same time each day. */
export const HOUR_STEPS = [1, 2, 3, 4, 6, 8, 12]

export type Preset =
  | { kind: 'daily'; time: string }
  | { kind: 'hours'; every: number; minute: number }
  | { kind: 'weekly'; day: number; time: string }
  | { kind: 'custom'; cron: string }

const two = (n: number) => String(n).padStart(2, '0')
const single = (set: Set<number>): number | null => (set.size === 1 ? [...set][0] : null)

/** `HH:MM` → minutes after midnight, or null. */
export function timeMinutes(time: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(time ?? '').trim())
  if (!m) return null
  const h = Number(m[1])
  const min = Number(m[2])
  return h <= 23 && min <= 59 ? h * 60 + min : null
}

const timeText = (minutes: number) => `${two(Math.floor(minutes / 60))}:${two(minutes % 60)}`

/** Shift minutes-after-midnight by `delta` minutes: the new time and how many days it moved. */
function shift(minutes: number, delta: number): { minutes: number; days: number } {
  const total = minutes + delta
  const days = Math.floor(total / 1440)
  return { minutes: total - days * 1440, days }
}

/**
 * Read a UTC cron as a preset in local time. `offsetMinutes` is local minus UTC. An expression
 * the presets cannot say exactly is `custom`.
 */
export function presetFromCron(expression: string, offsetMinutes: number): Preset {
  let cron: Cron
  try {
    cron = parseCron(expression)
  } catch {
    return { kind: 'custom', cron: String(expression ?? '') }
  }
  const [mf, hf, domf, monf, dowf] = cron.expression.split(' ')
  const custom: Preset = { kind: 'custom', cron: cron.expression }
  if (domf !== '*' || monf !== '*') return custom
  const minute = single(cron.minutes)
  if (minute === null || !DIGITS.test(mf)) return custom
  const step = /^\*\/(\d+)$/.exec(hf)
  if (step && dowf === '*') {
    const every = Number(step[1])
    // Every N hours with N a divisor of 24 is the same set of hours in every zone with a
    // whole-hour offset; with a half-hour zone the minute moves, so say it in local terms.
    if (!HOUR_STEPS.includes(every)) return custom
    const local = shift(minute, offsetMinutes % 60)
    return { kind: 'hours', every, minute: local.minutes % 60 }
  }
  const hour = single(cron.hours)
  if (hour === null || !DIGITS.test(hf)) return custom
  const local = shift(hour * 60 + minute, offsetMinutes)
  if (dowf === '*') return { kind: 'daily', time: timeText(local.minutes) }
  const dow = single(cron.weekdays)
  if (dow === null || !DIGITS.test(dowf)) return custom
  return { kind: 'weekly', day: (((dow + local.days) % 7) + 7) % 7, time: timeText(local.minutes) }
}

/** The UTC cron a preset says, or null when its inputs are not valid. */
export function cronFromPreset(preset: Preset, offsetMinutes: number): string | null {
  if (preset.kind === 'custom') return cronError(preset.cron) === null ? parseCron(preset.cron).expression : null
  if (preset.kind === 'hours') {
    if (!HOUR_STEPS.includes(preset.every)) return null
    if (!Number.isInteger(preset.minute) || preset.minute < 0 || preset.minute > 59) return null
    const utc = shift(preset.minute, -(offsetMinutes % 60))
    return `${utc.minutes % 60} */${preset.every} * * *`
  }
  const minutes = timeMinutes(preset.time)
  if (minutes === null) return null
  const utc = shift(minutes, -offsetMinutes)
  const hm = `${utc.minutes % 60} ${Math.floor(utc.minutes / 60)}`
  if (preset.kind === 'daily') return `${hm} * * *`
  if (!Number.isInteger(preset.day) || preset.day < 0 || preset.day > 6) return null
  return `${hm} * * ${(((preset.day + utc.days) % 7) + 7) % 7}`
}

/** A preset as words, in local time. */
export function presetWords(preset: Preset): string {
  if (preset.kind === 'daily') return `every day at ${preset.time}`
  if (preset.kind === 'weekly') return `every ${WEEKDAYS[preset.day]} at ${preset.time}`
  if (preset.kind === 'hours') {
    const at = preset.minute === 0 ? 'on the hour' : `at ${two(preset.minute)} past`
    return preset.every === 1 ? `every hour, ${at}` : `every ${preset.every} hours, ${at}`
  }
  return `on the schedule ${preset.cron} (UTC)`
}

/** The viewer's offset from UTC in minutes, east positive. */
export function localOffsetMinutes(at: Date = new Date()): number {
  return -at.getTimezoneOffset()
}

/** `UTC+2`, `UTC-5:30`, `UTC`. */
export function offsetLabel(offsetMinutes: number): string {
  if (offsetMinutes === 0) return 'UTC'
  const sign = offsetMinutes > 0 ? '+' : '-'
  const abs = Math.abs(offsetMinutes)
  const h = Math.floor(abs / 60)
  const m = abs % 60
  return `UTC${sign}${h}${m ? `:${two(m)}` : ''}`
}

// ── the form ──────────────────────────────────────────────────────────────────

export interface RefreshForm {
  pages: PagesMode
  schedule: string
  /** Minimum interval, in hours, as typed. */
  minIntervalHours: string
  consolidation: ConsolidationMode
  consolidationSchedule: string
}

type Live = Partial<typeof REFRESH_DEFAULTS> | null | undefined

const hoursText = (seconds: number) => {
  const h = seconds / 3600
  return Number.isInteger(h) ? String(h) : String(Math.round(h * 100) / 100)
}

/** The form for the saved `[memory.refresh]`, the defaults for anything it does not say. */
export function formFromLive(live: Live): RefreshForm {
  const v = { ...REFRESH_DEFAULTS, ...(live || {}) }
  return {
    pages: v.pages,
    schedule: v.schedule,
    minIntervalHours: hoursText(Number(v.min_interval_s)),
    consolidation: v.consolidation,
    consolidationSchedule: v.consolidation_schedule,
  }
}

/** The minimum interval in seconds, or null when the hours typed are not valid. */
export function intervalSecondsFromForm(form: RefreshForm): number | null {
  const text = String(form.minIntervalHours ?? '').trim()
  if (!/^\d+(\.\d+)?$/.test(text)) return null
  const seconds = Math.round(Number(text) * 3600)
  return seconds >= 0 && seconds <= MAX_INTERVAL_S ? seconds : null
}

export interface FormValidity {
  schedule: string | null
  minInterval: string | null
  consolidationSchedule: string | null
}

/**
 * What is wrong with each field, or null. A schedule the mode does not use is not checked: it is
 * saved as it was.
 */
export function formValidity(form: RefreshForm): FormValidity {
  return {
    schedule: form.pages === 'scheduled' ? cronError(form.schedule) : null,
    minInterval: intervalSecondsFromForm(form) === null ? 'a number of hours from 0 to 720' : null,
    consolidationSchedule: form.consolidation === 'scheduled' ? cronError(form.consolidationSchedule) : null,
  }
}

export const formValid = (form: RefreshForm) => Object.values(formValidity(form)).every((v) => v === null)

/**
 * The `PUT /api/settings` body for the keys that changed, or null when nothing did. A schedule
 * the mode leaves unused is sent only when it changed and is valid.
 */
export function refreshPutBody(form: RefreshForm, live: Live): { memory: { refresh: Record<string, unknown> } } | null {
  const before = { ...REFRESH_DEFAULTS, ...(live || {}) }
  const out: Record<string, unknown> = {}
  if (form.pages !== before.pages) out.pages = form.pages
  const schedule = cronError(form.schedule) === null ? parseCron(form.schedule).expression : null
  if (schedule !== null && schedule !== before.schedule) out.schedule = schedule
  const interval = intervalSecondsFromForm(form)
  if (interval !== null && interval !== Number(before.min_interval_s)) out.min_interval_s = interval
  if (form.consolidation !== before.consolidation) out.consolidation = form.consolidation
  const cs = cronError(form.consolidationSchedule) === null ? parseCron(form.consolidationSchedule).expression : null
  if (cs !== null && cs !== before.consolidation_schedule) out.consolidation_schedule = cs
  return Object.keys(out).length ? { memory: { refresh: out } } : null
}

/** What to tell the operator after a save, from the response's `_hal0.refresh_policy`. */
export function saveOutcome(response: unknown): { text: string; kind: 'ok' | 'warn' } {
  const rp = (response as { _hal0?: { refresh_policy?: { reconcile?: string } } } | null)?._hal0?.refresh_policy
  if (rp?.reconcile === 'unavailable') {
    return {
      text: 'Refresh settings saved. The memory engine is not running in hal0-api: run `hal0 memory refresh-policy apply` once it is',
      kind: 'warn',
    }
  }
  return { text: 'Refresh settings saved. hal0 is applying them to every page', kind: 'ok' }
}

/** `Mon 16 Nov, 04:00` in the viewer's locale and zone. */
export function formatLocal(date: Date | null, locale?: string): string {
  if (!date) return '—'
  return date.toLocaleString(locale, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}
