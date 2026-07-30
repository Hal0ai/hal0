/**
 * Port derivation for the Playwright dev server (#1399).
 *
 * The suite used to default to a fixed 5173 with `reuseExistingServer`, so two
 * git worktrees running e2e locally shared one Vite server: the second run
 * silently tested whatever branch the first had started. That produced
 * confidently wrong results in both directions — a real repro reported
 * `7 failed` that became `34 passed` on a unique port, same commits.
 *
 * The fix derives a STABLE port per worktree. Stable (not random) matters:
 * `reuseExistingServer` still gives the iteration speedup within a worktree,
 * while a different worktree can never land on the same number.
 */
import { describe, expect, it } from 'vitest'

import { resolveE2EPort } from './port'

const A = '/repos/hal0-mono/hal0-wt-alpha/ui'
const B = '/repos/hal0-mono/hal0-wt-beta/ui'

describe('resolveE2EPort', () => {
  it('honours an explicit HAL0_E2E_PORT above everything else', () => {
    expect(resolveE2EPort({ env: { HAL0_E2E_PORT: '5199' }, dir: A })).toBe(5199)
    // Explicit wins even in CI, where the fixed default would otherwise apply.
    expect(resolveE2EPort({ env: { HAL0_E2E_PORT: '5199', CI: '1' }, dir: A })).toBe(5199)
  })

  it('uses the fixed default in CI — one checkout, no contention', () => {
    expect(resolveE2EPort({ env: { CI: '1' }, dir: A })).toBe(5173)
    expect(resolveE2EPort({ env: { CI: 'true' }, dir: B })).toBe(5173)
  })

  it('derives DIFFERENT ports for different worktrees', () => {
    expect(resolveE2EPort({ env: {}, dir: A })).not.toBe(resolveE2EPort({ env: {}, dir: B }))
  })

  it('is stable for the same worktree across calls', () => {
    const first = resolveE2EPort({ env: {}, dir: A })
    expect(resolveE2EPort({ env: {}, dir: A })).toBe(first)
    expect(resolveE2EPort({ env: {}, dir: A })).toBe(first)
  })

  it('stays inside the reserved high range, clear of the 5173 default', () => {
    for (const dir of [A, B, '/x', '/a/very/deeply/nested/worktree/path/ui', '']) {
      const port = resolveE2EPort({ env: {}, dir })
      expect(port).toBeGreaterThanOrEqual(5300)
      expect(port).toBeLessThanOrEqual(5999)
      expect(Number.isInteger(port)).toBe(true)
      expect(port).not.toBe(5173)
    }
  })

  it('spreads a realistic fleet of worktrees without collision', () => {
    // The actual naming convention in this repo — a hash that collided across
    // these would reintroduce the bug for the exact layout we run.
    const dirs = [
      'wiring-specs', 'clear-template', 'migrate-flags', 'e2eport2', 'enabled-removal',
      'ctx-size', 'drawer-validation', '1378-ctx', '1380-mmproj', '1381-name',
    ].map((n) => `/mnt/dev/repos/hal0-mono/hal0-wt-${n}/ui`)
    const ports = dirs.map((dir) => resolveE2EPort({ env: {}, dir }))
    expect(new Set(ports).size).toBe(dirs.length)
  })

  it('ignores an unusable HAL0_E2E_PORT rather than crashing the run', () => {
    // A typo'd override must not silently become NaN in the vite --port arg.
    for (const bad of ['', '  ', 'abc', '0', '-1', '70000', '5173.5']) {
      const port = resolveE2EPort({ env: { HAL0_E2E_PORT: bad }, dir: A })
      expect(port).toBeGreaterThanOrEqual(5300)
      expect(port).toBeLessThanOrEqual(5999)
    }
  })
})
