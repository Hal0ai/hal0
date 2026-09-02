// hal0 dashboard — GTT feasibility verdict → hint copy mapper (drawer
// overhaul PR-3, Task 7 / spec 2026-09-01-slot-model-drawer-overhaul).
//
// No React, no API imports — vitest exercises this without a DOM, mirroring
// components-pure.ts. Consumed by the model drawer's feasibility row (Task
// 8) via `POST /api/models/feasibility` results (host-truth GTT signal,
// warn-never-block per 993ea3b6).
//
// Wording is exact per the drawer mockup's panel 06 — do not reflow it.

// Single source of truth is the API hook (useModelsFeasibility.ts) — this
// file used to redefine the same four-verdict union independently, which
// left two copies to keep in sync by hand. `import type` keeps this a
// type-only dependency with zero runtime import.
import type { FeasibilityVerdict } from '../api/hooks/useModelsFeasibility'
export type { FeasibilityVerdict }

export interface FeasibilityRow {
  verdict: FeasibilityVerdict | string
  needed_mb: number
  gtt_free_mb: number | null
  gtt_total_mb: number | null
}

export interface FeasibilityHint {
  tone: 'ok' | 'warn' | 'err' | null
  text: string
}

const gb = (mb: number | null | undefined) => Math.round((mb ?? 0) / 1024)

/**
 * Map one `/api/models/feasibility` result row to the drawer's hint copy.
 * `unknown` (host-truth signal unavailable) renders no hint at all — the
 * warn-never-block contract means a missing signal must not read as an
 * error state.
 */
export function feasibilityHint(row: FeasibilityRow): FeasibilityHint {
  const needed = gb(row.needed_mb)
  const free = gb(row.gtt_free_mb)
  const total = gb(row.gtt_total_mb)

  switch (row.verdict) {
    case 'fits':
      return {
        tone: 'ok',
        text: `✓ model + ceiling ≈ ${needed} GB · ${free} GB GTT free — fits`,
      }
    case 'tight':
      return {
        tone: 'warn',
        text:
          `◐ ≈ ${needed} GB needed · ${free} GB GTT free — tight. ` +
          `May load with swap pressure; lower the ceiling or free another slot.`,
      }
    case 'exceeds':
      return {
        tone: 'err',
        text:
          `○ ≈ ${needed} GB needed · exceeds ${free} GB free — will likely fail to load. ` +
          `Saves anyway; the slot will report the failure.`,
      }
    case 'exceeds_total':
      return {
        tone: 'err',
        text:
          `○ ≈ ${needed} GB needed · exceeds ${total} GB total GTT — will likely fail to load. ` +
          `Saves anyway; the slot will report the failure.`,
      }
    default:
      return { tone: null, text: '' }
  }
}
