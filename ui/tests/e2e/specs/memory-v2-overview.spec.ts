/**
 * memory-v2-overview — Playwright coverage for the Memory v2 Bank workspace
 * Overview (`window.MemV2Overview`, task C2).
 *
 * Unskipped by task C6: `#memory` now renders `MemoryView`'s Overview
 * sub-tab, which mounts `window.MemV2Overview` directly (memory.jsx). Runs
 * against the default forced-mock dataset — no page.route overrides
 * needed.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('memory-v2 Overview', () => {
  test('engine panel renders stats from the mock engine card', async ({ page }) => {
    await page.goto('/#memory')
    const panel = page.getByTestId('mv-engine-panel')
    await expect(panel).toBeVisible()
    // buildMemoryEngine() mock: reachable: true, engine: 'hindsight'.
    await expect(panel).toContainText('reachable')
    await expect(panel).toContainText('hindsight')
  })

  test('bank table renders a row for each of the six mock banks', async ({ page }) => {
    await page.goto('/#memory')
    // MEM_BANKS in mockFixtures.ts: primary, big, hermes, scratch, ingest, empty.
    for (const bank of ['primary', 'big', 'hermes', 'scratch', 'ingest', 'empty']) {
      await expect(page.getByTestId(`mv-bank-row-${bank}`)).toBeVisible()
    }
  })

  test('growth chart renders bars for the primary bank and range switch works', async ({ page }) => {
    await page.goto('/#memory')
    const growth = page.getByTestId('mv-growth')
    await expect(growth).toBeVisible()
    // The chart stacks 3 rects (world/experience/observation) per bucket —
    // any individual segment can legitimately be a real, correctly-drawn
    // height="0" rect on a day with zero facts of that type, so assert on
    // the total rect count rather than the first element's visibility.
    await expect(growth.locator('svg rect')).not.toHaveCount(0)

    await page.getByTestId('mv-growth-range-7d').click()
    await expect(page.getByTestId('mv-growth-range-7d')).toHaveClass(/on/)
    await page.getByTestId('mv-growth-range-1d').click()
    await expect(page.getByTestId('mv-growth-range-1d')).toHaveClass(/on/)
    await page.getByTestId('mv-growth-range-30d').click()
    await expect(page.getByTestId('mv-growth-range-30d')).toHaveClass(/on/)
  })

  test('explore button navigates to #memory/bank?bank=primary', async ({ page }) => {
    await page.goto('/#memory')
    await page.getByTestId('mv-bank-explore-primary').click()
    await expect(page).toHaveURL(/#memory\/bank\?bank=primary/)
  })

  // ── ADR-0023 graph-extraction panel (ported from the deleted
  // memory-graph-v3.spec.ts, task C7) — MemoryGraphPanel itself is
  // untouched and reused verbatim; its canonical home now is here, embedded
  // in MemV2Overview's EnginePanel, rather than the old #agent → Memory tab
  // path (which still works independently via memory-tab.jsx's pointer
  // card, unaffected by this migration). All 3 assertions here were
  // `test.skip`-ed in the deleted spec; they run for real here.
  test('ADR-0023 panel: default OFF state shows the enable affordance', async ({ page }) => {
    // buildMemoryGraphStatus() mock already defaults to enabled: false —
    // no page.route override needed.
    await page.goto('/#memory')
    const panel = page.getByTestId('mv-engine-panel')
    await expect(panel).toContainText('Graph extraction')
    await expect(panel).toContainText('OFF')
    await expect(panel.getByRole('button', { name: 'Enable graph extraction' })).toBeVisible()
  })

  test('ADR-0023 panel: enable with a chosen extraction_slot sends the correct PUT payload', async ({
    page,
  }) => {
    let putBody: unknown = null
    await page.route('**/api/memory/graph', async (route) => {
      putBody = route.request().postDataJSON()
      await route.fulfill({ json: { ...(putBody as object), status: { enabled: true } } })
    })
    await page.goto('/#memory')
    const panel = page.getByTestId('mv-engine-panel')
    await panel.getByRole('button', { name: 'Enable graph extraction' }).click()
    await page.locator('[data-testid=graph-slot-select]').selectOption('agent')
    await page.getByRole('button', { name: /Enable graph extraction|Save/i }).click()
    await expect.poll(() => putBody).toMatchObject({ enabled: true, extraction_slot: 'agent' })
  })

  test('ADR-0023 panel: disclosure + caveat copy match ADR §3 + §4 verbatim', async ({ page }) => {
    await page.goto('/#memory')
    const panel = page.getByTestId('mv-engine-panel')
    await panel.getByRole('button', { name: 'Enable graph extraction' }).click()
    await expect(panel).toContainText(/Graph extraction sends ingested memory text/)
    await expect(panel).toContainText(
      /Graph quality varies by model\. We don't currently measure it for you/,
    )
  })
})
