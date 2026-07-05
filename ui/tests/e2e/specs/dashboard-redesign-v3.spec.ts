/**
 * dashboard-redesign — the fixed-band, swap-in-place dashboard
 * (design_handoff_dashboard_redesign). Replaces dashboard-overhaul-v3.spec.ts
 * (the free-form DashGrid board was removed with the redesign).
 *
 * Covers the acceptance gates the FE owns:
 *   1. STATUS-DOT: a real slot reporting `state:"serving"` renders a GREEN
 *      dot with the looping `pulse` animation end-to-end; ready is AMBER +
 *      static. The §3 4-state vocabulary is pinned against the rendered DOM.
 *   2. FIXED BANDS: hero strip, 5-cell health strip, unified-memory hero,
 *      band A (3 cells), the locked full-width Slots card (dense rows for
 *      the injected slots), band C (3 cells). No drag/resize affordances.
 *   3. SWAP-IN-PLACE: ⇄ opens a per-cell whitelist picker; choosing another
 *      widget replaces the cell's widget in place (layout PUT is fail-soft).
 *      Customize toggles swap mode and exposes the quick-actions toggle.
 *   4. NO STUB: widgets whose backend source has not shipped (requests
 *      rollup, throughput history) gate to "source pending" / "—", never
 *      fabricated numbers.
 *   5. NEEDS ATTENTION derives real state: an error slot yields an item
 *      with inline actions; a healthy board reads "nothing needs you".
 *
 * Uses the apiMock fixture — injecting slot states is the contract-allowed
 * mock layer for tests (CONTRACTS §0: "mock layer only for tests").
 */
import { test, expect, type Page } from '../fixtures/apiMock'

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
  // Forced-mock mode (VITE_MOCK_HAL0) serves window.HAL0_DATA — inject the
  // slot states there via addInitScript BEFORE any module loads, the same
  // mechanism the slot-card specs use.
  await page.addInitScript((injected) => {
    const apply = () => {
      const w = window as any
      w.HAL0_DATA = w.HAL0_DATA || {}
      w.HAL0_DATA.slots = injected
    }
    apply()
    document.addEventListener('DOMContentLoaded', apply)
  }, slots)
  await page.goto('/#dashboard')
}

test.describe('dashboard redesign — status dot acceptance gate', () => {
  test('serving slot renders a GREEN dot with the looping pulse animation', async ({
    page,
  }) => {
    await gotoDashboard(page, [servingSlot(), readySlot()])

    const serving = page.locator('.rd-slot-row .sdot.serving').first()
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
    expect(style.boxShadow).not.toBe('none')
    // Green family (#6FCF97 → rgb(111, 207, 151)).
    expect(style.background).toMatch(/rgb\(111,\s*207,\s*151\)/)
  })

  test('ready slot is AMBER + static — green reserved for in-flight', async ({
    page,
  }) => {
    await gotoDashboard(page, [readySlot()])
    const dot = page.locator('.rd-slot-row .sdot.stale').first()
    await expect(dot).toBeVisible({ timeout: 10_000 })
    const anim = await dot.evaluate((el) => getComputedStyle(el).animationName)
    expect(anim === 'none' || anim === '').toBeTruthy()
  })

  test('slot rows honour the §3 4-state class table (rendered)', async ({ page }) => {
    const slots = [
      { ...servingSlot(), name: 'sv' }, // serving → .sdot.serving
      { ...readySlot(), name: 'rd' }, //  ready   → .sdot.stale
      { name: 'wm', state: 'warming', device: 'gpu-rocm', model: 'm', type: 'llm', group: 'chat', enabled: true, metrics: { toks: 0 } }, // → .sdot.warming
      { name: 'er', state: 'error', device: 'gpu-rocm', model: 'm', type: 'llm', group: 'chat', enabled: true, container_status: 'crashed', metrics: { toks: 0 } }, // → .sdot.error
      { name: 'of', state: 'stopped', device: 'cpu', model: 'm', type: 'llm', group: 'chat', enabled: false, metrics: { toks: 0 } }, // → .sdot.offline
    ]
    await gotoDashboard(page, slots)
    await expect(page.locator('.rd-slot-row .sdot.serving').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.rd-slot-row .sdot.serving')).not.toHaveCount(0)
    await expect(page.locator('.rd-slot-row .sdot.stale')).not.toHaveCount(0)
    await expect(page.locator('.rd-slot-row .sdot.warming')).not.toHaveCount(0)
    await expect(page.locator('.rd-slot-row .sdot.error')).not.toHaveCount(0)
    await expect(page.locator('.rd-slot-row .sdot.offline')).not.toHaveCount(0)
  })
})

test.describe('dashboard redesign — fixed bands', () => {
  test('renders every band with the locked slots card and no grid-edit chrome', async ({ page }) => {
    await gotoDashboard(page, [servingSlot(), readySlot()])

    // Health strip: exactly 5 cells, labelled per the design.
    await expect(page.locator('.rd-health-cell')).toHaveCount(5)
    await expect(page.locator('.rd-health-k')).toHaveText([
      'slots', 'throughput', 'unified memory', 'igpu', 'needs attention',
    ])

    // Unified memory hero + band A (3 cells) + band C (3 cells).
    await expect(page.locator('.rd-mem-bar')).toBeVisible()
    await expect(page.locator('.rd-band-a > *')).toHaveCount(3)
    await expect(page.locator('.rd-band-c > *')).toHaveCount(3)

    // Band-A defaults by header title.
    const titles = page.locator('.rd-band-a .rd-card-title')
    await expect(titles).toHaveText(['Throughput', 'Utilization', 'Requests'])

    // Locked slots card renders the injected slots as dense rows.
    await expect(page.locator('.rd-slot-row')).toHaveCount(2)
    await expect(page.getByText('primary').first()).toBeVisible()
    await expect(page.getByText('embed').first()).toBeVisible()
    // Serving row shows live tok/s in the state column.
    await expect(page.locator('.rd-slot-state').first()).toContainText('tok/s')

    // The old free-form grid edit chrome must be gone.
    await expect(page.locator('.card-library')).toHaveCount(0)
    await expect(page.locator('.cell-resize-handle')).toHaveCount(0)
    await expect(page.locator('.cell-grip')).toHaveCount(0)
  })
})

test.describe('dashboard redesign — swap-in-place', () => {
  test('⇄ opens the per-cell whitelist and swaps the widget in place', async ({ page }) => {
    await gotoDashboard(page, [servingSlot()])
    await expect(page.locator('.rd-band-a .rd-card-title').first()).toHaveText('Throughput')

    // Open the a1 picker (first swappable band-A cell).
    await page.locator('.rd-band-a .rd-swap').first().click()
    const menu = page.locator('.rd-swap-menu')
    await expect(menu).toBeVisible()
    // Whitelist: current widget flagged, unbuilt entries disabled.
    await expect(menu.locator('.rd-swap-item.current')).toContainText('throughput')

    // Swap to the built per-slot throughput widget.
    await menu.locator('.rd-swap-item', { hasText: 'per-slot throughput' }).click()
    // The cell re-renders in place with the swapped-in card (DCard shell).
    await expect(page.locator('.rd-band-a .dcard-h').first()).toContainText(/per-slot/i, { timeout: 5_000 })
    // Layout never reflows: still exactly 3 cells in band A.
    await expect(page.locator('.rd-band-a > *')).toHaveCount(3)
  })

  test('customize toggles swap mode + quick-actions visibility control', async ({ page }) => {
    await gotoDashboard(page, [servingSlot()])
    const customize = page.getByRole('button', { name: /^customize$/i })
    await expect(customize).toBeVisible()
    await customize.click()

    // Swap mode: the quick-actions toggle chip appears; button reads done.
    await expect(page.locator('.rd-qa-toggle')).toBeVisible()
    const done = page.getByRole('button', { name: /^done$/i })
    await expect(done).toBeVisible()

    // Toggle quick actions off — the strip hides (fail-soft in-memory PUT).
    await expect(page.locator('.rd-qa')).toBeVisible()
    await page.locator('.rd-qa-toggle').click()
    await expect(page.locator('.rd-qa')).toHaveCount(0)

    await done.click()
    await expect(page.getByRole('button', { name: /^customize$/i })).toBeVisible()
  })

  test('locked cells (slots, needs attention) expose no ⇄ control', async ({ page }) => {
    await gotoDashboard(page, [servingSlot()])
    await expect(page.locator('.rd-slot-row').first()).toBeVisible({ timeout: 10_000 })
    // Slots card header has no swap button.
    const slotsCard = page.locator('.rd-card', { has: page.locator('.rd-card-title', { hasText: /^Slots$/ }) })
    await expect(slotsCard.locator('.rd-swap')).toHaveCount(0)
    // Attention card header has no swap button.
    const attn = page.locator('.rd-card', { has: page.locator('.rd-card-title', { hasText: /needs attention/i }) })
    await expect(attn.locator('.rd-swap')).toHaveCount(0)
  })
})

test.describe('dashboard redesign — no stub data', () => {
  test('widgets with unshipped backend sources gate to "source pending" / "—"', async ({ page }) => {
    // requests rollup, throughput history, dashboard-layout all 404/{} in the
    // default mock. The widgets must gate, never fabricate.
    await gotoDashboard(page, [servingSlot()])
    await expect(page.locator('.rd-slot-row').first()).toBeVisible({ timeout: 10_000 })
    const gated = page.getByText(/source pending/i)
    await expect(gated.first()).toBeVisible({ timeout: 5_000 })
    // Requests hero renders an em-dash, not a number.
    const reqCard = page.locator('.rd-card', { has: page.locator('.rd-card-title', { hasText: /^Requests$/ }) })
    await expect(reqCard.locator('.rd-hero-num')).toHaveText('—')
  })
})

test.describe('dashboard redesign — needs attention', () => {
  test('derives real items with inline actions from an error slot', async ({ page }) => {
    const slots = [
      { ...servingSlot(), name: 'ok' },
      { name: 'broke', state: 'error', device: 'gpu-rocm', model: 'm', type: 'llm', group: 'chat', enabled: true, container_status: 'crashed', metrics: { toks: 0 } },
    ]
    await gotoDashboard(page, slots)
    const attn = page.locator('.rd-card', { has: page.locator('.rd-card-title', { hasText: /needs attention/i }) })
    await expect(attn).toBeVisible({ timeout: 10_000 })
    await expect(attn.locator('.rd-attn-item')).not.toHaveCount(0)
    await expect(attn.locator('.rd-attn-eyebrow').first()).toContainText(/slot · error/i)
    await expect(attn.getByRole('button', { name: 'Restart' })).toBeVisible()
    // Health strip mirrors the same derived count.
    await expect(page.locator('.rd-health-cell').last()).toContainText('1')
  })

  test('healthy board reads "nothing needs you" with an all-clear strip', async ({ page }) => {
    await gotoDashboard(page, [servingSlot(), readySlot()])
    const attn = page.locator('.rd-card', { has: page.locator('.rd-card-title', { hasText: /needs attention/i }) })
    await expect(attn).toBeVisible({ timeout: 10_000 })
    await expect(attn).toContainText(/nothing needs you/i)
    await expect(page.locator('.rd-health-cell').last()).toContainText(/all clear/i)
  })
})
