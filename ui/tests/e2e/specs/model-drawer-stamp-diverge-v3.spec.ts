/**
 * model-drawer-stamp-diverge-v3 — D1 model editor drawer (post-R3 rework).
 *
 * Exercises the core "the model is the launchable thing" flow:
 *   1. STAMP — selecting a profile in the template picker COPIES its `flags`
 *      text into the model's own editable flags editor; provenance updates.
 *   2. DIVERGE — editing the flags so they differ from the profile's current
 *      text raises the diverged chip + the inline client-side divergence diff.
 *   3. MANAGED-ARG REJECTION — a managed flag (--port) in the tune text surfaces
 *      an inline error, disables Save, and fires NO PUT (§21.7).
 *
 * /api/profiles is networkFirst in the mock harness, so page.route wins; the
 * drawer auto-targets the first installed model (qwen3.6-27b-mtp).
 */
import { test, expect } from '../fixtures/apiMock'

const PROFILE_FLAGS = '-fa on -b 2048 -ub 512 --threads 8'

function mockProfiles(page: import('@playwright/test').Page) {
  return page.route('**/api/profiles', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          name: 'rocm-moe',
          image: 'ghcr.io/hal0ai/hal0-rocmfp4',
          flags: PROFILE_FLAGS,
          resolved_flags: PROFILE_FLAGS,
          mtp: false,
          intent: 'MoE agents',
        },
      ]),
    }),
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

async function openDrawer(page: import('@playwright/test').Page) {
  await page.goto('/#models')
  await page.locator('button:has-text("Edit options")').click()
  await expect(page.getByTestId('model-flags-input')).toBeVisible()
}

test.describe('Model drawer — stamp & diverge', () => {
  test('selecting a profile copies its flags into the editor + sets provenance', async ({ page }) => {
    await mockProfiles(page)
    await mockChatTemplates(page)
    await openDrawer(page)

    await page.getByTestId('model-template-select').selectOption('rocm-moe')

    await expect(page.getByTestId('model-flags-input')).toHaveValue(PROFILE_FLAGS)
    await expect(page.getByTestId('model-provenance-chip')).toHaveText(/seeded from rocm-moe/i)
    // Freshly stamped text equals the profile — no divergence yet.
    await expect(page.getByTestId('model-diverged-chip')).toHaveCount(0)
  })

  test('editing stamped flags raises the diverged chip + inline diff', async ({ page }) => {
    await mockProfiles(page)
    await mockChatTemplates(page)
    await openDrawer(page)

    await page.getByTestId('model-template-select').selectOption('rocm-moe')
    // Add a flag the profile doesn't carry → diverged (added token).
    await page.getByTestId('model-flags-input').fill(`${PROFILE_FLAGS} --cache-type-k q8_0`)

    await expect(page.getByTestId('model-diverged-chip')).toBeVisible()
    const diff = page.getByTestId('model-divergence-diff')
    await expect(diff).toBeVisible()
    await expect(diff).toContainText('--cache-type-k')
  })

  test('a managed flag in the tune text blocks Save with an inline error, no PUT', async ({ page }) => {
    let putFired = false
    await page.route('**/api/models/qwen3.6-27b-mtp', async (route) => {
      if (route.request().method() === 'PUT') {
        putFired = true
        return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
      }
      return route.fallback()
    })
    await mockProfiles(page)
    await mockChatTemplates(page)
    await openDrawer(page)

    await page.getByTestId('model-flags-input').fill('-fa on --port 9000')

    const err = page.getByTestId('model-flags-error')
    await expect(err).toBeVisible()
    await expect(err).toContainText('--port')
    await expect(page.getByTestId('model-save')).toBeDisabled()

    // Clicking a disabled Save must not fire a PUT.
    await page.getByTestId('model-save').click({ force: true }).catch(() => {})
    await page.waitForTimeout(150)
    expect(putFired).toBe(false)
  })
})
