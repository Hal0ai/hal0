// Which models a slot may bind: the ONE engine-fit predicate behind every
// slot model dropdown.
//
// FLM (FastFlowLM) models run only on the NPU. A GPU/CPU llama.cpp slot
// (runner format `gguf`) cannot load them, and an NPU/FLM slot (runner format
// `flm`) cannot serve a GGUF. Every picker that offers models for a slot
// funnels through `modelFitsSlot` / `filterModelsForSlot` so the rule cannot
// drift per surface. The server enforces the same rule on create / config /
// load / swap (hal0.slots.config_write.check_model_fits_slot).
//
// Import-cycle-safe: depends only on lib/deviceMeta.

import { devKind } from '@/lib/deviceMeta'

/** The fields of a /api/models row the predicate reads (raw or normalized). */
export interface FlmModelLike {
  id?: string
  /** Server-computed engine identity (models_service.model_to_dict). */
  provider_effective?: string | null
  /** Stored engine identity. */
  provider?: string | null
  /** Host-flm probe rows carry `backend: "flm"` (singular). */
  backend?: string | null
  backends?: string[] | null
  owned_by?: string | null
  /** Older / normalized device token ("npu" for FLM rows). */
  device?: string | null
}

/** The fields of a slot (or a stack's slot spec) the predicate reads. */
export interface SlotEngineLike {
  device?: string | null
  device_class?: string | null
  provider?: string | null
}

const FLM_TAGS = new Set(['flm', 'npu'])

/** True when the model is served by FastFlowLM (runs on the NPU only). */
export function isFlmModel(m: FlmModelLike | null | undefined): boolean {
  if (!m) return false
  // The server's resolved engine identity is authoritative when present.
  if (typeof m.provider_effective === 'string' && m.provider_effective) {
    return m.provider_effective === 'flm'
  }
  if (typeof m.provider === 'string' && m.provider) return m.provider === 'flm'
  // Rows that skip model_to_dict: the host-flm probe rows (backend "flm",
  // `<tag>-FLM` id) and older fixtures. Same tag set as the server's
  // derive_model_provider (flm | npu).
  if (typeof m.backend === 'string' && FLM_TAGS.has(m.backend)) return true
  if (Array.isArray(m.backends) && m.backends.some((b) => FLM_TAGS.has(String(b).toLowerCase()))) {
    return true
  }
  if (m.owned_by === 'flm') return true
  if (typeof m.id === 'string' && m.id.endsWith('-FLM')) return true
  return m.device === 'npu'
}

/** True when the slot drives the NPU / FLM engine. */
export function isFlmSlot(slot: SlotEngineLike | null | undefined): boolean {
  if (!slot) return false
  return (
    devKind(slot.device) === 'npu' ||
    slot.device_class === 'npu' ||
    String(slot.provider || '').toLowerCase() === 'flm'
  )
}

/** A model fits a slot only when both are FLM or neither is. */
export function modelFitsSlot(
  m: FlmModelLike | null | undefined,
  slot: SlotEngineLike | null | undefined,
): boolean {
  return isFlmModel(m) === isFlmSlot(slot)
}

/** Narrow a model list to what `slot` can bind. */
export function filterModelsForSlot<T extends FlmModelLike>(
  models: T[] | null | undefined,
  slot: SlotEngineLike | null | undefined,
): T[] {
  return (models ?? []).filter((m) => modelFitsSlot(m, slot))
}
