// hal0 v3 dashboard — per-component version/converge hooks (Task 12, spec
// 2026-08-30 component-updates §3/§4).
//
// `/api/updates/components` (Task 8, hal0.components.status) reports the
// catalogue × recorded-state × live-probe status for each managed
// component (openwebui, runner-images, hermes, hindsight, …), joined to
// the Services page by `service_id`. `/api/updates/components/{id}/converge`
// re-runs one component's converge arm — the retry surface for a failed row
// (see components-pure.ts's RETRYABLE_STATUSES).
//
// Mirrors useUpdates.ts's fail-soft/poll conventions: an older daemon
// without this route degrades to `null` (never throws), and the list polls
// every 30s (slower than the 1.5s job-status poll — component convergence
// is not time-critical the way an in-flight self-update is).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost } from '../client'
import type { ComponentRow } from '@/dash/components-pure'

type ComponentsResponse = { components: ComponentRow[]; pending: number }

export function useComponents() {
  return useQuery<ComponentsResponse | null>({
    queryKey: ['updates', 'components'],
    queryFn: async () => {
      try {
        return await apiGet<ComponentsResponse>('/api/updates/components')
      } catch {
        return null // fail-soft: older daemon without the route
      }
    },
    refetchInterval: 30_000,
  })
}

export function useComponentConverge() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      apiPost<{ id: string }>(`/api/updates/components/${id}/converge`),
    onSettled: () => qc.invalidateQueries({ queryKey: ['updates', 'components'] }),
  })
}
