/**
 * memory-redirects-v3 — where the Memory addresses from before the Memory area go now.
 *
 *   #memory                  → #memory/health
 *   #memory/bank?…           → #memory/facts?…   (query kept: the bank and the open fact)
 *   #agent/memory[/…][?…]    → #memory/facts?…   (the old Agents ▸ Memory tab)
 *
 * and the sections of the deleted memory tools page:
 *
 *   #memworkspace[/overview]                       → #memory/health
 *   #memworkspace/kb, /mental-models, /directives  → #memory/knowledge
 *   #memworkspace/operations                       → #memory/operations
 *   #memworkspace/agents                           → #memory/agents
 *   #memworkspace/models                           → #settings/memory
 *
 * A redirect rewrites the address in place, so Back does not return to an address that redirects
 * again. With memory off, every `#memory/…` address goes to the dashboard once the status check
 * has answered. The redirects go with the next minor release (HAL0-SUNSET: v1.9.0).
 *
 * (The deep-link, sub-tab and alias cases here were memory-v2-routing's; that spec tested the old
 * page and is gone.)
 */
import { test, expect } from '../fixtures/apiMock'
import { memoryOff } from '../fixtures/memoryArea'

test.describe('old Memory addresses', () => {
  test('#memory opens Health', async ({ page }) => {
    await page.goto('/#memory')
    await expect(page).toHaveURL(/#memory\/health$/)
    await expect(page.getByTestId('ma-tab-health')).toHaveAttribute('aria-current', 'page')
    await expect(page.getByTestId('ma-health-summary')).toBeVisible()
  })

  test('#memory/bank opens Facts, on the first bank', async ({ page }) => {
    await page.goto('/#memory/bank')
    await expect(page).toHaveURL(/#memory\/facts\?bank=primary$/)
    await expect(page.getByTestId('ma-tab-facts')).toHaveAttribute('aria-current', 'page')
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
    await expect(page.getByTestId('mv-bankbar')).toBeVisible()
  })

  test('#memory/bank?bank&fact keeps its query: the bank is chosen and the fact is open', async ({ page }) => {
    // f26 is the newest fact in the mock's set, so it is on the first page of the list and the
    // inspector can resolve it.
    await page.goto('/#memory/bank?bank=big&fact=f26')
    await expect(page).toHaveURL(/#memory\/facts\?bank=big&fact=f26$/)
    await expect(page.getByTestId('ma-scope-bank').getByRole('radio', { name: 'big' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    await expect(page.getByTestId('mv-inspector')).toBeVisible()
    await expect(page.getByTestId('mv-fact-row-f26')).toHaveClass(/on/)
  })

  for (const [from, to] of [
    ['#agent/memory', /#memory\/facts\?bank=primary$/],
    ['#agent/memory/bank', /#memory\/facts\?bank=primary$/],
    ['#agent/memory/bank?bank=scratch', /#memory\/facts\?bank=scratch$/],
    ['#agent/memory?bank=hermes&fact=f1', /#memory\/facts\?bank=hermes&fact=f1$/],
  ] as const) {
    test(`${from} leaves Agents for Memory ▸ Facts`, async ({ page }) => {
      await page.goto(`/${from}`)
      await expect(page).toHaveURL(to)
      await expect(page.getByTestId('memory-area')).toBeVisible()
      await expect(page.getByTestId('mv-workspace')).toBeVisible()
      // It is the Memory area, not the Agents shell.
      await expect(page.getByTestId('agent-tab-nav')).toHaveCount(0)
      await expect(page.locator('.sidebar').getByTestId('nav-memory')).toHaveAttribute('aria-current', 'page')
    })
  }

  test('a redirect followed from inside the app lands the same way', async ({ page }) => {
    await page.goto('/#dashboard')
    await expect(page.locator('.sidebar .sb-list')).toBeVisible()
    await page.evaluate(() => {
      window.location.hash = '#memory/bank?bank=scratch'
    })
    await expect(page).toHaveURL(/#memory\/facts\?bank=scratch$/)
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
  })

  test('a redirect replaces the old address: Back goes to where the operator was', async ({ page }) => {
    await page.goto('/#dashboard')
    await expect(page.locator('.sidebar .sb-list')).toBeVisible()
    await page.evaluate(() => {
      window.location.hash = '#memory'
    })
    await expect(page).toHaveURL(/#memory\/health$/)
    await page.goBack()
    await expect(page).toHaveURL(/#dashboard$/)
    await expect(page.locator('.ov-now')).toBeVisible()
  })

  test('a bank id with colons and a dot survives the redirect', async ({ page }) => {
    await page.goto('/#memory/bank?bank=coding-agent::ultrareview-1.4')
    await expect(page).toHaveURL(/#memory\/facts\?bank=coding-agent::ultrareview-1\.4$/)
    // The mock has no such bank, and the page says so instead of opening another one.
    await expect(page.getByTestId('ma-facts-unknown-bank')).toContainText('coding-agent::ultrareview-1.4')
  })
})

test.describe('the deleted memory tools page (#memworkspace)', () => {
  for (const [from, to, tab, testId] of [
    ['#memworkspace', /#memory\/health$/, 'health', 'ma-health-summary'],
    ['#memworkspace/overview', /#memory\/health$/, 'health', 'ma-health-summary'],
    ['#memworkspace/kb', /#memory\/knowledge(\?bank=[^&]+)?$/, 'knowledge', 'ma-knowledge'],
    ['#memworkspace/mental-models', /#memory\/knowledge(\?bank=[^&]+)?$/, 'knowledge', 'ma-knowledge'],
    ['#memworkspace/directives', /#memory\/knowledge(\?bank=[^&]+)?$/, 'knowledge', 'ma-knowledge'],
    ['#memworkspace/operations', /#memory\/operations$/, 'operations', 'ma-operations'],
    ['#memworkspace/agents', /#memory\/agents$/, 'agents', 'ma-agents'],
  ] as const) {
    test(`${from} opens Memory ▸ ${tab}`, async ({ page }) => {
      await page.goto(`/${from}`)
      await expect(page).toHaveURL(to)
      await expect(page.getByTestId(`ma-tab-${tab}`)).toHaveAttribute('aria-current', 'page')
      await expect(page.getByTestId(testId)).toBeVisible()
      await expect(page.locator('.sidebar').getByTestId('nav-memory')).toHaveAttribute('aria-current', 'page')
    })
  }

  test('#memworkspace/models opens Settings ▸ Memory', async ({ page }) => {
    await page.goto('/#memworkspace/models')
    await expect(page).toHaveURL(/#settings\/memory$/)
    await expect(page.getByRole('heading', { name: 'Memory', exact: true, level: 2 })).toBeVisible()
    await expect(page.getByTestId('memory-area')).toHaveCount(0)
  })

  test('the old address is replaced: Back goes to where the operator was', async ({ page }) => {
    await page.goto('/#dashboard')
    await expect(page.locator('.sidebar .sb-list')).toBeVisible()
    await page.evaluate(() => {
      window.location.hash = '#memworkspace/kb'
    })
    // Knowledge then writes the first bank into the address, in place as well.
    await expect(page).toHaveURL(/#memory\/knowledge(\?bank=[^&]+)?$/)
    await page.goBack()
    await expect(page).toHaveURL(/#dashboard$/)
  })
})

test.describe('addresses that are not redirected', () => {
  test('#agent and #agent/mcp stay on the Agents page', async ({ page }) => {
    await page.goto('/#agent')
    await expect(page.getByTestId('agent-tab-nav')).toBeVisible()
    await expect(page).toHaveURL(/#agent$/)
    await page.goto('/#agent/mcp')
    await expect(page.getByTestId('agent-tab-mcp')).toBeVisible()
    await expect(page).toHaveURL(/#agent\/mcp$/)
  })

  test('#settings/memory is the settings page, not the area', async ({ page }) => {
    await page.goto('/#settings/memory')
    await expect(page).toHaveURL(/#settings\/memory$/)
    await expect(page.getByTestId('memory-area')).toHaveCount(0)
  })
})

test.describe('memory off ([memory].enabled = false)', () => {
  test.beforeEach(async ({ page }) => {
    await memoryOff(page)
  })

  for (const hash of ['#memory', '#memory/health', '#memory/facts?bank=primary', '#memory/bank', '#agent/memory', '#memworkspace/kb']) {
    test(`${hash} goes to the dashboard`, async ({ page }) => {
      await page.goto(`/${hash}`)
      await expect(page).toHaveURL(/#dashboard$/)
      await expect(page.locator('.ov-now')).toBeVisible()
      await expect(page.getByTestId('memory-area')).toHaveCount(0)
    })
  }

  test('the area is never mounted, and never asks the memory routes', async ({ page }) => {
    const asked: string[] = []
    page.on('request', (request) => {
      const path = new URL(request.url()).pathname
      if (path.startsWith('/api/memory/')) asked.push(path)
    })
    // Health is the tab that reads routes the mock layer does not answer in the bundle (the
    // models and the graph status), so a mounted area would show up here as requests.
    await page.goto('/#memory/health')
    await expect(page).toHaveURL(/#dashboard$/)
    await expect(page.locator('.ov-now')).toBeVisible()
    expect(asked).toEqual([])
  })
})
