// hal0 v3 dashboard — per-persona budget hooks (Phase 0 OpenRouter prereq).
//
// TanStack Query wiring for the new ``/api/agents/{id}/personas/{pid}/
// budget`` REST surface. The persona editor under personas-tab.jsx mounts
// a panel that:
//
//   - GET-polls the current budget + running spend stats
//   - PUT-mutates the budget block (other persona fields untouched)
//
// The check + charge endpoints are NOT consumed from the dashboard — V1's
// OpenRouter provider calls them server-side. We expose typed helpers
// here anyway so a future "what if I spent $X" preview surface can
// reuse them without re-fetching this hook's data.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query'

import { apiGet, apiPost, apiPut } from '../client'

// ── Types ──────────────────────────────────────────────────────────

/**
 * Persona budget block — matches the server's :class:`Budget` shape.
 *
 * ``null`` on any cap field means "no cap configured"; an explicit
 * ``0`` means "block every paid call". ``hard_cap=true`` (default)
 * enforces; ``false`` is warn-only.
 */
export interface PersonaBudget {
  daily_usd?: number | null
  monthly_usd?: number | null
  lifetime_usd?: number | null
  per_call_max_usd?: number | null
  hard_cap: boolean
}

export interface PersonaSpendStats {
  today_usd: number
  mtd_usd: number
  lifetime_usd: number
}

export interface PersonaRemaining {
  daily_usd?: number
  monthly_usd?: number
  lifetime_usd?: number
}

export interface PersonaBudgetResponse {
  budget: PersonaBudget
  spend: PersonaSpendStats
  remaining: PersonaRemaining
}

export interface BudgetCheckRequest {
  estimated_cost_usd: number
}

export interface BudgetCheckResponse {
  allowed: boolean
  reason: string | null
  remaining_usd: Record<string, number>
  hard_cap: boolean
}

export interface BudgetChargeRequest {
  surface: string
  model: string
  cost_usd: number
  request_id: string
}

// ── Polling cadence ────────────────────────────────────────────────
//
// Budget data doesn't change as fast as slot state — operator edits
// it once, the OpenRouter provider records charges asynchronously.
// 15s is a sensible default; the editor panel always invalidates on
// PUT so the operator sees their own change immediately.

const BUDGET_POLL_MS = 15_000

// ── Hooks ──────────────────────────────────────────────────────────

/**
 * Read the budget + spend snapshot for one persona.
 *
 * Pass ``null`` / ``undefined`` for either id to short-circuit the
 * fetch — useful while the persona detail loads and the active id
 * isn't known yet.
 */
export function usePersonaBudget(
  agentId: string | null | undefined,
  personaId: string | null | undefined,
): UseQueryResult<PersonaBudgetResponse> {
  return useQuery<PersonaBudgetResponse>({
    queryKey: ['agents', 'persona', 'budget', agentId, personaId],
    queryFn: async () => {
      if (!agentId || !personaId) {
        return {
          budget: { hard_cap: true },
          spend: { today_usd: 0, mtd_usd: 0, lifetime_usd: 0 },
          remaining: {},
        }
      }
      return apiGet<PersonaBudgetResponse>(
        `/api/agents/${encodeURIComponent(agentId)}/personas/${encodeURIComponent(personaId)}/budget`,
      )
    },
    enabled: !!agentId && !!personaId,
    refetchInterval: BUDGET_POLL_MS,
    refetchOnWindowFocus: true,
  })
}

/**
 * Mutation: replace the persona's budget block.
 *
 * Optimistically invalidates the read cache so the panel reflects the
 * new caps the moment the PUT resolves. Other persona fields (system
 * prompt, tool gating, approval policy) are NOT touched by this PUT
 * — the server preserves them on round-trip.
 */
export function usePutPersonaBudget(
  agentId: string | null | undefined,
  personaId: string | null | undefined,
): UseMutationResult<PersonaBudgetResponse, Error, PersonaBudget> {
  const qc = useQueryClient()
  return useMutation<PersonaBudgetResponse, Error, PersonaBudget>({
    mutationFn: async (budget: PersonaBudget) => {
      if (!agentId || !personaId) {
        throw new Error('agentId + personaId required to PUT a budget')
      }
      return apiPut<PersonaBudgetResponse>(
        `/api/agents/${encodeURIComponent(agentId)}/personas/${encodeURIComponent(personaId)}/budget`,
        budget as unknown as Record<string, unknown>,
      )
    },
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ['agents', 'persona', 'budget', agentId, personaId],
      })
    },
  })
}

/**
 * Convenience selector: pluck just the spend totals for surfacing in a
 * sidebar / footer pill without rendering the whole budget panel.
 */
export function usePersonaSpendStats(
  agentId: string | null | undefined,
  personaId: string | null | undefined,
): PersonaSpendStats {
  const q = usePersonaBudget(agentId, personaId)
  return q.data?.spend ?? { today_usd: 0, mtd_usd: 0, lifetime_usd: 0 }
}

/**
 * Dry-run pre-call gate. Not used by the dashboard today (V1's
 * OpenRouter provider calls this server-side) but exposed for a future
 * "preview cost" surface in the composer.
 */
export async function checkPersonaBudget(
  agentId: string,
  personaId: string,
  body: BudgetCheckRequest,
): Promise<BudgetCheckResponse> {
  return apiPost<BudgetCheckResponse>(
    `/api/agents/${encodeURIComponent(agentId)}/personas/${encodeURIComponent(personaId)}/budget/check`,
    body as unknown as Record<string, unknown>,
  )
}

/**
 * Post-response charge recorder. Same audience as ``checkPersonaBudget``
 * — exposed for completeness; the OpenRouter provider is the canonical
 * caller.
 */
export async function chargePersonaBudget(
  agentId: string,
  personaId: string,
  body: BudgetChargeRequest,
): Promise<PersonaBudgetResponse> {
  return apiPost<PersonaBudgetResponse>(
    `/api/agents/${encodeURIComponent(agentId)}/personas/${encodeURIComponent(personaId)}/budget/charge`,
    body as unknown as Record<string, unknown>,
  )
}
