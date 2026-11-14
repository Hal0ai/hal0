// Pure helpers for the FLM lane's staged changes (Slots → NPU → Lanes).
// A draft holds only the keys that differ from what is saved; applying it is ONE config write
// and ONE restart. Reads go through npu-modality.js so the absent-key defaults stay in one place.
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

/** Flip a capability in the draft; the key goes away once it equals the saved value. */
export function toggleCap(draft: FlmDraft, saved: SavedFlm, cap: FlmCap): FlmDraft {
  const cur = cap in draft ? (draft[cap] as boolean) : saved[cap]
  const next = !cur
  const out = { ...draft }
  if (next === saved[cap]) delete out[cap]
  else out[cap] = next
  return out
}

export function pickChatModel(draft: FlmDraft, saved: SavedFlm, id: string): FlmDraft {
  const out = { ...draft }
  if (id === saved.chatModel) delete out.chatModel
  else out.chatModel = id
  return out
}

/** asr/embed ride the chat model's process: with no saved or staged model they cannot be on
 *  (#1637/#1661). Chat itself is always switchable. */
export function capEnabled(saved: SavedFlm, draft: FlmDraft, cap: FlmCap): boolean {
  if (cap === 'chat') return true
  return !!(draft.chatModel ?? saved.chatModel)
}

export function draftCount(draft: FlmDraft): number {
  return Object.keys(draft).length
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
