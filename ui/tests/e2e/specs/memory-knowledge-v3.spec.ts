/**
 * memory-knowledge-v3 — Memory ▸ Knowledge (#memory/knowledge): a bank's knowledge pages, its
 * mental models and its directives.
 *
 * Pins what an operator does here: open a page from the tree (and by a link), read a mental
 * model's history, refresh one page and everything stale, edit a page, add and delete a
 * directive, change how often the bank refreshes; and the states around that (an empty bank, the
 * engine not answering, a phone-width screen).
 *
 * Forced mock. The bundle answers the GETs with the fixtures in src/api/mockFixtures.ts: bank
 * `primary` has three pages in two folders (`kb-thermal-envelope` and `kb-power-resilience`
 * fresh, `kb-operator-style` stale with a failed refresh), two mental models that are not pages
 * (`mm-project-decisions` with a history, `mm-operator-preferences` stale) and four directives;
 * bank `empty` has none of any. Writes are never answered by the bundle: they reach `page.route`,
 * where the fixture's catch-all answers `{}` unless a test routes them. A test that needs a read
 * to change after a write names it in `window.__hal0MockPassthrough` and serves it itself.
 */
import type { Page, Request } from '@playwright/test'

import { test, expect, json } from '../fixtures/apiMock'
import { routeEngineDown } from '../fixtures/memoryArea'

const URL_PRIMARY = '/#memory/knowledge?bank=primary'

const node = (page: Page, id: string) => page.getByTestId(`ma-kn-node-${id}`)
const reader = (page: Page) => page.getByTestId('ma-kn-reader')
const title = (page: Page) => page.getByTestId('ma-kn-title')

/** Record the writes a test makes, answering each `{}` (or what `answer` gives for it). */
async function recordWrites(
  page: Page,
  pattern: RegExp,
  answer: (request: Request) => { status?: number; body: unknown } = () => ({ body: {} }),
) {
  const seen: { method: string; path: string; body: unknown }[] = []
  await page.route(pattern, (route) => {
    const request = route.request()
    if (request.method() === 'GET') return route.fallback()
    seen.push({
      method: request.method(),
      path: decodeURIComponent(new URL(request.url()).pathname),
      body: request.postDataJSON(),
    })
    const { status = 200, body } = answer(request)
    return json(route, body, status)
  })
  return seen
}

// A view that throws lands on the dashboard's error boundary. No test here should ever see it:
// every route the tab reads is answered by the bundle or by the fixture's defaults.
test.afterEach(async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'This view hit an error' })).toHaveCount(0)
})

test.describe('reading', () => {
  test('the tab shows the bank: a summary, the page tree, the mental models and the directives', async ({
    page,
  }) => {
    await page.goto(URL_PRIMARY)
    const summary = page.getByTestId('ma-kn-summary')
    await expect(summary.locator('.ma-stat-label')).toHaveText([
      'Knowledge pages',
      'Mental models',
      'Directives',
      'Last successful refresh',
    ])
    await expect(summary.locator('[data-stat="pages"] .ma-stat-value')).toContainText('3')
    await expect(summary.locator('[data-stat="pages"]')).toContainText('1 of 3 stale · 1 failed its last refresh')
    await expect(summary.locator('[data-stat="models"]')).toContainText('1 of 2 stale')
    await expect(summary.locator('[data-stat="directives"] .ma-stat-value')).toContainText('4')

    // Each model is listed once: three as pages, two as mental models.
    await expect(page.getByTestId('ma-kn-tree').locator('[data-kind="page"]')).toHaveCount(3)
    await expect(page.getByTestId('ma-kn-models').getByRole('button')).toHaveCount(2)
    await expect(page.getByTestId('ma-kn-directives-list').locator('> li')).toHaveCount(4)

    // The stub is gone, and with it the link to the legacy tools page.
    await expect(page.getByTestId('ma-knowledge-stub')).toHaveCount(0)
    await expect(page.getByTestId('ma-knowledge').locator('a[href*="memworkspace"]')).toHaveCount(0)
    // No raw engine output anywhere on the tab.
    await expect(page.getByTestId('ma-knowledge')).not.toContainText(/\d{4}-\d\d-\d\dT|is_stale|mental_model|[{}]/)
  })

  test('opening a page from the tree shows it and writes it into the address, in place', async ({ page }) => {
    await page.goto('/#memory/health')
    await page.getByTestId('ma-tab-knowledge').click()
    // The first bank is written into the address, and the first page is shown.
    await expect(page).toHaveURL(/#memory\/knowledge\?bank=primary$/)
    await expect(title(page)).toHaveText('iGPU thermal envelope')

    await node(page, 'kb-operator-style').click()
    await expect(page).toHaveURL(/#memory\/knowledge\?bank=primary&page=kb-operator-style$/)
    await expect(title(page)).toHaveText('Operator interaction style')
    await expect(node(page, 'kb-operator-style')).toHaveAttribute('aria-selected', 'true')
    await expect(node(page, 'kb-operator-style')).toContainText('open')
    await expect(node(page, 'kb-thermal-envelope')).toHaveAttribute('aria-selected', 'false')

    await node(page, 'kb-power-resilience').click()
    await expect(page).toHaveURL(/page=kb-power-resilience$/)
    // Opening pages made no history entries: Back leaves the tab, it does not walk the pages.
    await page.goBack()
    await expect(page).toHaveURL(/#memory\/health$/)
  })

  test('a link with page= opens that page: title, question, tags, last refresh and its text', async ({ page }) => {
    await page.goto(`${URL_PRIMARY}&page=kb-thermal-envelope`)
    await expect(title(page)).toHaveText('iGPU thermal envelope')
    await expect(reader(page)).toHaveAttribute('data-kind', 'knowledge page')
    await expect(page.getByTestId('ma-kn-question')).toHaveText('what are the iGPU thermal limits?')
    await expect(page.getByTestId('ma-kn-tags')).toHaveText('performance')
    await expect(page.getByTestId('ma-kn-refreshed')).toContainText('9 Jun 2026')
    await expect(page.getByTestId('ma-kn-trigger')).toContainText('Refreshes every 4 h')
    await expect(reader(page).locator('.ma-card-head .chip')).toHaveText('fresh')

    // The text is the page's markdown, drawn as elements.
    const content = page.getByTestId('ma-kn-content')
    await expect(content.getByRole('heading', { name: 'Limits' })).toBeVisible()
    await expect(content.locator('strong')).toHaveText('95 C')
    await expect(content.getByRole('table')).toContainText('baseline')
    await expect(content.locator('ol > li')).toHaveCount(2)
    await expect(content.locator('pre')).toHaveText('ryzenadj --set-coall=0xFFFE2')
    await expect(node(page, 'kb-thermal-envelope')).toHaveAttribute('aria-selected', 'true')
  })

  test('a stale page whose last refresh failed says so, and why', async ({ page }) => {
    await page.goto(`${URL_PRIMARY}&page=kb-operator-style`)
    const notice = page.getByTestId('ma-kn-notice')
    await expect(notice.locator('.chip')).toHaveText('failed')
    await expect(notice).toContainText('The last refresh failed on 11 Jun 2026')
    await expect(notice).toContainText('Shown as of 28 May 2026 until a refresh succeeds.')
    await expect(page.getByTestId('ma-kn-failure-reason')).toHaveText(
      'The engine said: APIConnectionError: Connection error.',
    )
    // The chips in the tree are only on the rows they apply to.
    await expect(node(page, 'kb-operator-style').locator('.chip')).toHaveText(['failed', 'stale'])
    await expect(node(page, 'kb-thermal-envelope').locator('.chip')).toHaveCount(0)
  })

  test('a link to something the bank does not hold says so, and opens nothing else', async ({ page }) => {
    await page.goto(`${URL_PRIMARY}&page=kb-deleted`)
    await expect(page.getByTestId('ma-kn-reader-missing')).toContainText('Nothing in this bank has that address')
    await expect(title(page)).toHaveCount(0)
    await expect(page.getByTestId('ma-kn-tree').locator('[aria-selected="true"]')).toHaveCount(0)
  })

  test('the tree works from the keyboard: arrows move, Left closes a folder, Enter opens a page', async ({
    page,
  }) => {
    await page.goto(`${URL_PRIMARY}&page=kb-thermal-envelope`)
    await expect(title(page)).toHaveText('iGPU thermal envelope')
    // One tab stop: the open page.
    await expect(page.getByTestId('ma-kn-tree').locator('[tabindex="0"]')).toHaveCount(1)
    await node(page, 'kb-thermal-envelope').focus()
    await page.keyboard.press('ArrowDown')
    await expect(node(page, 'kb-power-resilience')).toBeFocused()
    // Moving does not open anything.
    await expect(title(page)).toHaveText('iGPU thermal envelope')
    await page.keyboard.press('Enter')
    await expect(title(page)).toHaveText('Power resilience posture')
    await expect(page).toHaveURL(/page=kb-power-resilience$/)

    await page.keyboard.press('ArrowLeft')
    await expect(node(page, 'kbf-box')).toBeFocused()
    await page.keyboard.press('ArrowLeft')
    await expect(node(page, 'kbf-box')).toHaveAttribute('aria-expanded', 'false')
    await expect(node(page, 'kb-power-resilience')).toHaveCount(0)
    await page.keyboard.press('ArrowRight')
    await expect(node(page, 'kb-power-resilience')).toBeVisible()
    await page.keyboard.press('End')
    await expect(node(page, 'kb-operator-style')).toBeFocused()
  })

  test('a mental model opens in the same reader, with its history; an earlier text can be read', async ({
    page,
  }) => {
    await page.goto(URL_PRIMARY)
    await page.getByTestId('ma-kn-model-mm-project-decisions').click()
    await expect(page).toHaveURL(/page=mm-project-decisions$/)
    await expect(reader(page)).toHaveAttribute('data-kind', 'mental model')
    await expect(title(page)).toHaveText('Project decisions')
    await expect(page.getByTestId('ma-kn-question')).toHaveText(
      'what has been decided about how this box is run, and why?',
    )
    await expect(page.getByTestId('ma-kn-trigger')).toContainText('Refreshes each time new facts are consolidated.')
    await expect(page.getByTestId('ma-kn-trigger')).toContainText('At most one automatic refresh every 1 h.')
    await expect(page.getByTestId('ma-kn-content')).toContainText('The UPS shuts the box down at 20% battery.')
    await expect(page.getByTestId('ma-kn-model-mm-project-decisions')).toHaveAttribute('aria-current', 'true')

    const history = page.getByTestId('ma-kn-history-list')
    await expect(history.locator('> li')).toHaveCount(5)
    await expect(history.locator('> li').nth(0)).toContainText('Current text')
    await expect(history.locator('> li').nth(1)).toContainText('Text replaced on 10 Jun 2026')
    await expect(history.locator('> li').nth(2)).toContainText('Refresh failed on 2 Jun 2026')
    await expect(history.locator('> li').nth(2)).toContainText('Task exceeded the 300s wall-clock limit')
    await expect(history.locator('> li').nth(4)).toContainText('First written on 6 May 2026')

    await page.getByTestId('ma-kn-history-version').first().click()
    await expect(page.getByTestId('ma-kn-version-note')).toContainText(
      'Showing the text that was replaced on 10 Jun 2026',
    )
    await expect(page.getByTestId('ma-kn-content')).toContainText('What the UPS should do on low battery.')
    await expect(page.getByTestId('ma-kn-history-version').first()).toHaveAttribute('aria-current', 'true')

    await page.getByTestId('ma-kn-history-current').click()
    await expect(page.getByTestId('ma-kn-version-note')).toHaveCount(0)
    await expect(page.getByTestId('ma-kn-content')).toContainText('The UPS shuts the box down at 20% battery.')
  })

  test('choosing another bank shows that bank and closes what was open', async ({ page }) => {
    await page.goto(`${URL_PRIMARY}&page=kb-operator-style`)
    await expect(title(page)).toHaveText('Operator interaction style')
    await page.getByTestId('ma-scope-bank').getByRole('radio', { name: 'scratch' }).click()
    await expect(page).toHaveURL(/#memory\/knowledge\?bank=scratch$/)
    await expect(page.getByTestId('ma-kn-directives-meta')).toContainText('scratch')
    await expect(title(page)).toHaveText('iGPU thermal envelope')
  })
})

test.describe('refreshing', () => {
  const POWER_REFRESH = '/api/memory/banks/primary/mental-models/mm-power-resilience/refresh'

  /**
   * Serve the bank's mental models from the test, so a refresh can change them. `finish` is what
   * the engine does when the queued refresh ends: it moves the page's model on.
   */
  async function routeModels(page: Page) {
    const stamp = { refreshedAt: '2026-06-10T08:00:00.000Z', failedAt: null as string | null }
    await page.addInitScript(() => {
      ;(window as unknown as { __hal0MockPassthrough?: unknown }).__hal0MockPassthrough = [
        /^\/api\/memory\/banks\/[^/]+\/mental-models$/,
      ]
    })
    await page.route(/\/api\/memory\/banks\/[^/]+\/mental-models(\?.*)?$/, (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      return json(route, {
        items: [
          {
            id: 'mm-power-resilience',
            name: 'Power resilience posture',
            source_query: 'how does the box handle power loss?',
            content: 'A UPS holds the box up.',
            tags: ['reliability'],
            is_stale: false,
            is_knowledge_page: true,
            last_refreshed_at: stamp.refreshedAt,
            last_refresh_failed_at: stamp.failedAt,
          },
          {
            id: 'mm-thermal-envelope',
            name: 'iGPU thermal envelope',
            source_query: 'what are the iGPU thermal limits?',
            content: '',
            tags: [],
            is_stale: false,
            is_knowledge_page: true,
            last_refreshed_at: '2026-06-09T19:30:00.000Z',
            last_refresh_failed_at: null,
          },
          {
            id: 'mm-operator-style',
            name: 'Operator interaction style',
            source_query: 'how should I talk to the operator?',
            content: '',
            tags: [],
            is_stale: true,
            is_knowledge_page: true,
            last_refreshed_at: '2026-05-28T23:00:00.000Z',
            last_refresh_failed_at: null,
          },
        ],
        total: 3,
      })
    })
    return stamp
  }

  test('a refresh that succeeds: the button waits, then the page says it finished', async ({ page }) => {
    const stamp = await routeModels(page)
    const writes = await recordWrites(page, /\/mental-models\/[^/]+\/refresh$/, () => {
      // The engine queues the refresh; here it has finished by the time the tab next looks.
      stamp.refreshedAt = '2026-06-12T09:00:00.000Z'
      return { body: { operation_id: 'op-1', status: 'queued' } }
    })
    await page.goto(`${URL_PRIMARY}&page=kb-power-resilience`)
    await expect(page.getByTestId('ma-kn-refreshed')).toContainText('10 Jun 2026')
    const button = page.getByTestId('ma-kn-refresh')
    await expect(button).toHaveText('Refresh')
    await expect(button).toHaveClass('btn ghost sm')
    await button.click()

    // A page is refreshed through its mental model.
    await expect(page.getByTestId('ma-kn-run-status')).toHaveText('The refresh finished.', { timeout: 15_000 })
    expect(writes).toEqual([{ method: 'POST', path: POWER_REFRESH, body: {} }])
    await expect(page.getByTestId('ma-kn-refreshed')).toContainText('12 Jun 2026')
    await expect(button).toBeEnabled()
    await expect(button).toHaveText('Refresh')
  })

  test('a refresh in progress disables the button and says why; one that fails later says it failed', async ({
    page,
  }) => {
    const stamp = await routeModels(page)
    await recordWrites(page, /\/mental-models\/[^/]+\/refresh$/, () => ({
      body: { operation_id: 'op-1', status: 'queued' },
    }))
    await page.goto(`${URL_PRIMARY}&page=kb-power-resilience`)
    const button = page.getByTestId('ma-kn-refresh')
    await button.click()
    await expect(button).toBeDisabled()
    await expect(button).toHaveText('Refreshing…')
    await expect(reader(page).locator('.ma-reason')).toHaveText('A refresh is running. This updates when it finishes.')
    await expect(reader(page).locator('.ma-card-head .chip')).toHaveText('running')

    // The queued refresh fails: the model carries a new failure the next time the tab looks.
    stamp.failedAt = '2026-06-12T09:00:00.000Z'
    await expect(page.getByTestId('ma-kn-run-status')).toHaveText('The refresh failed.', { timeout: 15_000 })
    await expect(page.getByTestId('ma-kn-notice').locator('.chip')).toHaveText('failed')
    await expect(page.getByTestId('ma-kn-notice')).toContainText('The last refresh failed on 12 Jun 2026')
    await expect(button).toBeEnabled()
  })

  test('a refresh the engine refuses says it did not start, with the reason and no JSON', async ({ page }) => {
    const writes = await recordWrites(page, /\/mental-models\/[^/]+\/refresh$/, () => ({
      status: 503,
      body: {
        error: {
          code: 'memory.engine_unreachable',
          message: 'hindsight-api is not responding. It was last reachable 4 min ago.',
          details: {},
        },
      },
    }))
    await page.goto(`${URL_PRIMARY}&page=kb-power-resilience`)
    await page.getByTestId('ma-kn-refresh').click()
    const error = page.getByTestId('ma-kn-run-error')
    await expect(error).toHaveText('The refresh did not start. hindsight-api is not responding.')
    await expect(error).toHaveAttribute('role', 'alert')
    await expect(page.getByTestId('ma-knowledge')).not.toContainText(/memory\.engine_unreachable|[{}]/)
    expect(writes).toHaveLength(1)
    // It can be tried again.
    await expect(page.getByTestId('ma-kn-refresh')).toBeEnabled()
  })

  test('"Refresh all stale" starts one refresh per stale page or model, and counts them', async ({ page }) => {
    const writes = await recordWrites(page, /\/mental-models\/[^/]+\/refresh$/)
    await page.goto(URL_PRIMARY)
    await expect(page.getByTestId('ma-kn-refresh-sentence')).toContainText(
      '2 of 5 knowledge pages and mental models are stale or failed their last refresh.',
    )
    const all = page.getByTestId('ma-kn-refresh-all')
    await expect(all).toHaveClass('btn ghost')
    await all.click()
    await expect(page.getByTestId('ma-kn-bulk-status')).toHaveText(
      '2 refreshes started. Each finishes on its own, and this tab updates as they do.',
    )
    expect(writes.map((w) => w.path)).toEqual([
      '/api/memory/banks/primary/mental-models/mm-operator-style/refresh',
      '/api/memory/banks/primary/mental-models/mm-operator-preferences/refresh',
    ])
  })

  test('"Refresh all stale" stops at the first that does not start, and names it', async ({ page }) => {
    const writes = await recordWrites(page, /\/mental-models\/[^/]+\/refresh$/, () => ({
      status: 500,
      body: { detail: 'The refresh queue is full. Try again in a minute.' },
    }))
    await page.goto(URL_PRIMARY)
    await page.getByTestId('ma-kn-refresh-all').click()
    await expect(page.getByTestId('ma-kn-bulk-stopped')).toHaveText(
      'Stopped after 0 of 2. “Operator interaction style” did not start: The refresh queue is full.',
    )
    expect(writes).toHaveLength(1)
  })

  test('the minimum refresh interval is shown in words and saved when it is changed', async ({ page }) => {
    const writes = await recordWrites(page, /\/api\/memory\/banks\/[^/]+\/config$/)
    await page.goto(URL_PRIMARY)
    const box = page.getByTestId('ma-kn-interval')
    await expect(box).toContainText('Refresh at most every')
    const select = page.getByTestId('ma-kn-interval-select')
    await expect(select).toContainText('time facts are consolidated')
    await select.click()
    await page.getByRole('option', { name: '4 h', exact: true }).click()
    await expect(page.getByTestId('ma-kn-interval-status')).toHaveText('Saved.')
    expect(writes).toEqual([
      {
        method: 'PATCH',
        path: '/api/memory/banks/primary/config',
        body: { updates: { mental_model_min_refresh_interval_seconds: 14400 } },
      },
    ])
    await expect(page.getByTestId('ma-knowledge')).not.toContainText('mental_model_min_refresh_interval_seconds')
  })
})

test.describe('editing a page', () => {
  test('Edit opens the question and the tags; Save sends what changed to the page’s model', async ({ page }) => {
    const writes = await recordWrites(page, /\/api\/memory\/banks\/[^/]+\/mental-models\/[^/]+$/)
    await page.goto(`${URL_PRIMARY}&page=kb-thermal-envelope`)
    await page.getByTestId('ma-kn-edit').click()
    const form = page.getByTestId('ma-kn-edit-form')
    await expect(form.getByLabel('Question')).toHaveValue('what are the iGPU thermal limits?')
    await expect(form.getByLabel('Tags, separated by commas')).toHaveValue('performance')
    await form.getByLabel('Question').fill('what are the iGPU thermal limits, and what holds them?')
    await form.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByTestId('ma-kn-edit-saved')).toContainText('Saved.')
    await expect(form).toHaveCount(0)
    expect(writes).toEqual([
      {
        method: 'PATCH',
        path: '/api/memory/banks/primary/mental-models/mm-thermal-envelope',
        body: { source_query: 'what are the iGPU thermal limits, and what holds them?' },
      },
    ])
  })

  test('Cancel closes the form and sends nothing', async ({ page }) => {
    const writes = await recordWrites(page, /\/api\/memory\/banks\/[^/]+\/mental-models\/[^/]+$/)
    await page.goto(`${URL_PRIMARY}&page=kb-thermal-envelope`)
    await page.getByTestId('ma-kn-edit').click()
    await page.getByTestId('ma-kn-edit-form').getByLabel('Question').fill('something else?')
    await page.getByTestId('ma-kn-edit-cancel').click()
    await expect(page.getByTestId('ma-kn-edit-form')).toHaveCount(0)
    await expect(page.getByTestId('ma-kn-question')).toHaveText('what are the iGPU thermal limits?')
    expect(writes).toEqual([])
  })
})

test.describe('directives', () => {
  type Directive = { id: string; name: string; content: string; priority: number; is_active: boolean; tags: string[] }

  /** Serve the bank's directives from the test, so adding and deleting one shows in the list. */
  async function routeDirectives(page: Page) {
    const store: Directive[] = [
      { id: 'dir-cite', name: 'Always cite sources', content: 'Include citations in answers.', priority: 1, is_active: true, tags: [] },
      { id: 'dir-terse', name: 'Keep it terse', content: 'Default to terse responses.', priority: 2, is_active: true, tags: [] },
    ]
    const writes: { method: string; path: string; body: unknown }[] = []
    await page.addInitScript(() => {
      ;(window as unknown as { __hal0MockPassthrough?: unknown }).__hal0MockPassthrough = [
        /^\/api\/memory\/banks\/[^/]+\/directives$/,
      ]
    })
    await page.route(/\/api\/memory\/banks\/[^/]+\/directives(\/[^/]+)?$/, (route) => {
      const request = route.request()
      const method = request.method()
      const path = decodeURIComponent(new URL(request.url()).pathname)
      const id = path.split('/directives/')[1]
      if (method === 'GET') return json(route, { items: store, total: store.length, limit: 100, offset: 0 })
      const body = request.postDataJSON()
      writes.push({ method, path, body })
      if (method === 'POST') {
        const created = { id: 'dir-new', is_active: true, tags: [], ...body }
        store.push(created)
        return json(route, created)
      }
      const at = store.findIndex((d) => d.id === id)
      if (method === 'DELETE') {
        store.splice(at, 1)
        return json(route, { success: true })
      }
      store[at] = { ...store[at], ...body }
      return json(route, store[at])
    })
    return { store, writes }
  }

  const row = (page: Page, id: string) => page.getByTestId(`ma-kn-directive-${id}`)

  test('the list shows name, text, priority and whether each is active, with no "managed" wording', async ({
    page,
  }) => {
    await page.goto(URL_PRIMARY)
    const first = page.getByTestId('ma-kn-directives-list').locator('> li').first()
    // Highest priority first.
    await expect(first.getByTestId('ma-kn-directive-name')).toHaveText('Heavy work in the morning')
    await expect(first.getByTestId('ma-kn-directive-priority')).toHaveText('priority 3')
    await expect(first.getByTestId('ma-kn-directive-text')).toContainText('Schedule consolidation')
    await expect(first.getByRole('checkbox', { name: 'Active: Heavy work in the morning' })).toBeChecked()
    await expect(row(page, 'dir-disk-guard').getByRole('checkbox')).not.toBeChecked()
    await expect(page.getByTestId('ma-kn-directives-meta')).toHaveText('primary · 4 directives, 3 active')
    await expect(page.getByTestId('ma-kn-directives')).not.toContainText(/managed/i)
  })

  test('adding a directive: three fields, and it appears in the list', async ({ page }) => {
    const { writes } = await routeDirectives(page)
    await page.goto(URL_PRIMARY)
    await expect(page.getByTestId('ma-kn-directives-list').locator('> li')).toHaveCount(2)
    await page.getByTestId('ma-kn-directive-add').click()
    const form = page.getByTestId('ma-kn-directive-add-form')
    await expect(form.locator('label')).toHaveText(['Name', 'Text given to agents', 'Priority'])
    await form.getByLabel('Name').fill('No secrets')
    await form.getByLabel('Text given to agents').fill('Never repeat a password or a key.')
    await form.getByLabel('Priority').fill('5')
    await form.getByRole('button', { name: 'Add the directive' }).click()

    await expect(form).toHaveCount(0)
    await expect(row(page, 'dir-new').getByTestId('ma-kn-directive-name')).toHaveText('No secrets')
    await expect(page.getByTestId('ma-kn-directives-list').locator('> li')).toHaveCount(3)
    // Priority 5 puts it first.
    await expect(page.getByTestId('ma-kn-directives-list').locator('> li').first()).toContainText('No secrets')
    await expect(page.locator('[data-testid="ma-kn-summary"] [data-stat="directives"] .ma-stat-value')).toContainText('3')
    expect(writes).toEqual([
      {
        method: 'POST',
        path: '/api/memory/banks/primary/directives',
        body: { name: 'No secrets', content: 'Never repeat a password or a key.', priority: 5 },
      },
    ])
  })

  test('editing a directive in place, and switching one off', async ({ page }) => {
    const { writes } = await routeDirectives(page)
    await page.goto(URL_PRIMARY)
    await row(page, 'dir-terse').getByTestId('ma-kn-directive-edit').click()
    const form = row(page, 'dir-terse').getByTestId('ma-kn-directive-edit-form')
    await expect(form.getByLabel('Name')).toHaveValue('Keep it terse')
    await form.getByLabel('Text given to agents').fill('Be brief.')
    await form.getByRole('button', { name: 'Save' }).click()
    await expect(row(page, 'dir-terse').getByTestId('ma-kn-directive-text')).toHaveText('Be brief.')

    await row(page, 'dir-cite').getByRole('checkbox', { name: 'Active: Always cite sources' }).click()
    await expect(row(page, 'dir-cite').getByRole('checkbox')).not.toBeChecked()
    await expect(page.getByTestId('ma-kn-directives-meta')).toHaveText('primary · 2 directives, 1 active')
    expect(writes).toEqual([
      {
        method: 'PATCH',
        path: '/api/memory/banks/primary/directives/dir-terse',
        body: { name: 'Keep it terse', content: 'Be brief.', priority: 2 },
      },
      { method: 'PATCH', path: '/api/memory/banks/primary/directives/dir-cite', body: { is_active: false } },
    ])
  })

  test('deleting a directive asks first: the button names it, "Keep it" keeps it', async ({ page }) => {
    const { writes } = await routeDirectives(page)
    await page.goto(URL_PRIMARY)
    await row(page, 'dir-terse').getByTestId('ma-kn-directive-delete').click()
    const confirm = page.getByTestId('ma-kn-directive-confirm')
    await expect(confirm.getByRole('heading')).toHaveText('Delete the directive “Keep it terse”?')
    await expect(confirm).toContainText('This cannot be undone.')
    // Focus starts on the safe choice.
    await expect(confirm.getByRole('button', { name: 'Keep it', exact: true })).toBeFocused()
    await confirm.getByRole('button', { name: 'Keep it', exact: true }).click()
    await expect(confirm).toHaveCount(0)
    await expect(row(page, 'dir-terse')).toBeVisible()
    expect(writes).toEqual([])

    await row(page, 'dir-terse').getByTestId('ma-kn-directive-delete').click()
    await confirm.getByRole('button', { name: 'Delete the directive “Keep it terse”' }).click()
    await expect(row(page, 'dir-terse')).toHaveCount(0)
    await expect(page.getByTestId('ma-kn-directives-list').locator('> li')).toHaveCount(1)
    expect(writes).toEqual([
      { method: 'DELETE', path: '/api/memory/banks/primary/directives/dir-terse', body: null },
    ])
  })
})

test.describe('states', () => {
  test('an empty bank says what belongs here and how pages come to exist', async ({ page }) => {
    await page.goto('/#memory/knowledge?bank=empty')
    const empty = page.getByTestId('ma-kn-empty')
    await expect(empty).toContainText('No knowledge pages or mental models in empty yet')
    await expect(empty).toContainText('hal0 memory spec apply')
    await expect(empty.getByRole('button')).toHaveText(['Add a mental model'])
    await expect(page.getByTestId('ma-kn-tree')).toHaveCount(0)
    await expect(page.getByTestId('ma-kn-reader')).toHaveCount(0)
    await expect(page.getByTestId('ma-kn-directives-empty')).toContainText('No directives for this bank')
    // Nothing to refresh, and the button says so.
    const all = page.getByTestId('ma-kn-refresh-all')
    await expect(all).toBeDisabled()
    await expect(page.getByTestId('ma-kn-refresh-card').locator('.ma-reason').first()).toHaveText(
      'There is nothing to refresh in this bank.',
    )
  })

  test('adding a mental model from the empty bank sends its name and question', async ({ page }) => {
    const writes = await recordWrites(page, /\/api\/memory\/banks\/[^/]+\/mental-models$/, () => ({
      body: { mental_model_id: 'mm-new', operation_id: 'op-2' },
    }))
    await page.goto('/#memory/knowledge?bank=empty')
    await page.getByTestId('ma-kn-model-add').click()
    const form = page.getByTestId('ma-kn-model-form')
    await form.getByLabel('Name').fill('Decisions')
    await form.getByLabel('Question').fill('what has been decided?')
    await form.getByRole('button', { name: 'Add the model' }).click()
    await expect(page.getByTestId('ma-kn-model-added')).toContainText('Added.')
    expect(writes).toEqual([
      {
        method: 'POST',
        path: '/api/memory/banks/empty/mental-models',
        body: { name: 'Decisions', source_query: 'what has been decided?' },
      },
    ])
  })

  test('with the engine not answering, the tab says so and points at Health', async ({ page }) => {
    await routeEngineDown(page)
    await page.goto('/#memory/knowledge')
    const card = page.getByTestId('ma-knowledge-unreachable')
    await expect(card).toContainText('The memory engine is not answering')
    await card.getByRole('link', { name: 'Open Health' }).click()
    await expect(page).toHaveURL(/#memory\/health$/)
  })

  test('a page that does not load says so in the reader, and the rest of the tab stands', async ({ page }) => {
    await page.addInitScript(() => {
      ;(window as unknown as { __hal0MockPassthrough?: unknown }).__hal0MockPassthrough = [
        /^\/api\/memory\/banks\/[^/]+\/knowledge-base\/pages\//,
      ]
    })
    await page.route(/\/knowledge-base\/pages\/[^/]+$/, (route) =>
      json(route, { detail: 'The page store timed out. Try again.' }, 500),
    )
    await page.goto(`${URL_PRIMARY}&page=kb-thermal-envelope`)
    const error = page.getByTestId('ma-kn-page-error')
    await expect(error).toContainText('This page did not load', { timeout: 15_000 })
    await expect(error).toContainText('The page store timed out.')
    await expect(error.getByRole('button', { name: 'Check again' })).toBeVisible()
    await expect(title(page)).toHaveText('iGPU thermal envelope')
    await expect(page.getByTestId('ma-kn-tree')).toBeVisible()
    await expect(page.getByTestId('ma-kn-directives-list')).toBeVisible()
  })
})

test.describe('at 390 px', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('the tree is above the reader, nothing overflows sideways, and a page still opens', async ({ page }) => {
    await page.goto(`${URL_PRIMARY}&page=kb-thermal-envelope`)
    await expect(page.getByTestId('ma-kn-content')).toBeVisible()
    const tree = await page.getByTestId('ma-kn-pages-card').boundingBox()
    const models = await page.getByTestId('ma-kn-models-card').boundingBox()
    const read = await reader(page).boundingBox()
    expect(tree && models && read).toBeTruthy()
    // One column: the pages, then the mental models, then the reader, each the full width.
    expect(tree!.y + tree!.height).toBeLessThanOrEqual(models!.y)
    expect(models!.y + models!.height).toBeLessThanOrEqual(read!.y)
    expect(Math.abs(tree!.x - read!.x)).toBeLessThan(2)
    expect(Math.abs(tree!.width - read!.width)).toBeLessThan(2)

    // Nothing is wider than the screen: the page does not scroll sideways, and no card is clipped.
    const overflow = await page.evaluate(() => {
      const root = document.querySelector('[data-testid="ma-knowledge"]') as HTMLElement
      const wide = [...root.querySelectorAll<HTMLElement>('.card, .ma-stats')].filter(
        (el) => el.getBoundingClientRect().right > window.innerWidth + 1,
      )
      return { page: document.documentElement.scrollWidth - window.innerWidth, wide: wide.length }
    })
    expect(overflow.page).toBeLessThanOrEqual(1)
    expect(overflow.wide).toBe(0)

    // A long name wraps inside its row instead of being cut.
    const name = node(page, 'kb-operator-style').locator('.ma-kn-node-name')
    expect(await name.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true)

    await node(page, 'kb-operator-style').click()
    await expect(title(page)).toHaveText('Operator interaction style')
    await expect(title(page)).toBeInViewport()
  })
})
