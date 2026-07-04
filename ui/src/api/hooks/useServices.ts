// hal0 dashboard — Services management page hooks.
//
// GET  /api/services              → useServices     (5s poll, fail-soft)
// POST /api/services/{id}/action  → useServiceAction (invalidates the list)
// GET/POST /api/services/mdns     → useMdnsStatus / useMdnsAdvertise
// GET  /api/logs?unit=…           → useUnitLogs     (on-demand logs drawer)
//
// Same fail-soft contract as useServicesHealth: a 404 (endpoint not built /
// older backend) or network error yields `pending`, never a throw, so the
// Services page renders "source pending" instead of an error wall.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface ServiceUnitState {
  active_state: string
  sub_state: string
  unit_file_state: string
  since: string | null
}

export interface ManagedService {
  id: string
  name: string
  description: string
  managed: boolean
  unit: string | null
  unit_state: ServiceUnitState | null
  up: boolean
  detail: string
  stat: { label: string; value: string } | null
  url: string | null
  mdns_url: string | null
  loopback_port: number | null
  actions: string[]
  mdns_capable: boolean
  hints: string[]
}

export interface MdnsStatus {
  available: boolean
  hostname: string
  base_advertised: boolean
  advertised: string[]
  advertisable?: { id: string; name: string; port: number | null }[]
}

export interface ServicesPayload {
  services: ManagedService[]
  mdns: MdnsStatus
}

export interface ServiceActionResult {
  id: string
  unit: string
  action: string
  ok: boolean
  active: boolean
  message: string
}

const SERVICES_POLL_MS = 5_000

async function fetchServices(): Promise<ServicesPayload | null> {
  // Raw fetch so a 404 from an older backend reads as "pending", not an error.
  let res: Response
  try {
    res = await fetch(ENDPOINTS.services, { headers: { Accept: 'application/json' } })
  } catch {
    return null
  }
  if (!res.ok) return null
  try {
    const body = (await res.json()) as ServicesPayload
    // Shape guard: an older backend (or a mock catch-all) may answer 200 {}
    // — treat anything without a services array as "not yet available".
    if (!body || !Array.isArray(body.services)) return null
    return body
  } catch {
    return null
  }
}

export function useServices(): {
  services: ManagedService[]
  mdns: MdnsStatus | null
  pending: boolean
} {
  const q = useQuery<ServicesPayload | null>({
    queryKey: ['services', 'list'],
    queryFn: fetchServices,
    refetchInterval: SERVICES_POLL_MS,
    retry: false,
  })
  return {
    services: q.data?.services ?? [],
    mdns: q.data?.mdns ?? null,
    pending: q.isPending || (q.isSuccess && q.data === null),
  }
}

export function useServiceAction() {
  const qc = useQueryClient()
  return useMutation<ServiceActionResult, Error, { id: string; action: string }>({
    mutationFn: ({ id, action }) =>
      apiPost<ServiceActionResult>(ENDPOINTS.serviceAction(id), { action }),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['services'] })
    },
  })
}

export function useMdnsAdvertise() {
  const qc = useQueryClient()
  return useMutation<MdnsStatus & { ok: boolean }, Error, boolean>({
    mutationFn: (advertise) =>
      apiPost<MdnsStatus & { ok: boolean }>(ENDPOINTS.servicesMdns, { advertise }),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['services'] })
    },
  })
}

export interface UnitLogs {
  unit: string
  lines: string[]
  count: number
  hint?: string
}

// On-demand journald tail for a unit's logs drawer. Disabled until the
// drawer opens; refetch via the returned query's refetch().
export function useUnitLogs(unit: string | null, enabled: boolean) {
  return useQuery<UnitLogs>({
    queryKey: ['services', 'logs', unit],
    queryFn: () => apiGet<UnitLogs>(ENDPOINTS.logsUnit(unit as string)),
    enabled: enabled && !!unit,
    refetchInterval: enabled ? 5_000 : false,
    retry: false,
  })
}
