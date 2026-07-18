/**
 * slot-create-redirect-v3 — D2 simplified create-slot flow.
 *
 * A slot is a pure instance now: model + name, port assigned by PortAuthority.
 * Reaching for a device redirects to the model's duplicate-for-device flow
 * (teaching the mental model instead of dead-ending). Asserts:
 *   - no profile/device picker is present in the create modal
 *   - the port row carries the PortAuthority affordance
 *   - the device-redirect teach is shown and links to the model duplicate flow
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

    // The device-redirect teach is present and points at the model duplicate.
    const teach = page.getByTestId('create-slot-device-redirect')
    await expect(teach).toBeVisible()
    await expect(teach).toContainText(/rides the model/i)
    await expect(page.getByTestId('create-slot-duplicate-link')).toHaveAttribute('href', '#models')
  })

  test('picking a model surfaces the derived device in the teach copy', async ({ page }) => {
    await page.goto('/#slots')
    await page.locator('.view .vh button:has-text("New slot")').click()
    await page.getByTestId('create-slot-model').selectOption({ index: 1 })
    // Once a model is chosen the teach names the device it runs on.
    await expect(page.getByTestId('create-slot-device-redirect')).toContainText(/because the model is stamped/i)
  })
})
