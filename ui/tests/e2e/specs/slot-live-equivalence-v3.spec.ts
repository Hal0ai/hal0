/**
 * slot-live-equivalence — pins isSlotLive() (slot-status.js, used by
 * memory-map for per-slot memory attribution) against the LEGACY behaviour
 * it replaced: a static `LIVE_STATES = {ready, serving, idle, warming}` set
 * matched on `slot.state`.
 *
 * Regression guard for the N1 refactor (PR #668): when slotPhase() was first
 * wired into memory-map, `warming` + `idle` slots silently dropped out of
 * memory attribution (slotPhase().isLive folds in enabled, which the legacy
 * set never did). CI missed it because the mocks had no idle/warming slots.
 * This spec encodes the exact equivalence so it cannot regress again.
 *
 *   Fallback:  un-enriched snapshots (no container_status — e.g. a stale
 *              /api/status union entry) classify on the bare state string:
 *              isSlotLive(slot) === LIVE_STATES.has(slot.state)
 *              for ALL state strings — independent of enabled.
 *   Container: isSlotLive = running + healthy (own rule, not state-string).
 */
import { test, expect } from '../fixtures/apiMock'

// The exact legacy set memory-map.jsx matched on slot.state.
const LEGACY_LIVE_STATES = new Set(['ready', 'serving', 'idle', 'warming'])

test.describe('isSlotLive — state-string fallback ≡ legacy LIVE_STATES', () => {
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
    test(`fallback state="${state || '<empty>'}" → live=${expectedLive}`, async ({ page }) => {
      const live = await page.evaluate<boolean, string>((s) => {
        // No container_status → bare state-string fallback path.
        return (window as any).isSlotLive({ state: s })
      }, state)
      expect(live).toBe(expectedLive)
    })
  }

  // The two states that regressed in PR #668 — pinned explicitly.
  test('un-enriched warming slot stays LIVE (regression #668)', async ({ page }) => {
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({ state: 'warming' }),
    )
    expect(live).toBe(true)
  })

  test('un-enriched idle slot stays LIVE (evicted-but-attributed; regression #668)', async ({ page }) => {
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({ state: 'idle' }),
    )
    expect(live).toBe(true)
  })

  test('un-enriched ready slot stays LIVE even when enabled=false (legacy ignored enabled)', async ({ page }) => {
    // Legacy LIVE_STATES.has('ready') === true regardless of enabled; a
    // disabled-but-resident slot still held memory, so it must still attribute.
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({ state: 'ready', enabled: false }),
    )
    expect(live).toBe(true)
  })

  test('offline slot is NOT live even with a stale health flag (state-only fallback)', async ({ page }) => {
    // The fallback keys solely off slot.state — state="offline" was never
    // live, even if a stale container_health lingered without enrichment.
    const live = await page.evaluate(() =>
      (window as any).isSlotLive({ state: 'offline', container_health: true }),
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
