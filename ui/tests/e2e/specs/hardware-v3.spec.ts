/**
 * hardware-v3 — `#hardware` route renders Host / CPU / GPU / NPU /
 * Memory panels (HwCard subgrid) read-only.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Hardware v3 (/hardware)', () => {
  test('renders Hardware view + the 5 host/CPU/GPU/NPU/Memory cards', async ({ page }) => {
    await page.goto('/#hardware')
    await expect(page.locator('.view .vh h1')).toHaveText('Hardware')
    // panels are rendered as inline cards; HwCard markup uses unique titles
    const cards = page.locator('.view .card')
    expect(await cards.count()).toBeGreaterThanOrEqual(5)
  })

  test('eyebrow + read-only hint visible', async ({ page }) => {
    await page.goto('/#hardware')
    await expect(page.locator('.view .vh .vh-eye')).toHaveText('System')
    await expect(page.locator('.view .vh .hint')).toContainText('read-only')
  })
})
