// hal0 v3 dashboard — updates hooks (Phase B1).
//
// Backs the Settings → Updates surface. `/api/updates/state` returns
// per-channel current + available versions; `/api/updates/check` re-
// probes; `/api/updates/apply` kicks off self-update.

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface UpdateChannel {
  current: string
  available?: string | null
  channel?: string
  pinned?: boolean
  source?: string
}

export interface UpdateState {
  hal0: UpdateChannel
  lemonade: UpdateChannel
  flm?: UpdateChannel
  autoCheck: boolean
}

export type UpdateJobState = 'queued' | 'running' | 'applied' | 'failed'

export interface UpdateJob {
  id: string
  state: UpdateJobState
  channel: string
  version: string | null
  created_at: number
  updated_at: number
  error: string | null
  error_code?: string | null
}

export function useUpdateState() {
  return useQuery({
    queryKey: ['updates', 'state'],
    queryFn: () => apiGet<UpdateState>(ENDPOINTS.updateState),
  })
}

// /api/updates/check is GET-only on the backend; the channel comes from
// server-side state, not a body. Expose a refetch-and-invalidate helper
// instead of a useMutation that would 405.
export function useUpdateCheck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiGet(ENDPOINTS.updateCheck),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['updates'] }),
  })
}

// Apply accepts an optional pinned version; channel is implicit. Returns
// the queued-job snapshot — callers should hand the `id` to useUpdateJob
// to track terminal state.
export function useUpdateApply() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (version?: string) =>
      apiPost<UpdateJob>(ENDPOINTS.updateApply, version ? { version } : {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['updates'] }),
  })
}

// Poll an apply job until it lands in a terminal state. Polling stops on
// applied/failed, on null jobId, or on unmount. Returns the latest job
// snapshot plus a terminal flag so callers can fire toasts once.
export function useUpdateJob(jobId: string | null): {
  job: UpdateJob | null
  terminal: boolean
} {
  const [job, setJob] = useState<UpdateJob | null>(null)
  const [terminal, setTerminal] = useState(false)

  useEffect(() => {
    if (!jobId) {
      setJob(null)
      setTerminal(false)
      return
    }
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = async () => {
      try {
        const snap = await apiGet<UpdateJob>(ENDPOINTS.updateStatus(jobId))
        if (cancelled) return
        setJob(snap)
        if (snap.state === 'applied' || snap.state === 'failed') {
          setTerminal(true)
          return
        }
      } catch {
        // Transient errors during a self-update are expected (hal0-api
        // is restarting). Keep polling — once the API comes back the
        // job entry will resolve to a terminal state. Worst case the
        // server lost the in-memory job map; the timeout below caps the
        // poll loop.
      }
      if (!cancelled) timer = setTimeout(tick, 1500)
    }
    tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [jobId])

  return { job, terminal }
}
