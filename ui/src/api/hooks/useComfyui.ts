// hal0 v3 dashboard — ComfyUI generation-engine hooks.
//
// /api/comfyui/status is a read-only aggregate (docker container state +
// systemd state of the LLM stack + ComfyUI's own /system_stats + /queue). The
// pane on the slots-page Image-Gen tab polls it; renders take minutes so a
// 4s cadence is plenty to track queue depth + memory pressure without hammering
// the cpp-httplib server behind :8188.
//
// The switchover (inference ⇄ generation) flips the single iGPU between the LLM
// stack and ComfyUI by running root-owned scripts. That path is feature-gated
// server-side (HAL0_COMFYUI_SWITCHOVER_ENABLED) and returns 501 until a scoped
// privileged path is provisioned — so the mutation surfaces that refusal as a
// toast rather than optimistically flipping the UI.

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

export interface ComfyuiStatus {
  mode: ComfyuiMode
  reachable: boolean
  engine: ComfyuiEngineState
  container: { name: string; state: string }
  endpoint: string | null
  memory: ComfyuiMemory | null
  queue: { running: number; pending: number }
  inference: { lemonade: boolean; hermes: boolean }
  inventory: Record<string, number> | null
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
  inference: { lemonade: false, hermes: false },
  inventory: null,
}

// Active (Image-Gen tab open): 4s, fast enough to track queue + pressure.
// Idle (other tab): 20s — keeps the tab's live dot honest without spending a
// docker inspect + 2× systemctl + 2× HTTP probe every few seconds. The proxy
// it mirrors (lemonade_proxy) had to add caching precisely because per-tab
// polling starved an embedded HTTP server; this is the cheap equivalent guard.
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
}

// raw:true so the dev mockFetch GET-shim can't mask the 501/503 gate — we want
// the real status code to drive the toast copy.
export function useComfyuiSwitchover() {
  return useMutation({
    mutationFn: (body: SwitchoverBody) =>
      api<unknown>(ENDPOINTS.comfyuiSwitchover, { method: 'POST', body: body as any, raw: true }),
  })
}
