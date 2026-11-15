// GET /api/stats/activity?window=5m|15m|1h — per-slot load and fleet duty in 60 equal buckets
// (src/hal0/metrics/read.py::slot_activity). Token rate comes from request_metric, so it covers
// the whole window (the in-memory throughput history keeps only the last few thousand chunks per
// slot) and a request still streaming appears once it ends.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export type ActivityWindow = '5m' | '15m' | '1h'
export const ACTIVITY_WINDOWS: readonly ActivityWindow[] = ['5m', '15m', '1h']

export interface SlotActivityLane {
  /** Token rate per bucket, oldest → newest. */
  tps: number[]
  peak_tps: number
  tokens: number
  requests: number
  errors: number
  ttft_p50_ms: number | null
}

export interface SlotActivity {
  window: ActivityWindow
  window_s: number
  bucket_s: number
  buckets: number
  /** ISO time of the newest bucket's right edge. */
  end: string
  /** Mean of the sampler's fleet readings per bucket; null where none landed. */
  fleet: { gpu_util: Array<number | null>; npu_util: Array<number | null> }
  /** Keyed by slot name; only slots with a request in the window. */
  slots: Record<string, SlotActivityLane>
}

export function useSlotActivity(window: ActivityWindow) {
  return useQuery<SlotActivity>({
    queryKey: ['stats', 'activity', window],
    queryFn: () => apiGet<SlotActivity>(`${ENDPOINTS.statsActivity}?window=${window}`),
    // One poll per bucket for the short window; the long ones move slowly.
    refetchInterval: window === '5m' ? 5_000 : 15_000,
    placeholderData: (prev) => (prev?.window === window ? prev : undefined),
  })
}
