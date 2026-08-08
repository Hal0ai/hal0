/**
 * model-edit-identity-v3 — display name editing in the model "Edit options"
 * pane (RecipeEditorModal).
 *
 * The curated "type" toggle row (mtp/moe/tool-calling/reasoning/coder/vision)
 * is RETIRED: behaviour is owned by typed fields (defaults.mtp,
 * defaults.enable_thinking, capability_flags.tool_calling, mmproj-derived
 * vision) and tags are freeform labels the editor no longer touches. These
 * tests pin the retirement (no toggles rendered, PUT never carries `tags`)
 * alongside the surviving display-name contract.
 *
 * The recipe editor auto-targets the first installed model exposed by the
 * dashboard mock — `qwen3.6-27b-mtp` — mirroring model-recipe-template-v3.
 */
import { test, expect } from '../fixtures/apiMock'

const RETIRED = ['mtp', 'moe', 'tool-calling', 'reasoning', 'coder', 'vision']

function mockChatTemplates(page: import('@playwright/test').Page) {
  return page.route('**/api/chat-templates', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ id: 'auto', label: 'Auto (GGUF embedded)' }]),
    }),
  )
}

test.describe('Model edit — display name, type toggles retired', () => {
  test('name input renders; no curated type toggles', async ({ page }) => {
    await mockChatTemplates(page)
    await page.goto('/#models')
    await page.locator('button:has-text("Edit options")').click()

    const nameInput = page.getByTestId('model-name-input')
    await expect(nameInput).toBeVisible()
    // Placeholder falls back to the model id so the field is self-describing
    // even when no display name is set.
    await expect(nameInput).toHaveAttribute('placeholder', 'qwen3.6-27b-mtp')

    for (const tag of RETIRED) {
      await expect(page.getByTestId(`type-toggle-${tag}`)).toHaveCount(0)
    }
  })

  test('editing the name writes name — and never tags — on Save', async ({ page }) => {
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
          }),
        })
      }
      return route.fallback()
    })
    await mockChatTemplates(page)

    await page.goto('/#models')
    await page.locator('button:has-text("Edit options")').click()

    await page.getByTestId('model-name-input').fill('My Renamed Qwen')
    await page.getByTestId('model-save').click()

    await expect.poll(() => putBody?.name).toBe('My Renamed Qwen')
    expect(putBody).not.toHaveProperty('tags')
  })
})
