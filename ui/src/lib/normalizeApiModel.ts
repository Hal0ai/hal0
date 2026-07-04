// Shared /api/models row normalizer.
//
// The backend registry intentionally stores `capabilities` (chat | embed |
// rerank | transcription | tts | vision | tool-calling | coding | image)
// and leaves `type` unset. Slots use the dispatcher vocabulary
// (llm | embedding | reranking | transcription | tts | image). The UI
// joins models ↔ slots on `model.type === slot.type`, so we derive `type`
// once at the consumer boundary instead of in every consumer.
//
// Kept in step with src/hal0/slots/manager.py:_VALID_SLOT_TYPES.

export type SlotType =
  | 'llm'
  | 'embedding'
  | 'reranking'
  | 'transcription'
  | 'tts'
  | 'image'
  | ''

export interface ApiModelRaw {
  id: string
  name?: string
  capabilities?: string[]
  backends?: string[]
  size_bytes?: number
  hf_repo?: string
  path?: string
  type?: string | null
  /** Explicit provenance from the backend: "local" | "upstream". Older
   * backends omit it — isUpstreamModel falls back to installed+upstream. */
  origin?: string
  /** Quantisation label ("Q4_K_M", "IQ2_XS", "F16") — header/filename derived. */
  quant?: string | null
  /** Freeform registry tags ("mtp", "coder", "user-added", …). */
  tags?: string[]
  /** Unix seconds the row was registered / first seen. */
  created?: number
  // ── Legacy HAL0_DATA mock shape (data.jsx fixtures / mock.ts 404
  // fallback). Real /api/models rows never carry these; the normalizer
  // tolerates them so mock mode keeps working through the same code path.
  /** Legacy alias of `capabilities`. */
  labels?: string[]
  /** Legacy pre-derived device token (rocm|vulkan|cpu|npu|…). */
  device?: string
  /** Legacy pre-derived display name. */
  longName?: string
  /** Legacy pre-formatted human size ("18.8 GB"). */
  size?: string
  /** Legacy pre-derived repo coordinate. */
  repo?: string
  [k: string]: unknown
}

export interface NormalizedModel extends ApiModelRaw {
  type: SlotType
  device: string
  longName: string
  size: string
  repo: string
}

function deriveType(caps: string[]): SlotType {
  if (caps.includes('chat') || caps.includes('coding') || caps.includes('tool-calling') || caps.includes('vision')) return 'llm'
  if (caps.includes('rerank') || caps.includes('reranking')) return 'reranking'
  if (caps.includes('embed') || caps.includes('embeddings')) return 'embedding'
  if (caps.includes('transcription') || caps.includes('asr')) return 'transcription'
  if (caps.includes('tts')) return 'tts'
  if (caps.includes('image')) return 'image'
  return ''
}

export function deriveDevice(backends: string[]): string {
  if (backends.includes('rocm')) return 'rocm'
  if (backends.includes('vulkan')) return 'vulkan'
  if (backends.includes('cpu')) return 'cpu'
  return backends[0] || ''
}

export function formatSize(b: number): string {
  if (!b) return '—'
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`
  return `${(b / 1024 ** 3).toFixed(2)} GB`
}

// A row `/api/models` aggregated from an upstream provider's `/v1/models`
// rather than the local registry: advertised-only, never on this host's
// disk, routed by the dispatcher to `m.upstream`. The backend now stamps an
// explicit `origin` ("local" | "upstream") on every row — prefer it when
// present. Fallback (older backends / legacy fixtures): infer from
// installed+upstream. FLM/NPU rows also carry `upstream` ("npu") but are
// installed host-side, so the `installed` check keeps them local.
export function isUpstreamModel(m: ApiModelRaw): boolean {
  if (m.origin === 'upstream') return true
  if (m.origin === 'local') return false
  return !m.installed && typeof m.upstream === 'string' && m.upstream !== ''
}

// Coordinate shown under a model's name. We standardize on the HuggingFace
// `<org>/<repo>` style and NEVER surface a raw `/mnt/ai-models/…/x.gguf`
// filesystem path (which leaked through the old `hf_repo || path` fallback):
//   1. an explicit `hf_repo` wins;
//   2. else reconstruct coords from a HF cache path
//      (`…/models--<org>--<repo>/snapshots/<sha>/…` → `<org>/<repo>`);
//   3. else an upstream-advertised row → name the provider, never `local/…`;
//   4. else it's a genuinely local model → a clean `local/<id>` coordinate.
function deriveRepo(m: ApiModelRaw): string {
  if (typeof m.hf_repo === 'string' && m.hf_repo) return m.hf_repo
  const path = typeof m.path === 'string' ? m.path : ''
  const cacheMatch = path.match(/models--([^/]+)/)
  if (cacheMatch) {
    const parts = cacheMatch[1].split('--')
    if (parts.length >= 2) return `${parts[0]}/${parts.slice(1).join('--')}`
  }
  if (isUpstreamModel(m)) return `via ${m.upstream}`
  if (m.id) return `local/${m.id}`
  return ''
}

// Accepts BOTH shapes: the registry/API shape (capabilities + backends +
// size_bytes + name + hf_repo) and the legacy HAL0_DATA seed shape
// (labels + device + size + longName + repo + type). Local dev without a
// backend falls back via src/api/mock.ts to HAL0_DATA.models, and the
// γ-suite hits that fallback when fetch fails before page.route catches.
// Explicit legacy fields win; API fields are derived otherwise. Also
// idempotent — normalizing an already-normalized row is a no-op.
export function normalizeApiModel(m: ApiModelRaw): NormalizedModel {
  const caps = Array.isArray(m.capabilities)
    ? m.capabilities
    : Array.isArray(m.labels) ? m.labels : []
  const backends = Array.isArray(m.backends) ? m.backends : []
  return {
    ...m,
    type: typeof m.type === 'string' && m.type ? (m.type as SlotType) : deriveType(caps),
    device: typeof m.device === 'string' && m.device ? m.device : deriveDevice(backends),
    longName: (typeof m.longName === 'string' && m.longName) || m.name || m.id,
    size: typeof m.size === 'string' && m.size ? m.size : formatSize(m.size_bytes || 0),
    repo: (typeof m.repo === 'string' && m.repo) || deriveRepo(m),
  }
}
