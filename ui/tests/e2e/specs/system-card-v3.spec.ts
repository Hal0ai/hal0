/**
 * system-card-v3 — the condensed System identity card in the dashboard
 * sidebar. Home-redesign (2026-06-05) collapsed the five Hardware cards
 * (Host / CPU / GPU / NPU / Memory) into one glanceable line each, added a
 * live RAM glance, and folded in the former standalone runtime HealthCard.
 * (Supersedes hardware-v3.spec.ts — the .hw-section spread it tested was
 * demoted into this card.)
 *
 * Every row reads live data (static probe /api/hardware + live counters
 * /api/stats/hardware). These assert the live wiring and
 * guard against the old hardcoded values (kernel "Linux version " noise, a
 * baked "ROCm 6.4 ✓") creeping back. Verbose fields that did NOT survive
 * the condense (NPU "currently loaded", the "recommended" backend, the
 * Memory pool/system/model rows) now live in the slot snapshot + the
 * Memory map widget and are covered by their own specs.
 */
import { test, expect } from '../fixtures/apiMock'

// RETIRED: dashboard overhaul has no identity card (handoff registry);
// identity moved off main page — flagged to user for ratification.
//
// (feat/dashboard-overhaul) The dashboard route renders DashboardOverhaulView
// — the widget board — whose card registry intentionally contains no host/
// cpu/gpu/npu identity widget. The SystemCard component still lives in
// dashboard.jsx but is no longer mounted on any route, so these assertions
// cannot pass. The file is kept (describe.skip) as a historical record of the
// retired surface rather than deleted.
test.describe.skip('System card (dashboard sidebar)', () => {
  test('renders the condensed host/os/cpu/gpu/npu/ram rows', async ({ page }) => {
    await page.goto('/#dashboard')
    const card = page.locator('.sys-card')
    await expect(card).toBeVisible()
    // one row per identity field. The former folded-in runtime health row was
    // removed (2026-06-05) — runtime status now lives in the sidebar Runtime
    // widget, so the System card is hardware identity only.
    await expect(card.locator('.sys-row .k')).toHaveText([
      'host',
      'os',
      'cpu',
      'gpu',
      'npu',
      'ram',
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

  test('runtime health row is no longer present in the System card', async ({ page }) => {
    await page.goto('/#dashboard')
    const card = page.locator('.sys-card')
    // Removed 2026-06-05 — folded into the sidebar Runtime widget instead.
    await expect(card.locator('.sys-row .k', { hasText: 'runtime' })).toHaveCount(0)
    await expect(card.locator('.sys-health')).toHaveCount(0)
  })
})
