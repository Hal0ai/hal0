// hal0 dashboard — services health hook (W5, §2d contract).
//
// GET /api/services/health → { services: [...] }
// NEW endpoint — fail SOFT: 404 / network error → pending/empty, never throws.
// Shape per CONTRACTS.md §2d:
//   { id, name, up, detail, url, stat:{label,value}|null }
//
// Poll 5s. Returns { services, pending, error } where `pending` is true
// while the endpoint has not yet returned a success (404 included).

import { useQuery } from '@tanstack/react-query'
import { ENDPOINTS } from '../endpoints'

export interface ServiceStat {
  label: string
  value: string
}

export interface ServiceEntry {
  id: string
  name: string
  up: boolean
  detail: string
  url: string | null
  stat: ServiceStat | null
}

export interface ServicesHealthPayload {
  services: ServiceEntry[]
}

const SERVICES_POLL_MS = 5_000

async function fetchServicesHealth(): Promise<ServicesHealthPayload | null> {
  // Use raw fetch — we need to handle 404 as "pending/not yet built" without
  // throwing so React Query considers it a success (null payload).
  let res: Response
  try {
    res = await fetch(ENDPOINTS.servicesHealth, {
      headers: { Accept: 'application/json' },
    })
  } catch {
    // Network error — treat as pending
    return null
  }
  if (res.status === 404) return null
  if (!res.ok) return null
  try {
    return (await res.json()) as ServicesHealthPayload
  } catch {
    return null
  }
}

export function useServicesHealth(): {
  services: ServiceEntry[]
  pending: boolean
  error: boolean
} {
  const q = useQuery<ServicesHealthPayload | null>({
    queryKey: ['services', 'health'],
    queryFn: fetchServicesHealth,
    refetchInterval: SERVICES_POLL_MS,
    // Never throw — null means "not yet available"
    retry: false,
  })

  const pending = q.isPending || (q.isSuccess && q.data === null)
  const services = q.data?.services ?? []

  return {
    services,
    pending,
    error: q.isError,
  }
}
