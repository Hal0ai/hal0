/**
 * model-recipe-template-v3 — Chat-template field in the recipe editor.
 *
 * Task 4 (Phase 3): the recipe editor gains a <select> populated from
 * GET /api/chat-templates. On Save the selected value is written into
 * PUT /api/models/{id} body as defaults.chat_template.
 *
 * The HAL0_DATA fixture auto-selects the first installed model
 * (qwen3.6-27b-mtp) and exposes "Edit options" in the detail pane —
 * mirroring the existing "Recipe editor opens" test in models-v3.spec.ts.
 */
import { test, expect } from '../fixtures/apiMock'
import { openRichSelect, pickRichOption } from '../fixtures/richSelect'

test.describe('Recipe editor — chat-template field', () => {
  test('select is populated from /api/chat-templates and Save writes defaults.chat_template', async ({
    page,
  }) => {
    // ── 1. Mock /api/chat-templates ───────────────────────────────
    await page.route('**/api/chat-templates', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { id: 'auto', label: 'Auto (GGUF embedded)' },
          { id: 'chatml', label: 'chatml' },
          { id: 'llama3', label: 'llama3' },
        ]),
      }),
    )

    // ── 2. Capture PUT /api/models/qwen3.6-27b-mtp ───────────────
    let putBody: any = null
    await page.route('**/api/models/qwen3.6-27b-mtp', async (route) => {
      if (route.request().method() === 'PUT') {
        putBody = JSON.parse(route.request().postData() || '{}')
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'qwen3.6-27b-mtp',
            defaults: putBody.defaults,
          }),
        })
      }
      return route.fallback()
    })

    // ── 3. Navigate and open recipe editor ───────────────────────
    await page.goto('/#models')
    await page.locator('button:has-text("Edit options")').click()

    // ── 4. Chat-template RichSelect is visible ────────────────────
    const tmplSelect = page.getByTestId('model-chat-template')
    await expect(tmplSelect).toBeVisible()

    // ── 5. Options are populated from the mock ───────────────────
    const listbox = await openRichSelect(tmplSelect)
    await expect(listbox.locator('[data-option-id="auto"]')).toHaveCount(1)
    await expect(listbox.locator('[data-option-id="chatml"]')).toHaveCount(1)
    await expect(listbox.locator('[data-option-id="llama3"]')).toHaveCount(1)

    // ── 6. Select chatml and save ─────────────────────────────────
    await pickRichOption(tmplSelect, 'chatml')
    await page.getByTestId('model-save').click()

    // ── 7. PUT body includes defaults.chat_template === 'chatml' ─
    await expect.poll(() => putBody?.defaults?.chat_template).toBe('chatml')
  })

  test('chat_template defaults to "auto" when model has no defaults', async ({ page }) => {
    await page.route('**/api/chat-templates', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { id: 'auto', label: 'Auto (GGUF embedded)' },
          { id: 'chatml', label: 'chatml' },
        ]),
      }),
    )

    await page.goto('/#models')
    await page.locator('button:has-text("Edit options")').click()

    const tmplSelect = page.getByTestId('model-chat-template')
    await expect(tmplSelect).toBeVisible()
    // Default value should be "auto" — the closed trigger renders the
    // selected option's row + chip directly (rich-select.jsx).
    await expect(tmplSelect).toContainText('Auto')
    await expect(tmplSelect).toContainText('GGUF embedded')
  })
})
