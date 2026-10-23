// hal0 dashboard — pure card-model derivation for the Agents overview cards.
//
// One function, `deriveCardModel`, turns what the dashboard already holds (the
// GET /api/agents record, the GET /api/agents/{name}/overview body, the backing
// slot, the running job, the capabilities bag, the config URLs) into the
// render-ready model a card draws from. It has no React, no hooks and no
// window access, so it is unit-testable and the .jsx cards only paint it.
// agents-hook-bridge.ts publishes it as `window.__hal0DeriveCardModel`.
//
// The curated identity blocks (ability and skill art, copy) stay in the JSX;
// this module only decides `unlocked` / `lit` from live facts.

import type { AgentDegradedRow, AgentOverview, AgentRecord } from '@/api/hooks/useAgents'
import type { CapabilitySelection } from '@/api/hooks/useCapabilities'
import type { ConfigUrls } from '@/api/hooks/useConfigUrls'
import type { UpdateJob } from '@/api/hooks/useUpdates'

// ── Types ──────────────────────────────────────────────────────────────

/** The overview's phases plus `roadmap` (an agent hal0 does not bundle yet). */
export type CardPhase =
  | 'working'
  | 'broken'
  | 'down'
  | 'degraded'
  | 'serving'
  | 'ready'
  | 'installed'
  | 'not_installed'
  | 'unknown'
  | 'roadmap'

/** StatusDot classes (`serving`, `stale`, `error`, `offline`, `soon`) plus `working`. */
export type StatusCls = 'serving' | 'stale' | 'error' | 'offline' | 'soon' | 'working'

/** A row of the overview's `degraded` list, carried through unchanged. */
export type DegradedReason = AgentDegradedRow

/** The AgentRecord fields the model reads; `GET /api/agents` rows satisfy it. */
export type RecordLike = Pick<AgentRecord, 'name' | 'status' | 'unit_active'>

/** The slice of a slot the model reads (throughput decides `serving`). */
export interface SlotLike {
  state?: string | null
  metrics?: { toks?: number | null } | null
}

/** A queued/running/finished job: `useUpdateJob`'s UpdateJob, or the overview's `job`. */
export type JobLike = Pick<UpdateJob, 'id'> &
  Partial<Omit<UpdateJob, 'id' | 'state'>> & { state: string; kind?: string; phase?: string }

/** `GET /api/capabilities` as far as the model reads it: `selections.<slot>.<child>`. */
export interface CapabilitiesLike {
  selections?: Record<
    string,
    Record<string, Pick<Partial<CapabilitySelection>, 'enabled'> | null | undefined> | undefined
  > | null
}

/** The most recent tool call, when the caller has one (the overview does not carry it). */
export interface LastTool {
  name: string
  at?: number | string | null
}

export interface CardModelInput {
  /** The agent this card is for. Defaults to the overview's or record's name, else `hermes`. */
  agent?: string
  record: RecordLike | null
  overview: AgentOverview | null
  slot: SlotLike | null
  job: JobLike | null
  caps: CapabilitiesLike | null
  urls: ConfigUrls | null
  lastTool?: LastTool | null
  /**
   * The active persona as the personas route reports it now. Preferred over
   * `provision.persona`, which provision.json records only at a provision or a
   * repair and so goes stale once a persona is activated.
   */
  persona?: string | null
}

export interface LoreAbility {
  name: string
  unlocked: boolean
  /** One line saying what is missing; `null` once unlocked. */
  note: string | null
  /** The check could not run (`ok: null`); the card says "unverified", not "locked". */
  unknown?: boolean
}

export interface LoreSkill {
  label: string
  lit: boolean
  /** A short reason while dim (`· no slot` for Hermes); `null` once lit. */
  note: string | null
  /** The check could not run (`ok: null`); the note says so instead of "missing". */
  unknown?: boolean
}

export interface CardModel {
  phase: CardPhase
  statusCls: StatusCls
  statusLabel: string
  /** Reasons shown on the amber `degraded` badge; 0 in every other phase. */
  degradedCount: number
  updateAvailable: boolean
  version: { label: string; sub: string; pinLabel: string }
  /** Every degraded reason, as the overview sent them. */
  reasons: DegradedReason[]
  /** The first three, for the front face. */
  reasonsTop: DegradedReason[]
  chips: { persona: string | null; approvals: string | null; terminal: string | null }
  reach: {
    /** Configured chat platforms; `api_server` is the `api` chip, not a platform chip. */
    platforms: { id: string; lit: boolean }[]
    api: { lit: boolean; profiles: string[] }
    desktop: string | null
  }
  lastTool: LastTool | null
  lore: { abilities: LoreAbility[]; skills: LoreSkill[] }
  lifecycle: { primary: 'upgrade' | 'repair'; previous: string | null }
  /** Copy-command strings; a Hermes card has buttons, so its upgrade/uninstall are null. */
  commands: { install: string; upgrade: string | null; uninstall: string | null }
  job: JobLike | null
  /** The gateway unit is deliberately disabled: a down gateway is then not a fault. */
  gatewayOff: boolean
  /** provision.json predates the live build, so its facts may be out of date. */
  provisionStale: boolean
  generatedAt: string | null
}

// ── Constants ──────────────────────────────────────────────────────────

const BUNDLED = new Set(['hermes', 'pi'])
const NO_VALUE = '—'
const NO_SLOT = '· no slot'
const TOP_REASONS = 3

const COMMANDS: Record<string, CardModel['commands']> = {
  hermes: { install: 'sudo hal0 agent install hermes', upgrade: null, uninstall: null },
  pi: {
    install: 'sudo hal0 agent install pi',
    upgrade: 'sudo hal0 agent upgrade pi',
    uninstall: 'sudo hal0 agent uninstall pi',
  },
}
const NO_COMMANDS: CardModel['commands'] = { install: '', upgrade: null, uninstall: null }

const STATUS: Record<CardPhase, { cls: StatusCls; label: string }> = {
  serving: { cls: 'serving', label: 'serving' },
  ready: { cls: 'stale', label: 'ready' },
  // Ruling (PR 5 review, M5): a healthy Pi reads like Hermes `ready`, the same
  // amber class and dot, whether or not it is behind its pin; the `↑` badge
  // carries "behind". Its word stays `installed`.
  installed: { cls: 'stale', label: 'installed' },
  degraded: { cls: 'stale', label: 'degraded' },
  down: { cls: 'error', label: 'down' },
  broken: { cls: 'error', label: 'broken' },
  working: { cls: 'working', label: 'working' },
  not_installed: { cls: 'offline', label: 'not installed' },
  unknown: { cls: 'offline', label: 'unknown' },
  roadmap: { cls: 'soon', label: 'on the roadmap' },
}

// ── Small readers (the overview's blobs are loosely typed) ─────────────

type Blob = Record<string, unknown>

function blob(v: unknown): Blob {
  return v && typeof v === 'object' ? (v as Blob) : {}
}

/** A non-empty string, or a finite number as text; anything else is absent. */
function text(v: unknown): string | undefined {
  if (typeof v === 'string') return v.trim() === '' ? undefined : v
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  return undefined
}

function strings(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

/** A full commit id shortens to seven characters; a tag or short ref stays as sent. */
function shortRef(ref: unknown): string | undefined {
  const s = text(ref)
  if (!s) return undefined
  return /^[0-9a-f]{12,}$/i.test(s) ? s.slice(0, 7) : s
}

function vPrefix(version: string): string {
  return version.startsWith('v') ? version : `v${version}`
}

function jobActive(job: { state?: string } | null | undefined): boolean {
  return job?.state === 'queued' || job?.state === 'running'
}

// ── Phase ──────────────────────────────────────────────────────────────

/** Today's `_derive` rule, from the AgentRecord alone. */
function phaseFromRecord(record: RecordLike | null, isPi: boolean): CardPhase {
  if (!record) return 'not_installed'
  const status = String(record.status ?? '').toLowerCase()
  if (status === 'broken') return 'broken'
  if (/error|fail|crash|down/.test(status)) return isPi ? 'broken' : 'down'
  // Pi is a CLI tool: no unit, so no liveness to read.
  if (isPi) return status === 'installed' ? 'installed' : 'unknown'
  if (record.unit_active === false) return 'down'
  // Unobservable is never healthy: null / absent liveness is unknown.
  if (record.unit_active !== true) return 'unknown'
  return 'ready'
}

function derivePhase(
  input: CardModelInput,
  agent: string,
  isPi: boolean,
): CardPhase {
  const { record, overview, slot } = input
  if (!BUNDLED.has(agent)) return 'roadmap'
  if (jobActive(input.job) || jobActive(overview?.job)) return 'working'
  // The overview is the fresher word: an uninstall can leave the agents-list
  // record behind for one poll.
  if (overview?.installed === false) return 'not_installed'

  let phase: CardPhase = overview?.phase ?? phaseFromRecord(record, isPi)
  // Pi never becomes ready or serving, whatever a stray signal says.
  if (isPi && (phase === 'ready' || phase === 'serving')) phase = 'installed'
  if (phase === 'ready' && (slot?.state === 'serving' || (slot?.metrics?.toks ?? 0) > 0)) {
    phase = 'serving'
  }
  return phase
}

// ── Version ────────────────────────────────────────────────────────────

function deriveVersion(overview: AgentOverview | null, isPi: boolean): CardModel['version'] {
  const v = blob(overview?.version)
  const pin = blob(v.pin ?? overview?.pin)

  if (isPi) {
    const installed = blob(v.installed)
    const pi = text(installed.pi)
    const adapter = text(installed.adapter)
    const pinParts = [
      text(pin.pi) && vPrefix(text(pin.pi) as string),
      text(pin.adapter) && `adapter ${text(pin.adapter)}`,
    ].filter(Boolean)
    return {
      label: pi ? vPrefix(pi) : NO_VALUE,
      sub: adapter ? `adapter ${adapter}` : NO_VALUE,
      pinLabel: pinParts.length ? pinParts.join(' · ') : NO_VALUE,
    }
  }

  const live = blob(v.live)
  const ref = shortRef(live.ref)
  const minor = text(live.python_minor)
  const sub = [minor && `py ${minor}`, ref].filter(Boolean).join(' · ')
  return {
    label: text(live.describe) ?? ref ?? NO_VALUE,
    sub: sub || NO_VALUE,
    pinLabel: text(pin.describe) ?? shortRef(pin.ref) ?? NO_VALUE,
  }
}

function previousRelease(overview: AgentOverview | null): string | null {
  const previous = blob(overview?.version?.previous)
  return text(previous.describe) ?? shortRef(previous.ref) ?? null
}

// ── Lore ───────────────────────────────────────────────────────────────

/**
 * Whether `selections.<slot>.<child>` is enabled. Only the named child counts:
 * the `embed` slot also holds `rerank`, and a reranker alone is not embeddings.
 */
function capabilityEnabled(caps: CapabilitiesLike | null, slot: string, child: string): boolean {
  return caps?.selections?.[slot]?.[child]?.enabled === true
}

function ability(name: string, unlocked: boolean, locked: string): LoreAbility {
  return { name, unlocked, note: unlocked ? null : locked }
}

function unknownAbility(name: string, note: string): LoreAbility {
  return { name, unlocked: false, note, unknown: true }
}

function unknownSkill(label: string, note: string): LoreSkill {
  return { label, lit: false, note, unknown: true }
}

function skill(label: string, lit: boolean, dim: string = NO_SLOT): LoreSkill {
  return { label, lit, note: lit ? null : dim }
}

function hermesLore(
  overview: AgentOverview | null,
  caps: CapabilitiesLike | null,
): CardModel['lore'] {
  const gatewayActive = overview?.units?.gateway === 'active'
  const chatPlatforms = strings(overview?.platforms).filter((p) => p !== 'api_server')
  const provision = overview?.provision
  const voiceWired = (provision?.phases ?? []).some(
    (p) => p.name === 'voice_wire' && p.status === 'ok',
  )
  const deepRun = provision?.terminal_tool === 'on' && overview?.approvals?.mode === 'smart'

  return {
    abilities: [
      // api_server runs on every box and is not a chat surface.
      ability(
        'Ghost Relay',
        gatewayActive && chatPlatforms.length > 0,
        gatewayActive ? 'no messaging platform is configured' : 'the gateway is not running',
      ),
      ability('Engram', blob(overview?.memory).available === true, 'memory is not available'),
      ability(
        'Deep Run',
        deepRun,
        provision?.terminal_tool === 'on'
          ? 'approvals mode is not smart'
          : 'the terminal tool is not on',
      ),
    ],
    skills: [
      skill('voice · tts', voiceWired),
      skill('speech · stt', voiceWired),
      skill('image-gen', capabilityEnabled(caps, 'img', 'img')),
      skill('memory', !!text(provision?.memory_provider)),
      skill('embeddings', capabilityEnabled(caps, 'embed', 'embed')),
    ],
  }
}

function piLore(overview: AgentOverview | null, phase: CardPhase): CardModel['lore'] {
  const checks = overview?.profile?.checks ?? []
  const checkOk = (id: string) => checks.some((c) => c.id === id && c.ok === true)
  // `ok: null` means the daemon could not read Pi's home: unknown, not absent.
  const checkUnknown = (id: string) => checks.some((c) => c.id === id && c.ok == null)
  const home = text(overview?.profile?.home)
  const cantVerify = `can't verify: home ${home ? home + ' ' : ''}isn't readable by hal0`
  const memory = blob(overview?.memory)
  const installed = phase === 'installed'
  const dualMemory = memory.mcp_entry === true && memory.hindsight_config === true
  // What stops Delegate and the install-bound chips: the status word, and for
  // a broken install the first check that fails, so a broken Pi never reads
  // "not installed".
  const word = STATUS[phase].label
  const failing = checks.find((c) => c.ok === false)
  const notReady =
    phase === 'broken' && failing ? `pi is broken: the ${failing.id} check fails` : `pi is ${word}`
  return {
    abilities: [
      checkUnknown('provider_ext') && !checkOk('provider_ext')
        ? unknownAbility('Slot Sync', cantVerify)
        : ability('Slot Sync', checkOk('provider_ext'), 'the hal0 provider extension is not present'),
      !dualMemory && (checkUnknown('mcp_entry') || checkUnknown('hindsight_config'))
        ? unknownAbility('Dual Memory', cantVerify)
        : ability(
            'Dual Memory',
            dualMemory,
            'the memory MCP entry and the Hindsight config are not both wired',
          ),
      ability('Delegate', installed, notReady),
    ],
    skills: [
      skill('repo-aware', installed, `· ${word}`),
      skill('shared memory', dualMemory, '· not wired'),
      skill('delegation', installed, `· ${word}`),
      skill('cli-native', checkOk('binary'), '· not on PATH'),
      checkUnknown('theme') && !checkOk('theme')
        ? unknownSkill('theme', '· unverified')
        : skill('theme', checkOk('theme'), '· not deployed'),
    ],
  }
}

// ── Commands and reach ─────────────────────────────────────────────────

function deriveCommands(agent: string, overview: AgentOverview | null): CardModel['commands'] {
  const base = COMMANDS[agent] ?? NO_COMMANDS
  const sent = blob(overview?.commands)
  return {
    install: text(sent.install) ?? base.install,
    upgrade: text(sent.upgrade) ?? base.upgrade,
    uninstall: text(sent.uninstall) ?? base.uninstall,
  }
}

/** The Hermes desktop link; only the Hermes card has one. */
function deriveDesktop(
  agent: string,
  overview: AgentOverview | null,
  urls: ConfigUrls | null,
): string | null {
  if (agent !== 'hermes') return null
  return (
    text(overview?.links?.desktop) ?? (urls?.hermes_enabled ? text(urls.hermes) : undefined) ?? null
  )
}

// ── Entry point ────────────────────────────────────────────────────────

export function deriveCardModel(input: CardModelInput): CardModel {
  const { record, overview, caps, urls } = input
  const agent = input.agent ?? overview?.name ?? record?.name ?? 'hermes'
  const isPi = agent === 'pi'

  const phase = derivePhase(input, agent, isPi)
  const reasons = overview?.degraded ?? []
  const updateAvailable = overview?.version?.update_available === true
  const gatewayActive = overview?.units?.gateway === 'active'
  const provision = overview?.provision

  return {
    phase,
    statusCls: STATUS[phase].cls,
    statusLabel: STATUS[phase].label,
    degradedCount: phase === 'degraded' ? reasons.length : 0,
    updateAvailable,
    version: deriveVersion(overview, isPi),
    reasons,
    reasonsTop: reasons.slice(0, TOP_REASONS),
    chips: {
      persona: input.persona || provision?.persona || null,
      approvals: overview?.approvals?.mode ?? null,
      terminal: provision?.terminal_tool ?? null,
    },
    reach: {
      platforms: strings(overview?.platforms)
        .filter((id) => id !== 'api_server')
        .map((id) => ({ id, lit: gatewayActive })),
      api: {
        lit: overview?.api_server?.healthy === true,
        profiles: strings(overview?.api_server?.profiles),
      },
      desktop: deriveDesktop(agent, overview, urls),
    },
    lastTool: input.lastTool ?? null,
    lore:
      phase === 'roadmap'
        ? { abilities: [], skills: [] }
        : isPi
          ? piLore(overview, phase)
          : hermesLore(overview, caps),
    lifecycle: {
      primary: updateAvailable ? 'upgrade' : 'repair',
      previous: previousRelease(overview),
    },
    commands: deriveCommands(agent, overview),
    job: input.job ?? overview?.job ?? null,
    gatewayOff: overview?.units?.gateway_enabled === false,
    provisionStale: provision?.stale === true,
    generatedAt: overview?.generated_at ?? null,
  }
}
