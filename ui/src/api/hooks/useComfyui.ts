// hal0 v3 dashboard — ComfyUI generation-engine hooks.
//
// /api/comfyui/status is a read-only aggregate (docker container state +
// systemd state of the LLM stack + ComfyUI's own /system_stats + /queue). The
// pane on the slots-page Image-Gen tab polls it; renders take minutes so a
// 4s cadence is plenty to track queue depth + memory pressure without hammering
// the cpp-httplib server behind :8188.
//
// The switchover (inference ⇄ generation) flips the single iGPU between the LLM
// stack and ComfyUI via the API's GPU arbiter (Phase D): switching to
// generation drains and STOPS the LLM slots, then starts the ComfyUI img slot;
// switching back restores the saved slots. The endpoint answers 202 and the
// arbiter runs the transition in-process; the `switchover` block on /status
// (active/target/error) is what tracks the transition to terminal — the pane's
// poll renders it, per the async-job-must-poll-to-terminal rule. Tearing down a
// non-empty queue needs `force: true` (the confirm dialog is that consent).
//
// `arbiter` is the arbiter-truth block ({mode img|llm, pinned, saved slots,
// idle_restore_at}); it is null when the arbiter is unavailable (gate off /
// older backend) and every consumer fails soft to the legacy display.

import { useMutation, useQuery, type UseQueryResult } from '@tanstack/react-query'
import { api, apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export type ComfyuiEngineState = 'stopped' | 'starting' | 'running' | 'generating' | 'error'
export type ComfyuiMode = 'generation' | 'inference'

export interface ComfyuiMemory {
  gtt_used_gb: number | null
  gtt_ceil_gb: number
  ram_used_gb: number | null
  ram_ceil_gb: number
  pressure: boolean
}

export interface ComfyuiSwitchover {
  active: boolean
  target: ComfyuiMode | null
  error: string | null
}

// GPU-arbiter truth block. `mode` is the arbiter's own vocabulary
// ('img' | 'llm', distinct from the legacy 'generation' | 'inference');
// `idle_restore_at` is an epoch (seconds) or null when pinned / not armed.
export interface ComfyuiArbiter {
  mode: string
  pinned: boolean
  saved_llm_slots: string[]
  idle_restore_at: number | null
}

export interface ComfyuiStatus {
  mode: ComfyuiMode
  reachable: boolean
  engine: ComfyuiEngineState
  container: { name: string; state: string }
  endpoint: string | null
  memory: ComfyuiMemory | null
  queue: { running: number; pending: number }
  util: number | null
  temp: number | null
  clock: number | null
  it_s: number | null
  eta: string | null
  step: number | null
  inference: { hermes: boolean }
  inventory: Record<string, number> | null
  switchover: ComfyuiSwitchover
  arbiter: ComfyuiArbiter | null
}

// Neutral default so the pane renders a coherent "stopped/inference" shell on
// first paint and whenever the backend is briefly unreachable — never undefined
// field access.
export const COMFYUI_FALLBACK: ComfyuiStatus = {
  mode: 'inference',
  reachable: false,
  engine: 'stopped',
  container: { name: 'comfyui', state: 'absent' },
  endpoint: null,
  memory: null,
  queue: { running: 0, pending: 0 },
  util: 0,
  temp: null,
  clock: null,
  it_s: null,
  eta: null,
  step: null,
  inference: { hermes: false },
  inventory: null,
  switchover: { active: false, target: null, error: null },
  arbiter: null,
}

// Active (Image-Gen tab open): 4s, fast enough to track queue + pressure.
// Idle (other tab): 20s — keeps the tab's live dot honest without spending a
// docker inspect + 2× systemctl + 2× HTTP probe every few seconds; per-tab
// polling can starve an embedded HTTP server, so this is the cheap guard.
const POLL_ACTIVE_MS = 4_000
const POLL_IDLE_MS = 20_000

export function useComfyui(opts: { active?: boolean } = {}): UseQueryResult<ComfyuiStatus> {
  return useQuery({
    queryKey: ['comfyui', 'status'],
    queryFn: () => apiGet<ComfyuiStatus>(ENDPOINTS.comfyuiStatus),
    refetchInterval: opts.active ? POLL_ACTIVE_MS : POLL_IDLE_MS,
  })
}

export interface SwitchoverBody {
  mode: ComfyuiMode
  // Required to tear down a non-empty render queue (jobs are dropped). The
  // confirm dialog's warning is the consent that sets this.
  force?: boolean
  // Optional: pin image mode as part of the switch (disables idle
  // auto-restore until unpinned).
  pin?: boolean
}

// raw:true so the dev mockFetch GET-shim can't mask 503 arbiter failures — we
// want the real status code to drive the toast copy.
export function useComfyuiSwitchover() {
  return useMutation({
    mutationFn: (body: SwitchoverBody) =>
      api<unknown>(ENDPOINTS.comfyuiSwitchover, { method: 'POST', body: body as any, raw: true }),
  })
}

// Pin / unpin image mode (disables / re-arms the arbiter's idle auto-restore).
// Synchronous 200 {"pinned":bool} — the caller refetches /status to reflect
// the new arbiter state. raw:true for the same failure handling as switchover.
export function useComfyuiPin() {
  return useMutation({
    mutationFn: (body: { pinned: boolean }) =>
      api<{ pinned: boolean }>(ENDPOINTS.comfyuiPin, {
        method: 'POST',
        body: body as any,
        raw: true,
      }),
  })
}

// Cancel current + queued renders (best-effort, 202).
export function useComfyuiRenderCancel() {
  return useMutation({
    mutationFn: () => api<{ status: string }>(ENDPOINTS.comfyuiRenderCancel, { method: 'POST' }),
  })
}

// Restart ComfyUI container (202, fire-and-forget).
export function useComfyuiRestart() {
  return useMutation({
    mutationFn: () => api<{ status: string }>(ENDPOINTS.comfyuiRestart, { method: 'POST' }),
  })
}

// Fetch container logs (tail=N, default 60).
export function useComfyuiLogs(tail = 60) {
  return useQuery({
    queryKey: ['comfyui', 'logs', tail],
    queryFn: () => apiGet<{ lines: string[] }>(`${ENDPOINTS.comfyuiLogs}?tail=${tail}`),
    enabled: false, // on-demand only
  })
}

// Quick-launch a named workflow (202).
export function useComfyuiWorkflowLaunch() {
  return useMutation({
    mutationFn: (name: string) =>
      api<{ status: string; prompt_id: string | null }>(
        ENDPOINTS.comfyuiWorkflowLaunch(name),
        { method: 'POST' },
      ),
  })
}

// ── V2 pane data transform ───────────────────────────────────────────────────
//
// The server /status payload has queue counts, memory, and device telemetry.
// Per-job detail (name, kind, node, progress) is not fully surfaced yet; it/s,
// ETA, and step are explicit nulls until a ComfyUI websocket subscription lands.
// The component renders placeholders for absent fields — never crashes on
// undefined.
//
// active_render and queue_jobs are optional extensions that may be injected
// by the window.__comfyuiV2MockOverride seam or a future richer backend.

export interface ComfyuiV2Run {
  name: string
  kind: string
  pct: number
  eta: string
  node: string
  step: number
  total: number
  its: number
  loaded: string
}

export interface ComfyuiV2QueueJob {
  name: string
  kind: string
}

export interface ComfyuiV2PaneData {
  engine: {
    name: string
    endpoint: string
    image: string
    restart: string
  }
  run: ComfyuiV2Run | null
  queue: ComfyuiV2QueueJob[]
  gtt: { used: number; ceil: number }
  ram: { used: number; ceil: number }
  stats: { util: number; temp: number; clk: number; its: number }
}

// Transform a live /status payload (+ optional V2 extension fields) into the
// component's data shape. All absent numeric fields get safe defaults so the
// pane renders coherently even when ComfyUI isn't running.
export function transformComfyuiStatus(
  status: ComfyuiStatus & {
    active_render?: ComfyuiV2Run | null
    queue_jobs?: ComfyuiV2QueueJob[]
  },
): ComfyuiV2PaneData {
  const mem = status.memory
  const hasRun = (status.queue?.running ?? 0) > 0

  // active_render is the rich per-job detail block — only present in the mock
  // or a future richer backend. Fall back to a placeholder skeleton when the
  // render count says something is running but the detail is absent.
  let run: ComfyuiV2Run | null = null
  if (status.active_render !== undefined) {
    run = status.active_render ?? null
  } else if (hasRun) {
    run = {
      name: 'rendering…',
      kind: '—',
      pct: 0,
      eta: '—',
      node: '—',
      step: 1,
      total: 1,
      its: 0,
      loaded: '—',
    }
  }

  // queue_jobs mirrors pending job list detail. Falls back to placeholder rows
  // equal to the pending count when detail is absent.
  let queue: ComfyuiV2QueueJob[]
  if (status.queue_jobs !== undefined) {
    queue = status.queue_jobs
  } else {
    const pending = status.queue?.pending ?? 0
    queue = Array.from({ length: pending }, (_, i) => ({ name: `job ${i + 1}`, kind: '—' }))
  }

  return {
    engine: {
      name: 'ComfyUI',
      endpoint: status.endpoint ?? ':8188',
      image: status.container?.name ?? 'comfyui',
      restart: 'no',
    },
    run,
    queue,
    gtt: {
      used: mem?.gtt_used_gb ?? 0,
      ceil: mem?.gtt_ceil_gb ?? 80,
    },
    ram: {
      used: mem?.ram_used_gb ?? 0,
      ceil: mem?.ram_ceil_gb ?? 96,
    },
    stats: {
      util: status.util ?? 0,
      temp: status.temp ?? 0,
      clk: status.clock ?? 0,
      its: status.it_s ?? 0,
    },
  }
}
