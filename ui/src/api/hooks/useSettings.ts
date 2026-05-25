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
