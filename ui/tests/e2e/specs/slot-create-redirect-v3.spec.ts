/**
 * slot-create-redirect-v3 — D2 simplified create-slot flow.
 *
 * A slot is a pure instance now: model + name, port assigned by PortAuthority.
 * Reaching for a device teaches that it rides the model, and that per-slot
 * divergence is a custom profile on the slot. Asserts:
 *   - no profile/device picker is present in the create modal
 *   - the port row carries the PortAuthority affordance
 *   - the device teach is shown and points at per-slot profiles
 *   - picking a model surfaces the derived device in the teach copy
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Create slot — redirect + simplification', () => {
  test('create modal has model + name, no profile/device picker, PortAuthority port', async ({ page }) => {
    await page.goto('/#slots')
    await page.locator('.view .vh button:has-text("New slot")').click()

    await expect(page.getByTestId('create-slot-model')).toBeVisible()
    await expect(page.getByTestId('create-slot-name')).toBeVisible()

    // No profile picker (moved to the model); device is not a slot choice.
    await expect(page.locator('.modal-shell .form-row', { hasText: 'Profile' })).toHaveCount(0)

    // Port is read-only, assigned by PortAuthority.
    await expect(page.getByTestId('create-slot-port')).toContainText(/PortAuthority/i)

    // The device teach is present and points at per-slot profiles.
    const teach = page.getByTestId('create-slot-device-redirect')
    await expect(teach).toBeVisible()
    await expect(teach).toContainText(/rides the model/i)
    await expect(teach).toContainText(/profile/i)
  })

  test('picking a model surfaces the derived device in the teach copy', async ({ page }) => {
    await page.goto('/#slots')
    await page.locator('.view .vh button:has-text("New slot")').click()
    await page.getByTestId('create-slot-model').selectOption({ index: 1 })
    // Once a model is chosen the teach names the device it runs on.
    await expect(page.getByTestId('create-slot-device-redirect')).toContainText(/because the model is stamped/i)
  })
})
