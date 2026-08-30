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
import { apiGet, apiPost, apiPut, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

// Which family default (if any) resolves to this row's image, and where that
// default comes from: the baked release constant or the operator's
// [slots].default_images override (runner-catalogue-v2 contract).
export interface RunnerImageDefault {
  family: string
  source: 'override' | 'release'
}

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
  // runner-catalogue-v2 enrichment (backend PR feat/runner-catalogue-backend).
  // Optional so rows from a pre-contract backend still type-check; the view
  // helpers degrade gracefully when they're absent.
  available_tags?: string[]
  is_default?: RunnerImageDefault | null
  in_use_by?: string[]
  // runner-catalogue-v3 enrichment: per-tag digest/store facts
  // (tags[].{tag,digest,size_bytes,last_seen,downloaded}), validated/
  // candidate/deprecated badges keyed by tag, and store-truth fields
  // mirroring what StoreStateChip / tagLanes() in runner-images.jsx read.
  // Optional for the same pre-contract-backend reason as above — type-only,
  // no behavior change.
  tags?: Array<{
    tag: string
    digest: string | null
    size_bytes: number | null
    last_seen: string | null
    downloaded: boolean | null
  }>
  badges?: Record<string, string>
  store_state?: 'present' | 'missing' | 'unknown'
  downloaded?: boolean
  store_context?: 'rootful' | 'rootless' | null
}

const RUNNER_IMAGES_POLL_MS = 30_000

// Launch-truth per-family summary (runner-image-catalogue v3, Task 9's
// GET /api/runner-images `families` entry): the effective ref a family
// resolves to right now, its source tier, the store state of that ref,
// which slots launch it (`slots`) vs. a different tag of the same repo
// (`pinned_slots`), and whether a newer release-shaped tag is catalogued.
export interface RunnerImageFamily {
  family: string
  effective_ref: string
  source: 'override' | 'env' | 'manifest' | 'release'
  store_state: 'present' | 'missing' | 'unknown'
  slots: string[]
  pinned_slots: string[]
  newest_release: { tag: string; digest: string } | null
  update_available: boolean
}

export interface RunnerImagesList {
  images: RunnerImage[]
  families: RunnerImageFamily[]
}

export function useRunnerImages() {
  return useQuery({
    queryKey: ['runner-images'],
    queryFn: async () => {
      const body = await apiGet<{ images: RunnerImage[]; families: RunnerImageFamily[] }>(
        ENDPOINTS.runnerImages,
      )
      return {
        images: Array.isArray(body?.images) ? body.images : [],
        families: Array.isArray(body?.families) ? body.families : [],
      } satisfies RunnerImagesList
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

// ─── useSetDefaultImage ──────────────────────────────────────────────
// Writes (or clears) a per-family operator default via the existing
// PUT /api/settings deep-merge: {"slots": {"default_images": {family: ref}}}.
// Clearing sends `ref: null` — the settings route treats an explicit null as
// key removal (Task C's verified deep-merge semantics). Invalidate both the
// settings cache and the runner-image rows: `is_default` is computed
// server-side from the effective default map.
export function useSetDefaultImage() {
  const qc = useQueryClient()
  return useMutation<unknown, Hal0Error, { family: string; ref: string | null }>({
    mutationFn: ({ family, ref }) =>
      apiPut(ENDPOINTS.settings, { slots: { default_images: { [family]: ref } } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['runner-images'] })
    },
  })
}

// ─── useRestartAffected ────────────────────────────────────────────
// #2096 page-side workaround (Task 12): one POST that restarts every slot
// whose launched image ref equals `ref` — the same restart service call
// `POST /api/slots/{name}/restart` makes, just batched over the affected
// slots so the operator doesn't have to visit each one after rolling a
// family default. Invalidates both `slots` (the dashboard's slot list)
// and `runner-images` (in_use_by/families are launch-truth, and a restart
// changes what's launched).
export interface RestartAffectedResult {
  restarted: string[]
}

export function useRestartAffected() {
  const qc = useQueryClient()
  return useMutation<RestartAffectedResult, Hal0Error, { ref: string }>({
    mutationFn: ({ ref }) =>
      apiPost<RestartAffectedResult>(ENDPOINTS.runnerImagesRestartAffected, { ref }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['slots'] })
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
  start: (id: string, tag?: string) => Promise<unknown>
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
  // Generation counter: bumped whenever the "current job" changes identity
  // (reset/start a new pull, or cancel the running one). The onerror
  // reconcile GET below is async and can resolve after the job it was
  // launched for is no longer current — a late "running" snapshot must not
  // resurrect a cancelled job, and a stale job's late reconcile must not
  // clobber a newer job's state (#2120 fix-round-1). Same epoch/abort-guard
  // idiom as useActivity.ts's epochRef.
  const genRef = useRef(0)
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
    const gen = genRef.current
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
    // A dropped connection (network blip, proxy timeout, server crash mid-pull)
    // fires `error`, not a terminal SSE payload — without this the hook's last
    // known state (often "running") sticks forever and the UI shows
    // "running — 0/? layers" with no way out (#2120). Stream death is not an
    // answer: close the stream, then ask the status route once so a
    // server-side terminal state (failed/completed/cancelled) still lands.
    // `id` is the value `attachStream` was called with, not the (possibly
    // stale, by the time this fires) `imageId` React state.
    esRef.current.onerror = () => {
      closeStream()
      ;(async () => {
        try {
          const status = await apiGet<any>(ENDPOINTS.runnerImagePullStatus(id))
          // The job this reconcile was launched for may no longer be current
          // (cancelled, reset, or superseded by a new start() while the GET
          // was in flight) — a late response must not resurrect or clobber.
          if (genRef.current !== gen) return
          if (status && typeof status === 'object') applyPayload(status)
        } catch {
          /* keep last known state; the pulls-list poll will refresh */
        }
      })()
    }
  }

  const reset = () => {
    genRef.current += 1
    closeStream()
    setImageId(null)
    setJobId(null)
    setState('idle')
    setLayersDone(0)
    setLayersTotal(0)
    setLine(null)
    setError(null)
  }

  const start: RunnerPullSnapshot['start'] = async (id, tag) => {
    reset()
    setImageId(id)
    setState('queued')
    try {
      const url = ENDPOINTS.runnerImagePull(id) + (tag ? `?tag=${encodeURIComponent(tag)}` : '')
      const res = await apiPost<any>(url)
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
      genRef.current += 1
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
