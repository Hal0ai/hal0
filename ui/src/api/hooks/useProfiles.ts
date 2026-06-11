// hal0 v3 dashboard — profiles hook (issue #658).
//
// Fetches /api/profiles — the list of named container-slot profiles
// (image + bench-tuned flags) seeded by profiles.toml.
//
// CRUD mutations (Phase C6): create (POST), update (PUT), delete (DELETE).
// Seeds are immutable server-side (409 profiles.seed_immutable); the UI
// reflects that with disabled Edit/Delete buttons and a Clone affordance.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface Profile {
  name: string
  image: string
  flags: string
  mtp: boolean
  resolved_flags: string
  device_class?: string
  /** Emitted by the API: true when the profile is one of the immutable
   *  SEED_PROFILES (server rejects PUT/DELETE with 409 seed_immutable). */
  seed?: boolean
}

export interface ProfileBody {
  name: string
  image: string
  flags?: string
  mtp?: boolean
  device_class?: string
}

export function useProfiles() {
  return useQuery({
    queryKey: ['profiles'],
    queryFn: () => apiGet<Profile[]>(ENDPOINTS.profiles),
    staleTime: 60_000,
  })
}

export function useProfileCreate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ProfileBody) =>
      api<Profile>(ENDPOINTS.profiles, { method: 'POST', body: body as any, raw: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profiles'] }),
  })
}

export function useProfileUpdate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: Partial<ProfileBody> }) =>
      api<Profile>(ENDPOINTS.profile(name), { method: 'PUT', body: body as any, raw: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profiles'] }),
  })
}

export function useProfileDelete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) =>
      api<void>(ENDPOINTS.profile(name), { method: 'DELETE', raw: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profiles'] }),
  })
}
