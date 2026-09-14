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
      // `current` and `live_target` on every row are stale the instant this
      // PUT lands — a save that fixes tool_model's live target must clear
      // AgentsBrainPage's "no live target" banner immediately, not up to
      // 15s later (see SETTINGS_FIELDS_KEY's staleTime below).
      qc.invalidateQueries({ queryKey: SETTINGS_FIELDS_KEY })
    },
  })
}

export function useSettingsReload() {
  const qc = useQueryClient()
  return useMutation<Hal0Settings, Hal0Error, void>({
    mutationFn: () => apiPost<Hal0Settings>(ENDPOINTS.settingsReload),
    onSuccess: (next) => {
      qc.setQueryData(SETTINGS_KEY, next)
      qc.invalidateQueries({ queryKey: SETTINGS_FIELDS_KEY })
    },
  })
}

// ── Settings JSON schema (pydantic Hal0Config) ──────────────────────────
//
// GET /api/settings/schema serves the full pydantic JSON Schema for
// hal0.toml — per-field types, bounds, and descriptions. The Advanced
// settings section renders its controls from this so descriptions stay
// server-sourced instead of drifting in frontend copy. Static for the
// server's lifetime.
export function useSettingsSchema() {
  return useQuery({
    queryKey: ['settings', 'json-schema'],
    queryFn: () => apiGet<Record<string, unknown>>(ENDPOINTS.settingsSchema),
    staleTime: Infinity,
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

// ── Schema-driven settings fields (#2108) ───────────────────────────────────
//
// GET /api/settings/fields is the one payload a schema-driven settings page
// renders from: one row per operator-editable Hal0Config leaf, joining three
// single-owner facts — schema metadata (group/label/description/type/enum/
// constraints/default) from Field(description=...) in hal0.config.schema,
// reload classification from the same apply-plan registry useApplyPlan()
// reads, and `current` from the live config. `live_target` is populated only
// for a `hal0/<slot>`-shaped value (null otherwise) — true/false for whether
// that alias currently resolves to a loaded slot, closing #2108's "the
// shipped tool_model default has nowhere live to route on a fresh install"
// gap server-side instead of the UI re-deriving slot-resolution logic.

export interface SettingsFieldRow {
  path: string
  group: string
  label: string
  description: string
  type: 'boolean' | 'number' | 'string' | 'string[]' | 'map' | 'enum'
  enum: string[] | null
  constraints: Record<string, number>
  default: unknown
  current: unknown
  secret: boolean
  apply_class: 'immediate' | 'service-restart' | 'manual-restart' | null
  services: string[]
  live_target: boolean | null
}

const SETTINGS_FIELDS_KEY = ['settings', 'fields'] as const

export function useSettingsFields() {
  return useQuery({
    queryKey: SETTINGS_FIELDS_KEY,
    queryFn: () => apiGet<{ fields: SettingsFieldRow[] }>(ENDPOINTS.settingsFields),
    // live_target resolves against the current slot set, so this can't be
    // Infinity like useSettingsSchema — but a settings page isn't a place
    // operators watch for slot churn in real time either.
    staleTime: 15_000,
  })
}

// ── One ChangeSet, shared by preview and apply (#1967, #2195, #2203, #1511) ──
//
// POST /api/settings/preview and the `_hal0.changeset` on PUT /api/settings
// both render this same shape — `hal0.api._settings_changeset.compute_
// settings_changeset` is the one function behind both, so a preview drawer
// built from this type shows exactly what the apply will write.

export interface SettingsChange {
  path: string
  before: unknown
  after: unknown
  kind: 'added' | 'removed' | 'changed'
  apply_class: 'immediate' | 'service-restart' | 'manual-restart' | null
  services: string[]
}

export interface SettingsChangesetPayload {
  changes: SettingsChange[]
  unknown: string[]
}

export interface SettingsPreviewResponse {
  changeset: SettingsChangesetPayload
  apply_plan: unknown
}

// Dry-run mutation — deliberately not cached (queryClient has no notion of
// "preview for this exact patch"), and never writes to SETTINGS_KEY: a
// preview must never be mistaken for a save by anything reading react-query
// state.
export function useSettingsPreview() {
  return useMutation<SettingsPreviewResponse, Hal0Error, Partial<Hal0Settings>>({
    mutationFn: (patch) => apiPost<SettingsPreviewResponse>(ENDPOINTS.settingsPreview, patch),
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
