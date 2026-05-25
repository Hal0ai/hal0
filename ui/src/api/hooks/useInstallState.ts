// hal0 v3 dashboard — install-state hook.
//
// Backs the post-install banner heading + the FirstRun progress card.
// `/api/install/state` already gates the first-run wizard; this hook
// also exposes the bundle the operator picked so the dashboard renders
// the real tier name instead of a hardcoded `hal0-Pro` (issue #214).

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface InstallStateBundle {
  name: string
  npu_opt_in?: boolean
  chosen_at?: string
  skipped?: boolean
  assignments?: Array<Record<string, unknown>>
}

export interface InstallState {
  first_run: boolean
  has_models: boolean
  has_default_slot: boolean
  openwebui_running: boolean
  sentinel_path: string
  bundle: InstallStateBundle | null
}

export function useInstallState() {
  return useQuery({
    queryKey: ['install', 'state'],
    queryFn: () => apiGet<InstallState>(ENDPOINTS.installState),
    // Bundle pick only changes via the picker flow; cache for a minute
    // so banners don't refetch on every render.
    staleTime: 60_000,
  })
}

/**
 * Resolve the current bundle's display name with a sensible fallback.
 *
 * Returns `'hal0'` when no pick has been made (fresh install) or when
 * the user explicitly skipped the picker — the banner copy already
 * carries the rest of the sentence ("Welcome to hal0 — hal0 is loaded"
 * reads cleanly when no tier exists).
 */
export function bundleNameOr(state: InstallState | undefined, fallback = 'hal0'): string {
  const name = state?.bundle?.name
  if (!name || state?.bundle?.skipped) return fallback
  return name
}
