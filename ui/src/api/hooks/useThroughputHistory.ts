// hal0 dashboard overhaul — W4: throughput history hook
//
// Consumes §2a: GET /api/stats/throughput/history?buckets=20&window_s=100
// This is a NEW backend endpoint (backend building it). Hook fails SOFT:
// on 404 / any error it returns isPending=true and empty samples so the
// ThroughputCard2 can show a "source pending" gate without crashing.
//
// DO NOT fall back to the client ring-buffer — contract (§0) requires
// real data from the history endpoint only.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface ThroughputSample {
  ts: number         // epoch seconds
  total_tps: number  // combined tok/s across all slots
  serving_slots: number
}

export interface ThroughputHistory {
  window_s: number
  bucket_s: number
  samples: ThroughputSample[]
  per_slot?: Record<string, number[]>  // optional; same length as samples
}

const POLL_MS = 5_000
const HISTORY_URL = `${ENDPOINTS.statsThroughputHistory}?buckets=20&window_s=100`

export interface UseThroughputHistoryResult {
  data: ThroughputHistory | null
  isPending: boolean
  isError: boolean
  errorStatus: number | null
}

export function useThroughputHistory(): UseThroughputHistoryResult {
  const query = useQuery<ThroughputHistory>({
    queryKey: ['stats', 'throughput', 'history'],
    queryFn: () => apiGet<ThroughputHistory>(HISTORY_URL),
    refetchInterval: POLL_MS,
    // Fail soft: retry=false so we don't hammer a 404 endpoint;
    // staleTime=0 so each poll attempt is fresh.
    retry: false,
    staleTime: 0,
  })

  // Any error (including 404 for not-yet-built endpoint) → isPending gate.
  const isPending =
    query.isLoading ||
    query.isError ||
    !query.data ||
    (query.data.samples?.length ?? 0) === 0

  return {
    data: query.isError ? null : (query.data ?? null),
    isPending,
    isError: query.isError,
    errorStatus: null,  // Hal0Error carries status but we only need the gate
  }
}
