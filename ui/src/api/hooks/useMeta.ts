// hal0 v3 dashboard — meta-enums hook.
//
// GET /api/meta/enums — the backend's authoritative taxonomy: devices (with
// device_class / default_profile / legacy_backend / recommended flags),
// selectable backends, slot types, model capabilities (+ aliases), model
// backends, runtime families and the backend→device / device→default-profile
// maps. Static per release, so the query caches forever (staleTime Infinity).
//
// Consumers should reach for `useMetaEnums()` — it always returns a fully
// populated MetaEnums: the live payload when the endpoint exists, merged
// per-key over META_ENUMS_FALLBACK (src/lib/deviceMeta.ts) so an older
// backend, a partial payload, or mock mode never leaves a picker empty.

import { useMemo } from 'react'
import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'
import {
  META_ENUMS_FALLBACK,
  resolveMetaEnums,
  type MetaDevice,
  type MetaEnums,
} from '../../lib/deviceMeta'

export { META_ENUMS_FALLBACK, resolveMetaEnums }
export type { MetaDevice, MetaEnums }

export function useMeta(): UseQueryResult<Partial<MetaEnums>> {
  return useQuery({
    queryKey: ['meta', 'enums'],
    queryFn: () => apiGet<Partial<MetaEnums>>(ENDPOINTS.metaEnums),
    // Enums are static per release — never refetch, never gc.
    staleTime: Infinity,
    gcTime: Infinity,
    // A missing endpoint (older backend) should settle to the fallback fast,
    // not retry-storm.
    retry: false,
  })
}

/** Resolved enums — never undefined; the static fallback fills any gap. */
export function useMetaEnums(): MetaEnums {
  const q = useMeta()
  return useMemo(() => resolveMetaEnums(q.data), [q.data])
}
