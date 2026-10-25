// Shared device / backend taxonomy helpers (meta-enums aware).
//
// Single source of truth for the device→class→backend vocabulary the dash
// panes used to hardcode (devKind was copied verbatim into slot-list.jsx,
// npu-pane.jsx and inference-pane.jsx; profiles/stacks each carried their own
// BACKEND_META / DEVICE_META tables). All helpers accept an optional
// `MetaEnums` (from GET /api/meta/enums via useMeta) and fall back to
// `META_ENUMS_FALLBACK` — a typed snapshot of today's known values — so every
// consumer keeps working against an older backend or in mock mode.
//
// This module is import-cycle-safe by design: it must NOT import from
// src/api/* (mock.ts and the hooks both import from here).

export interface MetaDevice {
  /** Device enum id — SlotConfig.device vocabulary: gpu-rocm | gpu-vulkan | cpu | npu | img. */
  id: string
  /** Human label ("ROCm", "FLM · NPU"). */
  label: string
  /** Silicon class: gpu | cpu | npu | img. */
  device_class: string
  /** Seed profile preselected for this device (DEVICE_DEFAULT_PROFILES). */
  default_profile: string | null
  /** ProfileConfig.backend value for GPU devices (rocm|vulkan); null off-GPU. */
  legacy_backend: string | null
  /** True for the device the project recommends (gpu-rocm on Strix Halo). */
  recommended: boolean
  /** One-line operator guidance ("Vulkan fallback — …"). */
  description: string
}

export interface MetaEnums {
  devices: MetaDevice[]
  selectable_backends: string[]
  device_classes: string[]
  slot_types: string[]
  model_capabilities: string[]
  capability_aliases: Record<string, string>
  model_backends: string[]
  /** Curated Model.tags vocabulary (type tags + provenance + catalogue descriptors). */
  curated_model_tags: string[]
  runtime_families: string[]
  backend_to_device: Record<string, string>
  device_default_profiles: Record<string, string>
}

/**
 * Static fallback — today's known values, kept in step with the backend:
 *   config/schema.py    DeviceLiteral, BACKEND_TO_DEVICE, DEVICE_DEFAULT_PROFILES,
 *                       SEED_PROFILES (device_class/backend/img)
 *   slots/manager.py    _VALID_SLOT_TYPES
 *   registry/model.py   capabilities + backends vocab
 * Enums are static per release, so this snapshot only drifts across upgrades —
 * and then only until the live /api/meta/enums response takes over.
 */
export const META_ENUMS_FALLBACK: MetaEnums = Object.freeze({
  devices: [
    {
      id: 'gpu-rocm',
      label: 'ROCm',
      device_class: 'gpu',
      default_profile: 'chat',
      legacy_backend: 'rocm',
      recommended: true,
      description: 'AMD ROCm — best throughput on Strix Halo (recommended)',
    },
    {
      id: 'gpu-vulkan',
      label: 'Vulkan',
      device_class: 'gpu',
      default_profile: 'chat',
      legacy_backend: 'vulkan',
      recommended: false,
      description: 'Vulkan fallback — broad compatibility, lower throughput than ROCm',
    },
    {
      id: 'npu',
      label: 'FLM · NPU',
      device_class: 'npu',
      default_profile: 'flm',
      legacy_backend: null,
      recommended: false,
      description: 'FLM on the XDNA NPU — coresident chat / ASR / embed stack',
    },
    {
      id: 'cpu',
      label: 'CPU',
      device_class: 'cpu',
      default_profile: 'cpu-chat',
      legacy_backend: null,
      recommended: false,
      description: 'CPU-only llama-server — no GPU required',
    },
    {
      id: 'img',
      label: 'ComfyUI · IMG',
      device_class: 'img',
      default_profile: 'comfyui',
      legacy_backend: null,
      recommended: false,
      description: 'ComfyUI image generation (GPU held via the arbiter)',
    },
  ],
  selectable_backends: ['rocm', 'vulkan', 'cpu', 'auto'],
  device_classes: ['gpu', 'cpu', 'npu', 'img'],
  slot_types: ['llm', 'embedding', 'reranking', 'transcription', 'tts', 'image'],
  // Canonical model-capability vocabulary (registry vocab + the routing tags
  // the add-model surfaces expose). Aliases below normalize legacy spellings.
  model_capabilities: [
    'chat',
    'tool-calling',
    'vision',
    'embed',
    'rerank',
    'asr',
    'tts',
    'image',
    'edit',
  ],
  capability_aliases: {
    embeddings: 'embed',
    embedding: 'embed',
    reranking: 'rerank',
    transcription: 'asr',
    stt: 'asr',
  },
  model_backends: ['rocm', 'vulkan', 'cpu', 'cuda', 'flm', 'moonshine', 'kokoro'],
  // Curated Model.tags vocabulary — kept in step with
  // src/hal0/model_meta CURATED_MODEL_TAGS (type tags first, then
  // provenance, then the curated-catalogue descriptors).
  curated_model_tags: [
    'mtp',
    'moe',
    'tool-calling',
    'reasoning',
    'coder',
    'vision',
    'curated',
    'user-added',
    'chat',
    'code',
    'coding',
    'frontier',
    'long-context',
    'multilingual',
    'default',
    'rocmfp4',
    'balanced',
    'tiny',
    'lite-bundle',
    'smoke-test',
    'fast',
    'low-vram',
    'mit',
    'embed',
    'light',
    'medium',
    'rerank',
    'stt',
    'transcription',
    'tts',
  ],
  // hal0.model_meta.RUNTIME_FAMILIES (src/hal0/model_meta/__init__.py) — this
  // fallback had drifted (llamacpp/whispercpp/sdcpp are not members; the
  // real engine-identity vocabulary is llama-server/flm/kokoro/qwen3tts/
  // moonshine/comfyui). Corrected for the model drawer's Engine select
  // (drawer overhaul PR-3, Task 8) — a client running against an older
  // backend without /api/meta/enums now offers the same options the write
  // path (`PUT /api/models/{id}` `provider`) actually accepts.
  runtime_families: ['llama-server', 'flm', 'kokoro', 'qwen3tts', 'moonshine', 'comfyui'],
  backend_to_device: {
    rocm: 'gpu-rocm',
    vulkan: 'gpu-vulkan',
    cpu: 'cpu',
    flm: 'npu',
    moonshine: 'cpu',
    kokoro: 'cpu',
  },
  device_default_profiles: {
    'gpu-rocm': 'chat',
    'gpu-vulkan': 'chat',
    cpu: 'cpu-chat',
    npu: 'flm',
    img: 'comfyui',
  },
})

const _isFilledArray = (v: unknown): v is unknown[] => Array.isArray(v) && v.length > 0
const _isFilledRecord = (v: unknown): v is Record<string, string> =>
  !!v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v as object).length > 0

/**
 * Merge a (possibly partial / empty / older-backend) /api/meta/enums payload
 * over the static fallback. Any missing or empty field falls back per-key, so
 * `resolveMetaEnums({})` === the fallback and a partial payload never leaves a
 * consumer with an empty vocabulary.
 */
export function resolveMetaEnums(data?: Partial<MetaEnums> | null): MetaEnums {
  if (!data || typeof data !== 'object') return META_ENUMS_FALLBACK
  const f = META_ENUMS_FALLBACK
  return {
    devices: _isFilledArray(data.devices) ? (data.devices as MetaDevice[]) : f.devices,
    selectable_backends: _isFilledArray(data.selectable_backends)
      ? (data.selectable_backends as string[])
      : f.selectable_backends,
    device_classes: _isFilledArray(data.device_classes)
      ? (data.device_classes as string[])
      : f.device_classes,
    slot_types: _isFilledArray(data.slot_types) ? (data.slot_types as string[]) : f.slot_types,
    model_capabilities: _isFilledArray(data.model_capabilities)
      ? (data.model_capabilities as string[])
      : f.model_capabilities,
    capability_aliases: _isFilledRecord(data.capability_aliases)
      ? data.capability_aliases
      : f.capability_aliases,
    model_backends: _isFilledArray(data.model_backends)
      ? (data.model_backends as string[])
      : f.model_backends,
    curated_model_tags: _isFilledArray(data.curated_model_tags)
      ? (data.curated_model_tags as string[])
      : f.curated_model_tags,
    runtime_families: _isFilledArray(data.runtime_families)
      ? (data.runtime_families as string[])
      : f.runtime_families,
    backend_to_device: _isFilledRecord(data.backend_to_device)
      ? data.backend_to_device
      : f.backend_to_device,
    device_default_profiles: _isFilledRecord(data.device_default_profiles)
      ? data.device_default_profiles
      : f.device_default_profiles,
  }
}

/** Chip/palette token vocabulary (drives .dev-* / .sl-dev-* / .dchip classes). */
export type DevKind = 'rocm' | 'vulkan' | 'cpu' | 'npu'

/**
 * Normalize a slot `device` (or backend token) to the chip device-kind token.
 * Replaces the three per-pane copies (slot-list / npu-pane / inference-pane).
 * Meta-aware: unknown tokens are folded through `backend_to_device` (e.g. an
 * "flm" backend string classifies as npu) before defaulting to cpu.
 */
export function devKind(device?: string | null, meta: MetaEnums = META_ENUMS_FALLBACK): DevKind {
  const d = String(device || '').toLowerCase()
  if (!d) return 'cpu'
  if (d === 'npu') return 'npu'
  if (d === 'cpu') return 'cpu'
  if (d.includes('vulkan')) return 'vulkan'
  if (d.includes('rocm') || d.startsWith('gpu')) return 'rocm'
  const mapped = meta.backend_to_device[d]
  if (mapped && mapped !== d) return devKind(mapped, meta)
  return 'cpu'
}

/** Map a capability alias (embeddings/reranking/transcription/…) to its canonical id. */
export function canonicalCapability(cap: string, meta: MetaEnums = META_ENUMS_FALLBACK): string {
  const c = String(cap || '').trim()
  return meta.capability_aliases[c] ?? c
}

/** Canonicalize + de-dupe a capability list (order of first occurrence wins). */
export function canonicalCapabilities(
  caps: unknown,
  meta: MetaEnums = META_ENUMS_FALLBACK,
): string[] {
  const list = Array.isArray(caps) ? caps : []
  const out: string[] = []
  const seen = new Set<string>()
  for (const c of list) {
    const canon = canonicalCapability(String(c), meta)
    if (canon && !seen.has(canon)) {
      seen.add(canon)
      out.push(canon)
    }
  }
  return out
}

/** Look up a device enum row by id ("gpu-rocm"). */
export function deviceById(
  id?: string | null,
  meta: MetaEnums = META_ENUMS_FALLBACK,
): MetaDevice | undefined {
  const key = String(id || '').toLowerCase()
  return meta.devices.find((d) => d.id === key)
}
