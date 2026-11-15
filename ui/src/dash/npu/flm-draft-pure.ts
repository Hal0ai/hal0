// Pure helpers for the FLM lane (Slots → NPU → Lanes): one card per role (chat, speech-to-text,
// embeddings) served by ONE FLM process. A role toggle or a chat model pick is a one-key draft,
// applied at once: ONE config write, then ONE restart (or a start / a stop) of that process.
// Reads go through npu-modality.js so the absent-key defaults stay in one place.
import { npuApplyBody, npuModalityOn } from '../npu-modality.js'

export type FlmCap = 'chat' | 'asr' | 'embed'
export interface SavedFlm {
  chat: boolean
  asr: boolean
  embed: boolean
  chatModel: string | null
}
export type FlmDraft = Partial<{ chat: boolean; asr: boolean; embed: boolean; chatModel: string }>

type AnchorCfg = { npu?: Record<string, unknown> | null; model?: { default?: unknown } | null } | null | undefined
type AnchorSlot = { npu?: Record<string, unknown> | null; modelDefault?: unknown; model_id?: unknown; model?: unknown } | null | undefined

/** The anchor's saved FLM settings. Model: `[model].default` first, then the slot's live ids
 *  (the `-FLM` suffix of a pre-#1629 id is dropped), as the occupancy card has always done. */
export function savedFlm(anchorCfg: AnchorCfg, anchorSlot: AnchorSlot): SavedFlm {
  const npu = anchorCfg?.npu ?? anchorSlot?.npu ?? {}
  // `||`: useSlots normalises an absent default to '', which must fall through to the live ids.
  const raw = anchorCfg?.model?.default || anchorSlot?.modelDefault || anchorSlot?.model_id || anchorSlot?.model || null
  const chatModel = typeof raw === 'string' && raw ? raw.replace(/-FLM$/, '') : null
  return {
    chat: npuModalityOn(npu, 'chat'),
    asr: npuModalityOn(npu, 'asr'),
    embed: npuModalityOn(npu, 'embed'),
    chatModel,
  }
}

/** asr/embed ride the chat model's process: with no saved model they cannot be on
 *  (#1637/#1661). Chat itself is always switchable. */
export function capEnabled(saved: SavedFlm, cap: FlmCap): boolean {
  if (cap === 'chat') return true
  return !!saved.chatModel
}

/** The PUT body: `[npu]` carries all three booleans, merged values. */
export function draftBody(draft: FlmDraft, saved: SavedFlm): Record<string, unknown> {
  return npuApplyBody({
    chat: draft.chat ?? saved.chat,
    asr: draft.asr ?? saved.asr,
    embed: draft.embed ?? saved.embed,
    chatModel: draft.chatModel ?? saved.chatModel,
  })
}

/** What "the anchor config changed elsewhere" is judged on: the `[npu]` table and the model. */
export function cfgFingerprint(anchorCfg: AnchorCfg): string {
  return JSON.stringify({ npu: anchorCfg?.npu ?? null, model: anchorCfg?.model?.default ?? null })
}

/** The bundled (non-chat) row serving a capability: real rows can carry several capabilities, so
 *  the bundled model is the one that is NOT chat-capable. */
export function bundledFlmModel(
  catalog: { models?: Array<{ model: string; capabilities?: string[] }> } | null | undefined,
  catCaps: string[],
): string | null {
  const rows = (catalog?.models ?? []).filter((m) => (m.capabilities ?? []).some((c) => catCaps.includes(c)))
  const row = rows.find((m) => !(m.capabilities ?? []).includes('chat'))
  return row?.model ?? null
}

export const FLM_ROLE_LABEL: Record<FlmCap, string> = { chat: 'chat', asr: 'speech-to-text', embed: 'embeddings' }
const ROLES: FlmCap[] = ['chat', 'asr', 'embed']

/** What applying a change does to the process after the config write. */
export type FlmAfter = 'restart' | 'start' | 'stop' | 'none'
export interface FlmConfirm {
  title: string
  body: string
  confirmLabel: string
  destructive: boolean
}
export interface FlmPlan {
  next: FlmDraft
  after: FlmAfter
  /** null: apply without asking (nothing running would be interrupted). */
  confirm: FlmConfirm | null
}

function listRoles(caps: FlmCap[]): string {
  const names = caps.map((c) => FLM_ROLE_LABEL[c])
  if (names.length <= 1) return names.join('')
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

function rolesOn(saved: SavedFlm, except?: FlmCap): FlmCap[] {
  return ROLES.filter((c) => c !== except && saved[c])
}

/**
 * Switching one role on or off. FLM serves every role from one process, so while it runs the
 * change restarts it and the other running roles pause; that is what the confirm says. Turning
 * off the last running role stops FLM instead. With FLM stopped nothing is interrupted: turning a
 * role on writes the config and starts FLM, turning one off only writes the config.
 */
export function planRoleToggle(saved: SavedFlm, cap: FlmCap, up: boolean): FlmPlan {
  const turnOn = !saved[cap]
  const next: FlmDraft = { [cap]: turnOn }
  const label = FLM_ROLE_LABEL[cap]
  if (!up) return { next, after: turnOn ? 'start' : 'none', confirm: null }
  const others = rolesOn(saved, cap)
  if (!turnOn && others.length === 0) {
    return {
      next,
      after: 'stop',
      confirm: {
        title: `Stop ${label}?`,
        body: `It is the only role running, so FLM stops and the NPU is released.`,
        confirmLabel: 'Stop FLM',
        destructive: true,
      },
    }
  }
  const pause = others.length
    ? ` While it restarts, ${listRoles(others)} stop${others.length === 1 ? 's' : ''} answering for a few seconds and in-flight requests fail.`
    : ''
  return {
    next,
    after: 'restart',
    confirm: {
      title: `${turnOn ? 'Start' : 'Stop'} ${label}?`,
      body: `FLM restarts to ${turnOn ? 'add' : 'drop'} this role.${pause}`,
      confirmLabel: `${turnOn ? 'Start' : 'Stop'} ${label} and restart FLM`,
      destructive: !turnOn,
    },
  }
}

/**
 * Picking a chat model. A model that is not installed downloads first (FLM keeps serving), then the
 * config is written and a running FLM restarts onto it. A stopped FLM only gets the config write.
 * `null`: the pick is the current model, nothing to do.
 */
export function planModelPick(saved: SavedFlm, id: string, installed: boolean, up: boolean): FlmPlan | null {
  if (!id || id === saved.chatModel) return null
  const next: FlmDraft = { chatModel: id }
  if (!up) return { next, after: 'none', confirm: null }
  const paused = rolesOn(saved)
  return {
    next,
    after: 'restart',
    confirm: {
      title: `Switch chat to ${id}?`,
      body:
        (installed ? '' : 'It downloads first while FLM keeps serving. ') +
        `Then FLM restarts onto it${paused.length ? `, and ${listRoles(paused)} stop answering for a few seconds` : ''}.`,
      confirmLabel: installed ? 'Switch and restart FLM' : 'Download and switch',
      destructive: false,
    },
  }
}

/**
 * A role card's status dot. A role that is off is grey whatever FLM is doing; a role that is on
 * follows the process. Only chat can be told apart as serving (FLM reports one busy flag), so a
 * busy process shows speech-to-text and embeddings as ready.
 */
export function flmRoleIndicator(
  ind: { cls: string; label?: string; tooltip?: string },
  on: boolean,
  cap: FlmCap,
): { cls: string; label: string; tooltip: string; off: boolean } {
  const label = FLM_ROLE_LABEL[cap]
  if (!on) return { cls: 'offline', label: 'off', tooltip: `${label} is switched off`, off: true }
  if (ind.cls === 'serving' && cap !== 'chat') return { cls: 'stale', label: 'ready', tooltip: `${label} is ready`, off: false }
  return { cls: ind.cls, label: ind.label ?? ind.cls, tooltip: ind.tooltip ?? ind.cls, off: false }
}
