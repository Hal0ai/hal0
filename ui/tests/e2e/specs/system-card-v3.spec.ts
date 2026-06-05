/**
 * system-card-v3 — the condensed System identity card in the dashboard
 * sidebar. Home-redesign (2026-06-05) collapsed the five Hardware cards
 * (Host / CPU / GPU / NPU / Memory) into one glanceable line each, added a
 * live RAM glance, and folded in the former standalone lemond HealthCard.
 * (Supersedes hardware-v3.spec.ts — the .hw-section spread it tested was
 * demoted into this card.)
 *
 * Every row reads live data (static probe /api/hardware + live counters
 * /api/stats/hardware + lemond rollup). These assert the live wiring and
 * guard against the old hardcoded values (kernel "Linux version " noise, a
 * baked "ROCm 6.4 ✓") creeping back. Verbose fields that did NOT survive
 * the condense (NPU "currently loaded", the "recommended" backend, the
 * Memory pool/system/model rows) now live in the slot snapshot + the
 * Memory map widget and are covered by their own specs.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('System card (dashboard sidebar)', () => {
  test('renders the condensed host/os/cpu/gpu/npu/ram + lemond rows', async ({ page }) => {
    await page.goto('/#dashboard')
    const card = page.locator('.sys-card')
    await expect(card).toBeVisible()
    // one row per identity field, plus the folded-in lemond health row
    await expect(card.locator('.sys-row .k')).toHaveText([
      'host',
      'os',
      'cpu',
      'gpu',
      'npu',
      'ram',
      'lemond',
    ])
  })

  test('host/os rows show live hostname + distro + stripped kernel', async ({ page }) => {
    await page.goto('/#dashboard')
    const card = page.locator('.sys-card')
    await expect(card).toContainText('hal0')
    await expect(card).toContainText('Debian GNU/Linux 13 (trixie)')
    await expect(card).toContainText('7.0.6-2-pve')
    // the leading "Linux version " noise is stripped
    await expect(card).not.toContainText('Linux version 7.0.6-2-pve')
  })

  test('cpu row shows live model + core/thread count', async ({ page }) => {
    await page.goto('/#dashboard')
    const card = page.locator('.sys-card')
    await expect(card).toContainText('Ryzen AI Max+ PRO 395')
    // normalizeHardware formats cores as "<n>c · <n>t"
    await expect(card).toContainText('16c · 32t')
  })

  test('gpu row shows capability chips from probe flags, not a baked version', async ({ page }) => {
    await page.goto('/#dashboard')
    const card = page.locator('.sys-card')
    await expect(card).toContainText('AMD Radeon 8060S')
    // seed: both compute_capable + vulkan_capable → both chips render
    await expect(card).toContainText('Vulkan ✓')
    await expect(card).toContainText('ROCm ✓')
    // the old fabricated version string must not return
    await expect(card).not.toContainText('ROCm 6.4')
  })

  test('npu row shows live device + driver', async ({ page }) => {
    await page.goto('/#dashboard')
    const card = page.locator('.sys-card')
    await expect(card).toContainText('AMD NPU (XDNA2)')
    await expect(card).toContainText('amdxdna')
  })

  test('lemond health row is folded into the System card', async ({ page }) => {
    await page.goto('/#dashboard')
    const card = page.locator('.sys-card')
    // the row key + the status pill render; the standalone Health card is gone
    await expect(card.locator('.sys-row .k', { hasText: 'lemond' })).toBeVisible()
    await expect(card.locator('.sys-health')).toBeVisible()
  })
})
