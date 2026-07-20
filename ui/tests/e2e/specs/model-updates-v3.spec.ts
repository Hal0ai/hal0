/**
 * model-updates-v3 — HF model-update surface on the `#models` catalog.
 *
 * Covers the merged feature (per-row badge, Update-all, per-model detail
 * Update) plus the follow-up "Check updates" control (last-checked +
 * manual re-check + in-flight state).
 *
 * Forced-mock (VITE_MOCK_HAL0) short-circuits page.route for /api/models,
 * so update badges are driven via `window.__hal0MockModelUpdates` — the
 * mock's buildModels + buildModelUpdatesCheck honour it (mirrors the
 * __hal0MockMemoryEnabled pattern). `/api/models/{id}/update` is a POST and
 * is NOT allowlisted, so it reaches page.route normally.
 */
import { test, expect } from '../fixtures/apiMock'

// An installed model id from the forced-mock HAL0_DATA catalog (dash/data.jsx).
const STALE_ID = 'qwen3.6-27b-mtp'

test.describe('Model updates (/models)', () => {
  test('no badge or Update-all button when everything is fresh', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.mdl-list')).toBeVisible()
    await expect(page.locator('[data-testid="mdl-update-all"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="mdl-row-update"]')).toHaveCount(0)
    // The "Check updates" control is always present, regardless of staleness.
    await expect(page.locator('[data-testid="mdl-check-updates"]')).toBeVisible()
  })

  test.describe('with one stale model', () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((id) => {
        ;(window as unknown as { __hal0MockModelUpdates?: unknown }).__hal0MockModelUpdates = {
          availableIds: [id],
          checked_at: Math.floor(Date.now() / 1000) - 120, // 2 min ago
        }
      }, STALE_ID)
    })

    test('stale model shows a row badge and the Update-all button', async ({ page }) => {
      await page.goto('/#models')
      await expect(page.locator('.mdl-list')).toBeVisible()
      const btn = page.locator('[data-testid="mdl-update-all"]')
      await expect(btn).toBeVisible()
      await expect(btn).toContainText('Update all (1)')
      await expect(page.locator('[data-testid="mdl-row-update"]')).toHaveCount(1)
    })

    test('Update-all POSTs /api/models/{id}/update for the stale model', async ({ page }) => {
      const posted: string[] = []
      await page.route('**/api/models/*/update', (route) => {
        posted.push(new URL(route.request().url()).pathname)
        return route.fulfill({ status: 202, contentType: 'application/json', body: '{}' })
      })
      await page.goto('/#models')
      await page.locator('[data-testid="mdl-update-all"]').click()
      await expect.poll(() => posted).toContain(`/api/models/${STALE_ID}/update`)
    })

    test('detail pane exposes a per-model Update action', async ({ page }) => {
      await page.goto('/#models')
      // Select the stale row so the detail pane targets it.
      await page.locator('[data-testid="mdl-row-update"]').first().click()
      await expect(page.locator('[data-testid="mdl-detail-update"]')).toBeVisible()
    })
  })

  test('Check-updates button re-probes with ?refresh=1 and shows a pending state', async ({ page }) => {
    let refreshed = false
    // Gate the refresh so the "Checking…" pending state is observable.
    let release: () => void = () => {}
    const gate = new Promise<void>((r) => (release = r))
    await page.route('**/api/models/updates/check?refresh=1', async (route) => {
      refreshed = true
      await gate
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ checked_at: Math.floor(Date.now() / 1000), checked: 3, updates_available: 0, models: {} }),
      })
    })
    await page.goto('/#models')
    const btn = page.locator('[data-testid="mdl-check-updates"]')
    await expect(btn).toBeVisible()
    await btn.click()
    await expect(btn).toContainText('Checking…')
    await expect(btn).toBeDisabled()
    release()
    await expect.poll(() => refreshed).toBe(true)
    await expect(btn).toContainText('Check updates')
  })
})
