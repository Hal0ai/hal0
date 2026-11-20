// hal0 Overview v2 — pure model for the Agents panel: the Hermes row, the Pi row, delegated runs
// and the board's in-progress count. No React; the one window read is the card model the Agents
// page publishes. The panel keeps its own four words (serving / ready / down / unknown), so the
// card's phase is mapped, not copied: `serving`, `ready`, `working` and `broken` map straight
// across, and a `degraded` card reads `down` when any of its reasons is an outage (see
// OUTAGE_REASONS) and `ready` otherwise (#3808, ADR-0044).

import type { AgentOverview, AgentRecord } from '@/api/hooks/useAgents'
import type { BridgeRun } from '@/api/hooks/useBridge'
import { downProfiles } from '@/dash/agents/agent-phase'
import { pctOf, relTime } from './overview-pure'

const DASH = '—'

export type HermesState = 'serving' | 'ready' | 'down' | 'unknown'
export type RunDot = 'serving' | 'ready' | 'err' | ''

export interface HermesRow {
  state: HermesState
  version: string | null
  pyLine: string | null
  /** Profiles the API server serves. */
  profiles: string[]
  /** Configured profiles whose server is not answering (#3157). */
  down: string[]
  ctx: { used: number; max: number; pct: number } | null
  persona: string | null
  approvals: string | null
  channels: string[]
}

export interface PiRow {
  label: string
  version: string | null
  adapter: string | null
}

export interface RunRow {
  key: string
  who: string
  what: string
  time: string
  dot: RunDot
}

/** The slice of a primary slot the Hermes row reads. */
export interface SlotLike {
  state?: string | null
  metrics?: { ctx?: number | null; toks?: number | null } | null
  ctx_max?: number | null
  ctx_effective?: number | null
}

/** The slice of the personas route the Hermes row reads. */
export interface PersonasLike {
  active?: string | null
  personas?: { id: string; display_name?: string | null }[]
}

type RecordLike = Pick<AgentRecord, 'name' | 'status' | 'unit_active'>
type Blob = Record<string, unknown>

function blob(v: unknown): Blob {
  return v && typeof v === 'object' ? (v as Blob) : {}
}

function text(v: unknown): string | null {
  if (typeof v === 'string') return v.trim() === '' ? null : v
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  return null
}

function strings(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

function shortRef(ref: unknown): string | null {
  const s = text(ref)
  if (!s) return null
  return /^[0-9a-f]{12,}$/i.test(s) ? s.slice(0, 7) : s
}

type CardPhase = string
type Derive = (input: Record<string, unknown>) => {
  phase?: CardPhase
  reasons?: { reason?: string }[]
}

function publishedDerive(): Derive | null {
  if (typeof window === 'undefined') return null
  const fn = (window as unknown as { __hal0DeriveCardModel?: Derive }).__hal0DeriveCardModel
  return typeof fn === 'function' ? fn : null
}

/** Degraded reasons (src/hal0/agents/overview.py `degraded_reasons`) that mean the agent is not
 *  serving: the gateway or API server is unreachable, or provisioning failed. The rest
 *  (`provision_warn`, `doctor_drift`, `approvals_drift`) are soft, and so is any reason added
 *  later: an unknown reason reads `ready` rather than a false `down`. */
const OUTAGE_REASONS: ReadonlySet<string> = new Set(['gateway_down', 'api_unhealthy', 'provision_failed'])

function phaseToState(phase: CardPhase | undefined, reasons?: { reason?: string }[]): HermesState {
  switch (phase) {
    case 'serving':
      return 'serving'
    case 'degraded':
      return (reasons ?? []).some((r) => OUTAGE_REASONS.has(r?.reason ?? '')) ? 'down' : 'ready'
    case 'ready':
    case 'working':
      return 'ready'
    case 'down':
    case 'broken':
      return 'down'
    default:
      return 'unknown'
  }
}

function slotServing(slot: SlotLike | null): boolean {
  return slot?.state === 'serving' || (slot?.metrics?.toks ?? 0) > 0
}

function stateOf(
  ov: AgentOverview | null,
  slot: SlotLike | null,
  record: RecordLike | null,
): HermesState {
  const derive = publishedDerive()
  if (derive) {
    const model = derive({ agent: 'hermes', record, overview: ov, slot, job: null, caps: null, urls: null })
    return phaseToState(model?.phase, model?.reasons)
  }
  const unit = ov?.units?.agent
  const active = unit != null ? unit === 'active' : record ? record.unit_active === true : null
  if (active === null) return 'unknown'
  if (!active) return 'down'
  return slotServing(slot) ? 'serving' : 'ready'
}

/** The Hermes row: state word, version, served and down profiles, ctx fill of the slot it orchestrates, channels. */
export function hermesRow(
  ov: AgentOverview | null,
  personas: PersonasLike | null,
  slot: SlotLike | null,
  record: RecordLike | null = null,
): HermesRow {
  const live = blob(blob(ov?.version).live)
  const minor = text(live.python_minor)
  // 0 means the counter is not reporting (as in the slot pane), so it reads `—`, never 0%.
  const rawUsed = slot?.metrics?.ctx
  const used = typeof rawUsed === 'number' && rawUsed > 0 ? rawUsed : undefined
  const max = slot?.ctx_effective ?? slot?.ctx_max
  const pct = typeof used === 'number' && typeof max === 'number' ? pctOf(used, max) : null
  const active = personas?.active ?? null
  const activeRow = active ? personas?.personas?.find((p) => p.id === active) : undefined
  return {
    state: stateOf(ov, slot, record),
    version: text(live.describe) ?? shortRef(live.ref),
    pyLine: minor ? `py ${minor}` : null,
    profiles: strings(ov?.api_server?.profiles),
    down: downProfiles(ov),
    ctx:
      pct !== null && typeof used === 'number' && typeof max === 'number'
        ? { used, max, pct: Math.round(pct) }
        : null,
    persona: activeRow?.display_name || active,
    approvals: text(blob(ov?.approvals).mode),
    channels: strings(ov?.platforms),
  }
}

/** The Pi row: a state word and its installed version and adapter. Pi has no unit, so no liveness. */
export function piRow(ov: AgentOverview | null): PiRow {
  if (!ov) return { label: 'unknown', version: null, adapter: null }
  const installed = blob(blob(ov.version).installed)
  const pi = text(installed.pi)
  const adapter = text(installed.adapter)
  let label = 'installed'
  if (ov.installed === false) label = 'not installed'
  else if (ov.phase === 'broken') label = 'broken'
  return {
    label,
    version: pi ? (pi.startsWith('v') ? pi : `v${pi}`) : null,
    adapter: adapter ? `adapter ${adapter}` : null,
  }
}

function elapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

const LIVE_WAIT: Record<string, string> = {
  waiting_for_approval: 'waiting on approval',
  waiting_for_clarification: 'waiting on input',
  stopping: 'stopping',
}

/** One delegated run as a list row: who asked whom, what for, and a status line with its dot. */
export function runRow(r: BridgeRun, nowS: number): RunRow {
  const status = String(r.status)
  const base = {
    key: r.run_id,
    who: `${r.client} → ${r.agent}`,
    what: (r.input_preview ?? '').split('\n')[0].trim() || DASH,
  }
  const rel = relTime(r.updated_at, nowS)
  const live = !['completed', 'done', 'failed', 'cancelled', 'interrupted', 'unknown'].includes(status)
  if (LIVE_WAIT[status]) return { ...base, time: LIVE_WAIT[status], dot: 'ready' }
  if (live && r.pending != null && r.pending !== false) {
    return { ...base, time: 'waiting on approval', dot: 'ready' }
  }
  switch (status) {
    case 'running':
      return { ...base, time: `running · ${elapsed(nowS - r.created_at)}`, dot: 'serving' }
    case 'queued':
      return { ...base, time: 'queued', dot: 'ready' }
    case 'completed':
    case 'done':
      return { ...base, time: `done · ${rel}`, dot: '' }
    case 'failed':
      return { ...base, time: `failed · ${rel}`, dot: 'err' }
    case 'cancelled':
      return { ...base, time: `cancelled · ${rel}`, dot: '' }
    case 'interrupted':
      return { ...base, time: `interrupted · ${rel}`, dot: 'err' }
    case 'unknown':
      return { ...base, time: `lost · ${rel}`, dot: 'err' }
    default:
      return { ...base, time: status, dot: '' }
  }
}

/** Tasks the board has in flight: the in_progress and running counts summed; null when stats are unread. */
export function inProgress(stats: { by_status?: Record<string, number> } | undefined): number | null {
  const by = stats?.by_status
  if (!by) return null
  return (by.in_progress ?? 0) + (by.running ?? 0)
}
