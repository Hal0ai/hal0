/**
 * model-drawer-reset-profile-v3 — D1 "Reset to profile" re-stamp.
 *
 * After a model's flags have diverged from the profile that seeded them, the
 * explicit "Reset to profile" action re-stamps: it replaces the model's launch
 * flags with the profile's CURRENT text (confirm first), and the diverged chip
 * clears. The profile is never mutated by any of this.
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

test.describe('Model drawer — reset to profile', () => {
  test('reset re-stamps the profile text and clears the diverged chip', async ({ page }) => {
    await mockProfiles(page)
    await mockChatTemplates(page)

    await page.goto('/#models')
    await page.locator('button:has-text("Edit options")').click()
    await expect(page.getByTestId('model-flags-input')).toBeVisible()

    // Stamp, then diverge.
    await page.getByTestId('model-template-select').selectOption('rocm-moe')
    await page.getByTestId('model-flags-input').fill(`${PROFILE_FLAGS} --cache-type-k q8_0`)
    await expect(page.getByTestId('model-diverged-chip')).toBeVisible()

    // Reset → confirm → flags restored to the profile's current text.
    await page.getByTestId('model-reset-profile').click()
    await page.getByRole('button', { name: 'Reset to profile', exact: true }).click()

    await expect(page.getByTestId('model-flags-input')).toHaveValue(PROFILE_FLAGS)
    await expect(page.getByTestId('model-diverged-chip')).toHaveCount(0)
  })
})
