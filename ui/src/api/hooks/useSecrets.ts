// hal0 v3 dashboard — secrets hooks (Phase B1).
//
// Backs Settings → Secrets. List + add + remove. Values are encrypted
// at rest; the API only ever returns masked previews.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface SecretEntry {
  name: string
  set: boolean
  masked?: string
}

export function useSecrets() {
  return useQuery({
    queryKey: ['secrets'],
    queryFn: async () => {
      const body = await apiGet<{ secrets: SecretEntry[] }>(ENDPOINTS.secrets)
      return body?.secrets ?? []
    },
  })
}

export function useSecretSet() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) =>
      apiPost(ENDPOINTS.secret(name), { value }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['secrets'] }),
  })
}

export function useSecretDelete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => apiDelete(ENDPOINTS.secret(name)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['secrets'] }),
  })
}
