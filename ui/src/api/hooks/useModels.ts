// hal0 v3 dashboard — models + pull-job hooks (Phase B1).
//
// Ported from ui-vue.bak/src/composables/usePullJob.js. Pull lifecycle:
//   POST /api/models/{id}/pull       — start
//   GET  /api/models/{id}/pull/status — resume after refresh
//   GET  /api/models/{id}/pull/stream — SSE: progress / completed / failed
//   POST /api/models/{id}/pull/cancel — cancel
//
// `usePullJob(id)` is hook-shaped (state + actions) — mirrors the v2
// composable so dash/models.jsx + dash/firstrun.jsx can swap in one
// line per download row.

import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPost, apiPut, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'
import { normalizeApiModel } from '@/lib/normalizeApiModel'

export interface Model {
  id: string
  longName: string
  repo: string
  params: string
  size: string
  labels: string[]
  type: string
  device: string
  ns: 'blessed' | 'pulled' | string
  installed: boolean
  runtime: string
  /** True when this model is its dispatcher type's default (per-type marker). */
  default?: boolean
}

const MODELS_POLL_MS = 30_000

export function useModels() {
  return useQuery({
    queryKey: ['models'],
    queryFn: async () => {
      const body = await apiGet<any>(ENDPOINTS.models)
      const rows = Array.isArray(body) ? body : Array.isArray(body?.models) ? body.models : []
      return rows.map(normalizeApiModel) as unknown as Model[]
    },
    refetchInterval: MODELS_POLL_MS,
  })
}

export function useModel(id: string | null | undefined) {
  return useQuery({
    queryKey: ['models', id],
    queryFn: () => apiGet<Model>(ENDPOINTS.model(id as string)),
    enabled: !!id,
  })
}

export interface ModelInspectVariant {
  id: string
  size_bytes: number
  size: string
  info: string
}

export interface ModelInspectResponse {
  repo: string
  cached: boolean
  variants: ModelInspectVariant[]
  tags: string[]
  metadata: {
    license: string
    readme_excerpt: string
  }
}

// ─── Scan + add-from-path (PR feat/models-scan-and-add-by-path) ─────

export interface ScanPreviewRow {
  path: string
  resolved_path: string
  size_bytes: number
  suggested_backends: string[]
  suggested_capabilities: string[]
  context_length: number | null
  confidence: 'high' | 'medium' | 'low' | string
  suggested_name: string
  kind: string
  raw_hints: Record<string, unknown>
}

export interface ScanPreviewResponse {
  preview: ScanPreviewRow[]
  count: number
}

export interface ScanPreviewRequest {
  paths: string[]
  recursive?: boolean
}

export function useScanPreview() {
  // POST a path + optional recursive flag → list of detection rows.
  // No registry mutation; the dashboard renders the list and the
  // operator picks which ones to add via useAddModelFromPath.
  return useMutation<ScanPreviewResponse, Hal0Error, ScanPreviewRequest>({
    mutationFn: (body) =>
      apiPost<ScanPreviewResponse>(ENDPOINTS.modelScanPreview, body as unknown as Record<string, unknown>),
  })
}

export interface AddFromPathRequest {
  path: string
  id?: string
  name?: string
  labels?: string[]
  overwrite?: boolean
}

export function useAddModelFromPath() {
  // Single-file convenience register — POST {path,...} and the backend
  // detects + writes a registry row. Invalidates models so the Models
  // page reflects the new entry within a render.
  const qc = useQueryClient()
  return useMutation<Model, Hal0Error, AddFromPathRequest>({
    mutationFn: (body) =>
      apiPost<Model>(ENDPOINTS.modelAddFromPath, body as unknown as Record<string, unknown>),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
  })
}

export function useModelInspect() {
  // POST a HF coord and get back the repo's pullable GGUF variants
  // plus tags + license + a short README excerpt. Accepts either an
  // ``hf_repo`` slug or the older ``hf_url`` alias.
  return useMutation<ModelInspectResponse, Hal0Error, { hf_repo?: string; hf_url?: string }>({
    mutationFn: (body) => apiPost<ModelInspectResponse>(ENDPOINTS.modelInspect, body),
  })
}

// ─── HF Hub free-text search (issue #311) ─────────────────────────────

export interface HfSearchResult {
  id: string
  downloads: number
  likes: number
  gated: boolean | string
  pipeline_tag: string
  library: string
  last_modified: string
}

export interface HfSearchResponse {
  results: HfSearchResult[]
  cached?: boolean
}

export function useHfSearch(q: string, type?: string | null) {
  // GET /api/hf/search?q=…&type=… — debounced upstream of the
  // dashboard "Search HF" panel. Disabled when ``q`` is empty so the
  // backend's cheap-empty path runs (no upstream hit). Same polling
  // rhythm as useModels so the search panel reuses the React Query
  // cache instead of firing per-keystroke.
  return useQuery<HfSearchResponse, Hal0Error>({
    queryKey: ['hf-search', q, type ?? ''],
    queryFn: () => {
      const params = new URLSearchParams({ q })
      if (type) params.set('type', type)
      return apiGet<HfSearchResponse>(`${ENDPOINTS.hfSearch}?${params.toString()}`)
    },
    enabled: q.trim().length > 0,
    staleTime: 30_000,
  })
}

export interface ModelDeleteResponse {
  id: string
  deleted: boolean
  affected_slots: string[]
}

export function useModelDelete() {
  const qc = useQueryClient()
  return useMutation<ModelDeleteResponse, Hal0Error, string>({
    mutationFn: (id: string) => apiDelete<ModelDeleteResponse>(ENDPOINTS.model(id)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
  })
}

export function useModelUpdate() {
  // Partial update — PUT /api/models/{id} with any subset of
  // ``name | capabilities | backends | defaults``. The dashboard's
  // Recipe editor uses this for the per-model defaults section.
  const qc = useQueryClient()
  return useMutation<Model, Hal0Error, { id: string; body: Record<string, unknown> }>({
    mutationFn: ({ id, body }) =>
      apiPut<Model>(ENDPOINTS.model(id), body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['models'] })
      // Model defaults feed the slot argv assembly (merge_flags), so any
      // open slot drawer's resolved command goes stale on a model save —
      // refetch the slot list + every slot-resolved query too.
      qc.invalidateQueries({ queryKey: ['slots'] })
      qc.invalidateQueries({ queryKey: ['slot-resolved'] })
    },
  })
}

export interface ModelDefaultResult {
  model_id: string
  type: string
  default: boolean
  demoted: string[]
  changed: boolean
}

export function useModelSetDefault() {
  // POST /api/models/{id}/default — promote (default:true, demoting the current
  // holder of this type) or clear (default:false) a model's per-type default.
  // The server enforces the single-holder invariant; we just invalidate the
  // catalog so every row's badge reflects the new holder on the next render.
  const qc = useQueryClient()
  return useMutation<ModelDefaultResult, Hal0Error, { id: string; default: boolean }>({
    mutationFn: ({ id, default: isDefault }) =>
      apiPost<ModelDefaultResult>(ENDPOINTS.modelSetDefault(id), { default: isDefault }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
  })
}

// ─── Duplicate (UI-API-1, models.py:674 `duplicate_model`) ───────────────

export interface ModelDuplicateRequest {
  /** Source model id — the row whose weights/metadata get copied. */
  id: string
  /** New registry id. Required; must differ from `id`. */
  new_id: string
  /** Optional profile whose flags get stamped into the new row's defaults. */
  profile?: string
}

export interface ModelDuplicateResponse extends Model {
  duplicated_from: string
  files_refcounted: number
}

export function useModelDuplicate() {
  // POST /api/models/{id}/duplicate — refcounted weight-sharing duplicate
  // (no byte copy); optionally stamps a profile's flags into the new row.
  // endpoints.ts has no `modelDuplicate` const yet (flagged for the
  // CONTRACTS lane) — built inline off the existing `model(id)` helper,
  // same route family as useModelDelete/useModelUpdate above.
  const qc = useQueryClient()
  return useMutation<ModelDuplicateResponse, Hal0Error, ModelDuplicateRequest>({
    mutationFn: ({ id, new_id, profile }) =>
      apiPost<ModelDuplicateResponse>(
        `${ENDPOINTS.model(id)}/duplicate`,
        profile ? { new_id, profile } : { new_id },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
  })
}

// ─── HF update check + in-place update ────────────────────────────────

export interface ModelUpdateVerdict {
  hf_repo: string
  hf_filename: string
  local_sha256: string | null
  remote_sha256: string | null
  update_available: boolean
  reason: string | null
}

export interface ModelUpdatesCheckResponse {
  checked_at: number
  checked: number
  updates_available: number
  models: Record<string, ModelUpdateVerdict>
}

const MODEL_UPDATES_POLL_MS = 30 * 60_000

export function useModelUpdatesCheck() {
  // GET /api/models/updates/check — the server probes HF (one tree fetch
  // per unique repo, TTL-cached an hour) and stashes the snapshot that
  // /api/models merges per-row `update_available` flags from. Invalidate
  // the catalog after each check so badges appear on the next render
  // instead of waiting out the 30s models poll.
  const qc = useQueryClient()
  return useQuery<ModelUpdatesCheckResponse, Hal0Error>({
    queryKey: ['model-updates'],
    queryFn: async () => {
      const res = await apiGet<ModelUpdatesCheckResponse>(ENDPOINTS.modelUpdatesCheck)
      qc.invalidateQueries({ queryKey: ['models'] })
      return res
    },
    staleTime: MODEL_UPDATES_POLL_MS,
    refetchInterval: MODEL_UPDATES_POLL_MS,
  })
}

export function useModelUpdatesForceCheck() {
  // GET /api/models/updates/check?refresh=1 — bypass the server's TTL
  // cache. Backs the Models page's explicit "Check updates" affordance:
  // the fresh snapshot is written into ['model-updates'] and the catalog
  // is refetched so row badges flip immediately.
  const qc = useQueryClient()
  return useMutation<ModelUpdatesCheckResponse, Hal0Error, void>({
    mutationFn: () =>
      apiGet<ModelUpdatesCheckResponse>(`${ENDPOINTS.modelUpdatesCheck}?refresh=1`),
    onSuccess: (res) => {
      qc.setQueryData(['model-updates'], res)
      qc.invalidateQueries({ queryKey: ['models'] })
    },
  })
}

export function useModelUpdateApply() {
  // POST /api/models/{id}/update — re-pull the row's HF file over its
  // installed path. Progress reports through the standard pull surface
  // (usePullsList / DownloadsPane pick the job up via the started event).
  // NOT the same as useModelUpdate above, which is the PUT metadata edit.
  const qc = useQueryClient()
  return useMutation<unknown, Hal0Error, string>({
    mutationFn: (id: string) => apiPost(ENDPOINTS.modelUpdate(id)),
    onSuccess: (_res, id) => {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('hal0:pull-started', { detail: { modelId: id } }))
      }
      qc.invalidateQueries({ queryKey: ['pulls'] })
    },
  })
}

export interface ModelUpdateAllResult {
  started: string[]
  failed: { id: string; message: string }[]
}

export function useModelUpdateAll() {
  // Fan a POST /update out to every id. Failures are collected, not
  // thrown — one gated repo must not abort the rest of the batch.
  const qc = useQueryClient()
  return useMutation<ModelUpdateAllResult, Hal0Error, string[]>({
    mutationFn: async (ids: string[]) => {
      const settled = await Promise.allSettled(ids.map((id) => apiPost(ENDPOINTS.modelUpdate(id))))
      const started: string[] = []
      const failed: { id: string; message: string }[] = []
      settled.forEach((r, i) => {
        if (r.status === 'fulfilled') started.push(ids[i])
        else failed.push({ id: ids[i], message: (r.reason as Error)?.message ?? String(r.reason) })
      })
      return { started, failed }
    },
    onSuccess: () => {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('hal0:pull-started', { detail: {} }))
      }
      qc.invalidateQueries({ queryKey: ['pulls'] })
    },
  })
}

// ─── usePullJob ─────────────────────────────────────────────────────

export type PullState =
  | 'idle'
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

const TERMINAL = new Set<PullState>(['completed', 'failed', 'cancelled'])

export interface PullSnapshot {
  modelId: string | null
  jobId: string | null
  state: PullState
  downloaded: number
  total: number
  speedBps: number
  etaS: number
  error: { code: string; message: string; details?: Record<string, unknown> } | null
  pct: number | null
  inFlight: boolean
  terminal: boolean
  start: (id: string, body?: Record<string, unknown>) => Promise<unknown>
  cancel: () => Promise<void>
  reset: () => void
  reattach: (id: string) => Promise<void>
}

/**
 * Pull-job composable. Owns one EventSource. The caller passes nothing —
 * `start(id)` initialises the modelId, opens the stream, and updates
 * local state from `progress | completed | failed | cancelled` events.
 */
export function usePullJob(): PullSnapshot {
  const [modelId, setModelId] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [state, setState] = useState<PullState>('idle')
  const [downloaded, setDownloaded] = useState(0)
  const [total, setTotal] = useState(0)
  const [speedBps, setSpeedBps] = useState(0)
  const [etaS, setEtaS] = useState(0)
  const [error, setError] = useState<PullSnapshot['error']>(null)
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
    if (typeof payload.state === 'string') setState(payload.state)
    const dl = payload.bytes_downloaded ?? payload.downloaded
    const tot = payload.bytes_total ?? payload.total
    if (typeof dl === 'number') setDownloaded(dl)
    if (typeof tot === 'number') setTotal(tot)
    if (typeof payload.speed_bps === 'number') setSpeedBps(payload.speed_bps)
    if (typeof payload.eta_s === 'number') setEtaS(payload.eta_s)
    if (payload.error) {
      setError(
        typeof payload.error === 'string'
          ? { code: 'pull.failed', message: payload.error, details: {} }
          : payload.error,
      )
    }
    if (typeof payload.state === 'string' && TERMINAL.has(payload.state)) {
      closeStream()
      qc.invalidateQueries({ queryKey: ['models'] })
      // Broadcast terminal state so route-independent listeners (e.g. the
      // command palette's "Cancel download" affordance) can stop offering
      // to cancel a pull that has finished.
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('hal0:pull-ended', { detail: { modelId } }))
      }
    }
  }

  const attachStream = (id: string) => {
    closeStream()
    try {
      esRef.current = new EventSource(ENDPOINTS.modelPullStream(id))
    } catch (e: any) {
      setError({ code: 'system.unknown', message: e?.message ?? 'EventSource failed' })
      return
    }
    const es = esRef.current
    const onMsg = (evt: MessageEvent) => {
      try {
        applyPayload(JSON.parse(evt.data))
      } catch {
        /* skip malformed */
      }
    }
    es.addEventListener('progress', onMsg)
    es.addEventListener('completed', (e) => {
      applyPayload({ state: 'completed' })
      onMsg(e as MessageEvent)
    })
    es.addEventListener('failed', (e) => {
      applyPayload({ state: 'failed' })
      onMsg(e as MessageEvent)
    })
    es.addEventListener('cancelled', (e) => {
      applyPayload({ state: 'cancelled' })
      onMsg(e as MessageEvent)
    })
    es.onmessage = onMsg
  }

  const reset = () => {
    closeStream()
    setModelId(null)
    setJobId(null)
    setState('idle')
    setDownloaded(0)
    setTotal(0)
    setSpeedBps(0)
    setEtaS(0)
    setError(null)
  }

  const start: PullSnapshot['start'] = async (id, body) => {
    reset()
    setModelId(id)
    setState('queued')
    try {
      const res = await apiPost<any>(ENDPOINTS.modelPull(id), body)
      setJobId(res?.id ?? res?.job_id ?? null)
      // Dispatch pull-started event so the downloads pane can open / refresh
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('hal0:pull-started', { detail: { modelId: id } }))
      }
      attachStream(id)
      return res
    } catch (e) {
      setState('failed')
      if (e instanceof Hal0Error) {
        setError({ code: e.code, message: e.message, details: e.details })
      } else {
        const err = e as Error
        setError({ code: 'system.unknown', message: err?.message ?? String(e) })
      }
      throw e
    }
  }

  const cancel = async () => {
    if (!modelId || !(state === 'queued' || state === 'running')) return
    try {
      await apiPost(ENDPOINTS.modelPullCancel(modelId))
      setState('cancelled')
      closeStream()
      // Cancelling via the POST bypasses the SSE terminal path that
      // normally refetches the catalog, so invalidate here too — the
      // registry row may flip back to its pre-pull state.
      qc.invalidateQueries({ queryKey: ['models'] })
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('hal0:pull-ended', { detail: { modelId } }))
      }
    } catch (e) {
      if (e instanceof Hal0Error) {
        setError({ code: e.code, message: e.message, details: e.details })
      }
      throw e
    }
  }

  const reattach = async (id: string) => {
    if (!id) return
    try {
      const status = await apiGet<any>(ENDPOINTS.modelPullStatus(id))
      if (!status || typeof status !== 'object') return
      setModelId(id)
      applyPayload(status)
      if (status.state === 'queued' || status.state === 'running') attachStream(id)
    } catch (e) {
      if (!(e instanceof Hal0Error) || e.status !== 404) {
        // best-effort; swallow
      }
    }
  }

  const pct = useMemo(() => {
    if (!total) return null
    return Math.min(100, Math.round((downloaded / total) * 100))
  }, [downloaded, total])

  return {
    modelId,
    jobId,
    state,
    downloaded,
    total,
    speedBps,
    etaS,
    error,
    pct,
    inFlight: state === 'queued' || state === 'running',
    terminal: TERMINAL.has(state),
    start,
    cancel,
    reset,
    reattach,
  }
}

// ─── usePullsList ─────────────────────────────────────────────────

export interface PullJob {
  job_id: string
  model_id: string
  hf_repo: string | null
  dest_path: string | null
  state: PullState
  bytes_downloaded: number
  bytes_total: number
  speed_bps: number
  eta_s: number
  error: { code: string; message: string } | null
  started_at: number | null
  finished_at: number | null
}

export function usePullsList({ enabled = true }: { enabled?: boolean } = {}) {
  const qc = useQueryClient()

  // Listen for pull lifecycle events to force a refetch
  useEffect(() => {
    if (!enabled) return
    const invalidate = () => qc.invalidateQueries({ queryKey: ['pulls'] })
    window.addEventListener('hal0:pull-started', invalidate)
    window.addEventListener('hal0:pull-ended', invalidate)
    return () => {
      window.removeEventListener('hal0:pull-started', invalidate)
      window.removeEventListener('hal0:pull-ended', invalidate)
    }
  }, [enabled, qc])

  const query = useQuery<PullJob[]>({
    queryKey: ['pulls'],
    queryFn: () => apiGet<PullJob[]>(ENDPOINTS.modelPulls),
    enabled,
    refetchInterval: enabled ? 2_000 : false,
  })

  // Defensive: an unrouted mock (or an older backend) can answer `{}` —
  // never let a non-array shape throw inside every subscriber's render.
  const jobs = Array.isArray(query.data) ? query.data : []
  const hasActive = jobs.some((j: PullJob) => j.state === 'queued' || j.state === 'running')

  return { jobs, hasActive, ...query }
}

export function useClearPullJob() {
  const qc = useQueryClient()
  return useMutation<void, Hal0Error, string>({
    mutationFn: (id: string) => apiDelete(ENDPOINTS.modelPullDelete(id)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pulls'] }),
  })
}


export function fmtBytes(b: number) {
  if (!b || b < 0) return '—'
  if (b < 1024) return `${b} B`
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`
  return `${(b / 1024 ** 3).toFixed(2)} GB`
}

export function fmtSpeed(b: number) {
  if (!b || b <= 0) return '—'
  return `${fmtBytes(b)}/s`
}

export function fmtEta(s: number) {
  if (!s || s <= 0 || !isFinite(s)) return '—'
  if (s < 60) return `${Math.ceil(s)}s`
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  if (m < 60) return `${m}m ${sec}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}
