// Single source of truth for the FLM [npu] modality defaults.
//
// Backend contract (config/schema.py NpuConfig + providers/flm.py build_env):
// chat defaults ON when the key is absent; asr/embed default OFF unless
// explicitly true. The slot-drawer toggle seeds and the NPU occupancy card's
// pills both read through here so the two surfaces can never disagree on an
// absent key.
export const npuModalityOn = (npu, role) => {
  const t = npu || {}
  return role === 'chat' ? t.chat !== false : t[role] === true
}

// Which [npu] role a slot card stands for. Keyed on slot TYPE — the same
// discriminator the backend trio uses (device=npu + type=transcription|
// embedding marks a shadow) — never on the display name: a shadow with a
// non-canonical name must not fall into the chat branch and toggle the
// anchor's chat modality.
export const npuRoleForSlot = (slot) =>
  slot?.type === 'transcription' ? 'asr' : slot?.type === 'embedding' ? 'embed' : 'chat'

// True for the trio shadow records (stt/embed) riding the anchor's process.
export const isNpuShadowSlot = (slot) =>
  slot?.type === 'transcription' || slot?.type === 'embedding'

// Resolved pill state — what the card should actually render as ON/OFF.
//
// #1637 guarded the CHAT toggle write against a model-less anchor ("model
// presence IS the activation signal") but left the asr/embed pills reading
// the raw [npu] table via npuModalityOn: a model-less anchor that already
// has (or later gets) `npu.asr=true` on disk renders the STT/Embed pill ON
// even though `hal0.slots.activation.npu_modality_active` — the one gate
// for "does an NPU request of this type actually route" — is False whenever
// `is_activated(anchor)` is False (#1661). The backend already lifts that
// resolved answer onto each trio SHADOW's `npu_modality_active` field
// (slot_view config_enrichment); the anchor's own chat entry has no such
// field (only shadows get it), so chat keeps reading the raw table.
export const npuPillOn = (slot, anchorNpu, role) =>
  role === 'chat' ? npuModalityOn(anchorNpu, role) : !!slot?.npu_modality_active
