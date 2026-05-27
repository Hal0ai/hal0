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
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
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
