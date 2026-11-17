// hal0 v3 dashboard — GET /api/runners/xdna/fit[?model=<id>].
//
// Per registry model: does the ggml-xdna runner run it on the NPU's fused path, per-op, or not at
// all (`xdna_path: null`, with the reason). A row whose file is missing or not GGUF still comes
// back (200, null path), never a 500.
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface XdnaFitRow {
  xdna_path: 'fused' | 'per-op' | null
  reason: string
  /** false = too large or the wrong kind to be practical on the NPU (a 74B model, a reranker). Absent = ok. */
  npu_ok?: boolean
  /** Why `npu_ok` is false. */
  npu_note?: string | null
}
export type XdnaFit = Record<string, XdnaFitRow>

/** `enabled: false` for a page that only wants the fit on a box with an NPU. */
export function useXdnaFit({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery<XdnaFit>({
    enabled,
    queryKey: ['runners', 'xdna', 'fit'],
    queryFn: async () => {
      const d = await apiGet<{ models?: XdnaFit } | null>(ENDPOINTS.xdnaFit)
      const m = d?.models
      return m && typeof m === 'object' && !Array.isArray(m) ? m : {}
    },
    staleTime: 30_000,
  })
}
