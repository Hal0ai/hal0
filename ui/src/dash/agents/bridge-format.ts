// hal0 dashboard — pure helpers for the agent-bridge surfaces (ADR-0043).
//
// bridge-clients-card.jsx and bridge-runs.jsx are window-globals files with no imports, so
// everything they need that is not a hook lives here and is published on
// `window.__hal0BridgeFmt` by agents-hook-bridge.ts (the way `deriveCardModel` is).

import { useEffect, useState } from 'react'
import { BRIDGE_TERMINAL_STATUSES, type BridgeRun } from '@/api/hooks/useBridge'

/**
 * A clock that re-renders its caller every `ms`, so "5m ago" and a live run's duration keep
 * moving. Both bridge .jsx files call it as `window.__hal0BridgeFmt.useNow(ms)`.
 */
export function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(t)
  }, [ms])
  return now
}

/** The client-name rule the API enforces (src/hal0/bridge/keys.py NAME_RE). */
export const BRIDGE_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/

/**
 * The reason a name cannot be a bridge client name, or null when it can. The wording is the
 * API's `bridge.invalid_name` message, so the page and the server say the same thing. (The
 * server also reserves a few names — `admin`, `hermes`, `pi`, … — and says so itself.)
 */
export function bridgeNameError(name: string): string | null {
  if (BRIDGE_NAME_RE.test(name)) return null
  return (
    `invalid bridge client name '${name}': use 1-32 characters from a-z, 0-9, '_' ` +
    `and '-', starting with a letter or digit`
  )
}

/** "just now", "42s ago", "5m ago", "3h ago", "2d ago". Empty for a missing timestamp. */
export function fmtAgo(epochSec: number | null | undefined, nowMs: number): string {
  if (epochSec == null) return ''
  const s = Math.max(0, Math.floor(nowMs / 1000 - epochSec))
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

/** "42s", "3m 12s", "1h 5m". */
export function fmtSpan(totalSec: number): string {
  const s = Math.max(0, Math.round(totalSec))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

/** "2026-10-28 14:03" in the browser's zone, for tooltips. Empty for a missing timestamp. */
export function fmtStamp(epochSec: number | null | undefined): string {
  if (epochSec == null) return ''
  const d = new Date(epochSec * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export type BridgeRunGroup = 'live' | 'waiting' | 'ok' | 'bad'

const LIVE = new Set(['queued', 'running', 'stopping'])

/**
 * The colour group of a run status. `live` is in flight; `waiting` needs a person (approval or
 * clarification); `ok` is completed; `bad` is every other terminal status: failed, cancelled,
 * interrupted, and `unknown` (Hermes no longer knows the run, which the backend treats as a
 * failure). A status this build has never heard of reads as `waiting`, since it wants a look.
 */
export function bridgeRunGroup(status: string): BridgeRunGroup {
  if (LIVE.has(status)) return 'live'
  if (status === 'completed') return 'ok'
  if (BRIDGE_TERMINAL_STATUSES.has(status)) return 'bad'
  return 'waiting'
}

/** The `chip` colour class for each group. */
export const BRIDGE_GROUP_CHIP: Record<BridgeRunGroup, string> = {
  live: 'info',
  waiting: 'warn',
  ok: 'ok',
  bad: 'err',
}

export const BRIDGE_GROUP_LABEL: Record<BridgeRunGroup, string> = {
  live: 'Live',
  waiting: 'Waiting',
  ok: 'Completed',
  bad: 'Failed or stopped',
}

/** `waiting_for_approval` → "waiting for approval". */
export function bridgeStatusLabel(status: string): string {
  return status.replace(/_/g, ' ')
}

/** The first 12 characters of a run id; the full id goes in the tooltip. */
export function bridgeShortRunId(runId: string): string {
  return runId.slice(0, 12)
}

/**
 * Seconds a run has taken: updated − created once it is terminal (nothing changes after), else
 * now − created.
 */
export function bridgeRunSeconds(run: Pick<BridgeRun, 'status' | 'created_at' | 'updated_at'>, nowMs: number): number {
  const end = BRIDGE_TERMINAL_STATUSES.has(run.status) ? run.updated_at : nowMs / 1000
  return Math.max(0, end - run.created_at)
}

export const BRIDGE_FMT = {
  useNow,
  bridgeNameError,
  fmtAgo,
  fmtSpan,
  fmtStamp,
  bridgeRunGroup,
  bridgeStatusLabel,
  bridgeShortRunId,
  bridgeRunSeconds,
  BRIDGE_GROUP_CHIP,
  BRIDGE_GROUP_LABEL,
}
