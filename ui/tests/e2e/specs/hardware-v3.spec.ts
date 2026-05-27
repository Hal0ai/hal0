/**
 * hardware-v3 — hardware spread (Host / CPU / GPU / NPU / Memory cards)
 * now lives inside the `#dashboard` view as `HardwareSection`. The
 * standalone `#hardware` route was retired in the chat-page overhaul.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Hardware section (on /dashboard)', () => {
  test('renders the Host/CPU/GPU/NPU/Memory cards inside .hw-section', async ({ page }) => {
    await page.goto('/#dashboard')
    await expect(page.locator('.hw-section .vh h2')).toHaveText('Hardware')
    // panels are rendered as inline cards inside hw-section
    const cards = page.locator('.hw-section .card')
    expect(await cards.count()).toBeGreaterThanOrEqual(5)
  })

  test('eyebrow + read-only hint visible', async ({ page }) => {
    await page.goto('/#dashboard')
    await expect(page.locator('.hw-section .vh .vh-eye')).toHaveText('System')
    await expect(page.locator('.hw-section .vh .hint')).toContainText('read-only')
  })
})
