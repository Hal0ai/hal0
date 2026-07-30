/**
 * Dev-server port selection for the Playwright suite (#1399).
 *
 * WHY THIS ISN'T JUST `5173`
 * --------------------------
 * `playwright.config.ts` sets `reuseExistingServer: !CI`, so a run attaches to
 * any server already listening on its port. With a constant default, two git
 * worktrees running e2e locally shared one Vite server and the second run
 * silently exercised the FIRST one's code — reporting results for a branch it
 * never checked out.
 *
 * That is not flakiness; it is confident, repeatable wrongness in both
 * directions. Observed: a combined-fix verification reported `7 failed`, and
 * the identical commits on a unique port reported `34 passed`. The failures
 * described a branch that wasn't under test.
 *
 * So the default port is derived from the worktree's own path. Properties that
 * matter, in order:
 *
 *   1. STABLE per worktree — a random port each run would fix the collision but
 *      also defeat `reuseExistingServer`, paying a cold Vite start every run.
 *   2. DISTINCT across worktrees — the actual bug.
 *   3. Clear of 5173 — so a stale server from before this change can never be
 *      reused by a run that now expects isolation.
 *
 * `HAL0_E2E_PORT` still wins when set explicitly (CI pinning, debugging, or
 * attaching to a hand-started server). CI keeps the fixed port: one checkout,
 * no contention, and `reuseExistingServer` is off there anyway.
 */
import { createHash } from 'node:crypto'

/** Fixed port for CI — single checkout, no cross-worktree contention. */
export const CI_PORT = 5173

/** Derived-port window. Above the Vite default block, below 6000. */
export const RANGE_START = 5300
export const RANGE_END = 5999

const SPAN = RANGE_END - RANGE_START + 1

/**
 * A usable TCP port, or null. Guards the override so a typo (`HAL0_E2E_PORT=abc`,
 * a stray empty string from `export HAL0_E2E_PORT=`) degrades to the derived
 * port instead of putting `NaN` into the `vite --port` argument, where it fails
 * far from the cause.
 */
function parsePort(raw: string | undefined): number | null {
  if (raw == null) return null
  const trimmed = raw.trim()
  if (!/^\d+$/.test(trimmed)) return null
  const n = Number(trimmed)
  return n >= 1 && n <= 65_535 ? n : null
}

/** Stable path → port. sha256 so unrelated sibling paths don't cluster. */
function derivePort(dir: string): number {
  const digest = createHash('sha256').update(dir).digest()
  return RANGE_START + (digest.readUInt32BE(0) % SPAN)
}

export interface ResolveE2EPortOptions {
  /** Process environment (injected so the resolution is unit-testable). */
  env: Record<string, string | undefined>
  /** Absolute path identifying this checkout — the config file's directory. */
  dir: string
}

/**
 * Resolve the port this run should use.
 *
 * Precedence: explicit `HAL0_E2E_PORT` > fixed CI port > per-worktree derived.
 */
export function resolveE2EPort({ env, dir }: ResolveE2EPortOptions): number {
  const explicit = parsePort(env.HAL0_E2E_PORT)
  if (explicit != null) return explicit
  if (env.CI) return CI_PORT
  return derivePort(dir)
}
