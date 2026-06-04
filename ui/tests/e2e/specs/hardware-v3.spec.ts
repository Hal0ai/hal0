/**
 * hardware-v3 — hardware spread (Host / CPU / GPU / NPU / Memory cards)
 * now lives inside the `#dashboard` view as `HardwareSection`. The
 * standalone `#hardware` route was retired in the chat-page overhaul.
 *
 * Every card reads live data (static probe /api/hardware + live counters
 * /api/stats/hardware). These tests assert the live wiring and guard
 * against the old hardcoded values (kernel string, "ROCm 6.4 ✓",
 * "currently loaded" trio, "3 models loaded") creeping back.
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

  test('Host card shows live hostname / kernel / distro from /api/hardware', async ({ page }) => {
    await page.goto('/#dashboard')
    const section = page.locator('.hw-section')
    await expect(section).toContainText('hal0')
    await expect(section).toContainText('7.0.6-2-pve')
    await expect(section).toContainText('Debian GNU/Linux 13 (trixie)')
    // The leading "Linux version " noise is stripped.
    await expect(section).not.toContainText('Linux version 7.0.6-2-pve')
  })

  test('GPU vendor stack reflects probe capability flags, not a baked version', async ({ page }) => {
    await page.goto('/#dashboard')
    const section = page.locator('.hw-section')
    // The old hardcoded "ROCm 6.4 ✓" (a fabricated version) must be gone;
    // the stack now shows live ✓/— flags from the probe capability bits.
    await expect(section).not.toContainText('ROCm 6.4')
    await expect(section).toContainText('Vulkan ✓')
    await expect(section).toContainText('llamacpp:vulkan')
  })

  test('NPU card reads live device + slot models, not the static trio string', async ({ page }) => {
    await page.goto('/#dashboard')
    const section = page.locator('.hw-section')
    // Device name comes from the probe; "currently loaded" comes from the
    // live NPU-device slots (seed has gemma3:1b on the NPU). The old
    // hardcoded FLM version string must not appear.
    await expect(section).toContainText('AMD NPU (XDNA2)')
    await expect(section).toContainText('currently loaded')
    await expect(section).not.toContainText('FLM v0.9.42')
  })

  test('Memory card is the reworked live widget (pool/system/model rows), not the old static one', async ({ page }) => {
    await page.goto('/#dashboard')
    const memCard = page.locator('.hw-section .card', { hasText: 'pool total' })
    // New live structure: separate pool total / system RAM / model memory
    // rows + a dynamic "N models loaded" count derived from live slots.
    await expect(memCard).toContainText('pool total')
    await expect(memCard).toContainText('system RAM')
    await expect(memCard).toContainText('model memory')
    await expect(memCard).toContainText('models loaded')
    // The old fabricated rows are gone.
    await expect(memCard).not.toContainText('per-type budget')
    await expect(memCard).not.toContainText('4 loaded models')
  })
})
