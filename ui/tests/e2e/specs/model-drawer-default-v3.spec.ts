/**
 * model-drawer-default-v3 — per-type default MODEL marker (Set / Remove).
 *
 * The model drawer surfaces whether the model is its dispatcher type's
 * default via a single toggle chip that POSTs /api/models/{id}/default. The
 * server enforces the single-holder invariant; the UI just fires the
 * mutation and lets the models-query invalidation flip the chip. Asserts the
 * chip's text reflects the (stateful-mocked) flag both ways round.
 *
 * model-drawer-2 Task 3 collapsed the old badge (`model-default-badge` /
 * `model-default-none`) + ghost chip + separate button trio into ONE
 * `model-default-toggle` chip whose own text carries the state ("llm
 * default" / "✓ llm default") — the testid is preserved, only the badge
 * testids and the "Set as default" / "Remove default" text are gone.
 */
import { test, expect } from '../fixtures/apiMock'
import { MOCK_DATA } from '../fixtures/mock-data'

function mockProfiles(page: import('@playwright/test').Page) {
  return page.route('**/api/profiles', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
  )
}

function mockChatTemplates(page: import('@playwright/test').Page) {
  return page.route('**/api/chat-templates', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ id: 'auto', label: 'Auto (GGUF embedded)' }]),
    }),
  )
}

test.describe('Model drawer — per-type default', () => {
  test('default chip toggles both ways', async ({ page }) => {
    await mockProfiles(page)
    await mockChatTemplates(page)

    // Stateful catalog: `defaultId` is toggled by the /default POST and
    // reflected on the row's `default` flag so the badge tracks the server.
    let defaultId: string | null = null
    await page.route('**/api/models', (route) => {
      const models = MOCK_DATA.models.map((m: any) => ({ ...m, default: m.id === defaultId }))
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ models, count: models.length }),
      })
    })
    await page.route('**/api/models/*/default', (route) => {
      const m = route.request().url().match(/\/api\/models\/([^/]+)\/default/)
      const id = m ? decodeURIComponent(m[1]) : ''
      const body = route.request().postDataJSON?.() ?? {}
      defaultId = body.default ? id : null
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          model_id: id,
          type: 'llm',
          default: !!body.default,
          demoted: [],
          changed: true,
        }),
      })
    })

    await page.goto('/#models')
    await page.locator('button:has-text("Edit options")').first().click()
    await expect(page.getByTestId('model-tune-raw-toggle')).toBeVisible()

    // Initially not the default.
    const toggle = page.getByTestId('model-default-toggle')
    await expect(toggle).toHaveText('llm default')

    // Promote → chip flips to the checked state.
    await toggle.click()
    await expect(page.getByTestId('model-default-toggle')).toHaveText('✓ llm default')

    // Clear → chip flips back.
    await page.getByTestId('model-default-toggle').click()
    await expect(page.getByTestId('model-default-toggle')).toHaveText('llm default')
  })
})
