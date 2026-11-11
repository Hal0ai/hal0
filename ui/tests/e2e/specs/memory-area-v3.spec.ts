/**
 * memory-area-v3 — the one top-level Memory area (#memory/<tab>, ADR-0058 decision 12).
 *
 * Pins the shell: the sidebar has exactly one row called "Memory" and it is its own top-level
 * entry (no longer a sub-link of Agents); the page has five tabs that are real links, exactly one
 * of them the current page; opening a tab is a history entry, so Back and Forward walk the tabs;
 * choosing a bank is not, so Back never walks through banks. Health and Facts have their own
 * specs (memory-health-v3, memory-v2-workspace and friends); the old addresses are in
 * memory-redirects-v3.
 *
 * Runs against the forced mock (six banks; see fixtures/memoryArea.ts).
 */
import type { Page } from '@playwright/test'

import { test, expect } from '../fixtures/apiMock'
import { routeAllClear } from '../fixtures/memoryArea'

const TABS = ['health', 'facts', 'knowledge', 'operations', 'agents'] as const

const tab = (page: Page, id: string) => page.getByTestId(`ma-tab-${id}`)
const currentTabs = (page: Page) => page.locator('[data-testid="ma-tabs"] a[aria-current="page"]')

test.describe('sidebar', () => {
  test('has exactly one row named "Memory", top-level, after Agents', async ({ page }) => {
    await page.goto('/#dashboard')
    const nav = page.locator('.sidebar .sb-list')
    await expect(nav).toBeVisible()

    // Exactly one row is called "Memory": by its visible label and by its accessible name.
    await expect(nav.locator('.sb-row .lbl').filter({ hasText: /^Memory$/ })).toHaveCount(1)
    await expect(nav.getByRole('button', { name: 'Memory', exact: true })).toHaveCount(1)

    const row = nav.getByTestId('nav-memory')
    await expect(row).toBeVisible()
    await expect(row).not.toHaveClass(/sb-sub/)
    // No sub-links: the area's tabs are in the page.
    await expect(nav.getByTestId('nav-memory-toggle')).toHaveCount(0)

    // Top-level order: … Agents, Memory, Memory (legacy tools), Services …
    const top = await nav.locator('.sb-row:not(.sb-sub) .lbl').allTextContents()
    const at = top.indexOf('Memory')
    expect(top[at - 1]).toBe('Agents')
    expect(top[at + 1]).toBe('Memory (legacy tools)')
  })

  test('Memory is no longer a sub-link of Agents', async ({ page }) => {
    await page.goto('/#dashboard')
    const nav = page.locator('.sidebar .sb-list')
    await nav.getByTestId('nav-agent-toggle').click()
    await expect(nav.getByTestId('nav-agent-overview')).toBeVisible()
    await expect(nav.getByTestId('nav-mcp')).toBeVisible()
    await expect(nav.locator('.sb-row.sb-sub .lbl').filter({ hasText: /^Memory$/ })).toHaveCount(0)
  })

  test('the row opens the area on Health and is the active row; Agents is not', async ({ page }) => {
    await page.goto('/#dashboard')
    const nav = page.locator('.sidebar .sb-list')
    await nav.getByTestId('nav-memory').click()
    await expect(page).toHaveURL(/#memory\/health$/)
    await expect(page.getByTestId('memory-area')).toBeVisible()
    await expect(nav.getByTestId('nav-memory')).toHaveAttribute('aria-current', 'page')
    await expect(nav.getByTestId('nav-agent')).not.toHaveAttribute('aria-current', 'page')
    await expect(nav.getByTestId('nav-memworkspace')).not.toHaveAttribute('aria-current', 'page')

    // It stays the active row on every tab.
    for (const id of TABS) {
      await page.goto(`/#memory/${id}`)
      await expect(nav.getByTestId('nav-memory')).toHaveAttribute('aria-current', 'page')
    }
  })

  test('the older tools page stays reachable under its own label, sub-links unchanged', async ({ page }) => {
    await page.goto('/#dashboard')
    const nav = page.locator('.sidebar .sb-list')
    const legacy = nav.getByTestId('nav-memworkspace')
    await expect(legacy.locator('.lbl')).toHaveText('Memory (legacy tools)')
    await nav.getByTestId('nav-memworkspace-toggle').click()
    for (const sub of ['overview', 'kb', 'mental-models', 'directives', 'operations', 'agents', 'models']) {
      await expect(nav.getByTestId(`nav-memworkspace-${sub}`)).toBeVisible()
    }
    await nav.getByTestId('nav-memworkspace-kb').click()
    await expect(page).toHaveURL(/#memworkspace\/kb$/)
    await expect(page.getByTestId('memws-tab-kb')).toHaveAttribute('aria-selected', 'true')
    // The new row is not lit while the legacy page is open.
    await expect(nav.getByTestId('nav-memory')).not.toHaveAttribute('aria-current', 'page')
  })
})

test.describe('page shell', () => {
  test('header: one h1, a summary line with real counts, no bank control', async ({ page }) => {
    await page.goto('/#memory/health')
    const area = page.getByTestId('memory-area')
    await expect(area.locator('.vh h1')).toHaveText('Memory')
    await expect(page.locator('h1')).toHaveCount(1)
    // The forced mock's six banks; the count is summed from their stats.
    await expect(page.getByTestId('ma-meta')).toHaveText(/^[\d,]+ facts in 6 banks$/)
    // The chosen bank lives in the address and the scope bar, never in the header.
    await expect(area.locator('.vh select, .vh [role="combobox"], .vh [role="radiogroup"]')).toHaveCount(0)
    await expect(area.locator('select')).toHaveCount(0)
  })

  test('the tabs are real links, and exactly one is the current page', async ({ page }) => {
    await page.goto('/#memory/health')
    const row = page.getByTestId('ma-tabs')
    await expect(row).toHaveAttribute('aria-label', 'Memory sections')
    await expect(row.locator('a.ma-tab')).toHaveText(['Health', 'Facts', 'Knowledge', 'Operations', 'Agents'])
    for (const id of TABS) {
      await expect(tab(page, id)).toHaveAttribute('href', `#memory/${id}`)
    }
    for (const id of TABS) {
      await tab(page, id).click()
      await expect(page).toHaveURL(new RegExp(`#memory/${id}(\\?|$)`))
      await expect(currentTabs(page)).toHaveCount(1)
      await expect(tab(page, id)).toHaveAttribute('aria-current', 'page')
    }
  })

  test('each tab shows its own view', async ({ page }) => {
    await page.goto('/#memory/health')
    await expect(page.getByTestId('ma-health-summary')).toBeVisible()
    await tab(page, 'facts').click()
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
    await expect(page.getByTestId('ma-health-summary')).toHaveCount(0)
    await tab(page, 'knowledge').click()
    await expect(page.getByTestId('ma-knowledge-stub')).toBeVisible()
    await expect(page.getByTestId('mv-workspace')).toHaveCount(0)
    await tab(page, 'operations').click()
    await expect(page.getByTestId('ma-operations-stub')).toBeVisible()
    await tab(page, 'agents').click()
    await expect(page.getByTestId('ma-agents-stub')).toBeVisible()
  })

  test('Back and Forward walk the tabs', async ({ page }) => {
    await page.goto('/#memory/health')
    await tab(page, 'knowledge').click()
    await tab(page, 'operations').click()
    await expect(tab(page, 'operations')).toHaveAttribute('aria-current', 'page')

    await page.goBack()
    await expect(page).toHaveURL(/#memory\/knowledge$/)
    await expect(tab(page, 'knowledge')).toHaveAttribute('aria-current', 'page')
    await expect(page.getByTestId('ma-knowledge-stub')).toBeVisible()
    await expect(currentTabs(page)).toHaveCount(1)

    await page.goBack()
    await expect(page).toHaveURL(/#memory\/health$/)
    await expect(tab(page, 'health')).toHaveAttribute('aria-current', 'page')
    await expect(page.getByTestId('ma-health-summary')).toBeVisible()

    await page.goForward()
    await expect(page).toHaveURL(/#memory\/knowledge$/)
    await expect(tab(page, 'knowledge')).toHaveAttribute('aria-current', 'page')
    await page.goForward()
    await expect(page).toHaveURL(/#memory\/operations$/)
    await expect(page.getByTestId('ma-operations-stub')).toBeVisible()
    await expect(currentTabs(page)).toHaveCount(1)
  })

  test('choosing a bank is not a history entry: Back leaves Facts, it does not walk the banks', async ({
    page,
  }) => {
    await page.goto('/#memory/health')
    await tab(page, 'facts').click()
    // No bank in the address: the first bank is written into it in place.
    await expect(page).toHaveURL(/#memory\/facts\?bank=primary$/)
    const banks = page.getByTestId('ma-scope-bank')
    await banks.getByRole('radio', { name: 'scratch' }).click()
    await expect(page).toHaveURL(/#memory\/facts\?bank=scratch$/)
    await banks.getByRole('radio', { name: 'hermes' }).click()
    await expect(page).toHaveURL(/#memory\/facts\?bank=hermes$/)

    await page.goBack()
    await expect(page).toHaveURL(/#memory\/health$/)
    await expect(page.getByTestId('ma-health-summary')).toBeVisible()
    // Forward returns to the bank that was chosen last.
    await page.goForward()
    await expect(page).toHaveURL(/#memory\/facts\?bank=hermes$/)
    await expect(banks.getByRole('radio', { name: 'hermes' })).toHaveAttribute('aria-checked', 'true')
  })

  test('the chosen bank travels with the tab links', async ({ page }) => {
    await page.goto('/#memory/facts?bank=scratch')
    await expect(tab(page, 'health')).toHaveAttribute('href', '#memory/health?bank=scratch')
    await expect(tab(page, 'operations')).toHaveAttribute('href', '#memory/operations?bank=scratch')
    await tab(page, 'knowledge').click()
    await tab(page, 'facts').click()
    await expect(page).toHaveURL(/#memory\/facts\?bank=scratch$/)
    await expect(page.getByTestId('ma-scope-bank').getByRole('radio', { name: 'scratch' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
  })

  test('a quiet "Engine settings" link goes to Settings ▸ Memory', async ({ page }) => {
    await page.goto('/#memory/facts')
    const link = page.getByTestId('ma-engine-settings')
    await expect(link).toHaveText('Engine settings')
    await expect(link).toHaveAttribute('href', '#settings/memory')
    await expect(link).not.toHaveAttribute('aria-current', 'page')
    await link.click()
    await expect(page).toHaveURL(/#settings\/memory$/)
  })

  test('an unknown tab opens Health', async ({ page }) => {
    await page.goto('/#memory/nope')
    await expect(page.getByTestId('ma-health-summary')).toBeVisible()
    await expect(currentTabs(page)).toHaveCount(1)
    await expect(tab(page, 'health')).toHaveAttribute('aria-current', 'page')
  })
})

test.describe('tabs that are not built yet', () => {
  for (const [id, heading, section] of [
    ['knowledge', 'Knowledge', 'kb'],
    ['operations', 'Operations', 'operations'],
    ['agents', 'Agents', 'agents'],
  ] as const) {
    test(`${heading} says it is being moved here and links to the legacy tools page`, async ({ page }) => {
      await page.goto(`/#memory/${id}`)
      const stub = page.getByTestId(`ma-${id}-stub`)
      await expect(stub.getByRole('heading', { level: 2 })).toHaveText(heading)
      await expect(stub).toContainText(/being moved (here|into this tab)/)
      const link = page.getByTestId(`ma-${id}-legacy`)
      await expect(link).toHaveAttribute('href', `#memworkspace/${section}`)
      await link.click()
      await expect(page).toHaveURL(new RegExp(`#memworkspace/${section}$`))
      await expect(page.getByTestId(`memws-tab-${section}`)).toHaveAttribute('aria-selected', 'true')
      // The section itself, with its data: the tab row renders before the section's requests
      // answer, so on its own it does not show that the page is still standing afterwards.
      await expect(page.getByTestId(`memws-${section}`)).toBeVisible()
      if (section === 'agents') {
        await expect(page.getByTestId('memws-install-command')).toContainText('install claude-code')
      }
      await expect(page.getByRole('heading', { name: 'This view hit an error' })).toHaveCount(0)
      await expect(page.getByTestId(`memws-tab-${section}`)).toHaveAttribute('aria-selected', 'true')
    })
  }
})

test.describe('ways in', () => {
  test('the command palette has a Memory entry that opens the area', async ({ page }) => {
    await page.goto('/#dashboard')
    await expect(page.locator('.sidebar .sb-list')).toBeVisible()
    await page.keyboard.press('Control+k')
    const palette = page.locator('.cp-shell')
    await expect(palette).toBeVisible()
    await page.keyboard.type('Memory')
    // The route entry, told apart from the Settings ▸ Memory entry by its own description.
    const entry = palette.locator('.cp-item').filter({ hasText: 'health, facts, knowledge' })
    await expect(entry).toHaveCount(1)
    await entry.click()
    await expect(page).toHaveURL(/#memory\/health$/)
    await expect(page.getByTestId('memory-area')).toBeVisible()
  })

  test("the Overview card opens the shown bank's facts", async ({ page }) => {
    await page.goto('/#dashboard')
    const link = page.getByRole('button', { name: /Open memory/ })
    await expect(link).toBeVisible()
    await link.click()
    await expect(page).toHaveURL(/#memory\/facts\?bank=primary$/)
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
  })

  test('the Overview card follows the bank last looked at in Facts', async ({ page }) => {
    await page.goto('/#memory/facts?bank=scratch')
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
    await page.goto('/#dashboard')
    await page.getByRole('button', { name: /Open memory/ }).click()
    await expect(page).toHaveURL(/#memory\/facts\?bank=scratch$/)
  })
})

test.describe('narrow screen', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('the tab row scrolls sideways instead of wrapping, and the page does not', async ({ page }) => {
    await routeAllClear(page)
    await page.goto('/#memory/health')
    await expect(page.getByTestId('ma-health-banks')).toBeVisible()
    const row = page.getByTestId('ma-tabs')
    // One row: every tab sits on the same line.
    const tops = await row.locator('a.ma-tab').evaluateAll((els) => els.map((el) => el.getBoundingClientRect().top))
    expect(new Set(tops.map((t) => Math.round(t))).size).toBe(1)
    expect(await row.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true)
    // Nothing pushes the page itself sideways: the wide table scrolls inside its own region.
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    const region = page.getByRole('region', { name: 'Banks table' })
    expect(await region.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true)
    // The last tab can be reached.
    await tab(page, 'agents').scrollIntoViewIfNeeded()
    await tab(page, 'agents').click()
    await expect(page.getByTestId('ma-agents-stub')).toBeVisible()
  })

  test('the drawer has exactly one row named "Memory", top-level', async ({ page }) => {
    await page.goto('/#dashboard')
    await page.locator('.tb-menu').click()
    const drawer = page.locator('.nav-drawer')
    await expect(drawer).toHaveClass(/\bopen\b/)
    await expect(drawer.locator('.sb-row .lbl').filter({ hasText: /^Memory$/ })).toHaveCount(1)
    await drawer.getByTestId('nav-drawer-memory').click()
    await expect(page).toHaveURL(/#memory\/health$/)
    await expect(drawer).not.toHaveClass(/\bopen\b/)
    await expect(page.getByTestId('memory-area')).toBeVisible()
  })
})
