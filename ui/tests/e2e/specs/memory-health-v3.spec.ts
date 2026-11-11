/**
 * memory-health-v3 — Memory ▸ Health (#memory/health) as an ordered repair path
 * (ADR-0058 decision 12).
 *
 * The tab reads ONE answer, `GET /api/memory/health`: the verdict, the path in the route's order
 * (the step to do now, the ones that wait on it, the clear ones), the banks table and the page's
 * summary line. Only the engine facts strip still makes two older reads (`/api/memory/models`
 * and `/api/memory/graph/status`), routed here by the helpers in fixtures/memoryArea.ts.
 *
 * Forced mock. The health answer is built in the bundle in the state named by
 * `window.__hal0MockMemoryHealth` (src/api/mockFixtures.ts): `attention` (the default: extraction
 * is current, the queue and knowledge wait on it), `clear`, `unreachable`, `queue`,
 * `embedder-mixed`, `embedder-conflict`, `knowledge` and `partial`. Memory off and a non-Hindsight
 * engine are not bundle states, so those tests take the route out of the bundle's hands.
 *
 * Every test also routes the per-bank stats and mental-models reads past the bundle, so that if
 * the tab ever asked for them again the request would reach the network and be counted here.
 */
import type { Page } from '@playwright/test'

import { test, expect, json } from '../fixtures/apiMock'
import { routeGraph, routeModels } from '../fixtures/memoryArea'

type HealthState =
  | 'attention'
  | 'clear'
  | 'unreachable'
  | 'queue'
  | 'embedder-mixed'
  | 'embedder-conflict'
  | 'knowledge'
  | 'partial'

const PER_BANK = /\/api\/memory\/banks\/[^/]+\/(stats|mental-models)/

/** Paths taken out of the bundle's hands, so a request for them reaches `page.route`. */
async function passthrough(page: Page, extra: string[] = []) {
  await page.addInitScript((paths) => {
    ;(window as unknown as { __hal0MockPassthrough?: unknown }).__hal0MockPassthrough = [
      /^\/api\/memory\/banks\/[^/]+\/(stats|mental-models)/,
      ...paths,
    ]
  }, extra)
}

/** Count every per-bank stats or mental-models request the page makes. */
async function countPerBank(page: Page) {
  const seen: string[] = []
  await page.route(PER_BANK, (route) => {
    seen.push(route.request().url())
    return json(route, {})
  })
  return seen
}

async function open(page: Page, state: HealthState) {
  await page.addInitScript((s) => {
    ;(window as unknown as { __hal0MockMemoryHealth?: string }).__hal0MockMemoryHealth = s
  }, state)
  await passthrough(page)
  const perBank = await countPerBank(page)
  await routeModels(page)
  await routeGraph(page)
  await page.goto('/#memory/health')
  return perBank
}

const summary = (page: Page) => page.getByTestId('ma-health-summary')
const path = (page: Page) => page.getByTestId('ma-health-path')
const step = (page: Page, id: string) => path(page).locator(`> li[data-step="${id}"]`)

test.afterEach(async ({ page }) => {
  // An unrouted read answered `{}` would throw in a component: the view falls to the boundary.
  await expect(page.getByText('This view hit an error')).toHaveCount(0)
})

test.describe('one request', () => {
  test('mounting Health asks no bank for its stats or its mental models', async ({ page }) => {
    const perBank = await open(page, 'attention')
    await expect(summary(page)).toHaveAttribute('data-state', 'attention')
    await expect(page.getByTestId('ma-health-banks')).toBeVisible()
    await expect(page.getByTestId('ma-meta')).toHaveText(/^[\d,]+ facts in 6 banks$/)
    // Let a 30 s poll's worth of work settle: nothing per bank, ever.
    await page.waitForTimeout(500)
    expect(perBank).toEqual([])
  })
})

test.describe('clear', () => {
  test('says nothing needs attention and, in one sentence, what was checked', async ({ page }) => {
    await open(page, 'clear')
    await expect(summary(page)).toHaveAttribute('data-state', 'clear')
    await expect(summary(page).getByRole('heading', { level: 2 })).toHaveText('Nothing needs attention')
    await expect(page.getByTestId('ma-health-detail')).toHaveText(
      'The engine answers, and the 4 things this page watches are clear: storing memories, the queue, the embedder and knowledge.',
    )
    await expect(summary(page).locator('.chip')).toHaveText('working')
    await expect(path(page).locator('> li')).toHaveCount(4)
    await expect(path(page).locator('> li[data-state="clear"]')).toHaveCount(4)
    await expect(page.locator('.btn.primary, .primary')).toHaveCount(0)
  })
})

test.describe('attention: extraction current, queue and knowledge waiting', () => {
  test.beforeEach(async ({ page }) => {
    await open(page, 'attention')
  })

  test('counts the open steps in words and names the current one', async ({ page }) => {
    await expect(summary(page).getByRole('heading', { level: 2 })).toHaveText('3 things need you')
    await expect(page.getByTestId('ma-health-detail')).toHaveText(
      /^Start with step 1, storing memories: fix what is failing when memories are stored\. The other 2 wait on it, in the order below\.$/,
    )
  })

  test('an ordered list in the route order; the current row is marked by words and aria-current', async ({
    page,
  }) => {
    await expect(path(page)).toHaveJSProperty('tagName', 'OL')
    const ids = await path(page).locator('> li').evaluateAll((lis) => lis.map((li) => `${li.dataset.step}:${li.dataset.state}`))
    expect(ids).toEqual(['extraction:current', 'queue:waiting', 'embedder:clear', 'knowledge:waiting'])
    await expect(step(page, 'extraction')).toHaveAttribute('aria-current', 'step')
    await expect(step(page, 'extraction')).toContainText('Step 1 of 4 · do this now')
    await expect(page.locator('[aria-current="step"]')).toHaveCount(1)
  })

  test('waiting steps say what they wait on', async ({ page }) => {
    await expect(page.getByTestId('ma-health-step-queue-waits')).toHaveText('Waiting on step 1: storing memories.')
    await expect(page.getByTestId('ma-health-step-knowledge-waits')).toHaveText('Waiting on step 2: the queue.')
  })

  test('extraction: Services and Engine settings links, the grouped errors, no primary control', async ({ page }) => {
    await expect(page.getByTestId('ma-health-step-extraction-services')).toHaveAttribute('href', '#services')
    await expect(page.getByTestId('ma-health-step-extraction-settings')).toHaveAttribute('href', '#settings/memory')
    const errors = page.getByTestId('ma-health-step-extraction-errors')
    await expect(errors.locator('li').first()).toContainText(/seen \d+ times?/)
    await expect(errors).not.toContainText(/[{}]/)
    await expect(page.locator('.btn.primary, .primary')).toHaveCount(0)
  })

  test('no reason code or figure key reaches the page', async ({ page }) => {
    // The engine's own error text (which may name a task type) is quoted as reported; the rest
    // of the path is the page's words.
    const text = await path(page).evaluate((el) => {
      const copy = el.cloneNode(true) as HTMLElement
      copy.querySelectorAll('[data-testid$="-errors"]').forEach((node) => node.remove())
      return copy.innerText
    })
    expect(text).not.toMatch(/\b[a-z]+_[a-z_]+\b/)
    expect(text).not.toMatch(/[{}]/)
  })

  test('"Open Operations" opens the Operations tab', async ({ page }) => {
    await page.getByTestId('ma-health-step-queue-operations').click()
    await expect(page).toHaveURL(/#memory\/operations/)
  })
})

test.describe('each later step current in turn', () => {
  test('the queue alone', async ({ page }) => {
    await open(page, 'queue')
    await expect(summary(page).getByRole('heading', { level: 2 })).toHaveText('1 thing needs you')
    await expect(step(page, 'queue')).toHaveAttribute('data-state', 'current')
    await expect(step(page, 'extraction')).toHaveAttribute('data-state', 'clear')
    await expect(page.getByTestId('ma-health-step-queue-operations')).toHaveText(/Open Operations/)
  })

  test('the embedder, for a mixed store: the switch as a dry run to copy, and nothing that runs it', async ({
    page,
  }) => {
    await open(page, 'embedder-mixed')
    await expect(step(page, 'embedder')).toHaveAttribute('data-state', 'current')
    await expect(page.getByTestId('ma-health-step-embedder-text')).toContainText('two embedders')
    await expect(page.getByTestId('ma-health-step-embedder-command').locator('code')).toHaveText(
      'hal0 memory embedder switch hindsight-ai/hs-embed-1',
    )
    await expect(step(page, 'embedder')).toContainText('dry run')
    await expect(step(page, 'embedder').locator('button')).toHaveCount(1)
    await expect(step(page, 'embedder').locator('button')).toHaveAttribute('aria-label', 'Copy command')
  })

  test('the embedder, for a conflict: both models named, hal0 doctor to copy', async ({ page }) => {
    await open(page, 'embedder-conflict')
    await expect(step(page, 'embedder')).toHaveAttribute('data-state', 'current')
    await expect(page.getByTestId('ma-health-step-embedder-text')).toContainText('hindsight-ai/hs-embed-1')
    await expect(page.getByTestId('ma-health-step-embedder-text')).toContainText('BAAI/bge-small-en-v1.5')
    await expect(page.getByTestId('ma-health-step-embedder-command').locator('code')).toHaveText('hal0 doctor')
  })

  test('knowledge alone', async ({ page }) => {
    await open(page, 'knowledge')
    await expect(step(page, 'knowledge')).toHaveAttribute('data-state', 'current')
    await expect(page.getByTestId('ma-health-step-knowledge-text')).toHaveText(
      '5 knowledge pages and 6 mental models are stale: behind the facts they were built from. No refresh has failed.',
    )
    await page.getByTestId('ma-health-step-knowledge-knowledge').click()
    await expect(page).toHaveURL(/#memory\/knowledge/)
  })
})

test.describe('partial', () => {
  test('never an unqualified all clear; the gap is said beside the figures', async ({ page }) => {
    await open(page, 'partial')
    await expect(summary(page).getByRole('heading', { level: 2 })).toHaveText('Nothing needs attention in what was read')
    await expect(summary(page).locator('.chip')).toHaveCount(0)
    await expect(page.getByTestId('ma-health-qualifier')).toContainText('did not answer in time')
    await expect(page.getByTestId('ma-health-step-queue-note')).toContainText('did not answer in time')
    await expect(page.getByTestId('ma-health-banks-partial')).toContainText('did not answer in time')
    // The banks that did not answer show the mark, not zero.
    await expect(page.locator('[data-bank="big"] [data-col="waiting"] .ma-missing')).toHaveCount(1)
  })
})

test.describe('engine unreachable', () => {
  test.beforeEach(async ({ page }) => {
    await open(page, 'unreachable')
  })

  test('says what is known and what is not, with no figure', async ({ page }) => {
    const card = page.getByTestId('ma-health-unreachable')
    await expect(card.getByRole('heading', { level: 2 })).toHaveText('The memory engine is not answering')
    await expect(page.getByText('What is known', { exact: true })).toBeVisible()
    await expect(page.getByText('What is not known until it answers')).toBeVisible()
    await expect(page.getByTestId('ma-health-banks')).toHaveCount(0)
    await expect(page.getByTestId('ma-meta')).toHaveText('Engine unreachable · no counts available')
  })

  test('links to Services and gives the doctor command', async ({ page }) => {
    await expect(page.getByTestId('ma-health-services')).toHaveAttribute('href', '#services')
    await expect(page.getByTestId('ma-health-doctor').locator('code')).toHaveText('hal0 doctor')
  })

  test('"Check again" asks for the health answer again', async ({ page }) => {
    await page.getByTestId('ma-health-recheck').click()
    await expect(page.getByTestId('ma-health-unreachable')).toBeVisible()
  })
})

test.describe('no path', () => {
  const noPath = (enabled: boolean, reason: string) => ({
    enabled,
    reason,
    checked_at: '2026-06-12T22:20:00+00:00',
    verdict: null,
    partial: [],
    steps: [],
  })

  for (const [name, enabled, reason, testId] of [
    ['memory turned off', false, 'memory_disabled', 'ma-health-off'],
    ['a non-Hindsight engine', true, 'engine_unsupported', 'ma-health-fallback'],
  ] as const) {
    test(`${name}: says so, links to engine settings, shows no path`, async ({ page }) => {
      await passthrough(page, ['/api/memory/health'])
      await countPerBank(page)
      await routeModels(page)
      await routeGraph(page)
      await page.route('**/api/memory/health', (route) => json(route, noPath(enabled, reason)))
      await page.goto('/#memory/health')
      await expect(page.getByTestId(testId)).toBeVisible()
      await expect(page.getByTestId(testId).locator('a')).toHaveAttribute('href', '#settings/memory')
      await expect(path(page)).toHaveCount(0)
    })
  }
})

test.describe('below the path', () => {
  test('engine facts strip: five cells, in order', async ({ page }) => {
    await open(page, 'clear')
    const strip = page.getByTestId('ma-health-strip')
    await expect(strip.locator('.ma-stat-label')).toHaveText(['Engine', 'Extraction', 'Embedder', 'Reranker', 'Decision model'])
    await expect(strip.locator('[data-stat="engine"]')).toContainText('Hindsight 0.9.2')
    await expect(strip.locator('[data-stat="embedder"]')).toContainText('hindsight-ai/hs-embed-1')
    await expect(strip.locator('[data-stat="reranker"]')).toContainText('hs-rerank-1')
  })

  test('banks table: a row for each of the six banks, largest first, a row opens its facts', async ({ page }) => {
    await open(page, 'clear')
    const rows = page.getByTestId('ma-health-banks').locator('tbody tr')
    await expect(rows).toHaveCount(6)
    const facts = await rows
      .locator('[data-col="facts"]')
      .evaluateAll((tds) => tds.map((td) => Number((td.textContent || '').replace(/,/g, ''))))
    expect([...facts].sort((a, b) => b - a)).toEqual(facts)
    await rows.first().locator('a').click()
    await expect(page).toHaveURL(/#memory\/facts\?bank=/)
  })
})

test.describe('narrow screen', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('the page does not scroll sideways', async ({ page }) => {
    await open(page, 'attention')
    await expect(path(page)).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
  })
})
