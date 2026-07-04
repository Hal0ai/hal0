// hal0 dashboard — requests & latency rollup hook (dashboard-redesign).
//
// GET /api/stats/requests → dispatcher-side /v1 rollup over the last 60s:
//   { window_s, req_per_min, p50_ms, p95_ms,
//     endpoints: [{ path, count }], errors, dedupe }
//
// NEW endpoint — the backend has not shipped it yet. Same fail-soft
// contract as useServicesHealth: a 404 / network error / bad shape yields
// `pending`, never a throw, so the Requests card renders "—" + "source
// pending" instead of fabricated numbers.

import { useQuery } from '@tanstack/react-query'
import { ENDPOINTS } from '../endpoints'

export interface RequestsEndpointCount {
  path: string
  count: number
}

export interface RequestsRollup {
  window_s: number
  req_per_min: number | null
  p50_ms: number | null
  p95_ms: number | null
  endpoints: RequestsEndpointCount[]
  errors: number | null
  /** True when single-flight dedupe is active on the dispatcher. */
  dedupe?: boolean
}

const POLL_MS = 5_000

async function fetchRequestsRollup(): Promise<RequestsRollup | null> {
  // Raw fetch — a 404 from an older backend reads as "pending", not an error.
  let res: Response
  try {
    res = await fetch(ENDPOINTS.statsRequests, { headers: { Accept: 'application/json' } })
  } catch {
    return null
  }
  if (!res.ok) return null
  try {
    const body = (await res.json()) as RequestsRollup
    // Shape guard: a mock catch-all may answer 200 {} — treat anything
    // without an endpoints array as "not yet available".
    if (!body || !Array.isArray(body.endpoints)) return null
    return body
  } catch {
    return null
  }
}

export function useRequestsRollup(): {
  data: RequestsRollup | null
  pending: boolean
} {
  const q = useQuery<RequestsRollup | null>({
    queryKey: ['stats', 'requests'],
    queryFn: fetchRequestsRollup,
    refetchInterval: POLL_MS,
    retry: false,
  })
  return {
    data: q.data ?? null,
    pending: q.isPending || (q.isSuccess && q.data === null),
  }
}
