// hal0 v3 dashboard — profiles hook (issue #658).
//
// Fetches /api/profiles — the list of named container-slot profiles
// (image + bench-tuned flags) seeded by profiles.toml.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface Profile {
  name: string
  image: string
  flags: string
  mtp: boolean
  resolved_flags: string
}

export function useProfiles() {
  return useQuery({
    queryKey: ['profiles'],
    queryFn: () => apiGet<Profile[]>(ENDPOINTS.profiles),
    staleTime: 60_000,
  })
}
