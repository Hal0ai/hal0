/**
 * memory-graph-empty-v3 — regression for the empty-bank "lock up".
 *
 * When a bank with no graph data was selected, the explorer rendered its
 * `.mem-graph-empty` placeholder. That placeholder carried `position:absolute;
 * inset:0`, but the A/B/C overhaul had moved it from the old position:relative
 * `.mem-graph-stage` into the static `.mg-host` — so the absolute box escaped
 * to the viewport (full-page, transparent) and intercepted every pointer event.
 * The whole dashboard became unclickable: any empty bank/search/filter "locked
 * up" the UI.
 *
 * These specs select the baked `empty` bank (fact_count 0, empty graph) and
 * assert the placeholder is shown AND that the toolbar controls above it stay
 * hit-testable — i.e. the empty box does not overlap the toolbar.
 */
import { test, expect } from '../fixtures/apiMock'

async function gotoEmptyGraph(page: any) {
  await page.addInitScript(() => {
    localStorage.setItem('hal0.mem.bank', 'empty')
    localStorage.setItem('hal0.mem.dir', 'a')
  })
  await page.goto('/#memory/graph')
  await page.waitForSelector('[data-testid="mem-graph-explorer"]', { timeout: 10_000 })
  // ensure the empty bank is the active selection
  await page.selectOption('[data-testid="mem-graph-bank"]', 'empty')
  await expect(page.locator('[data-testid="mem-graph-bank"]')).toHaveValue('empty')
}

test.describe('Memory graph — empty bank', () => {
  test('shows the empty placeholder for a bank with no graph data', async ({ page }) => {
    await gotoEmptyGraph(page)
    await expect(page.locator('.mem-graph-empty')).toContainText('No graph data')
    await expect(page.locator('[data-testid="mem-graph-meta"]')).toContainText('0 nodes')
  })

  test('empty placeholder does not overlay the toolbar (controls stay clickable)', async ({ page }) => {
    await gotoEmptyGraph(page)
    await expect(page.locator('.mem-graph-empty')).toBeVisible()

    // The placeholder must be contained below the toolbar, not stretched over
    // the whole viewport. Assert no vertical overlap between the empty box and
    // the toolbar, and that hit-testing the bank picker hits the picker itself.
    const overlap = await page.evaluate(() => {
      const empty = document.querySelector('.mem-graph-empty') as HTMLElement
      const toolbar = document.querySelector('.mg-toolbar') as HTMLElement
      const bank = document.querySelector('[data-testid="mem-graph-bank"]') as HTMLElement
      const er = empty.getBoundingClientRect()
      const tr = toolbar.getBoundingClientRect()
      const br = bank.getBoundingClientRect()
      const hit = document.elementFromPoint(br.x + br.width / 2, br.y + br.height / 2)
      return {
        coversToolbar: er.top <= tr.top, // bug: empty box starts at/above the toolbar
        emptyTop: Math.round(er.top),
        toolbarBottom: Math.round(tr.bottom),
        bankHitsItself: hit === bank || bank.contains(hit as Node),
      }
    })
    expect(overlap.coversToolbar).toBe(false)
    expect(overlap.emptyTop).toBeGreaterThanOrEqual(overlap.toolbarBottom)
    expect(overlap.bankHitsItself).toBe(true)

    // And a real click on a toolbar control must succeed (was blocked by the
    // overlay). Switching the direction is a representative interaction.
    await page.locator('.mg-dirswitch button', { hasText: 'Structured' }).click()
    await expect(page.locator('.mg-dirswitch button', { hasText: 'Structured' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })
})
