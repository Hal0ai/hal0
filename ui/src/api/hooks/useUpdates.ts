// hal0 v3 dashboard — updates hooks (Phase B1).
//
// Backs the Settings → Updates surface. `/api/updates/state` returns
// per-channel current + available versions; `/api/updates/check` re-
// probes; `/api/updates/apply` kicks off self-update.

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, apiPut } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface UpdateChannel {
  current: string
  available?: string | null
  channel?: string
  // #2128: set when HAL0_RELEASES_URL overrides the manifest source — the
  // `channel` field is reported but NOT consulted while this is present.
  releases_url_override?: string | null
  pinned?: boolean
  source?: string
  // Set when the latest release on this channel was yanked/withdrawn. When
  // true, `available` is null (a revoked release is never offered) and the
  // Settings → Updates surface can explain why with `revoked_reason`.
  revoked?: boolean
  revoked_reason?: string
  revoked_version?: string | null
}

export interface UpdateState {
  hal0: UpdateChannel
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

// Rollback to the retained previous version. Synchronous on the backend
// (no job id) — returns {rolled_back, channel, profile_conversion_restored}
// once the swap completes. `profile_conversion_restored` lists what the
// ADR-0029 profile conversion's backup put back (empty/absent when the
// rollback did not cross the conversion).
export interface UpdateRollbackResult {
  rolled_back: boolean
  channel: string
  profile_conversion_restored?: string[]
}

export function useUpdateRollback() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost<UpdateRollbackResult>(ENDPOINTS.updateRollback, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['updates'] }),
  })
}

// Channel switch (issue #546). PUT /api/updates/channel with {channel:
// "stable" | "preview" | "nightly"} persists to hal0.toml; on success the
// updates/state query is invalidated so the per-component channel fields
// (which the UI binds to for the current value) refetch the persisted channel
// before the next render.
export const UPDATE_CHANNEL_NAMES = ['stable', 'preview', 'nightly'] as const
export type UpdateChannelName = (typeof UPDATE_CHANNEL_NAMES)[number]

export function useSetUpdateChannel() {
  const qc = useQueryClient()
  return useMutation<{ channel: UpdateChannelName }, Error, UpdateChannelName>({
    mutationFn: (channel) =>
      apiPut<{ channel: UpdateChannelName }>(ENDPOINTS.updateChannel, { channel }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['updates'] }),
  })
}

// Poll an apply job until it lands in a terminal state. Polling stops on
// applied/failed, on null jobId, or on unmount. Returns the latest job
// snapshot plus a terminal flag so callers can fire toasts once.
// ── Post-update slot drift (WS-J, #1111) ────────────────────────────────────
//
// After a self-update the slot unit files are re-rendered on disk, but the
// running containers are NOT bounced (a restart could kill a mid-inference
// request). Drifted slots therefore keep serving the pre-update launch
// command until an operator opts into a restart. `useSlotDrift` backs the
// dashboard "N slots need restart" banner; `useRestartDriftedSlots` bounces
// only the drifted slots on demand.

export interface SlotDriftEntry {
  slot: string
  diffs: Array<{ key: string; running: string | null; rendered: string | null }>
}

export interface SlotDrift {
  count: number
  slots: SlotDriftEntry[]
}

export function useSlotDrift() {
  return useQuery({
    queryKey: ['updates', 'slot-drift'],
    queryFn: () => apiGet<SlotDrift>(ENDPOINTS.updateSlotDrift),
    // Cheap live probe (podman inspect per slot) — a slow refetch keeps the
    // banner honest after a background update without hammering the runtime.
    refetchInterval: 30_000,
  })
}

export interface RestartSlotsResult {
  restarted: string[]
  failed: Array<{ slot: string; error: string }>
  count: number
}

export function useRestartDriftedSlots() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (slots?: string[]) =>
      apiPost<RestartSlotsResult>(
        ENDPOINTS.updateRestartSlots,
        slots && slots.length ? { slots } : {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['updates', 'slot-drift'] })
      qc.invalidateQueries({ queryKey: ['slots'] })
    },
  })
}

export interface UseUpdateJobOptions {
  /**
   * A snapshot still `queued`/`running` whose `updated_at` is older than this
   * many milliseconds ends the poll as lost. Unset (the default) never ends a
   * poll on age: a long single step legitimately goes quiet, so only a caller
   * that knows its job's longest silence should set it.
   */
  staleAfterMs?: number
}

interface JobPoll {
  /** The job id this state belongs to. */
  id: string | null
  job: UpdateJob | null
  terminal: boolean
  lost: boolean
}

const IDLE_POLL: JobPoll = { id: null, job: null, terminal: false, lost: false }

/**
 * Poll `/api/updates/status/{jobId}` every 1.5 s until the job is `applied` or
 * `failed`. The loop also ends, with `terminal: true, lost: true`, when the
 * server no longer knows the job (a 404) or, with `staleAfterMs`, when the
 * last snapshot is a `queued`/`running` one that has gone quiet for longer
 * than that. Any other error keeps polling: hal0-api restarts during a
 * self-update and the job resolves once it is back. `job` stays the last
 * snapshot read, so a lost job still shows its last known state. What comes
 * back always belongs to `jobId`: a new id starts from nothing.
 */
export function useUpdateJob(
  jobId: string | null,
  opts?: UseUpdateJobOptions,
): {
  job: UpdateJob | null
  terminal: boolean
  lost: boolean
} {
  const [state, setState] = useState<JobPoll>(IDLE_POLL)
  const staleAfterMs = opts?.staleAfterMs

  useEffect(() => {
    setState(IDLE_POLL)
    if (!jobId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const patch = (p: Partial<JobPoll>) =>
      setState((prev) => ({ ...(prev.id === jobId ? prev : { ...IDLE_POLL, id: jobId }), ...p }))

    const tick = async () => {
      try {
        const snap = await apiGet<UpdateJob>(ENDPOINTS.updateStatus(jobId))
        if (cancelled) return
        if (snap.state === 'applied' || snap.state === 'failed') {
          patch({ job: snap, terminal: true })
          return
        }
        if (
          staleAfterMs !== undefined &&
          typeof snap.updated_at === 'number' &&
          Date.now() - snap.updated_at * 1000 > staleAfterMs
        ) {
          patch({ job: snap, terminal: true, lost: true })
          return
        }
        patch({ job: snap })
      } catch (err) {
        if (cancelled) return
        // The server answered and has no such job: nothing will ever resolve it.
        if ((err as { status?: number } | null)?.status === 404) {
          patch({ terminal: true, lost: true })
          return
        }
        // Anything else is transient (hal0-api restarting mid-update): keep
        // polling until the job resolves or the server says it is gone.
      }
      timer = setTimeout(tick, 1500)
    }
    tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [jobId, staleAfterMs])

  const cur = state.id === jobId ? state : IDLE_POLL
  return { job: cur.job, terminal: cur.terminal, lost: cur.lost }
}
