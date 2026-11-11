/**
 * memory-health-v3 — Memory ▸ Health (#memory/health), the interim status view.
 *
 * It is built from the per-subject routes, not yet from the composed `/api/memory/health`, so it
 * has no order to it: it lists what it can see and never calls anything a "step". This spec pins
 * its states (all clear, something needs attention, engine unreachable), the notice for each state
 * of the embedder record, and the banks table.
 *
 * The engine panel, bank rows and outage assertions that memory-v2-overview, memory-v2-view and
 * memory-bank-error-branches made against the old Overview live here now; the growth chart and
 * the New bank form they also covered were dropped with that page.
 *
 * Forced mock: six banks, `ingest` with 7 operations waiting and 1 failed (so the default box
 * needs attention). Per-spec routes set the rest; see fixtures/memoryArea.ts.
 */
import type { Page } from '@playwright/test'

import { test, expect } from '../fixtures/apiMock'
import {
  RECORD_CONFLICT,
  RECORD_MIXED,
  RECORD_UPGRADED,
  routeAllClear,
  routeEngineDown,
  routeGraph,
  routeModels,
} from '../fixtures/memoryArea'

const stat = (page: Page, id: string) => page.locator(`[data-testid="ma-health-strip"] [data-stat="${id}"]`)
const summary = (page: Page) => page.getByTestId('ma-health-summary')

/** Nothing on this page is a step, in any state. */
async function expectNoStepWording(page: Page) {
  await expect(page.getByTestId('memory-area')).not.toContainText(/\bstep\b/i)
}

test.describe('all clear', () => {
  test.beforeEach(async ({ page }) => {
    await routeAllClear(page)
    await page.goto('/#memory/health')
  })

  test('says nothing needs attention', async ({ page }) => {
    await expect(summary(page)).toHaveAttribute('data-state', 'clear')
    await expect(summary(page).getByRole('heading', { level: 2 })).toHaveText('Nothing needs attention')
    await expect(summary(page).locator('.chip')).toHaveText('working')
    await expect(page.getByTestId('ma-health-findings')).toHaveCount(0)
    await expect(page.getByTestId('ma-health-embedder')).toHaveCount(0)
    await expectNoStepWording(page)
  })

  test('engine facts: version and reachability, extraction, embedder, reranker, decision model', async ({
    page,
  }) => {
    const strip = page.getByTestId('ma-health-strip')
    await expect(strip.locator('.ma-stat-label')).toHaveText([
      'Engine',
      'Extraction',
      'Embedder',
      'Reranker',
      'Decision model',
    ])
    // buildMemoryEngine() in the mock: reachable, version 0.9.2.
    await expect(stat(page, 'engine')).toContainText('Hindsight 0.9.2')
    await expect(stat(page, 'engine').locator('.chip')).toHaveText('reachable')
    await expect(stat(page, 'extraction')).toContainText('memory slot')
    await expect(stat(page, 'extraction')).toContainText('dedicated slot · resolves to a model')
    await expect(stat(page, 'embedder')).toContainText('hs-embed-1')
    await expect(stat(page, 'reranker')).toContainText('hs-rerank-1')
    await expect(stat(page, 'decision')).toContainText('Off')
  })

  test('one line for the queue and one for knowledge, each a stated sum, each with its link', async ({
    page,
  }) => {
    await expect(page.getByTestId('ma-health-work')).toContainText('each line is a sum of per-bank figures')
    const queue = page.getByTestId('ma-health-queue')
    await expect(queue).toContainText('7 operations waiting and 0 failed, summed across 6 banks.')
    await expect(queue.getByRole('link', { name: 'Open Operations' })).toHaveAttribute('href', '#memory/operations')
    const knowledge = page.getByTestId('ma-health-knowledge')
    await expect(knowledge).toContainText(/\d+ of \d+ mental models are stale, summed across 6 banks\./)
    await knowledge.getByRole('link', { name: 'Open Knowledge' }).click()
    await expect(page).toHaveURL(/#memory\/knowledge$/)
    await expect(page.getByTestId('ma-knowledge-stub')).toBeVisible()
  })

  test('banks table: a real table, a row for each of the six banks, largest first', async ({ page }) => {
    const region = page.getByRole('region', { name: 'Banks table' })
    await expect(region).toHaveAttribute('tabindex', '0')
    const table = page.getByTestId('ma-health-banks')
    await expect(table.locator('thead th')).toHaveText(['Bank', 'Facts', 'Documents', 'Waiting', 'Failed'])
    await expect(table.locator('thead th[scope="col"]')).toHaveCount(5)
    await expect(table.locator('tbody th[scope="row"]')).toHaveText([
      'ingest',
      'hermes',
      'big',
      'scratch',
      'primary',
      'empty',
    ])
    const ingest = table.locator('tr[data-bank="ingest"]')
    await expect(ingest.locator('[data-col="facts"]')).toHaveText('4,310')
    await expect(ingest.locator('[data-col="waiting"]')).toHaveText('7')
    await expect(ingest.locator('[data-col="failed"]')).toHaveText('0')
    // An empty bank reports zero: that is a figure, not a gap.
    await expect(table.locator('tr[data-bank="empty"] [data-col="facts"]')).toHaveText('0')
    await expect(page.getByTestId('ma-health-banks-foot')).toHaveCount(0)
  })

  test('a bank row opens that bank in Facts', async ({ page }) => {
    const link = page.getByTestId('ma-health-banks').getByRole('link', { name: 'scratch' })
    await expect(link).toHaveAttribute('href', '#memory/facts?bank=scratch')
    await link.click()
    await expect(page).toHaveURL(/#memory\/facts\?bank=scratch$/)
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
    await expect(page.getByTestId('ma-scope-bank').getByRole('radio', { name: 'scratch' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
  })

  test('no chart, and no figure at display size', async ({ page }) => {
    const area = page.getByTestId('memory-area')
    await expect(area.locator('canvas')).toHaveCount(0)
    await expect(area.locator('svg rect')).toHaveCount(0)
    // The page title is the largest type; no number is set larger than the kit's 18px.
    const sizes = await area.evaluate((root) =>
      [...root.querySelectorAll('*')]
        .filter((el) => el.tagName !== 'H1' && /\d/.test(el.textContent || '') && el.children.length === 0)
        .map((el) => parseFloat(getComputedStyle(el).fontSize)),
    )
    expect(Math.max(...sizes)).toBeLessThanOrEqual(18)
  })
})

test.describe('embedder record', () => {
  test('the ordinary upgraded box gets one quiet line, and stays clear', async ({ page }) => {
    await routeAllClear(page)
    await routeModels(page, RECORD_UPGRADED)
    await page.goto('/#memory/health')
    const note = page.getByTestId('ma-health-embedder')
    await expect(note).toHaveAttribute('data-kind', 'quiet')
    await expect(note.locator('.chip')).toHaveCount(0)
    await expect(note).toHaveText(
      'This store was built with BAAI/bge-small-en-v1.5 and the engine stays on it. ' +
        'Settings ask for hs-embed-1, which has no effect until the store is re-embedded.',
    )
    await expect(summary(page)).toHaveAttribute('data-state', 'clear')
    await expect(stat(page, 'embedder')).toContainText('settings ask for hs-embed-1')
  })

  test('a mixed store is a warning that names both models', async ({ page }) => {
    await routeAllClear(page)
    await routeModels(page, RECORD_MIXED)
    await page.goto('/#memory/health')
    const note = page.getByTestId('ma-health-embedder')
    await expect(note.locator('.chip')).toHaveText('mixed')
    await expect(note.locator('.chip')).toHaveAttribute('data-tone', 'warn')
    await expect(note.locator('.ma-notice-text')).toHaveText(
      'This store may hold vectors from two embedders: it existed before the engine moved from ' +
        'BAAI/bge-small-en-v1.5 to hs-embed-1. Recall over older facts is degraded until the store is re-embedded.',
    )
    await expect(summary(page)).toHaveAttribute('data-state', 'attention')
    await expect(page.getByTestId('ma-health-findings')).toContainText('two embedders')
    await expect(stat(page, 'embedder').locator('.chip')).toHaveText('mixed')
  })

  test('a conflict is a warning that names both models', async ({ page }) => {
    await routeAllClear(page)
    await routeModels(page, RECORD_CONFLICT)
    await page.goto('/#memory/health')
    const note = page.getByTestId('ma-health-embedder')
    await expect(note.locator('.chip')).toHaveAttribute('data-tone', 'warn')
    await expect(note.locator('.ma-notice-text')).toHaveText(
      'The embedder record says hs-embed-1, but the engine is set to run on BAAI/bge-small-en-v1.5. ' +
        'hal0 has changed neither. Remove the override or correct the record so the two agree.',
    )
    await expect(summary(page)).toHaveAttribute('data-state', 'attention')
  })

  test('no state of the notice offers a button: nothing here starts a switch', async ({ page }) => {
    await routeAllClear(page)
    await routeModels(page, RECORD_MIXED)
    await page.goto('/#memory/health')
    await expect(page.getByTestId('ma-health-embedder')).toBeVisible()
    await expect(page.getByTestId('ma-health-embedder').locator('button, a')).toHaveCount(0)
    await expect(page.getByTestId('memory-area').getByRole('button')).toHaveCount(0)
  })
})

test.describe('something needs attention', () => {
  test('lists what failed, in no order, and never as steps', async ({ page }) => {
    // The default mock: `ingest` has a failed operation. Add an extraction slot that does not
    // resolve.
    await routeModels(page)
    await routeGraph(page, { slot_resolves: false })
    await page.goto('/#memory/health')
    await expect(summary(page)).toHaveAttribute('data-state', 'attention')
    await expect(summary(page).getByRole('heading', { level: 2 })).toHaveText('2 things need attention')
    const findings = page.getByTestId('ma-health-findings')
    await expect(findings.locator('li')).toHaveText([
      'Extraction is stalled: its slot does not match an enabled LLM slot.',
      '1 operation has failed.',
    ])
    await expect(stat(page, 'extraction').locator('.chip')).toHaveText('unreachable')
    await expect(page.getByTestId('ma-health-queue')).toContainText(
      /\d+ operations waiting and 1 failed, summed across 6 banks\./,
    )
    await expect(page.getByTestId('ma-health-banks').locator('tr[data-bank="ingest"] [data-col="failed"]')).toHaveText('1')
    await expectNoStepWording(page)
  })

  test('a record the models route does not carry reads as not reported, never as fine', async ({ page }) => {
    // The default fixtures: the models route answers `{}`.
    await routeAllClear(page)
    await page.unroute('**/api/memory/models')
    await page.goto('/#memory/health')
    await expect(stat(page, 'embedder').getByRole('img', { name: 'not reported' })).toBeVisible()
    await expect(summary(page)).toHaveAttribute('data-state', 'partial')
    await expect(summary(page)).toContainText('Not reported: the embedder record.')
  })
})

test.describe('engine unreachable', () => {
  test.beforeEach(async ({ page }) => {
    await routeEngineDown(page)
    await page.goto('/#memory/health')
  })

  test('says what is known and what is not, with no figure anywhere', async ({ page }) => {
    await expect(page.getByTestId('ma-meta')).toHaveText('Engine unreachable · no counts available')
    const card = page.getByTestId('ma-health-unreachable')
    await expect(card.getByRole('heading', { level: 2 })).toHaveText('The memory engine is not answering')
    await expect(card.locator('.chip').first()).toHaveText('unreachable')
    await expect(card).toContainText('Nothing here is counted as zero')
    await expect(page.getByRole('heading', { name: 'What is known' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'What is not known until it answers' })).toBeVisible()
    await expect(page.getByTestId('ma-health-strip')).toHaveCount(0)
    await expect(page.getByTestId('ma-health-banks')).toHaveCount(0)
    await expect(page.getByTestId('ma-health-summary')).toHaveCount(0)
    // No count of anything: an outage is not an empty engine.
    const words = (await page.getByTestId('memory-area').innerText()).replace(/hal0/g, '')
    expect(words).not.toMatch(/\d/)
    await expectNoStepWording(page)
  })

  test('links to Services and gives the doctor command', async ({ page }) => {
    const box = page.getByTestId('ma-health-doctor')
    await expect(box.locator('code')).toHaveText('hal0 doctor')
    await expect(box.getByRole('button', { name: 'Copy command' })).toBeVisible()
    await page.getByTestId('ma-health-services').click()
    await expect(page).toHaveURL(/#services$/)
  })

  test('"Check again" asks the engine again', async ({ page }) => {
    let checks = 0
    await page.route('**/api/memory/engine', (route) => {
      checks += 1
      return route.fallback()
    })
    await page.getByTestId('ma-health-recheck').click()
    await expect.poll(() => checks).toBeGreaterThan(0)
    await expect(page.getByTestId('ma-health-unreachable')).toBeVisible()
  })

  test('the other tabs say so too, instead of showing an empty bank', async ({ page }) => {
    await page.getByTestId('ma-tab-facts').click()
    await expect(page.getByTestId('ma-facts-unreachable')).toContainText('The memory engine is not answering')
    await expect(page.getByTestId('mv-workspace')).toHaveCount(0)
    await page.getByTestId('ma-facts-unreachable').getByRole('link', { name: 'Open Health' }).click()
    await expect(page.getByTestId('ma-health-unreachable')).toBeVisible()
  })
})

test.describe('a bank that does not answer (#1539)', () => {
  // An outage must not read as an empty bank: with every bank-scoped route failing, a row shows
  // the "not reported" mark, never a zero, and each sum says it has nothing to add up.
  test.beforeEach(async ({ page }) => {
    await routeModels(page)
    await routeGraph(page)
    await page.addInitScript(() => {
      ;(window as unknown as { __hal0MockPassthrough?: unknown }).__hal0MockPassthrough = ['/api/memory/banks/']
    })
    await page.route('**/api/memory/banks/*/**', (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          error: { code: 'memory.engine_unreachable', message: 'hindsight-api is not responding', details: {} },
        }),
      }),
    )
    await page.goto('/#memory/health')
  })

  test('rows show "not reported", not zero facts', async ({ page }) => {
    const row = page.getByTestId('ma-health-banks').locator('tr[data-bank="hermes"]')
    await expect(row.locator('[data-col="documents"]').getByRole('img', { name: 'not reported' })).toBeVisible({
      timeout: 15_000,
    })
    await expect(row.locator('[data-col="waiting"]')).toHaveText('–')
    await expect(row.locator('[data-col="failed"]')).toHaveText('–')
    await expect(page.getByTestId('ma-health-banks-foot')).toHaveText('– means the engine did not report a figure')
  })

  test('the sums say they have nothing to add up, and the page is not called clear', async ({ page }) => {
    await expect(page.getByTestId('ma-health-queue')).toContainText(
      'Not reported: no bank answered with its operation counts.',
      { timeout: 15_000 },
    )
    await expect(page.getByTestId('ma-health-knowledge')).toContainText(
      'Not reported: no bank answered with its mental models.',
    )
    await expect(summary(page)).toHaveAttribute('data-state', 'partial')
    await expect(summary(page).getByRole('heading', { level: 2 })).toHaveText('No problems found in what was reported')
  })
})
