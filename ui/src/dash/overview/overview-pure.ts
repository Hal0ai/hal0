// hal0 Overview v2 — pure formatting helpers shared by the overview sections.
// No React, no window globals: unit-testable in isolation.

const DASH = '—'

/** MB to GB for every disk and memory figure on the page (binary, matching the Hardware card). */
export const MB_PER_GB = 1024

/** Free disk in whole GB, floored so free space is never overstated: 124429 MB is 121 GB. */
export function freeGb(mb: number): number {
  return Math.floor(mb / MB_PER_GB)
}

/** Token / request counts: 312, 1.9k (under 10k), 19k (10k and up). */
export function fmtK(n: number): string {
  if (!Number.isFinite(n)) return DASH
  const abs = Math.abs(n)
  if (abs >= 10_000) return `${Math.round(n / 1000)}k`
  if (abs >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(Math.round(n))
}

/** Gigabytes with one decimal and the unit; a dash when the figure is unread. */
export function fmtGb(gb: number | null): string {
  if (typeof gb !== 'number' || !Number.isFinite(gb)) return DASH
  return `${gb.toFixed(1)} GB`
}

/** used / total as a 0-100 percentage; null when either side is missing or the total is not positive. */
export function pctOf(used: number | null, total: number | null): number | null {
  if (typeof used !== 'number' || typeof total !== 'number') return null
  if (!Number.isFinite(used) || !Number.isFinite(total) || total <= 0) return null
  return Math.min(100, Math.max(0, (used / total) * 100))
}

/** Age of an epoch-seconds timestamp: "just now" under a minute, then floored minutes, then floored hours. */
export function relTime(epochS: number, nowS: number): string {
  const delta = Math.max(0, nowS - epochS)
  if (delta < 60) return 'just now'
  if (delta < 3600) return `${Math.floor(delta / 60)} min ago`
  return `${Math.floor(delta / 3600)} h ago`
}
