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
  catalogs: Record<string, Record<string, CapabilityCatalogItem[]>>
  selections: Record<string, Record<string, CapabilitySelection>>
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
