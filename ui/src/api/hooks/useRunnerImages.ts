// hal0 v3 dashboard — runner-image catalogue hooks (feat/runner-image-catalogue).
//
// Mirrors useModels.ts's shape:
//   useRunnerImages()        — GET /api/runner-images (list)
//   useRunnerImage(id)       — GET /api/runner-images/{id} (card detail)
//   useRunnerImageSync()     — POST /api/runner-images/sync ("sync now" button)
//   useRunnerImagePullJob()  — start/status/stream/cancel a download, SSE-driven
//                               (same lifecycle shape as usePullJob(), progress
//                               is layers_done/layers_total instead of bytes)
//   useRunnerImagePullsList()— GET /api/runner-images/pulls/list, for a
//                               downloads pane

import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface RunnerImage {
  id: string
  image: string
  tag: string
  digest: string | null
  size_bytes: number | null
  manifest_key: string | null
  ownership: 'owned' | 'referenced' | string | null
  publish: 'ci' | 'external' | 'manual' | string | null
  notes: string | null
  build: Record<string, unknown> | null
  local_path: string | null
  downloaded_at: string | null
  discovered_at: string | null
  updated_at: string | null
  extra: Record<string, unknown>
}

const RUNNER_IMAGES_POLL_MS = 30_000

export function useRunnerImages() {
  return useQuery({
    queryKey: ['runner-images'],
    queryFn: async () => {
      const body = await apiGet<{ images: RunnerImage[] }>(ENDPOINTS.runnerImages)
      return Array.isArray(body?.images) ? body.images : []
    },
    refetchInterval: RUNNER_IMAGES_POLL_MS,
  })
}

export function useDownloadedRunnerImages() {
  return useQuery({
    queryKey: ['runner-images', 'downloaded'],
    queryFn: async () => {
      const body = await apiGet<{ images: RunnerImage[] }>(ENDPOINTS.runnerImagesDownloaded)
      return Array.isArray(body?.images) ? body.images : []
    },
  })
}

export function useRunnerImage(id: string | null | undefined) {
  return useQuery({
    queryKey: ['runner-images', id],
    queryFn: () => apiGet<RunnerImage>(ENDPOINTS.runnerImage(id as string)),
    enabled: !!id,
  })
}

export interface RunnerImageSyncResult {
  images: RunnerImage[]
  images_json_ok: boolean
  images_json_error: string | null
  probe_errors: Record<string, string>
}

export function useRunnerImageSync() {
  const qc = useQueryClient()
  return useMutation<RunnerImageSyncResult, Hal0Error, void>({
    mutationFn: () => apiPost<RunnerImageSyncResult>(ENDPOINTS.runnerImagesSync),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['runner-images'] })
    },
  })
}

// ─── useRunnerImagePullJob ───────────────────────────────────────────

export type RunnerPullState = 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

const TERMINAL = new Set<RunnerPullState>(['completed', 'failed', 'cancelled'])

export interface RunnerPullSnapshot {
  imageId: string | null
  jobId: string | null
  state: RunnerPullState
  layersDone: number
  layersTotal: number
  line: string | null
  error: { code: string; message: string; details?: Record<string, unknown> } | null
  pct: number | null
  inFlight: boolean
  terminal: boolean
  start: (id: string) => Promise<unknown>
  cancel: () => Promise<void>
  reset: () => void
  reattach: (id: string) => Promise<void>
}

/**
 * Runner-image pull-job hook. Owns one EventSource, same shape as
 * usePullJob() in useModels.ts — progress here is layer counts
 * (layers_done/layers_total) rather than bytes, since the download is a
 * whole OCI image pulled via podman, not a single HTTP file stream.
 */
export function useRunnerImagePullJob(): RunnerPullSnapshot {
  const [imageId, setImageId] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [state, setState] = useState<RunnerPullState>('idle')
  const [layersDone, setLayersDone] = useState(0)
  const [layersTotal, setLayersTotal] = useState(0)
  const [line, setLine] = useState<string | null>(null)
  const [error, setError] = useState<RunnerPullSnapshot['error']>(null)
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
    if (typeof payload.layers_done === 'number') setLayersDone(payload.layers_done)
    if (typeof payload.layers_total === 'number') setLayersTotal(payload.layers_total)
    if (typeof payload.line === 'string') setLine(payload.line)
    if (payload.error) {
      setError({ code: payload.error_code || 'runner_image.pull_failed', message: payload.error, details: {} })
    }
    if (typeof payload.state === 'string' && TERMINAL.has(payload.state)) {
      closeStream()
      qc.invalidateQueries({ queryKey: ['runner-images'] })
    }
  }

  const attachStream = (id: string) => {
    closeStream()
    try {
      esRef.current = new EventSource(ENDPOINTS.runnerImagePullStream(id))
    } catch (e: any) {
      setError({ code: 'system.unknown', message: e?.message ?? 'EventSource failed' })
      return
    }
    esRef.current.onmessage = (evt: MessageEvent) => {
      try {
        applyPayload(JSON.parse(evt.data))
      } catch {
        /* skip malformed */
      }
    }
  }

  const reset = () => {
    closeStream()
    setImageId(null)
    setJobId(null)
    setState('idle')
    setLayersDone(0)
    setLayersTotal(0)
    setLine(null)
    setError(null)
  }

  const start: RunnerPullSnapshot['start'] = async (id) => {
    reset()
    setImageId(id)
    setState('queued')
    try {
      const res = await apiPost<any>(ENDPOINTS.runnerImagePull(id))
      setJobId(res?.id ?? null)
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
    if (!imageId || !(state === 'queued' || state === 'running')) return
    try {
      await apiPost(ENDPOINTS.runnerImagePullCancel(imageId))
      setState('cancelled')
      closeStream()
      qc.invalidateQueries({ queryKey: ['runner-images'] })
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
      const status = await apiGet<any>(ENDPOINTS.runnerImagePullStatus(id))
      if (!status || typeof status !== 'object') return
      setImageId(id)
      applyPayload(status)
      if (status.state === 'queued' || status.state === 'running') attachStream(id)
    } catch {
      // best-effort; swallow (mirrors usePullJob's reattach)
    }
  }

  const pct = useMemo(() => {
    if (!layersTotal) return null
    return Math.min(100, Math.round((layersDone / layersTotal) * 100))
  }, [layersDone, layersTotal])

  return {
    imageId,
    jobId,
    state,
    layersDone,
    layersTotal,
    line,
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

export interface RunnerImagePullJobRow {
  id: string
  image_id: string
  image_ref: string
  state: RunnerPullState
  layers_done: number
  layers_total: number
  line: string | null
  error: string | null
  error_code: string | null
  started_at: number | null
  finished_at: number | null
  local_path: string | null
}

export function useRunnerImagePullsList({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery<RunnerImagePullJobRow[]>({
    queryKey: ['runner-images', 'pulls'],
    queryFn: () => apiGet<RunnerImagePullJobRow[]>(ENDPOINTS.runnerImagesPulls),
    enabled,
    refetchInterval: enabled ? 2_000 : false,
  })
}
