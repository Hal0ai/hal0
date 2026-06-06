// hal0 v3 dashboard — capabilities hooks (Phase B1).
//
// /api/capabilities is the capabilities.toml rollup that backs the
// FirstRun bundle picker + Settings → Runtime. Per the v0.3
// capability-slots system memory: capability cards group provider +
// model + slot routing per cap key (chat, embed, voice, img, npu).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPatch } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface CapabilityRow {
  provider: string
  model?: string
  slot?: string
  enabled?: boolean
  [k: string]: unknown
}

export interface CapabilitiesBag {
  capabilities: Record<string, CapabilityRow>
}

export function useCapabilities() {
  return useQuery({
    queryKey: ['capabilities'],
    queryFn: () => apiGet<CapabilitiesBag>(ENDPOINTS.capabilities),
  })
}

export function useCapability(key: string | null | undefined) {
  return useQuery({
    queryKey: ['capabilities', key],
    queryFn: () => apiGet<CapabilityRow>(ENDPOINTS.capability(key as string)),
    enabled: !!key,
  })
}

export function useCapabilityPatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ key, body }: { key: string; body: Partial<CapabilityRow> }) =>
      apiPatch(ENDPOINTS.capability(key), body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['capabilities'] }),
  })
}
