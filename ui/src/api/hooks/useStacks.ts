// hal0 dashboard — stacks hook (PR-5, spec §8/§9).
//
// A Stack is a named, portable bundle of slots + their profiles + model
// assignments + capability selections. This hook wraps /api/stacks:
//   - list (with the active stack + its drift status)
//   - create / update / delete (seed stacks are immutable server-side: 409)
//   - apply (dry-run diff preview, then commit + lifecycle converge)
//   - export (.hal0stack.json envelope), import (dry-run resolve, then create)
//   - snapshot the current live config into a StackConfig
//
// Mirrors useProfiles: queries via apiGet, mutations via api({raw:true}).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface StackCapabilityRow {
  child: string
  device: string
  provider: string
  model: string
  enabled: boolean
}

export interface StackSlotEntry {
  slot: string
  profile?: string | null
  model?: string | null
  device?: string | null
  provider?: string | null
  role?: string | null
  vision?: boolean
  mtp?: boolean | null
  enable_thinking?: boolean | null
  server_extra_args?: string | null
  capabilities?: StackCapabilityRow[]
}

/** The body persisted under a slug (POST/PUT). */
export interface StackBody {
  name?: string
  description?: string
  author?: string
  icon?: string
  tags?: string[]
  schema_version?: number
  hal0_version?: string
  slots?: StackSlotEntry[]
  profiles?: Record<string, unknown>
  models?: Record<string, unknown>
}

/** A stack as returned by list/detail — body + derived seed/active/drift. */
export interface Stack extends StackBody {
  slug: string
  name: string
  description: string
  author: string
  icon: string
  tags: string[]
  seed: boolean
  slots: StackSlotEntry[]
  /** True when this is the currently-applied stack. */
  active?: boolean
  /** Drift status for the active stack: clean | modified (null otherwise). */
  drift?: string | null
}

export interface StackList {
  stacks: Stack[]
  active: string | null
  drift: 'clean' | 'modified' | 'none'
}

export interface StackDiffRow {
  slot: string
  before_model: string | null
  after_model: string | null
  changed: boolean
}

export interface StackConvergeReport {
  loaded: string[]
  swapped: string[]
  skipped: string[]
  unloaded: string[]
  capabilities_applied: string[]
  errors: { target: string; error: string }[]
}

export interface StackApplyResult {
  stack: string
  dry_run: boolean
  summary: string[]
  changes: StackDiffRow[]
  /** Dry-run: slot names the stack will create (don't exist yet). */
  creates?: string[]
  /** Dry-run: running slots the stack doesn't name — apply will unload them (#1511). */
  unloads?: string[]
  /** Commit: slot names that were created during apply. */
  created?: string[]
  converged?: StackConvergeReport
}

export interface StackModelResolution {
  model_id: string
  status: 'present' | 'pullable' | 'unresolvable'
  hf_repo: string
  hf_filename: string
}

export interface StackImportDryResult {
  dry_run: true
  valid: boolean
  checksum_ok: boolean
  name: string
  schema_version: number
  resolutions: StackModelResolution[]
  present: string[]
  pullable: string[]
  unresolvable: string[]
}

export interface StackEnvelope {
  kind: string
  schema_version: number
  hal0_version: string
  exported_at: string
  checksum: string
  stack: StackBody
}

export function useStacks() {
  return useQuery({
    queryKey: ['stacks'],
    queryFn: () => apiGet<StackList>(ENDPOINTS.stacks),
    staleTime: 30_000,
  })
}

export function useStackCreate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ slug, stack }: { slug: string; stack: StackBody }) =>
      api<Stack>(ENDPOINTS.stacks, { method: 'POST', body: { slug, stack } as any, raw: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['stacks'] }),
  })
}

export function useStackUpdate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ slug, stack }: { slug: string; stack: StackBody }) =>
      api<Stack>(ENDPOINTS.stack(slug), { method: 'PUT', body: stack as any, raw: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['stacks'] }),
  })
}

export function useStackDelete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (slug: string) =>
      api<void>(ENDPOINTS.stack(slug), { method: 'DELETE', raw: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['stacks'] }),
  })
}

export function useStackApply() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ slug, dryRun }: { slug: string; dryRun: boolean }) =>
      api<StackApplyResult>(
        ENDPOINTS.stackApply(slug) + (dryRun ? '?dry_run=true' : ''),
        { method: 'POST', raw: true },
      ),
    // Only a commit changes server state — refresh the active/drift view.
    onSuccess: (_data, vars) => {
      if (!vars.dryRun) {
        qc.invalidateQueries({ queryKey: ['stacks'] })
        qc.invalidateQueries({ queryKey: ['slots'] })
      }
    },
  })
}

export function useStackExport() {
  return useMutation({
    mutationFn: (slug: string) =>
      api<StackEnvelope>(ENDPOINTS.stackExport(slug), { method: 'POST', raw: true }),
  })
}

export function useStackImport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: { envelope: unknown; dry_run?: boolean; slug?: string }) =>
      api<StackImportDryResult | { dry_run: false; stack: Stack }>(
        ENDPOINTS.stackImport,
        { method: 'POST', body: payload as any, raw: true },
      ),
    onSuccess: (_data, vars) => {
      if (!vars.dry_run) qc.invalidateQueries({ queryKey: ['stacks'] })
    },
  })
}

export function useStackSnapshot() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: { name?: string; description?: string; slug?: string }) =>
      api<{ created: boolean; stack: StackBody | Stack }>(
        ENDPOINTS.stackSnapshot,
        { method: 'POST', body: payload as any, raw: true },
      ),
    onSuccess: (_data, vars) => {
      if (vars.slug) qc.invalidateQueries({ queryKey: ['stacks'] })
    },
  })
}
