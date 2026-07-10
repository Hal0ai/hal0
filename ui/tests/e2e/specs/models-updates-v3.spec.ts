/**
 * models-updates-v3 — HF update-availability surface on the Models page.
 *
 * Backed by GET /api/models/updates (sha probe, mocked via MODEL_UPDATES):
 * qwen3.6-27b-q5kxl has a newer build upstream, qwen3-coder-next-q4kxl is
 * up to date. Asserts the per-row badge, the header "Update all" button
 * (→ POST /api/models/updates/apply), the detail-pane update affordances,
 * and the Model card section (GET /api/models/{id}/card).
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Models v3 — HF updates + model card', () => {
  test('update badge renders only on outdated rows', async ({ page }) => {
    await page.goto('/#models')
    const outdated = page.locator('.mdl-row', { hasText: 'Qwen3.6-27B-MTP' })
    await expect(outdated.getByTestId('mdl-row-update-available')).toBeVisible()
    const upToDate = page.locator('.mdl-row', { hasText: 'Qwen3-Coder-30B-A3B' })
    await expect(upToDate.getByTestId('mdl-row-update-available')).toHaveCount(0)
  })

  test('Update all header button posts to updates/apply', async ({ page }) => {
    await page.goto('/#models')
    const btn = page.getByTestId('mdl-update-all')
    await expect(btn).toBeVisible()
    await expect(btn).toContainText('Update all · 1')
    const [req] = await Promise.all([
      page.waitForRequest(
        (r) => r.url().includes('/api/models/updates/apply') && r.method() === 'POST',
      ),
      btn.click(),
    ])
    expect(req).toBeTruthy()
  })

  test('detail pane shows update chip + per-model Update action', async ({ page }) => {
    await page.goto('/#models')
    await page.locator('.mdl-row', { hasText: 'Qwen3.6-27B-MTP' }).click()
    await expect(page.getByTestId('mdl-detail-update-available')).toBeVisible()
    await expect(page.getByTestId('mdl-detail-update')).toBeVisible()
    // Selecting the up-to-date model hides both affordances.
    await page.locator('.mdl-row', { hasText: 'Qwen3-Coder-30B-A3B' }).click()
    await expect(page.getByTestId('mdl-detail-update-available')).toHaveCount(0)
    await expect(page.getByTestId('mdl-detail-update')).toHaveCount(0)
  })

  test('model card section fetches and renders the README', async ({ page }) => {
    await page.goto('/#models')
    await page.locator('.mdl-row', { hasText: 'Qwen3.6-27B-MTP' }).click()
    const toggle = page.getByTestId('mdl-card-toggle')
    await expect(toggle).toBeVisible()
    await toggle.click()
    await expect(page.getByTestId('mdl-card-body')).toContainText('Mock model card for e2e')
    // Collapses back.
    await toggle.click()
    await expect(page.getByTestId('mdl-card-body')).toHaveCount(0)
  })
})
