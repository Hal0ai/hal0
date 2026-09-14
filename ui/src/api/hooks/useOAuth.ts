// hal0 v3 dashboard — OAuth passthrough hooks (Connections → Connected accounts).
//
// Backs the agent-driven OAuth passthrough (study 3.3): a Hermes skill
// that needs OAuth (Google Calendar, Spotify, GitHub) is connected by
// opening a provider consent link in a new tab; the provider redirects
// straight back to hal0-api's own callback route, never through the
// dashboard. This file only ever sees connection STATUS — a token value
// never round-trips here.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface OAuthProviderStatus {
  id: string
  name: string
  skill_id: string
  scopes: string[]
  pkce: boolean
  configured: boolean
  requires_client_secret: boolean
  has_client_secret: boolean
  connected: boolean
  expires_at: number | null
  expired: boolean | null
  notes: string
}

export interface OAuthStartResult {
  authorize_url: string
  state: string
  provider_id: string
}

export function useOAuthProviders() {
  return useQuery({
    queryKey: ['oauth', 'providers'],
    queryFn: () => apiGet<{ providers: OAuthProviderStatus[] }>(ENDPOINTS.oauthProviders),
    select: (data) => data.providers,
    // Connection state can change out-of-band (the operator authorizes in
    // another tab, then comes back) — poll gently so the chip catches up
    // without the operator having to refresh.
    refetchInterval: 5000,
  })
}

export function useOAuthStart() {
  return useMutation({
    mutationFn: (id: string) => apiPost<OAuthStartResult>(ENDPOINTS.oauthStart(id)),
  })
}

export function useOAuthDisconnect() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiDelete(ENDPOINTS.oauthDisconnect(id)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['oauth', 'providers'] }),
  })
}

export function useOAuthSetClientSecret() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, value }: { id: string; value: string }) =>
      apiPost(ENDPOINTS.oauthClientSecret(id), { value }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['oauth', 'providers'] }),
  })
}
