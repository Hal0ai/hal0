/**
 * memory-operations-v3 — Memory ▸ Operations (#memory/operations, ADR-0058 decision 12).
 *
 * The tab's states and actions: the backlog with its cause line while storing memories is
 * failing (retry disabled, with the reason), the status filter, the page controls, the per-bank
 * table, retrying one operation, discarding behind a confirm, the empty queue, the engine not
 * answering, and the layout at phone width.
 *
 * Forced mock. `GET /api/memory/health` is answered in the bundle in one of three states, chosen
 * by `window.__hal0MockMemoryHealth` (src/api/mockFixtures.ts): `attention` (the default:
 * extraction is the current step, the queue waits on it), `clear` and `unreachable`. Its queue
 * figures are counted from the same operation fixtures the list is served from: bank `big` has
 * 137 operations (96 waiting, 9 failed), `ingest` has 4, three banks have one waiting each, and
 * `empty` has none: 100 waiting and 10 failed in all.
 *
 * Retry, discard and consolidate are requests the mock never answers, so each test that sends
 * one routes it. The clock is fixed a few minutes after the newest fixture row, so "20 min ago"
 * is the same on every run.
 */
import type { Page } from '@playwright/test'

import { test, expect, json } from '../fixtures/apiMock'
import { pickRichOption } from '../fixtures/richSelect'

const NOW = '2026-06-12T22:20:00.000Z'
const FAILED_IN_BIG = 9

type HealthState = 'attention' | 'clear' | 'unreachable'

async function healthState(page: Page, state: HealthState) {
  await page.addInitScript((s) => {
    ;(window as unknown as { __hal0MockMemoryHealth?: string }).__hal0MockMemoryHealth = s
  }, state)
}

/**
 * A box where operations have failed but storing memories works again: extraction is clear and
 * the queue is the current step. None of the bundle's three states is this one, so the route is
 * taken out of the bundle's hands and answered here, in the real route's shape.
 */
async function routeHealthExtractionClear(page: Page) {
  await page.addInitScript(() => {
    ;(window as unknown as { __hal0MockPassthrough?: unknown }).__hal0MockPassthrough = ['/api/memory/health']
  })
  const waitingOne = (bank: string) => ({ bank_id: bank, waiting: 1, failed: 0, oldest_waiting_s: 840 })
  await page.route('**/api/memory/health', (route) =>
    json(route, {
      enabled: true,
      verdict: 'attention',
      checked_at: '2026-06-12T22:20:00+00:00',
      partial: [],
      engine: { reachable: true, version: '0.9.2' },
      extraction: {
        slot: 'memory',
        mode: 'dedicated',
        slot_resolves: true,
        llm_reachable: true,
        concurrency: 2,
        failures_6h: 0,
        top_errors: [],
      },
      embedder: {
        recorded: { provider: 'local', model: 'hindsight-ai/hs-embed-1' },
        configured: { provider: 'local', model: 'hindsight-ai/hs-embed-1' },
        mixed: false,
        needs_switch: false,
        conflict: null,
        informational: false,
      },
      queue: {
        waiting: 100,
        failed: 10,
        oldest_waiting_s: 24480,
        consolidation_pending: 2,
        consolidation_failed: 0,
        by_bank: [
          waitingOne('primary'),
          { bank_id: 'big', waiting: 96, failed: FAILED_IN_BIG, oldest_waiting_s: 24480 },
          waitingOne('hermes'),
          waitingOne('scratch'),
          { bank_id: 'ingest', waiting: 1, failed: 1, oldest_waiting_s: 390 },
          { bank_id: 'empty', waiting: 0, failed: 0, oldest_waiting_s: null },
        ],
        truncated: false,
      },
      knowledge: { pages: 15, pages_stale: 0, models: 18, models_stale: 0, refresh_failed: 0, last_success_at: null },
      banks: { layout: 'per_bank', total: 6, facts: 5636, list: [] },
      writes: null,
      steps: [
        { id: 'engine', state: 'clear', waits_on: null, reason_code: null, figures: { reachable: true } },
        { id: 'extraction', state: 'clear', waits_on: null, reason_code: null, figures: {} },
        {
          id: 'queue',
          state: 'current',
          waits_on: null,
          reason_code: 'queue_operations_failed',
          figures: { failed: 10, waiting: 100, oldest_waiting_s: 24480, max_waiting_age_s: 1800 },
        },
        { id: 'embedder', state: 'clear', waits_on: null, reason_code: null, figures: {} },
        { id: 'knowledge', state: 'clear', waits_on: null, reason_code: null, figures: {} },
      ],
    }),
  )
}

/** Record every retry and discard request, and answer each as the engine does. */
async function routeMutations(page: Page) {
  const sent = { retried: [] as string[], discarded: [] as string[] }
  await page.route(/\/api\/memory\/banks\/[^/]+\/operations\/[^/]+(\/retry)?$/, (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === 'POST' && path.endsWith('/retry')) {
      sent.retried.push(path)
      return json(route, { success: true })
    }
    if (request.method() === 'DELETE') {
      sent.discarded.push(path)
      return json(route, { success: true })
    }
    return route.fallback()
  })
  return sent
}

const tab = (page: Page) => page.getByTestId('ma-operations')
const stat = (page: Page, id: string) => page.locator(`[data-testid="ma-ops-strip"] [data-stat="${id}"]`)
const rows = (page: Page) => page.locator('[data-testid="ma-ops-table"] tbody tr')
const filter = (page: Page, name: string) =>
  page.getByRole('radiogroup', { name: 'Operation status' }).getByRole('radio', { name, exact: true })
const scopeBank = (page: Page) => page.getByTestId('ma-scope-bank')

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date(NOW))
})

// A response in the wrong shape makes a component throw when it lands. Every test ends by
// checking that the page is still the page.
test.afterEach(async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'This view hit an error' })).toHaveCount(0)
  await expect(page.getByTestId('memory-area')).toBeVisible()
})

test.describe('backlog, with storing memories failing', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/#memory/operations')
    await expect(tab(page)).toHaveAttribute('data-state', 'ready')
  })

  test('leads with the cause, and links to Health', async ({ page }) => {
    const cause = page.getByTestId('ma-ops-cause')
    await expect(cause.getByRole('heading', { level: 2 })).toHaveText('New memories are failing to be stored')
    await expect(cause.locator('.chip')).toHaveText('failing')
    await expect(page.getByTestId('ma-ops-cause-text')).toContainText(
      '7 attempts to store a memory failed in the last 6 h.',
    )
    await expect(page.getByTestId('ma-ops-cause-reason')).toContainText(
      'Most frequent reason, seen 6 times: APIConnectionError: Connection error.',
    )
    await expect(page.getByTestId('ma-ops-cause-reason')).toContainText('1 other reason seen')
    // The cause offers no action; the fix is on Health.
    await expect(cause.getByRole('button')).toHaveCount(0)
    await page.getByTestId('ma-ops-cause-health').click()
    await expect(page).toHaveURL(/#memory\/health$/)
    await expect(page.getByTestId('ma-tab-health')).toHaveAttribute('aria-current', 'page')
  })

  test('shows the queue figures, each with a label and a unit', async ({ page }) => {
    await expect(page.getByTestId('ma-ops-strip').locator('.ma-stat-label')).toHaveText([
      'Waiting',
      'Failed',
      'Oldest waiting',
      'Waiting on consolidation',
      'Failed consolidation',
    ])
    await expect(stat(page, 'waiting')).toContainText('100')
    await expect(stat(page, 'waiting')).toContainText('operations')
    await expect(stat(page, 'waiting')).toContainText('in 5 banks')
    await expect(stat(page, 'failed')).toContainText('10')
    await expect(stat(page, 'failed')).toContainText('in 2 banks')
    await expect(stat(page, 'oldest').locator('.ma-stat-value')).toHaveText('6h')
    await expect(stat(page, 'consolidation-waiting').locator('.ma-stat-value')).toHaveText('2facts')
  })

  test('disables every retry with its reason and a link to Health; discard stays available', async ({ page }) => {
    const retryAll = page.getByTestId('ma-ops-retry-all')
    await expect(retryAll).toBeDisabled()
    await expect(retryAll).toHaveAccessibleDescription(/Retrying will fail until storing memories works again\./)
    await expect(page.getByTestId('ma-ops-retry-health')).toHaveAttribute('href', '#memory/health')
    await expect(page.getByTestId('ma-ops-discard')).toBeEnabled()
    await expect(page.getByTestId('ma-ops-discard')).toHaveText('Discard 10 failed operations')

    await filter(page, 'Failed').click()
    await expect(rows(page)).toHaveCount(10)
    const rowRetry = rows(page).first().getByTestId('ma-ops-retry')
    await expect(rowRetry).toBeDisabled()
    await expect(rowRetry).toHaveAccessibleDescription(/Retrying will fail until storing memories works again\./)
    await expect(page.getByTestId('ma-ops-list-blocked')).toBeVisible()
  })

  test('lists every bank’s newest operations in plain words, and says the list is capped', async ({ page }) => {
    await expect(scopeBank(page)).toContainText('All banks')
    await expect(page.getByRole('region', { name: 'Operations table' })).toBeVisible()
    await expect(page.getByTestId('ma-ops-table').locator('thead th')).toHaveText([
      'Operation',
      'Bank',
      'Queued',
      'Status',
      'Actions',
    ])
    await expect(rows(page)).toHaveCount(25)
    // Newest first: the two rows of `ingest` are newer than anything in `big`.
    const first = rows(page).first()
    await expect(first.locator('th')).toHaveText('Store a document')
    await expect(first.locator('th')).toHaveAttribute('title', 'document_ingest')
    await expect(first.locator('td').nth(0)).toHaveText('ingest')
    await expect(first.locator('time')).toHaveText('6 min ago')
    await expect(first.locator('time')).toHaveAttribute('title', /^12 Jun 2026, \d\d:\d\d$/)
    await expect(first.locator('.chip')).toHaveText('running')
    // No engine word and no raw timestamp reaches the list.
    for (const name of await page.getByTestId('ma-ops-table').locator('tbody th').allTextContents()) {
      expect(name).not.toContain('_')
    }
    await expect(page.getByTestId('ma-ops-table')).not.toContainText(/\d{4}-\d\d-\d\dT/)

    await expect(page.getByTestId('ma-ops-range')).toHaveText('1–25 of 56 operations · page 1 of 3')
    await expect(page.getByTestId('ma-ops-capped')).toHaveText(
      'These are the newest 56 of 150 operations across the banks. Choose a bank to page through all of its operations.',
    )
  })

  test('filters by status, says when a filter has no rows, and "Show all" returns', async ({ page }) => {
    await filter(page, 'Failed').click()
    await expect(filter(page, 'Failed')).toHaveAttribute('aria-checked', 'true')
    await expect(rows(page)).toHaveCount(10)
    for (const chip of await rows(page).locator('.chip').all()) await expect(chip).toHaveText('failed')
    await expect(page.getByTestId('ma-ops-range')).toHaveText('1–10 of 10 operations')
    await expect(page.getByTestId('ma-ops-capped')).toHaveCount(0)

    // A failed row shows its error on one line, and the whole of it on request.
    const long = page.locator('tr[data-op="op-big-8896"]')
    const text = long.getByTestId('ma-ops-error-text')
    await expect(text).toHaveText(/^Task exceeded the 14400s wall-clock limit .*…$/)
    const toggle = long.getByTestId('ma-ops-error-toggle')
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(text).toContainText('Raise HINDSIGHT_API_RETAIN_WALL_TIMEOUT if this is a legitimately long operation')
    await expect(long.locator('.ma-ops-status')).toContainText('retried 3 times')

    await filter(page, 'Running').click()
    await expect(rows(page)).toHaveCount(3)

    // `scratch` has one waiting operation and nothing failed.
    await pickRichOption(scopeBank(page), 'scratch')
    await expect(page).toHaveURL(/#memory\/operations\?bank=scratch$/)
    await filter(page, 'Failed').click()
    const empty = page.getByTestId('ma-ops-filter-empty')
    await expect(empty.getByRole('heading', { level: 3 })).toHaveText('No failed operations')
    await expect(empty).toContainText('Nothing in scratch is failed right now.')
    await empty.getByRole('button', { name: 'Show all' }).click()
    await expect(filter(page, 'All')).toHaveAttribute('aria-checked', 'true')
    await expect(rows(page)).toHaveCount(3)
  })

  test('the per-bank table lists the banks that hold the queue, and a bank opens its operations', async ({
    page,
  }) => {
    const banks = page.getByTestId('ma-ops-banks')
    await expect(page.getByRole('region', { name: 'Queue by bank table' })).toBeVisible()
    await expect(banks.locator('thead th')).toHaveText(['Bank', 'Waiting', 'Failed', 'Oldest waiting'])
    await expect(banks.locator('tbody tr th')).toHaveText(['big', 'ingest', 'hermes', 'primary', 'scratch'])
    await expect(banks.locator('tbody tr').first().locator('td')).toHaveText(['96', '9', '6 h'])
    await expect(page.getByTestId('ma-ops-banks-quiet')).toHaveText('1 bank has nothing waiting or failed.')

    await banks.getByRole('link', { name: 'big', exact: true }).click()
    await expect(page).toHaveURL(/#memory\/operations\?bank=big$/)
    await expect(scopeBank(page)).toContainText('big')
    // Everything under the scope bar is now that bank's.
    await expect(page.getByTestId('ma-ops-banks')).toHaveCount(0)
    await expect(stat(page, 'waiting').locator('.ma-stat-value')).toHaveText('96operations')
    await expect(page.getByTestId('ma-ops-failed-count')).toHaveText('9 failed operations in big')
    await expect(rows(page)).toHaveCount(25)
    for (const cell of await rows(page).locator('td.ma-ops-bank').all()) await expect(cell).toHaveText('big')
    // The cause is the engine's, not a bank's: it stays.
    await expect(page.getByTestId('ma-ops-cause')).toBeVisible()
  })

  test('pages through one bank with real page controls', async ({ page }) => {
    await page.goto('/#memory/operations?bank=big')
    const nav = page.getByRole('navigation', { name: 'Operations pages' })
    await expect(page.getByTestId('ma-ops-range')).toHaveText('1–25 of 137 operations · page 1 of 6')
    await expect(nav.getByRole('button', { name: 'Previous' })).toHaveCount(0)
    await expect(rows(page).first()).toHaveAttribute('data-op', 'op-big-9000')

    await nav.getByRole('button', { name: 'Next' }).click()
    await expect(page.getByTestId('ma-ops-range')).toHaveText('26–50 of 137 operations · page 2 of 6')
    await expect(rows(page).first()).toHaveAttribute('data-op', 'op-big-8975')
    await expect(rows(page)).toHaveCount(25)

    await nav.getByRole('button', { name: 'Previous' }).click()
    await expect(page.getByTestId('ma-ops-range')).toHaveText('1–25 of 137 operations · page 1 of 6')

    // A filter starts again from its first page, with its own total.
    await nav.getByRole('button', { name: 'Next' }).click()
    await filter(page, 'Done').click()
    await expect(page.getByTestId('ma-ops-range')).toHaveText('1–25 of 30 operations · page 1 of 2')
    await nav.getByRole('button', { name: 'Next' }).click()
    await expect(page.getByTestId('ma-ops-range')).toHaveText('26–30 of 30 operations · page 2 of 2')
    await expect(rows(page)).toHaveCount(5)
    await expect(nav.getByRole('button', { name: 'Next' })).toHaveCount(0)
  })

  test('discard opens a confirm; "Keep them" discards nothing', async ({ page }) => {
    const sent = await routeMutations(page)
    await page.getByTestId('ma-ops-discard').click()
    const confirm = page.getByTestId('ma-ops-discard-confirm')
    await expect(confirm.getByRole('heading', { level: 3 })).toHaveText('Discard 10 failed operations?')
    await expect(confirm).toContainText('These are 10 writes that were never stored.')
    await expect(confirm).toContainText('Discarding cannot be undone.')
    await expect(confirm.getByRole('button')).toHaveText(['Discard 10 failed operations', 'Keep them'])
    // Focus starts on the choice that keeps the data.
    await expect(confirm.getByRole('button', { name: 'Keep them' })).toBeFocused()

    await confirm.getByRole('button', { name: 'Keep them' }).click()
    await expect(confirm).toHaveCount(0)
    await expect(page.getByTestId('ma-ops-discard')).toBeFocused()
    await expect(page.getByTestId('ma-ops-run')).toHaveText('')
    expect(sent.discarded).toEqual([])
  })

  test('confirming discards each failed operation and reports the count', async ({ page }) => {
    const sent = await routeMutations(page)
    await page.getByTestId('ma-ops-discard').click()
    await page
      .getByTestId('ma-ops-discard-confirm')
      .getByRole('button', { name: 'Discard 10 failed operations' })
      .click()
    await expect(page.getByTestId('ma-ops-run')).toHaveText('Discarded 10 failed operations.')
    expect(sent.discarded).toHaveLength(10)
    expect(sent.discarded.filter((path) => path.startsWith('/api/memory/banks/big/operations/'))).toHaveLength(
      FAILED_IN_BIG,
    )
    expect(sent.discarded).toContain('/api/memory/banks/ingest/operations/op-ing-8990')
    expect(sent.retried).toEqual([])
  })

  test('a discard that the engine refuses stops and says so in a sentence', async ({ page }) => {
    await page.route(/\/api\/memory\/banks\/[^/]+\/operations\/[^/]+$/, (route) =>
      route.request().method() === 'DELETE'
        ? json(route, { error: { code: 'memory.engine_error', message: 'hindsight-api is not responding' } }, 503)
        : route.fallback(),
    )
    await page.goto('/#memory/operations?bank=big')
    await page.getByTestId('ma-ops-discard').click()
    await page.getByTestId('ma-ops-discard-confirm').getByRole('button', { name: /^Discard 9/ }).click()
    await expect(page.getByTestId('ma-ops-run')).toHaveText(
      'Stopped after 0 of 9. 9 operations were not discarded. hindsight-api is not responding.',
    )
    await expect(page.getByTestId('ma-ops-run')).not.toContainText('{')
  })

  test('consolidate now needs a bank, and queues a consolidation for the chosen one', async ({ page }) => {
    const consolidated: string[] = []
    await page.route(/\/api\/memory\/banks\/[^/]+\/consolidate$/, (route) => {
      consolidated.push(new URL(route.request().url()).pathname)
      return json(route, { operation_id: 'op-new' })
    })
    const button = page.getByTestId('ma-ops-consolidate')
    await expect(button).toBeDisabled()
    await expect(button).toHaveAccessibleDescription('Choose a bank to consolidate it.')

    await pickRichOption(scopeBank(page), 'ingest')
    await expect(button).toBeEnabled()
    await button.click()
    await expect(page.getByTestId('ma-ops-consolidate-status')).toHaveText(
      'Consolidation queued. It appears in this list as an operation.',
    )
    expect(consolidated).toEqual(['/api/memory/banks/ingest/consolidate'])
  })

  test('has no primary button and no link to the legacy tools page', async ({ page }) => {
    await expect(tab(page).locator('.btn:not(.ghost):not(.danger)')).toHaveCount(0)
    await expect(tab(page).locator('a[href^="#memworkspace"]')).toHaveCount(0)
  })
})

test.describe('failed operations, with storing memories working', () => {
  test.beforeEach(async ({ page }) => {
    await routeHealthExtractionClear(page)
  })

  test('retries one failed operation and says it was queued again', async ({ page }) => {
    const sent = await routeMutations(page)
    await page.goto('/#memory/operations?bank=big')
    await expect(page.getByTestId('ma-ops-cause')).toHaveCount(0)
    await filter(page, 'Failed').click()
    await expect(rows(page)).toHaveCount(FAILED_IN_BIG)

    const row = page.locator('tr[data-op="op-big-8902"]')
    const retry = row.getByRole('button', { name: /^Retry: Store a batch, queued/ })
    await expect(retry).toBeEnabled()
    await retry.click()
    await expect(row.getByTestId('ma-ops-row-note')).toHaveText('Queued again.')
    expect(sent.retried).toEqual(['/api/memory/banks/big/operations/op-big-8902/retry'])
    expect(sent.discarded).toEqual([])
  })

  test('a retry the engine refuses says so on its row, in a sentence', async ({ page }) => {
    await page.route(/\/operations\/[^/]+\/retry$/, (route) =>
      json(route, { error: { code: 'memory.engine_error', message: 'The operation is no longer failed' } }, 409),
    )
    await page.goto('/#memory/operations?bank=big')
    await filter(page, 'Failed').click()
    const row = page.locator('tr[data-op="op-big-8902"]')
    await row.getByTestId('ma-ops-retry').click()
    await expect(row.getByTestId('ma-ops-row-note')).toHaveText('Retry failed. The operation is no longer failed.')
    await expect(page.locator('tr[data-op="op-big-8901"]').getByTestId('ma-ops-row-note')).toHaveCount(0)
  })

  test('retries all failed operations of the scope, with a count', async ({ page }) => {
    const sent = await routeMutations(page)
    await page.goto('/#memory/operations')
    const retryAll = page.getByTestId('ma-ops-retry-all')
    await expect(retryAll).toBeEnabled()
    await retryAll.click()
    await expect(page.getByTestId('ma-ops-run')).toHaveText('Queued 10 failed operations again.')
    expect(sent.retried).toHaveLength(10)
    expect(sent.discarded).toEqual([])
  })
})

test.describe('the empty queue', () => {
  test.beforeEach(async ({ page }) => {
    await healthState(page, 'clear')
    await page.goto('/#memory/operations')
    await expect(tab(page)).toHaveAttribute('data-state', 'clear')
  })

  test('says nothing is waiting, nothing failed, and when the last operation finished', async ({ page }) => {
    const clear = page.getByTestId('ma-ops-clear')
    await expect(clear.getByRole('heading', { level: 2 })).toHaveText('Nothing is waiting. Nothing has failed.')
    await expect(clear.locator('.chip')).toHaveText('done')
    await expect(clear).toContainText('Every operation agents queued has been worked through.')
    // The newest finished row is `ingest`'s, done at 18:41.
    await expect(page.getByTestId('ma-ops-last-completed')).toHaveText('3 h ago')
    await expect(clear).toContainText('Operations appear below while they wait or run')

    await expect(stat(page, 'waiting').locator('.ma-stat-value')).toHaveText('0operations')
    await expect(stat(page, 'failed').locator('.ma-stat-value')).toHaveText('0operations')
    await expect(stat(page, 'oldest')).toContainText('nothing is waiting')
    await expect(page.getByTestId('ma-ops-cause')).toHaveCount(0)
    await expect(page.getByTestId('ma-ops-failed')).toHaveCount(0)
    await expect(page.getByTestId('ma-ops-banks')).toHaveCount(0)
  })

  test('still lists what finished, and says a filter has nothing', async ({ page }) => {
    await expect(rows(page).first().locator('.chip')).toHaveText('done')
    await filter(page, 'Waiting').click()
    await expect(page.getByTestId('ma-ops-filter-empty').getByRole('heading', { level: 3 })).toHaveText(
      'No waiting operations',
    )
    await page.getByRole('button', { name: 'Show all' }).click()
    await expect(rows(page).first()).toBeVisible()
  })

  test('a bank with no operations at all says the queue is empty', async ({ page }) => {
    await pickRichOption(scopeBank(page), 'empty')
    const empty = page.getByTestId('ma-ops-list-empty')
    await expect(empty.getByRole('heading', { level: 3 })).toHaveText('The queue is empty')
    await expect(empty).toContainText('Operations appear here while they wait or run')
    await expect(page.getByTestId('ma-ops-last-completed')).toHaveCount(0)
  })
})

test.describe('engine unreachable', () => {
  test('the whole tab says so, shows no figure, and links to Health', async ({ page }) => {
    await healthState(page, 'unreachable')
    await page.goto('/#memory/operations')
    await expect(tab(page)).toHaveAttribute('data-state', 'unreachable')
    const card = page.getByTestId('ma-ops-unreachable')
    await expect(card.getByRole('heading', { level: 3 })).toHaveText('The memory engine is not answering')
    await expect(card.locator('.chip')).toHaveText('unreachable')
    await expect(tab(page)).not.toContainText(/\d/)
    await expect(tab(page).getByRole('button')).toHaveCount(0)
    await expect(page.getByTestId('ma-ops-table')).toHaveCount(0)
    await expect(page.getByTestId('ma-meta')).toHaveText('Engine unreachable · no counts available')
    await card.getByRole('link', { name: 'Open Health' }).click()
    await expect(page).toHaveURL(/#memory\/health$/)
  })
})

test.describe('phone width', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('fits the page: only the tables scroll sideways, and every action is in reach', async ({ page }) => {
    await page.goto('/#memory/operations')
    await expect(tab(page)).toHaveAttribute('data-state', 'ready')
    await expect(rows(page).first()).toBeVisible()

    // The page itself does not scroll sideways.
    const overflow = await page.evaluate(() => {
      const root = document.documentElement
      return root.scrollWidth - root.clientWidth
    })
    expect(overflow).toBeLessThanOrEqual(1)

    // Nothing in the tab is wider than the screen, except inside a scroll box of its own.
    const tooWide = await page.evaluate(() => {
      const out: string[] = []
      for (const el of document.querySelectorAll<HTMLElement>('[data-testid="ma-operations"] *')) {
        if (el.closest('.ma-table-region')) continue
        const box = el.getBoundingClientRect()
        if (box.width > 0 && (box.right > window.innerWidth + 1 || box.left < -1)) {
          out.push(`${el.tagName}.${el.className}`)
        }
      }
      return out
    })
    expect(tooWide).toEqual([])

    // The table scrolls inside its own region, which the keyboard can reach.
    const region = page.getByRole('region', { name: 'Operations table' })
    await expect(region).toHaveAttribute('tabindex', '0')
    const scrolls = await region.evaluate((el) => el.scrollWidth > el.clientWidth)
    expect(scrolls).toBe(true)

    await expect(page.getByTestId('ma-ops-cause')).toBeVisible()
    await page.getByTestId('ma-ops-discard').scrollIntoViewIfNeeded()
    await page.getByTestId('ma-ops-discard').click()
    const confirm = page.getByTestId('ma-ops-discard-confirm')
    await expect(confirm.getByRole('button', { name: 'Keep them' })).toBeVisible()
    await confirm.getByRole('button', { name: 'Keep them' }).click()
    await expect(confirm).toHaveCount(0)
  })
})
