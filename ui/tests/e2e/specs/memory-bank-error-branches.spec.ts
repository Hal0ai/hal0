/**
 * memory-bank-error-branches — the v2 Bank workspace must not render an
 * outage as an empty bank (#1539, migrated to the v2 DOM for task C8).
 *
 * The original bug (fixed pre-v2 in memory.jsx, ported here in C7/C8):
 * several panels read `query.data?.<list> || []` and rendered an
 * empty-state when the list came back short. A failed query has
 * `data === undefined`, so the fallback fires and an engine outage is
 * indistinguishable from a healthy quiet bank. This spec pins the v2
 * equivalents — Overview's growth chart + bank rows (`mv-overview-error`),
 * the Bank workspace's units list (`mv-units-error`) and sources panel
 * (`mv-sources-error`), and the BankBar's reflect (`mv-reflect-error`) and
 * rules (`mv-rules-error`) tabs — plus a regression guard that a healthy
 * bank still shows its normal empty/quiet states, not errors.
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

async function gotoOverview(page: import('@playwright/test').Page) {
  await page.goto('/#memory')
  await page.waitForSelector('[data-testid="mv-engine-panel"]', { timeout: 10_000 })
}

async function gotoBank(page: import('@playwright/test').Page, bank = 'primary') {
  await page.goto(`/#memory/bank?bank=${bank}`)
  await page.waitForSelector('[data-testid="mv-workspace"]', { timeout: 10_000 })
}

test.describe('Memory Overview — engine outage (#1539, v2 DOM)', () => {
  test('growth chart: a 503 says unreachable instead of "no retain activity"', async ({ page }) => {
    await breakBankRoutes(page)
    await gotoOverview(page)

    const growth = page.getByTestId('mv-growth')
    const err = growth.getByTestId('mv-overview-error')
    await expect(err).toBeVisible({ timeout: 15_000 })
    await expect(err).toContainText('Memory engine unreachable')
    await expect(growth.getByText('No retain activity in this window.', { exact: true })).toHaveCount(0)
    await expect(growth.getByTestId('mv-overview-error-retry')).toBeVisible()
  })

  test('bank rows: failed stats read as unavailable, not as zero facts', async ({ page }) => {
    // One row per bank, so the signal is a per-row chip rather than a
    // banner — but it must exist, or an unreachable engine reads as an
    // empty bank (the exact #1539 shape, now on the v2 table). Scope to the
    // bank row specifically — the growth chart above it renders its own
    // (differently-worded) mv-overview-error first in DOM order.
    await breakBankRoutes(page)
    await gotoOverview(page)

    // Both the stats and the ops chip below share `mv-overview-error`
    // (both fail together under `breakBankRoutes`) — scope to the stats one
    // specifically via its exact copy.
    const chip = page.getByTestId('mv-bank-row-primary').getByText('stats unavailable')
    await expect(chip).toBeVisible({ timeout: 15_000 })
  })

  test('bank rows: failed operations read as unavailable, not as idle', async ({ page }) => {
    // The old memory.jsx operations panel `return null`'d on an empty
    // list, so a failed query made the whole card VANISH. The v2 table's
    // equivalent silent failure is reading a failed ops query as "idle" —
    // this pins that it announces the outage instead.
    await breakBankRoutes(page)
    await gotoOverview(page)

    const row = page.getByTestId('mv-bank-row-primary')
    await expect(row.getByText('ops unavailable')).toBeVisible({ timeout: 15_000 })
    await expect(row.getByText('idle', { exact: true })).toHaveCount(0)
  })

  test('a healthy overview shows no outage affordances', async ({ page }) => {
    // Regression guard: the error branches sit in front of the normal
    // states, so they must stay scoped to real failures. No passthrough —
    // forced-mock serves its usual payload, exactly as every other spec
    // sees it. (The mock's default bank-operations fixture always seeds one
    // pending op, so every bank shows a "pending" chip rather than "idle" —
    // assert on the absence of the error chips, not on a specific activity
    // label.)
    await gotoOverview(page)
    await expect(page.getByTestId('mv-overview-error')).toHaveCount(0)
    await expect(page.getByTestId('mv-bank-row-primary').getByText('stats unavailable')).toHaveCount(0)
    await expect(page.getByTestId('mv-bank-row-primary').getByText('ops unavailable')).toHaveCount(0)
  })
})

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

  test('rules tab: a 503 says unreachable and suppresses the empty directive/model lists', async ({
    page,
  }) => {
    await breakBankRoutes(page)
    await gotoBank(page)

    await page.getByTestId('mv-rules-tab').click()
    const err = page.getByTestId('mv-rules-error')
    await expect(err).toBeVisible({ timeout: 15_000 })
    await expect(err).toContainText('Memory engine unreachable')
    await expect(page.locator('[data-testid^="mv-rule-row-"]')).toHaveCount(0)
  })

  test('a healthy bank workspace still renders its normal states, not errors', async ({ page }) => {
    await gotoBank(page)
    await expect(page.getByTestId('mv-units-error')).toHaveCount(0)
    await expect(page.getByTestId('mv-sources-error')).toHaveCount(0)
    await expect(page.locator('[data-testid^="mv-fact-row-"]').first()).toBeVisible()

    await page.getByTestId('mv-rules-tab').click()
    await expect(page.getByTestId('mv-rules-error')).toHaveCount(0)
  })
})
