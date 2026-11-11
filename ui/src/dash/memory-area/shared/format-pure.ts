// The Memory area's formats, in one place, so no raw engine value reaches the page:
//
//   counts      52,955         thousands with a comma
//   durations   6 h, 12 min    a number, a space, a short unit (never "6 hours")
//   relative    6 h ago        the same units, "just now" under a minute
//   dates       5 Nov 2026     day, short month, full year, every time
//
// Every function answers `null` for a value that is missing or cannot be read. A caller then
// shows the "not reported" mark (StatStrip does) and never prints `0`, `NaN` or an ISO string.

/** A point in time as the API sends it (an ISO string), or as code holds it. */
export type TimeInput = string | number | Date | null | undefined

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const MINUTE = 60
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** Past this age a relative time stops helping, and the date is shown. */
const RELATIVE_LIMIT_S = 30 * DAY

function toDate(value: TimeInput): Date | null {
  if (value === null || value === undefined || value === '') return null
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function finite(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** `52955` → `52,955`. Whole numbers only: a count is rounded, never shown with decimals. */
export function formatCount(value: number | null | undefined): string | null {
  if (!finite(value)) return null
  const rounded = Math.round(value)
  const digits = String(Math.abs(rounded)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return rounded < 0 ? `-${digits}` : digits
}

/**
 * A length of time in seconds → `45 s`, `12 min`, `6 h`, `3 d`.
 *
 * One unit, rounded down: an operation that has waited 5 h 59 min has waited `5 h`, not `6 h`.
 * A negative length is not a length and answers `null`.
 */
export function formatDuration(seconds: number | null | undefined): string | null {
  if (!finite(seconds) || seconds < 0) return null
  if (seconds < MINUTE) return `${Math.floor(seconds)} s`
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)} min`
  if (seconds < 2 * DAY) return `${Math.floor(seconds / HOUR)} h`
  return `${formatCount(Math.floor(seconds / DAY))} d`
}

/** Calendar parts of `date` in `timeZone` (the viewer's zone when it is not given). */
function dateParts(date: Date, timeZone?: string) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  const read = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  return {
    day: Number(read('day')),
    month: MONTHS[Number(read('month')) - 1] ?? '',
    year: read('year'),
    hour: read('hour'),
    minute: read('minute'),
  }
}

/** `2026-11-05T09:14:00Z` → `5 Nov 2026`, in the viewer's time zone unless one is named. */
export function formatDate(value: TimeInput, timeZone?: string): string | null {
  const date = toDate(value)
  if (!date) return null
  const p = dateParts(date, timeZone)
  return `${p.day} ${p.month} ${p.year}`
}

/** `2026-11-05T09:14:00Z` → `5 Nov 2026, 09:14` (24-hour clock). */
export function formatDateTime(value: TimeInput, timeZone?: string): string | null {
  const date = toDate(value)
  if (!date) return null
  const p = dateParts(date, timeZone)
  return `${p.day} ${p.month} ${p.year}, ${p.hour}:${p.minute}`
}

/**
 * How long ago `value` was, read against `now`: `just now`, `12 min ago`, `6 h ago`, `3 d ago`.
 * A time ahead of `now` reads `in 12 min`. Past 30 days either way the date is shown instead.
 */
export function formatRelativeTime(
  value: TimeInput,
  now: TimeInput = Date.now(),
  timeZone?: string,
): string | null {
  const date = toDate(value)
  const base = toDate(now)
  if (!date || !base) return null
  const delta = (base.getTime() - date.getTime()) / 1000
  const age = Math.abs(delta)
  if (age >= RELATIVE_LIMIT_S) return formatDate(date, timeZone)
  if (age < MINUTE) return 'just now'
  const length = formatDuration(age)
  return delta >= 0 ? `${length} ago` : `in ${length}`
}

/** Seconds from `value` to `now`, for a caller that needs the age itself (an "oldest" figure). */
export function ageSeconds(value: TimeInput, now: TimeInput = Date.now()): number | null {
  const date = toDate(value)
  const base = toDate(now)
  if (!date || !base) return null
  return Math.max(0, (base.getTime() - date.getTime()) / 1000)
}
