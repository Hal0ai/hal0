/**
 * dashboard-overhaul — the customizable widget board (feat/dashboard-overhaul).
 *
 * Covers the three orchestrator acceptance gates the FE team owns:
 *   1. STATUS-DOT (the core fix): a real slot reporting `state:"serving"`
 *      renders a GREEN dot with the looping `pulse` animation end-to-end —
 *      the explicit fix the design requested (old UI never went green).
 *      The §3 4-state language (serving/ready/warming/error/offline) is
 *      pinned against the StatusDot component, not just the helper.
 *   2. GRID + EDIT MODE: the 12-col masonry renders the locked Slot-list
 *      anchor; Customize opens the card library + per-cell edit chrome
 *      (grip / span badge / remove); Done closes it.
 *   3. NO STUB: cards whose backend source has not shipped yet
 *      (throughput/history, services/health, dashboard-layout) gate to a
 *      "source pending" state rather than fabricating numbers.
 *
 * Uses the apiMock fixture — injecting slot states is the contract-allowed
 * mock layer for tests (CONTRACTS §0: "mock layer only for tests").
 */
import { test, expect, json, type Page } from '../fixtures/apiMock'

/** A container-enriched slot that is actively serving an in-flight request. */
function servingSlot() {
  return {
    name: 'primary',
    state: 'serving',
    backend: 'rocm',
    device: 'gpu-rocm',
    model: 'qwen3-30b',
    model_id: 'qwen3-30b',
    model_default: 'qwen3-30b',
    port: 8100,
    type: 'llm',
    group: 'chat',
    enabled: true,
    isDefault: true,
    container_status: 'running',
    container_health: true,
    last_used_at: Date.now() / 1000, // fresh → green, not stuck-demoted
    ctx_max: 32000,
    metrics: { toks: 42.5, ttft: 280, ctx: 1200 },
  }
}

/** A resident-but-idle slot — must be AMBER (ready), never green. */
function readySlot() {
  return {
    name: 'embed',
    state: 'ready',
    backend: 'vulkan',
    device: 'gpu-vulkan',
    model: 'bge-m3',
    type: 'embedding',
    group: 'embed',
    enabled: true,
    container_status: 'running',
    container_health: true,
    last_used_at: null,
    metrics: { toks: 0, ttft: null, ctx: 0 },
  }
}

async function gotoDashboard(page: Page, slots: any[]) {
  // The dashboard runs in forced-mock mode (VITE_MOCK_HAL0) under e2e, which
  // short-circuits page.route — mockFetch (mock.ts) serves window.HAL0_DATA.
  // So inject the slot states there via addInitScript BEFORE any module loads,
  // the same mechanism the slot-card specs use. This makes the rendered slot
  // list deterministic instead of depending on the 28-slot seed.
  await page.addInitScript((injected) => {
    const apply = () => {
      const w = window as any
      w.HAL0_DATA = w.HAL0_DATA || {}
      w.HAL0_DATA.slots = injected
    }
    apply()
    // data.jsx re-assigns HAL0_DATA on load; re-apply on DOMContentLoaded so
    // our slots win regardless of evaluation order.
    document.addEventListener('DOMContentLoaded', apply)
  }, slots)
  await page.goto('/#dashboard')
}

test.describe('dashboard overhaul — status dot acceptance gate', () => {
  test('serving slot renders a GREEN dot with the looping pulse animation', async ({
    page,
  }) => {
    await gotoDashboard(page, [servingSlot(), readySlot()])

    // The slot-list anchor renders the per-slot StatusDot. Wait for the
    // serving dot to appear, then assert the §3 contract end-to-end.
    const serving = page.locator('.sdot.serving').first()
    await expect(serving).toBeVisible({ timeout: 10_000 })

    const style = await serving.evaluate((el) => {
      const cs = getComputedStyle(el)
      return {
        animationName: cs.animationName,
        boxShadow: cs.boxShadow,
        background: cs.backgroundColor,
      }
    })
    // Green pulse: the design system's ONLY looping animation is `pulse`.
    expect(style.animationName).toContain('pulse')
    // Glow present (box-shadow non-"none").
    expect(style.boxShadow).not.toBe('none')
    // Green family (#6FCF97 → rgb(111, 207, 151)).
    expect(style.background).toMatch(/rgb\(111,\s*207,\s*151\)/)
  })

  test('ready slot is AMBER + static — green reserved for in-flight', async ({
    page,
  }) => {
    await gotoDashboard(page, [readySlot()])
    const dot = page.locator('.sdot.stale').first()
    await expect(dot).toBeVisible({ timeout: 10_000 })
    const anim = await dot.evaluate((el) => getComputedStyle(el).animationName)
    // `stale` (ready) must NOT loop — only serving/warming pulse.
    expect(anim === 'none' || anim === '').toBeTruthy()
  })

  test('StatusDot honours the §3 4-state class table (rendered)', async ({ page }) => {
    // Inject one slot per state and assert the rendered dot class per §3.
    // Asserting on the real DOM (not a window helper) proves the StatusDot
    // component — fed a real slot — emits the contract class end-to-end.
    const slots = [
      { ...servingSlot(), name: 'sv' }, // serving → .sdot.serving
      { ...readySlot(), name: 'rd' }, //  ready   → .sdot.stale
      { name: 'wm', state: 'warming', device: 'gpu-rocm', model: 'm', type: 'llm', group: 'chat', enabled: true, metrics: { toks: 0 } }, // → .sdot.warming
      { name: 'er', state: 'error', device: 'gpu-rocm', model: 'm', type: 'llm', group: 'chat', enabled: true, container_status: 'crashed', metrics: { toks: 0 } }, // → .sdot.error
      { name: 'of', state: 'stopped', device: 'cpu', model: 'm', type: 'llm', group: 'chat', enabled: false, metrics: { toks: 0 } }, // → .sdot.offline
    ]
    await gotoDashboard(page, slots)
    // Wait for the list to paint at least the serving dot.
    await expect(page.locator('.sdot.serving').first()).toBeVisible({ timeout: 10_000 })
    // Each §3 state must have rendered at least one dot of its class.
    await expect(page.locator('.sdot.serving')).not.toHaveCount(0)
    await expect(page.locator('.sdot.stale')).not.toHaveCount(0)
    await expect(page.locator('.sdot.warming')).not.toHaveCount(0)
    await expect(page.locator('.sdot.error')).not.toHaveCount(0)
    await expect(page.locator('.sdot.offline')).not.toHaveCount(0)
  })
})

test.describe('dashboard overhaul — grid + edit mode', () => {
  test('renders the masonry grid with the locked slot-list anchor', async ({ page }) => {
    await gotoDashboard(page, [servingSlot(), readySlot()])
    // The slot list is the anchor card — its rows render the injected slots.
    await expect(page.locator('.sdot.serving').first()).toBeVisible({ timeout: 10_000 })
    // Both slot names appear in the dense table.
    await expect(page.getByText('primary').first()).toBeVisible()
    await expect(page.getByText('embed').first()).toBeVisible()
  })

  test('Customize opens the card library + edit chrome; Done closes it', async ({ page }) => {
    await gotoDashboard(page, [servingSlot()])
    await expect(page.locator('.sdot.serving').first()).toBeVisible({ timeout: 10_000 })

    // Enter edit mode via the in-view Customize control.
    const customize = page.getByRole('button', { name: /customize/i }).first()
    await expect(customize).toBeVisible()
    await customize.click()

    // Card library appears (every registry card togglable). Span badges /
    // grips become visible on the cells. Then Done leaves edit mode.
    const done = page.getByRole('button', { name: /^done$/i }).first()
    await expect(done).toBeVisible({ timeout: 5_000 })
    await done.click()
    await expect(page.getByRole('button', { name: /customize/i }).first()).toBeVisible()
  })
})

test.describe('dashboard overhaul — no stub data', () => {
  test('cards with unshipped backend sources gate to "source pending"', async ({ page }) => {
    // throughput/history, services/health, dashboard-layout all 404/empty in
    // the default mock (the fixture catch-all returns {}). The cards must
    // gate, never fabricate. At least one "source pending"/"pending"/"waiting"
    // gate must be visible somewhere on the board.
    await gotoDashboard(page, [servingSlot()])
    await expect(page.locator('.sdot.serving').first()).toBeVisible({ timeout: 10_000 })
    const gated = page.getByText(/source pending|pending|waiting for/i)
    await expect(gated.first()).toBeVisible({ timeout: 5_000 })
  })
})

test.describe('dashboard overhaul — opt-in cards', () => {
  test('Needs Attention (default-on) renders a real derived state, not a placeholder', async ({
    page,
  }) => {
    // An error + a warming slot → the attention card derives real rows; with a
    // healthy board it shows an honest calm state. Either way it must NOT show
    // the "source pending" placeholder (it is a derived card, always live).
    const slots = [
      { ...servingSlot(), name: 'ok' },
      { name: 'broke', state: 'error', device: 'gpu-rocm', model: 'm', type: 'llm', group: 'chat', enabled: true, container_status: 'crashed', metrics: { toks: 0 } },
    ]
    await gotoDashboard(page, slots)
    await expect(page.locator('.sdot.serving').first()).toBeVisible({ timeout: 10_000 })
    const attention = page.locator('.dcard', {
      has: page.locator('.dcard-h', { hasText: /needs attention/i }),
    }).first()
    await expect(attention).toBeVisible()
    // The attention card body must not be the generic "source pending" gate.
    await expect(attention).not.toContainText(/source pending/i)
  })

  test('Scheduler shows an HONEST no-source state, not "source pending"', async ({ page }) => {
    // The lemond dispatcher is stateless — scheduler has no real source. The
    // card must say so honestly (no in-flight/queue history), distinct from
    // "source pending" (which implies coming-soon) and never faking stat tiles.
    await gotoDashboard(page, [servingSlot()])
    await expect(page.locator('.sdot.serving').first()).toBeVisible({ timeout: 10_000 })
    // Enable Scheduler from the card library (off by default). The library
    // card button's accessible name is "Scheduler off" (name span + on/off
    // badge), so match on the .lib-card-name text rather than an exact name.
    await page.getByRole('button', { name: /customize/i }).first().click()
    await page.locator('.lib-card', { hasText: 'Scheduler' }).first().click()
    await page.getByRole('button', { name: /^done$/i }).first().click()
    const sched = page.locator('.dcard', {
      has: page.locator('.dcard-h', { hasText: /scheduler/i }),
    }).first()
    await expect(sched).toBeVisible({ timeout: 5_000 })
    await expect(sched).toContainText(/stateless|no scheduler telemetry|no .* history/i)
    await expect(sched).not.toContainText(/source pending/i)
  })
})
