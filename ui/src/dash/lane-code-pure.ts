// The lane vocabulary: what a slot or a model runs on, as a three-letter code. One table, so the
// slot cards, the slot rail, the model rows and the endpoint rows cannot name a lane differently.
import { devKind } from '@/lib/deviceMeta'
import { isFlmModel, isXdnaSlot } from '@/lib/slotModelFit'

export type LaneKey = 'vulkan' | 'rocm' | 'cuda' | 'cpu' | 'npu' | 'flm' | 'ext'

export const LANE_CODES: Record<LaneKey, { code: string; label: string }> = {
  vulkan: { code: 'VLK', label: 'Vulkan (GPU)' },
  rocm: { code: 'ROC', label: 'ROCm (GPU)' },
  cuda: { code: 'CUD', label: 'CUDA (GPU)' },
  cpu: { code: 'CPU', label: 'CPU' },
  npu: { code: 'NPU', label: 'NPU (ggml-xdna)' },
  flm: { code: 'FLM', label: 'NPU (FastFlowLM)' },
  ext: { code: 'EXT', label: 'External (upstream provider)' },
}

type SlotLike = { device?: string | null; binary?: string | null }

/** The lane of a slot. An NPU slot is FLM unless it runs the xdna runner. */
export function laneForSlot(slot: SlotLike | null | undefined): LaneKey {
  const kind = devKind(slot?.device)
  if (kind !== 'npu') return kind
  return isXdnaSlot(slot as Parameters<typeof isXdnaSlot>[0]) ? 'npu' : 'flm'
}

/** The lane of one entry of a model's `backends` list, or null for a token outside the vocabulary.
 *  An `npu` backend on an FLM model is the FLM lane. */
export function laneForBackend(
  backend: string | null | undefined,
  model?: Parameters<typeof isFlmModel>[0],
): LaneKey | null {
  const b = String(backend || '').toLowerCase().replace(/^gpu-/, '')
  if (b === 'npu') return model && isFlmModel(model) ? 'flm' : 'npu'
  return b in LANE_CODES ? (b as LaneKey) : null
}
