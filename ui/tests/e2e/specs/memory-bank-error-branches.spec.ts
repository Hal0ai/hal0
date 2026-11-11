/**
 * memory-bank-error-branches — the v2 Bank workspace must not render an
 * outage as an empty bank (#1539, migrated to the v2 DOM for task C8).
 *
 * The original bug (fixed pre-v2 in memory.jsx, ported here in C7/C8):
 * several panels read `query.data?.<list> || []` and rendered an
 * empty-state when the list came back short. A failed query has
 * `data === undefined`, so the fallback fires and an engine outage is
 * indistinguishable from a healthy quiet bank. This spec pins the fact
 * workspace's equivalents, on Memory ▸ Facts (#memory/facts): the units
 * list (`mv-units-error`), the sources panel (`mv-sources-error`) and the
 * BankBar's reflect console (`mv-reflect-error`) — plus a regression guard
 * that a healthy bank still shows its normal empty/quiet states, not errors.
 *
 * The same contract for the page that lists every bank (a failed bank must
 * not read as zero facts or as idle) is in memory-health-v3: the old
 * Overview that carried it here is gone. The BankBar's Rules tab is not
 * drawn inside the area, so its outage case went with it.
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

async function gotoBank(page: import('@playwright/test').Page, bank = 'primary') {
  await page.goto(`/#memory/facts?bank=${bank}`)
  await page.waitForSelector('[data-testid="mv-workspace"]', { timeout: 10_000 })
}

test.describe('Memory Bank workspace — engine outage (#1539, v2 DOM)', () => {
  test('units list: a 503 says unreachable and suppresses the empty-state', async ({ page }) => {
    await breakBankRoutes(page)
    await gotoBank(page)

    const err = page.getByTestId('mv-units-error')
    await expect(err).toBeVisible({ timeout: 15_000 })
    await expect(err).toContainText('Memory engine unreachable')
    await expect(page.getByText('no facts match — clear a filter', { exact: true })).toHaveCount(0)
    await expect(page.getByTestId('mv-units-error-retry')).toBeVisible()
  })

  test('sources panel: a 503 says unreachable and suppresses the empty-state', async ({ page }) => {
    await breakBankRoutes(page)
    await gotoBank(page)

    const err = page.getByTestId('mv-sources-error')
    await expect(err).toBeVisible({ timeout: 15_000 })
    await expect(err).toContainText('Memory engine unreachable')
    await expect(page.getByTestId('mv-sources-error-retry')).toBeVisible()
  })

  test('reflect tab: a 503 says unreachable, not a silently empty answer', async ({ page }) => {
    await breakBankRoutes(page)
    await gotoBank(page)

    await page.getByTestId('mv-reflect-tab').click()
    await page.getByTestId('mv-reflect-q').fill('why is extraction lagging?')
    await page.getByTestId('mv-reflect-run').click()

    const err = page.getByTestId('mv-reflect-error')
    await expect(err).toBeVisible({ timeout: 15_000 })
    await expect(err).toContainText('Memory engine unreachable')
    await expect(page.getByTestId('mv-reflect-out')).toHaveCount(0)
  })

  test('units list: a 404 (deploy-skew — route missing from this install) gets its own copy, not "unreachable"', async ({
    page,
  }) => {
    // Live-verified 2026-08-21 (post-smoke report): CT105's deployed backend
    // predated the /units route entirely, so it 404'd — a plain 404 is not
    // an engine outage, it's a stale-deploy symptom, and saying "unreachable"
    // was actively misleading (the engine answered fine).
    await page.addInitScript(() => {
      ;(window as unknown as { __hal0MockPassthrough?: unknown }).__hal0MockPassthrough = [
        '/api/memory/banks/',
      ]
    })
    await page.route('**/api/memory/banks/*/units**', (route) =>
      route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not Found' }),
      }),
    )
    await gotoBank(page)

    const err = page.getByTestId('mv-units-error')
    await expect(err).toBeVisible({ timeout: 15_000 })
    await expect(err).toContainText("This install's API doesn't serve this view yet")
    await expect(err).not.toContainText('Memory engine unreachable')
  })

  test('a healthy bank workspace still renders its normal states, not errors', async ({ page }) => {
    await gotoBank(page)
    await expect(page.getByTestId('mv-units-error')).toHaveCount(0)
    await expect(page.getByTestId('mv-sources-error')).toHaveCount(0)
    await expect(page.locator('[data-testid^="mv-fact-row-"]').first()).toBeVisible()
    await expect(page.getByTestId('mv-reflect-error')).toHaveCount(0)
  })
})
