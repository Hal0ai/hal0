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
})
