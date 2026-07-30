/**
 * memory-graph-error-v3 — the empty placeholder must stay scoped to actually
 * empty banks now that an error branch sits in front of it (#1471).
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
 * COVERAGE LIMIT — read before adding to this file. The error branch itself is
 * NOT exercised here, and that is deliberate rather than an oversight: under
 * the e2e harness the explorer never issues a graph request at all (verified —
 * a `page.on('request')` probe over a full mount recorded zero requests
 * matching `/graph`), so `activeQuery` is the inert
 * `{ data: null, isLoading: false }` fallback with no `isError` to assert on,
 * and a `page.route` 503 is never reached. Stubbing `window.__hal0UseBankGraph`
 * via `addInitScript` does not work either — `memory-hook-bridge.ts`
 * `Object.assign`s over it at module load. Covering the error state properly
 * needs the memory-banks + graph endpoints wired into `fixtures/apiMock.ts`
 * so a bank resolves and the query actually runs; that is worth doing, but it
 * is harness work, not part of this fix.
 *
 * What IS asserted below is the regression the change could plausibly cause:
 * the new error branch is evaluated FIRST in the stage, so if it were not
 * strictly scoped to real query failures it would swallow the empty state.
 * The companion memory-graph-empty-v3 spec pins the rest of that behaviour.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Memory graph — the error branch does not swallow the empty state (#1471)', () => {
  test('a healthy but empty bank still shows the empty placeholder, not an error', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('hal0.mem.bank', 'empty')
      localStorage.setItem('hal0.mem.dir', 'a')
    })
    await page.goto('/#memory/graph')
    await page.waitForSelector('[data-testid="mem-graph-explorer"]', { timeout: 10_000 })
    await page.selectOption('[data-testid="mem-graph-bank"]', 'empty')

    await expect(page.locator('.mem-graph-empty')).toContainText('No graph data')
    await expect(page.getByTestId('mem-graph-error')).toHaveCount(0)
  })
})
