// hal0 v3 dashboard — profiles hook (issue #658).
//
// Fetches /api/profiles — the list of named tunes seeded by profiles.toml.
// ADR-0029: a profile owns EVERY tune — llama-server flags, context window,
// thinking default, vision, speculation and runner. The model carries file facts only,
// the slot carries placement. `device_class`, `backend` and `quant` are gone.
//
// CRUD mutations (Phase C6): create (POST), update (PUT), delete (DELETE).
// Seeds are immutable server-side (409 profiles.seed_immutable); the UI
// reflects that with disabled Edit/Delete buttons and a Clone affordance.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export type ProfileVision = 'auto' | 'off'
export type ProfileSpeculation = 'auto' | 'off' | 'ngram'

export type ReasoningEffort = 'default' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'

export interface Profile {
  name: string
  flags: string
  /** Context window the profile asks for; null = defer to the model's native
   *  window (ADR-0029). Server range 128..16777216. */
  ctx_size?: number | null
  /** Thinking default: null = off unless the request asks, true = on, false = off.
   *  A structured-output request that states no preference keeps the chat
   *  template's default instead of the off default. */
  enable_thinking?: boolean | null
  /** Reasoning effort passed to llama-server (--reasoning-effort); "default"
   *  emits nothing. Needs a runner build that supports it. */
  reasoning_effort?: ReasoningEffort
  /** Thinking-token budget; -1 = unlimited (emits nothing), 0 = end immediately. */
  reasoning_budget?: number
  /** Vision: "auto" loads the model's mmproj when present, "off" never does. */
  vision?: ProfileVision
  /** Speculative decoding (ADR-0031): "auto" uses the model's drafter or MTP
   *  heads when it has them, "off" never drafts, "ngram" is model-free. */
  speculation?: ProfileSpeculation
  resolved_flags: string
  /** Emitted by the API: true when the profile is one of the immutable
   *  SEED_PROFILES (server rejects PUT/DELETE with 409 seed_immutable). */
  seed?: boolean
  /** Optional runtime binding — a RUNNER_IMAGES key (D4). Saving a slot on
   *  this profile flips its `binary` (+ `device` for a single-lane runner)
   *  via the server's profile-wins reconcile. null/absent = flags-only tune,
   *  no runtime opinion. */
  runner?: string | null
  /** Runtime family the profile resolves to (llama-server | flm | kokoro |
   *  qwen3tts | moonshine | comfyui) — derived server-side, never chosen. A
   *  stored `runner` takes first precedence (its registry entry names exactly
   *  one family); only without one do the older device_class/name rules
   *  decide, defaulting to llama-server. The card chips a non-llama-server
   *  family. */
  runtime_family?: string
  /** Provenance: profile this one was cloned from (clone / edit-a-copy). */
  cloned_from?: string | null
  /** Human label shown as the card headline (e.g. "MoE agents"). */
  intent?: string
  /** Bench tok/s hero metric — null when un-benched (custom profiles). */
  tps?: number | null
  /** Real-time factor for synth slots (e.g. TTS) — null when n/a. */
  rtf?: number | null
  /** Slot names currently bound to this profile. */
  used_by?: string[]
  /** True for a profiles.toml entry that failed validation and was set aside
   *  (#2653). Such a row carries only `name`, `error`, `seed` and `used_by` —
   *  no tune. DELETE removes it; POST under the same name replaces it. */
  quarantined?: boolean
  /** Why a quarantined entry was rejected. */
  error?: string
}

export interface ProfileBody {
  name: string
  flags?: string
  /** 128..16777216, or null to clear on PUT. Omit to leave alone. */
  ctx_size?: number | null
  enable_thinking?: boolean | null
  reasoning_effort?: ReasoningEffort
  reasoning_budget?: number
  vision?: ProfileVision
  speculation?: ProfileSpeculation
  /** Optional runtime binding — a RUNNER_IMAGES key (D4). On PUT, "" clears
   *  it and omitting leaves it alone. */
  runner?: string | null
  cloned_from?: string | null
  intent?: string
}


/** Portable profile bundle (.hal0profile.json), mirrors StackEnvelope. */
export interface ProfileEnvelope {
  kind: string
  schema_version: number
  hal0_version: string
  exported_at: string
  name: string
  checksum: string
  profile: ProfileBody
}

/** Dry-run import result — identity + integrity + collision (no model refs). */
export interface ProfileImportDryResult {
  dry_run: true
  valid: boolean
  checksum_ok: boolean
  name: string
  schema_version: number
  collides: boolean
  /** Runtime key the ENVELOPE carries (not the one that will be stored). */
  runner?: string | null
  /** True when that key is not in this box's registry: the import proceeds
   *  and lands the profile with no runtime pinned. Never a block. */
  runner_stripped?: boolean
  /** Runtime family the imported profile will resolve to. */
  runtime_family?: string
}

export function useProfiles() {
  return useQuery({
    queryKey: ['profiles'],
    queryFn: () => apiGet<Profile[]>(ENDPOINTS.profiles),
    staleTime: 25_000,
    // Profiles can be edited externally (CLI, another browser, hal0 setup);
    // a 30s background refetch converges the page without a manual reload.
    refetchInterval: 30_000,
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

export function useProfileExport() {
  return useMutation({
    mutationFn: (name: string) =>
      api<ProfileEnvelope>(ENDPOINTS.profileExport(name), { method: 'POST', raw: true }),
  })
}

export function useProfileImport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: { envelope: unknown; dry_run?: boolean; name?: string }) =>
      api<ProfileImportDryResult | { dry_run: false; profile: Profile }>(
        ENDPOINTS.profileImport,
        { method: 'POST', body: payload as any, raw: true },
      ),
    onSuccess: (_d, vars) => {
      if (!vars.dry_run) qc.invalidateQueries({ queryKey: ['profiles'] })
    },
  })
}
