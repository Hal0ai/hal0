/**
 * slot-live-equivalence — pins isSlotLive() (slot-status.js, used by
 * memory-map for per-slot memory attribution) against the LEGACY behaviour
 * it replaced: a static `LIVE_STATES = {ready, serving, idle, warming}` set
 * matched on `slot.state`.
 *
 * Regression guard for the N1 refactor (PR #668): when slotPhase() was first
 * wired into memory-map, lemond `warming` + `idle` slots silently dropped out
 * of memory attribution (slotPhase().isLive folds in enabled + lemonade_state,
 * which the legacy set never did). CI missed it because the mocks had no
 * idle/warming lemond slots. This spec encodes the exact equivalence so it
 * cannot regress again.
 *
 *   Lemond:  isSlotLive(slot) === LIVE_STATES.has(slot.state)
 *            for ALL state strings — independent of enabled / lemonade_state.
 *   Container: isSlotLive = running + healthy (own rule, not state-string).
 */
import { test, expect } from '../fixtures/apiMock'

// The exact legacy set memory-map.jsx matched on slot.state.
const LEGACY_LIVE_STATES = new Set(['ready', 'serving', 'idle', 'warming'])

test.describe('isSlotLive — lemond ≡ legacy LIVE_STATES', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/#slots')
    await page.waitForFunction(() => typeof (window as any).isSlotLive === 'function')
  })

  // Every state string the dashboard can show — live ones AND not-live ones.
  const states = [
    'ready', 'serving', 'idle', 'warming', // legacy-live
    'offline', 'error', 'starting', 'pulling', 'unloading', '', 'unknown',
  ]

  for (const state of states) {
    const expectedLive = LEGACY_LIVE_STATES.has(state)
    test(`lemond state="${state || '<empty>'}" → live=${expectedLive}`, async ({ page }) => {
      const live = await page.evaluate<boolean, string>((s) => {
        // No runtime / container_status → lemond path.
        return (window as any).isSlotLive({ state: s })
      }, state)
      expect(live).toBe(expectedLive)
    })
  }

  // The two states that regressed in PR #668 — pinned explicitly with the
  // extra fields that previously flipped them off (enabled / lemonade_state).
  test('lemond warming slot stays LIVE even if lemonade_state is empty (regression #668)', async ({ page }) => {
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({ state: 'warming', lemonade_state: '' }),
    )
    expect(live).toBe(true)
  })

  test('lemond idle slot stays LIVE (evicted-but-attributed; regression #668)', async ({ page }) => {
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({ state: 'idle', lemonade_state: 'idle' }),
    )
    expect(live).toBe(true)
  })

  test('lemond ready slot stays LIVE even when enabled=false (legacy ignored enabled)', async ({ page }) => {
    // Legacy LIVE_STATES.has('ready') === true regardless of enabled; a
    // disabled-but-resident slot still held memory, so it must still attribute.
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({ state: 'ready', enabled: false }),
    )
    expect(live).toBe(true)
  })

  test('lemond offline+lemonade_state=loaded is NOT live (matches legacy state-only test)', async ({ page }) => {
    // Legacy keyed solely off slot.state — state="offline" was never live,
    // even if a stale lemonade_state lingered.
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({ state: 'offline', lemonade_state: 'loaded' }),
    )
    expect(live).toBe(false)
  })
})

test.describe('isSlotLive — container rule', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/#slots')
    await page.waitForFunction(() => typeof (window as any).isSlotLive === 'function')
  })

  test('container running + healthy → live', async ({ page }) => {
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({
        runtime: 'container', container_status: 'running', container_health: true,
      }),
    )
    expect(live).toBe(true)
  })

  test('container running + UNhealthy → not live', async ({ page }) => {
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({
        runtime: 'container', container_status: 'running', container_health: false,
      }),
    )
    expect(live).toBe(false)
  })

  test('container starting → not live', async ({ page }) => {
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({
        container_status: 'starting', container_health: false,
      }),
    )
    expect(live).toBe(false)
  })

  test('container stopped → not live', async ({ page }) => {
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({ container_status: 'stopped' }),
    )
    expect(live).toBe(false)
  })
})
