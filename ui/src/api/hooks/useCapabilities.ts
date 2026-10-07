// hal0 v3 dashboard — capabilities hooks (Phase B1).
//
// /api/capabilities is the capabilities.toml rollup that backs the
// FirstRun bundle picker + Settings → Runtime. Per the v0.3
// capability-slots system memory: capability cards group provider +
// model + slot routing per cap key (chat, embed, voice, img, npu).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPatch, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface CapabilityRow {
  provider: string
  model?: string
  slot?: string
  enabled?: boolean
  [k: string]: unknown
}

// A picker row from a `catalogs.<slot>.<child>` array — one installable/
// installed model, with its per-backend download state.
export interface CapabilityCatalogItem {
  id: string
  capabilities?: string[]
  size_gb?: number
  backends?: { id: string; provider: string; downloaded: boolean; pullable?: boolean }[]
  [k: string]: unknown
}

// The live selection for one `selections.<slot>.<child>` pair.
export interface CapabilitySelection {
  device: string
  backend: string
  provider: string
  model: string | null
  enabled: boolean
  slot: string
  status: string
  [k: string]: unknown
}

export interface CapabilityBackend {
  id: string
  label?: string
  short?: string
  provider?: string
  multiplex?: boolean
  [k: string]: unknown
}

// Real GET /api/capabilities envelope (orchestrator.py get_state /
// catalogs_by_slot / catalog.available_backends). `catalogs.<slot>.<child>`
// and `selections.<slot>.<child>` are keyed the same way — e.g.
// catalogs.voice.stt is a bare CapabilityCatalogItem[], not
// `{items: [...]}`/`{models: [...]}`. Replaces the obsolete
// pre-orchestrator `{capabilities: Record<...>}` shape, which doesn't match
// what the API (or mockFixtures.ts buildCapabilities()) actually ships.
export interface CapabilitiesBag {
  backends: CapabilityBackend[]
  // False while the backend's FLM-image probe has no answer yet (just after
  // boot or an FLM pull), so `backends` may still gain/lose NPU (#1974).
  backends_settled?: boolean
  // Whole seconds until the backend's next FLM-image probe is due: 0 while a
  // probe is pending, the rest of its 30 s retry window while the probe could
  // not answer (podman/sudo broken). Sets the re-poll cadence below.
  backends_retry_in_s?: number
  catalogs: Record<string, Record<string, CapabilityCatalogItem[]>>
  selections: Record<string, Record<string, CapabilitySelection>>
}

// Query-key root for GET /api/capabilities. Exported so other hooks can
// invalidate it, e.g. usePullJob after an FLM pull resets the backend's
// FLM-image probe (#1974), without restating the literal.
export const CAPABILITIES_QUERY_KEY = ['capabilities'] as const

/** Re-poll cadence for useCapabilities: ms, or false to stop polling (#1974). */
export function capabilitiesRefetchInterval(data: CapabilitiesBag | undefined): number | false {
  if (data?.backends_settled !== false) return false
  return Math.max(2000, (data.backends_retry_in_s ?? 0) * 1000)
}

export function useCapabilities() {
  return useQuery({
    queryKey: CAPABILITIES_QUERY_KEY,
    queryFn: () => apiGet<CapabilitiesBag>(ENDPOINTS.capabilities),
    // Re-poll only while NPU presence is unsettled (#1974): every 2 s while a
    // probe is pending, once per retry window while it cannot answer, never
    // once settled (or against an API without the field).
    refetchInterval: (query) => capabilitiesRefetchInterval(query.state.data),
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

/**
 * POST /api/capabilities/{slot}/{child} — apply a partial selection to one
 * (slot, child) pair. Accepted keys: model, provider, enabled.
 * This is the correct persistence path for voice/img capability picks;
 * the orchestrator reconciles slot lifecycle (load/swap/unload) automatically.
 */
export function useCapabilityApply() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ slot, child, body }: { slot: string; child: string; body: Partial<CapabilityRow> }) =>
      apiPost(ENDPOINTS.capabilityApply(slot, child), body as Record<string, unknown>),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['capabilities'] })
      qc.invalidateQueries({ queryKey: ['slots'] })
    },
  })
}
