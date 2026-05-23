// hal0 v3 dashboard — slots hooks (Phase B1).
//
// /api/slots is the authoritative slot list (system.js note: backend
// merge fix #26 lands later). Until then we union /api/status.slots
// over /api/slots — same approach as the v2 store.
//
// Slot metrics rev fast — they get a 2.5s refetch; the list rev slowly
// (slot defs change on edit), so 5s is enough.

import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface SlotMetrics {
  toks?: number
  ttft?: number | null
  ctx?: number
  kv?: number | null
  mem?: number
  rpm?: number
  lat?: number | null
  dim?: number
  xrt?: number
  precision?: string
  maxDocs?: number
  secs?: number
  voice?: string
  avg?: number
  res?: string
}

export interface Slot {
  name: string
  type: string
  device: string
  model: string
  model_id?: string
  modelLong?: string
  group?: string
  state: string
  isDefault?: boolean
  coresident?: boolean
  cpuOnly?: boolean
  port?: number
  pid?: number
  metrics: SlotMetrics
  spark?: number[]
}

const SLOTS_POLL_MS = 5_000
const SLOT_DETAIL_POLL_MS = 2_500

// Backend /api/slots can omit `metrics` (and other fields) for offline /
// not-yet-loaded slots. Components dereference `slot.metrics.toks` etc.
// directly, so guarantee a present object with neutral defaults.
const DEFAULT_METRICS: SlotMetrics = {
  toks: 0,
  ttft: null,
  ctx: 0,
  kv: null,
  mem: 0,
  rpm: 0,
  lat: null,
  dim: 0,
  xrt: 0,
  precision: '',
  maxDocs: 0,
  secs: 0,
  voice: '',
  avg: 0,
  res: '',
}

// Backend /api/slots returns sparse slots without `type`, `device`, or
// `group` for built-in slots (primary, embed, stt, …). The v3 SlotsView
// groups via `slots.filter(s => s.group === "chat")` etc., so without
// inference the page renders just the header — looks blank/black.
// Infer from BUILTIN_SLOTS conventions (primary→chat/llm, embed→embed/…).
function inferSlotShape(s: any): { type: string; group: string; device: string } {
  const name = String(s?.name ?? '').toLowerCase()
  const provider = String(s?.provider ?? '').toLowerCase()
  const backend = String(s?.backend ?? s?.metadata?.backend ?? '').toLowerCase()

  let type = s?.type as string | undefined
  let group = s?.group as string | undefined
  let device = s?.device as string | undefined

  if (!type || !group) {
    if (name === 'primary' || name === 'coder' || name === 'agent' || name.includes('chat')) {
      type ??= 'llm'; group ??= 'chat'
    } else if (name === 'rerank' || name.includes('rerank')) {
      type ??= 'reranking'; group ??= 'embed'
    } else if (name === 'embed' || name.includes('embed')) {
      type ??= 'embedding'; group ??= 'embed'
    } else if (name === 'stt' || name.includes('whisper') || name.includes('moonshine')) {
      type ??= 'transcription'; group ??= 'voice'
    } else if (name === 'tts' || name.includes('kokoro') || name.includes('vibe')) {
      type ??= 'tts'; group ??= 'voice'
    } else if (name === 'img' || name === 'image' || name.includes('image') || name.includes('sd')) {
      type ??= 'image'; group ??= 'img'
    } else if (provider.includes('llama') || provider.includes('llm')) {
      type ??= 'llm'; group ??= 'chat'
    }
  }

  if (!device) {
    if (backend === 'vulkan' || backend === 'rocm') device = 'gpu-' + backend
    else if (backend === 'flm' || backend === 'npu') device = 'npu'
    else if (backend === 'cpu' || backend.includes('cpu')) device = 'cpu'
    else device = 'cpu'
  }

  return { type: type ?? 'llm', group: group ?? 'chat', device }
}

function normalizeSlot(s: any): Slot {
  const shape = inferSlotShape(s)
  return {
    ...s,
    type: shape.type,
    group: shape.group,
    device: shape.device,
    metrics: { ...DEFAULT_METRICS, ...(s?.metrics ?? {}) },
    spark: Array.isArray(s?.spark) ? s.spark : [],
    model: s?.model ?? s?.model_id ?? s?.model_default ?? '',
  }
}

async function fetchSlotsUnion(): Promise<Slot[]> {
  // Race /api/status (which may have stale slot list) + /api/slots
  // (authoritative for real slots). Real wins on conflict.
  let statusSlots: Slot[] = []
  let realSlots: Slot[] = []
  try {
    const s = await apiGet<any>(ENDPOINTS.status)
    statusSlots = Array.isArray(s?.slots) ? s.slots : []
  } catch {
    // soft-fail
  }
  try {
    const r = await apiGet<any>(ENDPOINTS.slots)
    if (Array.isArray(r)) realSlots = r
    else if (Array.isArray(r?.slots)) realSlots = r.slots
  } catch {
    // soft-fail; statusSlots covers
  }
  const byName = new Map<string, Slot>()
  for (const s of statusSlots) byName.set(s.name, s)
  for (const s of realSlots) byName.set(s.name, s)
  return [...byName.values()].map(normalizeSlot)
}

export function useSlots(): UseQueryResult<Slot[]> {
  return useQuery({
    queryKey: ['slots'],
    queryFn: fetchSlotsUnion,
    refetchInterval: SLOTS_POLL_MS,
  })
}

/**
 * Slot detail. Polls faster than the list because the metrics tile
 * inside SlotCard wants live tok/s + KV%.
 */
export function useSlotDetail(name: string | null | undefined): UseQueryResult<Slot> {
  return useQuery({
    queryKey: ['slots', name],
    queryFn: async () => normalizeSlot(await apiGet<any>(ENDPOINTS.slot(name as string))),
    enabled: !!name,
    refetchInterval: SLOT_DETAIL_POLL_MS,
  })
}

// ── Mutations ──────────────────────────────────────────────────────

function useSlotsInvalidator() {
  const qc = useQueryClient()
  return () => {
    qc.invalidateQueries({ queryKey: ['slots'] })
    qc.invalidateQueries({ queryKey: ['lemonade', 'health'] })
  }
}

export function useSlotRestart() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: (name: string) => apiPost(ENDPOINTS.slotRestart(name)),
    onSuccess: invalidate,
  })
}

export function useSlotLoad() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: (name: string) => apiPost(ENDPOINTS.slotLoad(name)),
    onSuccess: invalidate,
  })
}

export function useSlotUnload() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: (name: string) => apiPost(ENDPOINTS.slotUnload(name)),
    onSuccess: invalidate,
  })
}

export function useSlotSwap() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: ({ name, model_id }: { name: string; model_id: string }) =>
      apiPost(ENDPOINTS.slotSwap(name), { model_id }),
    onSuccess: invalidate,
  })
}

export function useSlotCreate() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => apiPost(ENDPOINTS.slots, body),
    onSuccess: invalidate,
  })
}

export function useSlotEdit() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: Record<string, unknown> }) =>
      apiPost(ENDPOINTS.slot(name), body),
    onSuccess: invalidate,
  })
}

export function useSlotDelete() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: (name: string) => apiDelete(ENDPOINTS.slot(name)),
    onSuccess: invalidate,
  })
}
