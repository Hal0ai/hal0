// hal0 v3 dashboard — settings (hal0.toml) read / write hooks.
//
// PR feat/models-scan-and-add-by-path: introduces typed access to
// /api/settings so the dashboard's Settings view can surface
// [models].roots + [models].pull_root (so the user can point hal0 at
// /mnt/ai-models) without going through `hal0 config edit`.
//
// The backend deep-merges the body on PUT so callers only need to send
// the keys they're changing; we keep the hook surface deliberately
// thin and let consumers shape the patch.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, apiPut, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface ModelsSettings {
  roots: string[]
  auto_scan_on_start: boolean
  file_extensions: string[]
  pull_root: string
}

export interface Hal0Settings {
  meta?: { schema_version?: number }
  slots?: Record<string, unknown>
  dispatcher?: Record<string, unknown>
  telemetry?: Record<string, unknown>
  models?: ModelsSettings
  memory?: Record<string, unknown>
  [key: string]: unknown
}

const SETTINGS_KEY = ['settings'] as const

export function useSettings() {
  return useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: () => apiGet<Hal0Settings>(ENDPOINTS.settings),
    // No background refetch — the file changes rarely and the operator
    // is in the seat when they care; aggressive polling would just
    // spam the disk read.
    staleTime: 60_000,
  })
}

export function useSettingsUpdate() {
  const qc = useQueryClient()
  return useMutation<Hal0Settings, Hal0Error, Partial<Hal0Settings>>({
    mutationFn: (patch) => apiPut<Hal0Settings>(ENDPOINTS.settings, patch),
    onSuccess: (next) => {
      qc.setQueryData(SETTINGS_KEY, next)
      qc.invalidateQueries({ queryKey: ['models'] })
    },
  })
}

export function useSettingsReload() {
  const qc = useQueryClient()
  return useMutation<Hal0Settings, Hal0Error, void>({
    mutationFn: () => apiPost<Hal0Settings>(ENDPOINTS.settingsReload),
    onSuccess: (next) => qc.setQueryData(SETTINGS_KEY, next),
  })
}

// ── Model storage (single source of truth) ──────────────────────────────
//
// `[models].store` (v0.3) replaces #313's roots + pull_root with one
// path that hal0's pull engine points at. The dedicated endpoints
// below give the Settings page +
// Firstrun "Storage" step precise validation, a dry-run probe for
// "needs migration" detection, and an explicit migrate call so the
// confirmation modal has a clean URL to fire at.

export interface StoreStateProbe {
  path: string
  exists: boolean
  is_dir: boolean
  readable: boolean
  writable: boolean
  files_count: number
  size_bytes: number
  free_bytes: number
}

export interface StoreSuggestion extends StoreStateProbe {
  is_current: boolean
}

export interface ModelStoreState {
  store: string | null
  effective: string
  fallback_active: boolean
  pull_root_legacy: string
  current_state: StoreStateProbe
  suggestions: StoreSuggestion[]
}

export interface MigrationPlan {
  source: string | null
  target: string
  files_count: number
  size_bytes: number
  same_filesystem: boolean
}

export interface MigrationOutcome {
  source: string
  target: string
  moved: string[]
  failed: { name: string; reason: string; target?: string }[]
}

export type SetStoreResponse =
  | { status: 'needs_migration'; plan: MigrationPlan; state: ModelStoreState }
  | {
      status: 'ok'
      config: Hal0Settings
      state: ModelStoreState
      migration: MigrationOutcome | null
    }

const MODEL_STORE_KEY = ['settings', 'models', 'store'] as const

export function useModelStore() {
  return useQuery({
    queryKey: MODEL_STORE_KEY,
    queryFn: () => apiGet<ModelStoreState>(ENDPOINTS.settingsModelsStore),
    // Suggestions probe the filesystem (file counts + free-bytes) so the
    // refetch cost is non-trivial; 30s is enough for the firstrun chip
    // labels to stay fresh without spinning under the user.
    staleTime: 30_000,
  })
}

export function useModelStoreSet() {
  const qc = useQueryClient()
  return useMutation<
    SetStoreResponse,
    Hal0Error,
    { path: string; migrate?: boolean }
  >({
    mutationFn: (body) =>
      apiPost<SetStoreResponse>(ENDPOINTS.settingsModelsStore, body),
    onSuccess: (resp) => {
      if (resp.status === 'ok') {
        qc.setQueryData(MODEL_STORE_KEY, resp.state)
        qc.setQueryData(SETTINGS_KEY, resp.config)
        qc.invalidateQueries({ queryKey: ['models'] })
      } else {
        qc.setQueryData(MODEL_STORE_KEY, resp.state)
      }
    },
  })
}

// ── Apply-plan registry (issue #552) ────────────────────────────────────────
//
// The dashboard fetches the full key→apply-class registry once on mount so
// each settings row can render the right effect badge (live / ⟳ restart
// <service> / ⚠ manual restart) without a per-save server round-trip.
// The registry is static for the lifetime of the process — staleTime is
// long so we never re-fetch it unless the user hard-reloads.

export interface ApplyPlanEntry {
  apply_class: 'immediate' | 'service-restart' | 'manual-restart'
  services: string[]
}

export interface ApplyPlanRegistry {
  apply_classes: string[]
  registry: Record<string, ApplyPlanEntry>
}

const APPLY_PLAN_KEY = ['settings', 'apply-plan'] as const

export function useApplyPlan() {
  return useQuery({
    queryKey: APPLY_PLAN_KEY,
    queryFn: () => apiGet<ApplyPlanRegistry>(ENDPOINTS.settingsApplyPlan),
    // Registry is static for the server's lifetime — never auto-refetch.
    staleTime: Infinity,
  })
}

export function useModelStoreMigrate() {
  const qc = useQueryClient()
  return useMutation<SetStoreResponse, Hal0Error, { path: string }>({
    mutationFn: (body) =>
      apiPost<SetStoreResponse>(ENDPOINTS.settingsModelsStoreMigrate, body),
    onSuccess: (resp) => {
      if (resp.status === 'ok') {
        qc.setQueryData(MODEL_STORE_KEY, resp.state)
        qc.setQueryData(SETTINGS_KEY, resp.config)
        qc.invalidateQueries({ queryKey: ['models'] })
      }
    },
  })
}
