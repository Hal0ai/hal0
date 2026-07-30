// hal0 v3 dashboard — flag-migration report hook (D5, post-R3 surface rework).
//
// The flags/slot migration folds slot-level launch overrides into models. When
// several slots shared ONE model with DIVERGENT overrides, the migrator can't
// fold them (R3 gives each model a single launch-flags text) — it refuses that
// model and reports it for the operator to resolve.
//
// There is NO backend report endpoint yet (flagged — GET /api/migrations/
// flag-report is an API-lane request; it lands with the migration lane). So
// this is a TYPED CLIENT STUB: it returns an empty report by default and fails
// soft to empty on 404 / network error, keeping the banner + resolution view
// dormant in production until the real endpoint exists. The Tweaks-panel demo
// toggle drives the same view off DEMO_MIGRATION_REPORT (see MigrationResolveHost).

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

/** One slot that shared the refused model, with its stable id + display name. */
export interface MigrationSlotRef {
  slot_id: number
  name: string
}

/** One launch flag that DIFFERS across the sharing slots. `values` is keyed by
 *  slot name → the value that slot ran with (only conflicting flags appear). */
export interface MigrationFlagConflict {
  flag: string
  values: Record<string, string>
}

/** One model the migrator refused to fold. */
export interface RefusedModel {
  model_id: string
  model_label: string
  severity: 'warning' | 'critical'
  slots: MigrationSlotRef[]
  conflicts: MigrationFlagConflict[]
}

/** The whole report. `id` is the doctor diagnosis id it shares (HAL0-*). */
export interface MigrationReport {
  id: string
  refused_models: RefusedModel[]
}

export const EMPTY_MIGRATION_REPORT: MigrationReport = { id: '', refused_models: [] }

// Demo fixture — the R3 canvas example. Drives the Tweaks-panel preview only;
// never fetched. Two refused models so the resolution view's pager (1 of N) is
// exercised, and only conflicting flags are listed per the "side-by-side
// divergent values" contract.
export const DEMO_MIGRATION_REPORT: MigrationReport = {
  id: 'HAL0-0142',
  refused_models: [
    {
      model_id: 'qwen3-8b-q4_k_m',
      model_label: 'Qwen3-8B-Q4_K_M',
      severity: 'warning',
      slots: [
        { slot_id: 1, name: 'primary' },
        { slot_id: 4, name: 'chat-alt' },
        { slot_id: 6, name: 'coder-lite' },
      ],
      conflicts: [
        { flag: '-b', values: { primary: '2048', 'chat-alt': '2048', 'coder-lite': '4096' } },
        { flag: '-ub', values: { primary: '512', 'chat-alt': '512', 'coder-lite': '1024' } },
        { flag: '--threads', values: { primary: '8', 'chat-alt': '6', 'coder-lite': '8' } },
      ],
    },
    {
      model_id: 'gemma3-4b-it',
      model_label: 'gemma3-4b-it',
      severity: 'warning',
      slots: [
        { slot_id: 2, name: 'voice' },
        { slot_id: 5, name: 'embed' },
      ],
      conflicts: [
        { flag: '-fa', values: { voice: 'on', embed: 'off' } },
        { flag: '--ctx-size', values: { voice: '4096', embed: '8192' } },
      ],
    },
  ],
}

const POLL_MS = 60_000

/**
 * GET /api/migrations/flag-report — typed stub. Returns an empty report until
 * the endpoint exists; a resolved query is always a valid MigrationReport.
 *
 * `enabled: false` (GH #1439): the route genuinely does not exist server-side
 * yet, so firing this on a real box logs a 404 to the console on EVERY page
 * load — MigrationBanner mounts at the app root (main.jsx), and this hook
 * polled every 60s regardless of route. The try/catch below only stops a JS
 * crash; it can't stop the browser from making (and logging) the failed
 * request in the first place. Flip back to enabled once the migration lane
 * ships the real endpoint — `report`/`count`/`hasWork` are already correct
 * in the meantime (they fall back to EMPTY_MIGRATION_REPORT when the query
 * never runs, same value it always resolved to against a 404).
 */
export function useMigrationReport() {
  const q = useQuery({
    queryKey: ['migration-flag-report'],
    queryFn: async (): Promise<MigrationReport> => {
      try {
        const r = await apiGet<MigrationReport>(ENDPOINTS.migrationFlagReport)
        // Tolerate a bare/absent body — normalise to the empty report.
        if (!r || !Array.isArray(r.refused_models)) return EMPTY_MIGRATION_REPORT
        return r
      } catch {
        // No endpoint / network error → dormant, never a crash.
        return EMPTY_MIGRATION_REPORT
      }
    },
    enabled: false,
    refetchInterval: POLL_MS,
    retry: false,
  })
  const report = q.data ?? EMPTY_MIGRATION_REPORT
  return {
    ...q,
    report,
    count: report.refused_models.length,
    hasWork: report.refused_models.length > 0,
  }
}
