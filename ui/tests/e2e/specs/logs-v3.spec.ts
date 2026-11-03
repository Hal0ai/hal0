/**
 * logs-v3 — `#logs` route renders the filter bar (channel / level / slot
 * / search), the follow-tail indicator, and the Pause/Resume control.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Logs v3 (/logs)', () => {
  test('renders Logs view + channel selector', async ({ page }) => {
    await page.goto('/#logs')
    await expect(page.locator('.view .vh h1')).toHaveText('Logs')
    // channel selector (events | slot | merged) — replaces the old dead
    // merged/hal0 toggle where both positions showed the same data.
    await expect(page.locator('.view button', { hasText: 'events' })).toBeVisible()
    await expect(page.locator('.view button', { hasText: 'slot' })).toBeVisible()
    await expect(page.locator('.view button', { hasText: 'merged' })).toBeVisible()
  })

  test('slot channel prompts for a slot selection', async ({ page }) => {
    await page.goto('/#logs')
    await page.locator('.view button', { hasText: 'slot' }).click()
    // With no slot picked the page guides the user instead of showing nothing.
    await expect(page.locator('.view')).toContainText('Select a slot')
  })

  test('search input + slot select + pause button render', async ({ page }) => {
    await page.goto('/#logs')
    await expect(page.locator('.view input[placeholder="search…"]')).toBeVisible()
    await expect(page.locator('.view select').first()).toBeVisible()
    await expect(page.locator('.view button', { hasText: 'Pause' })).toBeVisible()
  })

  test('eyebrow shows Runtime + lines hint visible', async ({ page }) => {
    await page.goto('/#logs')
    await expect(page.locator('.view .vh .vh-eye')).toHaveText('Runtime')
    await expect(page.locator('.view .vh .hint')).toContainText('lines')
  })
  // hal0#2931: the agent card's Unit logs control opens #logs?unit=<unit>.
  test('#logs?unit=<unit> opens the unit channel with that unit\'s journal', async ({ page }) => {
    let asked: string | null = null
    await page.route('**/api/logs?*', (route) => {
      asked = new URL(route.request().url()).searchParams.get('unit')
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          unit: 'hal0-agent@hermes.service',
          lines: ['hermes: venv missing at the live release', 'hermes: exited with status 1'],
          count: 2,
        }),
      })
    })
    await page.goto('/#logs?unit=hal0-agent%40hermes.service')
    await expect(page.locator('.view')).toContainText('exited with status 1')
    await expect(page.locator('.view .log-source').first()).toHaveText('hal0-agent@hermes.service')
    expect(asked).toBe('hal0-agent@hermes.service')
    await expect(page.locator('.view button', { hasText: /^unit$/ })).toBeVisible()
  })

  test('plain #logs offers no unit channel', async ({ page }) => {
    await page.goto('/#logs')
    await expect(page.locator('.view button', { hasText: 'events' })).toBeVisible()
    await expect(page.locator('.view button', { hasText: /^unit$/ })).toHaveCount(0)
  })
})
