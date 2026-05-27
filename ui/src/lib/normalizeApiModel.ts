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
  if (caps.includes('rerank')) return 'reranking'
  if (caps.includes('embed') || caps.includes('embeddings')) return 'embedding'
  if (caps.includes('transcription') || caps.includes('asr')) return 'transcription'
  if (caps.includes('tts')) return 'tts'
  if (caps.includes('image')) return 'image'
  return ''
}

function deriveDevice(backends: string[]): string {
  if (backends.includes('rocm')) return 'rocm'
  if (backends.includes('vulkan')) return 'vulkan'
  if (backends.includes('cpu')) return 'cpu'
  return backends[0] || ''
}

function formatSize(b: number): string {
  if (!b) return '—'
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`
  return `${(b / 1024 ** 3).toFixed(2)} GB`
}

export function normalizeApiModel(m: ApiModelRaw): NormalizedModel {
  const caps = Array.isArray(m.capabilities) ? m.capabilities : []
  const backends = Array.isArray(m.backends) ? m.backends : []
  return {
    ...m,
    type: deriveType(caps),
    device: deriveDevice(backends),
    longName: m.name || m.id,
    size: formatSize(m.size_bytes || 0),
    repo: m.hf_repo || m.path || '',
  }
}
