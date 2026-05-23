// hal0 v3 dashboard — auth hooks (Phase B1).
//
// Backs Settings → Auth. Token visibility + rotation + CORS-allowed
// origins. The actual Bearer token is fetched on-demand via the
// "Show" button; default state is masked.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface AuthTokenInfo {
  token?: string
  token_masked?: string
  issued?: string
}

export interface AllowedOrigins {
  origins: string[]
}

export function useAuthToken() {
  return useQuery({
    queryKey: ['auth', 'token'],
    queryFn: () => apiGet<AuthTokenInfo>(ENDPOINTS.authToken),
  })
}

export function useAuthTokenReveal() {
  // Distinct query that hits a privileged variant — backend may emit the
  // plaintext token. UI calls this once on user click.
  return useMutation({
    mutationFn: () => apiPost<AuthTokenInfo>(ENDPOINTS.authToken + '?reveal=1'),
  })
}

export function useAuthTokenRotate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost<AuthTokenInfo>(ENDPOINTS.authTokenRotate),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['auth'] }),
  })
}

export function useAllowedOrigins() {
  return useQuery({
    queryKey: ['auth', 'allowed-origins'],
    queryFn: () => apiGet<AllowedOrigins>(ENDPOINTS.authAllowedOrigins),
  })
}

export function useAllowedOriginsSet() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (origins: string[]) =>
      apiPost(ENDPOINTS.authAllowedOrigins, { origins }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['auth', 'allowed-origins'] }),
  })
}
