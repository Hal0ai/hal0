// hal0 v3 dashboard — backends hooks (Phase B1).
//
// Ported from ui-vue.bak/src/stores/backends.js. `/api/backends`
// envelope is `{backends: Backend[], lemonade: LemonadeSelf}`.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface Backend {
  id: string
  version: string
  state: 'installed' | 'unavailable' | 'updating' | string
  usedBy?: string[]
  recommended?: boolean
  note?: string
  kind?: string
  device?: string
}

export interface LemonadeSelf {
  version: string | null
  pinned: boolean | null
  sha?: string | null
  channel?: string | null
}

const POLL_MS = 30_000
const SNAPSHOT_POLL_MS = 5_000

/**
 * Normalize a backend row. Defaults `usedBy: []` and `state: 'unavailable'`
 * so views that iterate `b.usedBy` or branch on state don't crash on
 * partial backend data.
 */
export function normalizeBackend(raw: any): Backend {
  const src = (raw && typeof raw === 'object') ? raw : {}
  return {
    id: typeof src.id === 'string' ? src.id : (typeof src.name === 'string' ? src.name : ''),
    version: typeof src.version === 'string' ? src.version : (typeof src.ver === 'string' ? src.ver : ''),
    state: typeof src.state === 'string' ? src.state : 'unavailable',
    usedBy: Array.isArray(src.usedBy) ? src.usedBy.filter((x: any) => typeof x === 'string') : [],
    recommended: !!src.recommended,
    note: typeof src.note === 'string' ? src.note : undefined,
    kind: typeof src.kind === 'string' ? src.kind : undefined,
    device: typeof src.device === 'string' ? src.device : undefined,
  }
}

function normalizeLemonadeSelf(raw: any): LemonadeSelf | null {
  if (!raw || typeof raw !== 'object') return null
  return {
    version: typeof raw.version === 'string' ? raw.version : null,
    pinned: typeof raw.pinned === 'boolean' ? raw.pinned : null,
    sha: typeof raw.sha === 'string' ? raw.sha : null,
    channel: typeof raw.channel === 'string' ? raw.channel : null,
  }
}

export function useBackends() {
  return useQuery({
    queryKey: ['backends'],
    queryFn: async () => {
      const body = await apiGet<any>(ENDPOINTS.backends)
      const arr = Array.isArray(body)
        ? body
        : (Array.isArray(body?.backends) ? body.backends : [])
      return {
        backends: arr.map(normalizeBackend),
        lemonade: normalizeLemonadeSelf(Array.isArray(body) ? null : body?.lemonade),
      }
    },
    refetchInterval: POLL_MS,
  })
}

/** Per-backend snapshot (loaded models + status). 5s poll. */
export function useBackendSnapshot(id: string | null | undefined) {
  return useQuery({
    queryKey: ['backends', id],
    queryFn: async () => {
      const raw = await apiGet<any>(ENDPOINTS.backend(id as string))
      const base = normalizeBackend(raw)
      const loaded = Array.isArray(raw?.loaded)
        ? raw.loaded.filter((m: any) => m && typeof m === 'object')
        : []
      return { ...base, loaded } as Backend & {
        loaded: Array<{ model_name: string; slot: string }>
      }
    },
    enabled: !!id,
    refetchInterval: SNAPSHOT_POLL_MS,
  })
}

export function useBackendInstall() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiPost(ENDPOINTS.backendInstall(id)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backends'] }),
  })
}

export function useBackendUninstall() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiDelete(ENDPOINTS.backend(id)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backends'] }),
  })
}
