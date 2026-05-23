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
  toks: number
  ttft: number | null
  ctx: number
  kv: number | null
  mem: number
  rpm: number
  lat: number | null
  dim: number
  xrt: number
  precision: string
  maxDocs: number
  secs: number
  voice: string
  avg: number
  res: string
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

/**
 * Default SlotMetrics. Every numeric field defaults to 0, nullable
 * lat/kv/ttft default to null (components treat null as "—"), strings
 * default to "". Components do `.toFixed()`/`.replace()` on these
 * unguarded, so partial backend data MUST be filled in before render.
 */
function defaultMetrics(): SlotMetrics {
  return {
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
}

/**
 * Normalize a slot from the wire into a shape every component can
 * read without crashing. Root cause this guards against: real
 * `/api/slots` rows arrive WITHOUT a `metrics` field, so e.g.
 * `s.metrics.toks` throws TypeError and unmounts the React tree.
 *
 * Mirrors the HAL0_DATA mock contract in ui/src/dash/data.jsx.
 */
export function normalizeSlot(raw: any): Slot {
  const src = (raw && typeof raw === 'object') ? raw : {}
  const m = (src.metrics && typeof src.metrics === 'object') ? src.metrics : {}
  return {
    name: typeof src.name === 'string' ? src.name : '',
    type: typeof src.type === 'string' ? src.type : 'llm',
    device: typeof src.device === 'string' ? src.device : 'cpu',
    state: typeof src.state === 'string' ? src.state : 'offline',
    model: typeof src.model === 'string' ? src.model : '',
    model_id: src.model_id ?? undefined,
    modelLong: src.modelLong ?? undefined,
    group: src.group ?? undefined,
    isDefault: !!src.isDefault,
    coresident: !!src.coresident,
    cpuOnly: !!src.cpuOnly,
    port: typeof src.port === 'number' ? src.port : undefined,
    pid: typeof src.pid === 'number' ? src.pid : undefined,
    spark: Array.isArray(src.spark) ? src.spark : undefined,
    metrics: { ...defaultMetrics(), ...m },
  }
}

function normalizeSlots(raw: any): Slot[] {
  if (!Array.isArray(raw)) return []
  return raw.map(normalizeSlot)
}

async function fetchSlotsUnion(): Promise<Slot[]> {
  // Race /api/status (which may have stale slot list) + /api/slots
  // (authoritative for real slots). Real wins on conflict.
  let statusSlots: any[] = []
  let realSlots: any[] = []
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
  for (const s of statusSlots) {
    const n = normalizeSlot(s)
    if (n.name) byName.set(n.name, n)
  }
  for (const s of realSlots) {
    const n = normalizeSlot(s)
    if (n.name) byName.set(n.name, n)
  }
  return [...byName.values()]
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
    queryFn: async () => {
      const raw = await apiGet<any>(ENDPOINTS.slot(name as string))
      return normalizeSlot(raw)
    },
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
