// hal0 v3 dashboard — slots hooks (Phase B1).
//
// /api/slots is the authoritative slot list (system.js note: backend
// merge fix #26 lands later). Until then we union /api/status.slots
// over /api/slots — same approach as the v2 store.
//
// Slot metrics rev fast — they get a 2.5s refetch; the list rev slowly
// (slot defs change on edit), so 5s is enough.

import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

// Mutating verbs go through `api(..., { raw: true })` so the dev-time
// mockFetch shim (which is GET-shaped and answers POST/PUT/DELETE with
// the same slot-list payload as GET) cannot mask a missing request.
// Playwright specs route directly on the network call; production keeps
// hitting the real backend (mockFetch only kicks in on 404 fallback,
// which mutation endpoints don't trigger).
const slotPost = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body as any, raw: true })
const slotPut = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: 'PUT', body: body as any, raw: true })
const slotPatch = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: 'PATCH', body: body as any, raw: true })
const slotDelete = <T = unknown>(path: string) =>
  api<T>(path, { method: 'DELETE', raw: true })

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
  modelDefault?: string
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
  /** Resident model + KV-cache memory in MiB while the slot is loaded
   *  (state ready/serving/idle/warming). 0/absent when the slot holds no
   *  model in memory. Source of truth for the memory-map attribution
   *  (BE-METRICS contract). Prefer this over equal-split GTT division. */
  mem_mb?: number
  /** Whether the slot is activated. Disabled slots fade on the card, sort to
   *  the end of the grid, and hide their lifecycle buttons. Defaults to true
   *  when absent (a slot is enabled unless explicitly off). */
  enabled?: boolean
  /** Per-slot reasoning default (llm slots). true → thinking on; false/null →
   *  off (suppressed). Seeds the drawer's Thinking toggle. */
  enable_thinking?: boolean | null
  /** GPU offload layer count for the slot's model (-1 = all). Seeds the
   *  drawer's Advanced n_gpu_layers input. */
  n_gpu_layers?: number
  /** Wall-clock epoch (seconds) of the most recent request served by
   *  this slot. ``null``/undefined means hal0-api has not seen a request
   *  for this slot since startup. Used by the slots view to render the
   *  "recently live within 1h" green indicator vs "loaded but stale"
   *  yellow indicator. See ui/src/dash/slots.jsx → ``slotIndicator``. */
  last_used_at?: number | null

  // ── Backend selection (ADR-0022) ────────────────────────────────────
  /** DECLARED backend — the normalized backend token (rocm|vulkan|cpu|flm)
   *  derived from the slot TOML `device` field via device_to_backend().
   *  ALWAYS present for a configured slot. Compare like-for-like against
   *  `actual_backend` (both are the bare token, NOT the gpu- device form). */
  declared_backend?: string | null
  /** ACTUAL runtime backend — the build directory of the live llama-server
   *  child (rocm|vulkan|cpu|flm). Omitted/null when the slot is not loaded
   *  or the child cannot be introspected. Treat absence as "unknown — show
   *  no actual badge". */
  actual_backend?: string | null
  /** True iff declared_backend and actual_backend are both known AND differ.
   *  Backend-computed; the UI renders the mismatch warning ONLY when this is
   *  true and never recomputes it from device strings. */
  backend_mismatch?: boolean

  // ── Container runtime fields (#657) ─────────────────────────────────
  /** Slot runtime engine: "lemonade" (default) or "container". Container
   *  slots dispatch through ContainerProvider (podman/docker systemd unit)
   *  instead of Lemonade. */
  runtime?: 'lemonade' | 'container'
  /** Profile name from /etc/hal0/profiles.toml. Container slots use a
   *  profile to supply the container image + bench-tuned flags. */
  profile?: string | null
  /** Container image ref (from the resolved profile). E.g.
   *  "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server". */
  image?: string | null
  /** Container image availability: "present" | "pulling" | "missing".
   *  Populated by the backend when image_status is tracked. */
  image_status?: 'present' | 'pulling' | 'missing' | null
  /** ACTUAL running container image ref, read from ``podman inspect`` by
   *  _container_state_enrichment (#663). Omitted/null when the container is
   *  not running or inspect fails — treat absence as "unknown". */
  actual_image?: string | null
  /** True iff actual_image is known AND differs from the declared profile
   *  ``image`` (deterministic image-tag drift; replaces the /proc backend
   *  sniff for container slots). UI renders the warning ONLY when true. */
  image_mismatch?: boolean
  /** Container unit state: "running" | "stopped" | "starting" | "crashed".
   *  Set by _container_state_enrichment() in /api/slots. Absent for
   *  Lemonade slots. */
  container_status?: 'running' | 'stopped' | 'starting' | 'crashed' | null
  /** NPU trio modality toggles — present on container-runtime npu slots
   *  (Phase A). Reflects the TOML-backed [npu] section; reads/writes go
   *  through PUT /api/slots/{name}/config rather than lemond flm_args.
   *  Absent/null for Lemonade-runtime NPU slots (legacy path). */
  npu?: { asr: boolean; embed: boolean } | null
  /** True when the container unit is active AND /health returns ok.
   *  False when stopped, starting (health probe not yet passing), or crashed.
   *  Absent for Lemonade slots. */
  container_health?: boolean | null
  /** Canonical llama-server argv for this container slot, starting from the
   *  image tag (omits the podman boilerplate). Populated by
   *  _container_state_enrichment() via resolved_command_for_slot() in
   *  container.py. Absent/null for Lemonade slots. */
  resolved_command?: string[] | null

  // ── Synthetic upstream-backed entries ───────────────────────────────
  // /api/slots merges real lifecycle-managed slots with synthetic
  // entries (slots.py → _synthesize_slots_from_upstreams) that represent
  // composite /v1 upstreams — e.g. the auto-registered ``hal0`` endpoint
  // that fronts every chat model. These are NOT loadable/unloadable/
  // deletable slots, so `useSlots()` filters them out of the slot grid
  // and `useEndpoints()` surfaces them in the sidebar instead.
  /** True for synthetic upstream-backed entries (composite endpoints). */
  _synthetic?: boolean
  /** Operator-facing explanation of why this entry isn't a real slot. */
  _synthetic_reason?: string
  /** Upstream base URL (synthetic entries only). */
  url?: string
  /** Coarse liveness for synthetic entries: "serving" | "offline". */
  status?: string
  /** Count of models this composite upstream advertises via /v1/models. */
  advertised_models?: number
  /** Most recently dispatched model id for this upstream, if any. */
  last_used_model?: string | null
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
    // Configured (TOML) model, surfaced separately so the NPU-trio
    // read-only labels can show the intended FLM tag even when the live
    // model_id is stale on the pre-trio GGUF (trio slots never load as
    // their own process, so model_id never reconciles).
    modelDefault: s?.model_default ?? '',
    // Backend selection (ADR-0022) — pass through verbatim. The backend
    // emits declared_backend always (when configured) and actual_backend/
    // backend_mismatch only when the child is introspectable; we surface
    // null for the absent keys and coerce the flag to a strict boolean.
    declared_backend: s?.declared_backend ?? null,
    actual_backend: s?.actual_backend ?? null,
    backend_mismatch: !!s?.backend_mismatch,
    // Spec 1: a slot is enabled unless explicitly off. /api/status-sourced
    // entries in the union may omit the flag, so default it here rather than
    // letting the card read undefined as "disabled".
    enabled: s?.enabled !== false,
    // Container runtime fields (#657). Pass through verbatim; absent keys
    // surface as null/undefined so the card can safely branch on runtime.
    runtime: s?.runtime ?? 'lemonade',
    profile: s?.profile ?? null,
    // image/image_status may come from profile resolution (backend TBD) or
    // be omitted; null means "unknown — don't show image chip".
    image: s?.image ?? null,
    image_status: s?.image_status ?? null,
    // #663: actual_image (podman inspect) + image_mismatch (running != declared
    // profile image). Absent for Lemonade slots; coerce the flag to a boolean.
    actual_image: s?.actual_image ?? null,
    image_mismatch: !!s?.image_mismatch,
    // container_status / container_health are set by _container_state_enrichment.
    // Absent for Lemonade slots; null here keeps the type honest.
    container_status: s?.container_status ?? null,
    container_health: s?.container_health ?? null,
    // resolved_command: backend-provided llama-server argv for container slots
    // (issue #658). Absent for Lemonade slots.
    resolved_command: s?.resolved_command ?? null,
    // npu: trio modality toggles for container-runtime npu slots (Phase A).
    // Absent/null for Lemonade-runtime slots (legacy path reads flm_args).
    npu: s?.npu ?? null,
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

/**
 * Real, lifecycle-managed slots only. Synthetic upstream-backed entries
 * (composite /v1 endpoints like ``hal0``) are filtered out — they can't
 * be loaded/unloaded/deleted, so showing them in the slot grid is
 * misleading. The sidebar renders them via `useEndpoints()` instead.
 *
 * `useEndpoints` shares this query's cache (same `queryKey`) so the
 * single 5s poll backs both views; only the `select` projection differs.
 */
export function useSlots(): UseQueryResult<Slot[]> {
  return useQuery({
    queryKey: ['slots'],
    queryFn: fetchSlotsUnion,
    refetchInterval: SLOTS_POLL_MS,
    select: (all) => all.filter((s) => !s._synthetic),
  })
}

/**
 * Synthetic upstream-backed entries (composite /v1 endpoints) — the
 * complement of `useSlots()`. These represent aggregate connections
 * (e.g. the auto-registered ``hal0`` endpoint that fronts every chat
 * model), not real slots, and render in the sidebar as read-only
 * endpoint/connection rows. Shares the `['slots']` query cache.
 */
export function useEndpoints(): UseQueryResult<Slot[]> {
  return useQuery({
    queryKey: ['slots'],
    queryFn: fetchSlotsUnion,
    refetchInterval: SLOTS_POLL_MS,
    select: (all) => all.filter((s) => !!s._synthetic),
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
    mutationFn: (name: string) => slotPost(ENDPOINTS.slotRestart(name)),
    onSuccess: invalidate,
  })
}

export function useSlotLoad() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: (name: string) => slotPost(ENDPOINTS.slotLoad(name)),
    onSuccess: invalidate,
  })
}

export function useSlotUnload() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: (name: string) => slotPost(ENDPOINTS.slotUnload(name)),
    onSuccess: invalidate,
  })
}

export function useSlotSwap() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: ({ name, model_id }: { name: string; model_id: string }) =>
      slotPost(ENDPOINTS.slotSwap(name), { model_id }),
    onSuccess: invalidate,
  })
}

export function useSlotCreate() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => slotPost(ENDPOINTS.slots, body),
    onSuccess: invalidate,
  })
}

/**
 * Edit a slot. PUT /api/slots/{name}/config — body is a partial
 * SlotConfig (shallow merged into the existing TOML). Use
 * `useSlotDefaults` when the caller only needs to update keys inside the
 * `[model]` sub-table (ctx_size, temperature, …); use `useSlotBackend`
 * for the single-field backend switch.
 */
export function useSlotEdit() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: Record<string, unknown> }) =>
      slotPut(ENDPOINTS.slotConfig(name), body),
    onSuccess: invalidate,
  })
}

/**
 * PATCH /api/slots/{name}/defaults — body keys merge into the slot's
 * `[model]` sub-table (ctx_size, temperature, …). Backend convenience
 * wrapper over PUT /config so the dashboard doesn't have to assemble a
 * nested envelope for what is conceptually a single field tweak.
 */
export function useSlotDefaults() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: Record<string, unknown> }) =>
      slotPatch(ENDPOINTS.slotDefaults(name), body),
    onSuccess: invalidate,
  })
}

/**
 * POST /api/slots/{name}/backend — switch the slot's backend
 * (e.g. vulkan → rocm). Body shape: `{ backend: string }`.
 */
export function useSlotBackend() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: ({ name, backend }: { name: string; backend: string }) =>
      slotPost(ENDPOINTS.slotBackend(name), { backend }),
    onSuccess: invalidate,
  })
}

export function useSlotDelete() {
  const invalidate = useSlotsInvalidator()
  return useMutation({
    mutationFn: (name: string) => slotDelete(ENDPOINTS.slot(name)),
    onSuccess: invalidate,
  })
}

/**
 * GET /api/slots/{name}/config — read a slot's full TOML config as a dict.
 * Used by voice + image-gen settings sections to reflect current effective
 * values (e.g. default_voice, default_steps) that live in the slot TOML
 * but are not surfaced by the capabilities/selections payload.
 */
export function useSlotConfig(name: string | null | undefined) {
  return useQuery<Record<string, unknown>>({
    queryKey: ['slot-config', name],
    queryFn: () => apiGet<Record<string, unknown>>(ENDPOINTS.slotConfig(name as string)),
    enabled: !!name,
    staleTime: 10_000,
  })
}

// ─── useSlotImagePull ─────────────────────────────────────────────────────────

export type ImagePullState = 'idle' | 'pulling' | 'completed' | 'failed' | 'present' | 'missing'

export interface ImagePullSnapshot {
  slotName: string | null
  image: string | null
  state: ImagePullState
  layer: number
  totalLayers: number
  error: string | null
  inFlight: boolean
  /** Start a pull for the given slot name: POST then open SSE stream. */
  start: (name: string) => Promise<void>
  reset: () => void
}

const IMAGE_PULL_TERMINAL = new Set<ImagePullState>(['completed', 'failed', 'present', 'missing'])

/**
 * Container image-pull composable — mirrors the model `usePullJob` pattern.
 *
 * Usage:
 *   const pull = useSlotImagePull()
 *   pull.start(slot.name)   // POSTs /api/slots/{name}/pull, opens SSE stream
 *   // render pull.state, pull.layer, pull.totalLayers in a progress bar
 */
export function useSlotImagePull(): ImagePullSnapshot {
  const [slotName, setSlotName] = useState<string | null>(null)
  const [image, setImage] = useState<string | null>(null)
  const [state, setState] = useState<ImagePullState>('idle')
  const [layer, setLayer] = useState(0)
  const [totalLayers, setTotalLayers] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)
  const qc = useQueryClient()

  const closeStream = () => {
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
  }

  useEffect(() => () => closeStream(), [])

  const applyPayload = (payload: any) => {
    if (!payload || typeof payload !== 'object') return
    if (typeof payload.slot_name === 'string') setSlotName(payload.slot_name)
    if (typeof payload.image === 'string') setImage(payload.image)
    if (typeof payload.state === 'string') setState(payload.state as ImagePullState)
    if (typeof payload.layer === 'number') setLayer(payload.layer)
    if (typeof payload.total_layers === 'number') setTotalLayers(payload.total_layers)
    if (payload.error) setError(String(payload.error))
    if (typeof payload.state === 'string' && IMAGE_PULL_TERMINAL.has(payload.state as ImagePullState)) {
      closeStream()
      // Invalidate slots so image_status refreshes on the card.
      qc.invalidateQueries({ queryKey: ['slots'] })
    }
  }

  const attachStream = (name: string) => {
    closeStream()
    try {
      esRef.current = new EventSource(ENDPOINTS.slotPullStream(name))
    } catch (e: any) {
      setError(e?.message ?? 'EventSource failed')
      setState('failed')
      return
    }
    const es = esRef.current
    es.onmessage = (evt: MessageEvent) => {
      try { applyPayload(JSON.parse(evt.data)) } catch { /* skip */ }
    }
    es.onerror = () => {
      setState('failed')
      setError('stream error')
      closeStream()
    }
  }

  const start = async (name: string) => {
    setSlotName(name)
    setState('pulling')
    setLayer(0)
    setTotalLayers(0)
    setError(null)
    try {
      const resp = await api<any>(ENDPOINTS.slotPull(name), { method: 'POST', raw: true })
      if (typeof resp?.image === 'string') setImage(resp.image)
    } catch (e: any) {
      setState('failed')
      setError(e?.message ?? 'pull start failed')
      return
    }
    attachStream(name)
  }

  const reset = () => {
    closeStream()
    setSlotName(null)
    setImage(null)
    setState('idle')
    setLayer(0)
    setTotalLayers(0)
    setError(null)
  }

  return {
    slotName,
    image,
    state,
    layer,
    totalLayers,
    error,
    inFlight: state === 'pulling',
    start,
    reset,
  }
}
