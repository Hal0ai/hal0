/**
 * model-updates-v3 — HF model-update feature on the `#models` catalog.
 *
 * Backend surface: GET /api/models/updates reports which installed models
 * have a newer file on their HuggingFace repo; POST /api/models/update-all
 * re-pulls every stale one. The UI shows a per-row "update" chip and a
 * top-of-page "Update all · N" button that appears only when something is
 * stale.
 */
import { test, expect } from '../fixtures/apiMock'

// An installed model id from the forced-mock HAL0_DATA catalog (src/dash/data.jsx)
// that renders on the default Inference tab.
const STALE_ID = 'qwen3.6-27b-mtp'

async function mockUpdates(page: import('@playwright/test').Page, available: number) {
  await page.route('**/api/models/updates', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        updates: [
          {
            model_id: STALE_ID,
            hf_repo: 'unsloth/Qwen3.6-27B-A3B-MTP-GGUF',
            hf_filename: 'Qwen3.6-27B-A3B-MTP-Q4_K_M.gguf',
            update_available: available > 0,
            current_sha: 'a'.repeat(64),
            remote_sha: 'b'.repeat(64),
            reason: null,
          },
        ],
        count: 1,
        available,
      }),
    }),
  )
}

test.describe('Model updates (/models)', () => {
  test('no update chip or Update-all button when everything is fresh', async ({ page }) => {
    // Fixture default already returns available:0; assert the absence.
    await page.goto('/#models')
    await expect(page.locator('.mdl-list')).toBeVisible()
    await expect(page.locator('[data-testid="mdl-update-all"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="mdl-row-update"]')).toHaveCount(0)
  })

  test('stale model shows a row indicator and the Update-all button', async ({ page }) => {
    await mockUpdates(page, 1)
    await page.goto('/#models')
    await expect(page.locator('.mdl-list')).toBeVisible()
    // Top button reflects the count.
    const btn = page.locator('[data-testid="mdl-update-all"]')
    await expect(btn).toBeVisible()
    await expect(btn).toContainText('Update all · 1')
    // Exactly the stale row carries the update chip.
    await expect(page.locator('[data-testid="mdl-row-update"]')).toHaveCount(1)
  })

  test('Update-all button POSTs to /api/models/update-all', async ({ page }) => {
    await mockUpdates(page, 1)
    let posted = false
    await page.route('**/api/models/update-all', (route) => {
      posted = true
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          started: [{ model_id: STALE_ID, id: 'job1', state: 'queued' }],
          skipped: [],
          count: 1,
        }),
      })
    })
    await page.goto('/#models')
    await page.locator('[data-testid="mdl-update-all"]').click()
    await expect.poll(() => posted).toBe(true)
  })
})
