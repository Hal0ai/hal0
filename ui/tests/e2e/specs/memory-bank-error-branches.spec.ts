/**
 * memory-bank-error-branches — the bank surface must not render an outage as
 * an empty bank (#1539).
 *
 * Six panels across #memory and #memory/tools read `query.data?.<list> || []`
 * and render an empty-state when the list is short. A failed query has
 * `data === undefined`, so the fallback fires and an engine outage is
 * indistinguishable from a healthy quiet bank — which is exactly what a fresh
 * install has. None of these panels consulted `isError` at all; unlike #1471
 * (the graph explorer) there was no branch to get wrong.
 *
 * Two of them fail worse than "looks empty":
 *   - Operations returned `null` on an empty list, so an outage made the whole
 *     card VANISH — no empty-state, no error, nothing.
 *   - Bank cards showed every count as 0, so an unreachable engine read as a
 *     bank with nothing in it.
 *
 * These could not be tested at all until #1538 made a non-ok response
 * representable under forced-mock, which is why they shipped unnoticed. Each
 * test claims the paths it drives via `__hal0MockPassthrough` and asserts BOTH
 * that the outage is announced and that the empty-state it used to be confused
 * with is absent — the second half is what makes it a real distinction rather
 * than an extra banner.
 */
import { test, expect } from '../fixtures/apiMock'

const DOWN = {
  error: { code: 'memory.engine_unreachable', message: 'hindsight-api is not responding', details: {} },
}

/** Claim the bank-scoped routes and fail them all. Bank LIST stays mocked so
 *  a bank still resolves and the per-panel queries actually fire. */
async function breakBankRoutes(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    ;(window as unknown as { __hal0MockPassthrough?: unknown }).__hal0MockPassthrough = [
      '/api/memory/banks/',
    ]
  })
  await page.route('**/api/memory/banks/*/**', (route) =>
    route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify(DOWN) }),
  )
}

// The standalone #memory/tools route was retired — the tools surface is
// embedded in #memory and renders once a bank is selected (same navigation the
// memory-tools-v3 spec uses).
async function gotoTools(page: import('@playwright/test').Page) {
  await page.goto('/#memory')
  await page.waitForSelector('[data-testid="mem-bank-primary"]', { timeout: 10_000 })
  await page.click('[data-testid="mem-bank-primary"]')
  await page.waitForSelector('[data-testid="mem-tools"]', { timeout: 10_000 })
}

async function gotoMemory(page: import('@playwright/test').Page) {
  await page.goto('/#memory')
  await page.waitForSelector('[data-testid="mem-bank-primary"]', { timeout: 10_000 })
  await page.click('[data-testid="mem-bank-primary"]')
  await page.waitForSelector('[data-testid="mem-timeseries"]', { timeout: 10_000 })
}

test.describe('Memory tools — engine outage is announced, not rendered as empty (#1539)', () => {
  for (const [name, errorId, emptyText] of [
    ['documents', 'mem-documents-error', 'No documents in this bank.'],
    ['mental models', 'mem-models-error', 'No mental models defined.'],
    ['directives', 'mem-directives-error', 'No directives.'],
  ] as const) {
    test(`${name}: a 503 says unreachable and suppresses the empty-state`, async ({ page }) => {
      await breakBankRoutes(page)
      await gotoTools(page)

      const err = page.getByTestId(errorId)
      await expect(err).toBeVisible({ timeout: 15_000 })
      await expect(err).toContainText('Memory engine unreachable')
      // The lie: the empty-state must NOT also be showing.
      await expect(page.getByText(emptyText, { exact: true })).toHaveCount(0)
      await expect(page.getByTestId(`${errorId}-retry`)).toBeVisible()
    })
  }

  test('a healthy bank still renders its normal empty-states, not errors', async ({ page }) => {
    // Regression guard: the error branches sit in front of the empty-states,
    // so they must stay scoped to real failures. No passthrough — forced-mock
    // serves its usual payload, exactly as every other spec sees it.
    await gotoTools(page)
    await expect(page.getByTestId('mem-documents-error')).toHaveCount(0)
    await expect(page.getByTestId('mem-models-error')).toHaveCount(0)
    await expect(page.getByTestId('mem-directives-error')).toHaveCount(0)
  })
})

test.describe('Memory overview — engine outage (#1539)', () => {
  test('timeseries: a 503 says unreachable instead of "no retain activity"', async ({ page }) => {
    await breakBankRoutes(page)
    await gotoMemory(page)

    const err = page.getByTestId('mem-timeseries-error')
    await expect(err).toBeVisible({ timeout: 15_000 })
    await expect(err).toContainText('Memory engine unreachable')
    await expect(page.getByText('No retain activity in this window.', { exact: true })).toHaveCount(0)
  })

  test('operations: the card announces the outage instead of vanishing', async ({ page }) => {
    // This panel used to `return null` on an empty list, and a failed query
    // produces an empty list — so the entire card silently disappeared.
    await breakBankRoutes(page)
    await gotoMemory(page)

    const err = page.getByTestId('mem-operations-error')
    await expect(err).toBeVisible({ timeout: 15_000 })
    await expect(err).toContainText('Memory engine unreachable')
  })

  test('bank card: failed stats read as unavailable, not as zero', async ({ page }) => {
    await breakBankRoutes(page)
    await gotoMemory(page)

    // One card per bank, so the signal is a chip rather than a banner — but it
    // must exist, or an unreachable engine reads as an empty bank.
    const chip = page.locator('[data-testid^="mem-bank-stats-error-"]').first()
    await expect(chip).toBeVisible({ timeout: 15_000 })
    await expect(chip).toContainText('stats unavailable')
  })

  test('a healthy overview shows no outage affordances', async ({ page }) => {
    await gotoMemory(page)
    await expect(page.getByTestId('mem-timeseries-error')).toHaveCount(0)
    await expect(page.getByTestId('mem-operations-error')).toHaveCount(0)
    await expect(page.locator('[data-testid^="mem-bank-stats-error-"]')).toHaveCount(0)
  })
})
