/**
 * model-edit-identity-v3 — display name + curated type toggles in the
 * model "Edit options" pane (RecipeEditorModal).
 *
 * The editor gains:
 *   1. A display-name text input, prefilled from the model's `name`
 *      (placeholder = model id), written to PUT /api/models/{id} as `name`
 *      only when changed.
 *   2. A curated row of type toggles (mtp, moe, tool-calling, reasoning,
 *      coder, vision) prefilled from the model's `tags`, written back as a
 *      `tags` union that preserves non-curated provenance tags. The union
 *      logic itself is pinned by ../../src/dash/__tests__/model-types.test.mjs;
 *      here we assert the UI wiring + the PUT contract end-to-end.
 *
 * The recipe editor auto-targets the first installed model exposed by the
 * dashboard mock — `qwen3.6-27b-mtp` — mirroring model-recipe-template-v3.
 */
import { test, expect } from '../fixtures/apiMock'

const CURATED = ['mtp', 'moe', 'tool-calling', 'reasoning', 'coder', 'vision']

function mockChatTemplates(page: import('@playwright/test').Page) {
  return page.route('**/api/chat-templates', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ id: 'auto', label: 'Auto (GGUF embedded)' }]),
    }),
  )
}

test.describe('Model edit — display name + type toggles', () => {
  test('name input and all curated type toggles render', async ({ page }) => {
    await mockChatTemplates(page)
    await page.goto('/#models')
    await page.locator('button:has-text("Edit options")').click()

    const nameInput = page.getByTestId('model-name-input')
    await expect(nameInput).toBeVisible()
    // Placeholder falls back to the model id so the field is self-describing
    // even when no display name is set.
    await expect(nameInput).toHaveAttribute('placeholder', 'qwen3.6-27b-mtp')

    for (const tag of CURATED) {
      await expect(page.getByTestId(`type-toggle-${tag}`)).toBeVisible()
    }
  })

  test('toggling a type flips its aria-checked state', async ({ page }) => {
    await mockChatTemplates(page)
    await page.goto('/#models')
    await page.locator('button:has-text("Edit options")').click()

    const mtp = page.getByTestId('type-toggle-mtp')
    await expect(mtp).toHaveAttribute('aria-checked', 'false')
    await mtp.click()
    await expect(mtp).toHaveAttribute('aria-checked', 'true')
  })

  test('editing the name and toggling a type writes name + tags on Save', async ({ page }) => {
    let putBody: any = null
    await page.route('**/api/models/qwen3.6-27b-mtp', async (route) => {
      if (route.request().method() === 'PUT') {
        putBody = JSON.parse(route.request().postData() || '{}')
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'qwen3.6-27b-mtp',
            name: putBody.name,
            tags: putBody.tags,
          }),
        })
      }
      return route.fallback()
    })
    await mockChatTemplates(page)

    await page.goto('/#models')
    await page.locator('button:has-text("Edit options")').click()

    await page.getByTestId('model-name-input').fill('My Renamed Qwen')
    await page.getByTestId('type-toggle-mtp').click()
    await page.locator('button:has-text("Save options")').click()

    await expect.poll(() => putBody?.name).toBe('My Renamed Qwen')
    await expect.poll(() => putBody?.tags).toContain('mtp')
  })
})
