/**
 * memory-graph-error-v3 — an engine outage must not look like an empty bank.
 *
 * #1471: the explorer stage only consulted `isLoading` and the node count, so a
 * 503 `memory.unavailable` / `memory.engine_unreachable` from
 * /api/memory/banks/{id}/graph painted the same
 * "No graph data for this bank/filter." placeholder as a genuinely empty bank.
 * Every sibling surface already distinguishes the two (the Overview engine
 * card's "unreachable" chip, the settings panel's statusQuery.isError branch),
 * and this is the tab where "empty" is the most plausible-looking lie, because
 * a new install legitimately has an empty graph.
 *
 * COVERAGE HISTORY (#1498). These error-path tests could not run when #1471
 * shipped. The memory graph routes are on the forced-mock allowlist WITHOUT
 * `networkFirst`, so `mockFetch` substituted a baked payload BEFORE issuing any
 * request — a `page.on('request')` probe over a full mount recorded zero
 * requests matching `/graph`, and a `page.route` 503 was therefore never
 * reached. The spec shipped asserting only what it honestly could. The
 * `__hal0MockPassthrough` escape hatch added in #1498/#1527 is what makes an
 * outage representable at all, so the assertions are restored here.
 */
import { test, expect } from '../fixtures/apiMock'

const ENGINE_DOWN = {
  error: {
    code: 'memory.engine_unreachable',
    message: 'hindsight-api is not responding',
    details: {},
  },
}

/** Claim the bank-scoped paths from forced-mock, then fail every one of them. */
async function breakGraphEndpoints(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    // Everything under a bank — graph, subgraph, entities/graph — is driven by
    // this spec rather than substituted. The bank LIST is left mocked so a bank
    // still resolves and the graph query actually fires.
    ;(window as unknown as { __hal0MockPassthrough?: unknown }).__hal0MockPassthrough = [
      '/api/memory/banks/',
    ]
  })
  await page.route('**/api/memory/banks/*/**', (route) =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify(ENGINE_DOWN),
    }),
  )
}

async function gotoGraph(page: import('@playwright/test').Page, bank = 'shared') {
  await page.addInitScript((b) => {
    localStorage.setItem('hal0.mem.bank', b)
    localStorage.setItem('hal0.mem.dir', 'a')
  }, bank)
  await page.goto('/#memory/graph')
  await page.waitForSelector('[data-testid="mem-graph-explorer"]', { timeout: 10_000 })
}

test.describe('Memory graph — engine outage (#1471, coverage restored #1498)', () => {
  test('a 503 renders an explicit unreachable state, not the empty placeholder', async ({ page }) => {
    await breakGraphEndpoints(page)
    await gotoGraph(page)

    // `retry: 1` in the shared QueryClient means two attempts before isError.
    const err = page.getByTestId('mem-graph-error')
    await expect(err).toBeVisible({ timeout: 15_000 })
    await expect(err).toContainText('Memory engine unreachable')

    // The lie this fix is about: the empty placeholder must NOT be what an
    // operator sees when the engine is down.
    await expect(page.locator('.mem-graph-empty')).toHaveCount(0)
  })

  test('the unreachable state offers a retry affordance', async ({ page }) => {
    await breakGraphEndpoints(page)
    await gotoGraph(page)

    const retry = page.getByTestId('mem-graph-retry')
    await expect(retry).toBeVisible({ timeout: 15_000 })
    // Clicking must re-issue rather than throw — the button is the only way
    // out of this state without a page reload.
    await retry.click()
    await expect(page.getByTestId('mem-graph-error')).toBeVisible()
  })

  test('a healthy but empty bank still shows the empty placeholder, not an error', async ({ page }) => {
    // The regression guard: the error branch is evaluated FIRST in the stage,
    // so it must stay strictly scoped to actual query failures. No passthrough
    // here — forced-mock serves its normal payload, as every other spec sees.
    await gotoGraph(page, 'empty')
    await page.selectOption('[data-testid="mem-graph-bank"]', 'empty')
    await expect(page.locator('.mem-graph-empty')).toContainText('No graph data')
    await expect(page.getByTestId('mem-graph-error')).toHaveCount(0)
  })
})
