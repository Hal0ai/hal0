// hal0 v3 dashboard — updates hooks (Phase B1).
//
// Backs the Settings → Updates surface. `/api/updates/state` returns
// per-channel current + available versions; `/api/updates/check` re-
// probes; `/api/updates/apply` kicks off self-update.

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

export function useUpdateState() {
  return useQuery({
    queryKey: ['updates', 'state'],
    queryFn: () => apiGet<UpdateState>(ENDPOINTS.updateState),
  })
}

export function useUpdateCheck() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (channel?: string) =>
      apiPost(ENDPOINTS.updateCheck, channel ? { channel } : undefined),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['updates'] }),
  })
}

export function useUpdateApply() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (channel: string) => apiPost(ENDPOINTS.updateApply, { channel }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['updates'] }),
  })
}
