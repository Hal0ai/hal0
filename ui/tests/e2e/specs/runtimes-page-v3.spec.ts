/**
 * runtimes-page-v3 — runner-image evidence, now on Settings → Hardware &
 * Runtimes (settings-panel cleanup merged the Runtimes page into it).
 *
 * Evidence UI for the runner/image axis: one row per RUNNER_IMAGES entry with
 * family, image ref + digest (read-only), on-disk state, and the models/slots
 * reverse index. Nothing on the page edits an image string; the old disabled
 * "Pre-pull" placeholder is gone (no per-runner pull endpoint — a dead
 * control is worse than none), and a fully-unavailable probe (no podman)
 * degrades gracefully with a reason.
 *
 * /api/system-info is NOT in the in-bundle mock allowlist, so page.route wins.
 */
import { test, expect } from '../fixtures/apiMock'

function mockSystemInfo(page: import('@playwright/test').Page, backends: Record<string, any>) {
  return page.route('**/api/system-info', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ hardware: {}, features: {}, backends }),
    }),
  )
}

async function openRuntimes(page: import('@playwright/test').Page) {
  await page.goto('/#settings')
  await page.locator('.settings-nav .nav-item', { hasText: 'Hardware & Runtimes' }).click()
  await expect(page.getByTestId('hardware-page')).toBeVisible()
}

test.describe('Settings → Hardware & Runtimes (runner images)', () => {
  test('renders runner rows with status derived from system-info state', async ({ page }) => {
    await mockSystemInfo(page, {
      rocmfpx: { image: 'ghcr.io/hal0ai/hal0-rocmfp4:v0.9.4', runtime_family: 'llama-server', device_class: 'gpu', backend: 'rocm', state: 'installed' },
      vulkanfpx: { image: 'ghcr.io/ggml-org/llama.cpp:server-vulkan', runtime_family: 'llama-server', device_class: 'gpu', backend: 'vulkan', state: 'installable' },
    })
    await openRuntimes(page)

    await expect(page.getByTestId('runtime-row-rocmfpx')).toBeVisible()
    await expect(page.getByTestId('runtime-row-vulkanfpx')).toBeVisible()

    // Status derives from state, not hardcoded.
    await expect(page.getByTestId('runtime-status-rocmfpx')).toContainText(/installed/i)
    await expect(page.getByTestId('runtime-status-vulkanfpx')).toContainText(/not pulled/i)

    // Image ref is shown read-only.
    await expect(page.getByTestId('runtime-image-rocmfpx')).toContainText('hal0-rocmfp4')

    // Evidence UI: nothing on the page edits an image string.
    await expect(page.getByTestId('hardware-page').locator('input, textarea')).toHaveCount(0)

    // The dead disabled "Pre-pull" placeholder is gone (no endpoint backs it).
    await expect(page.getByTestId('runtime-prepull-vulkanfpx')).toHaveCount(0)
  })

  test('degraded probe (all unavailable) shows a reason', async ({ page }) => {
    await mockSystemInfo(page, {
      rocmfpx: { image: 'ghcr.io/hal0ai/hal0-rocmfp4:v0.9.4', runtime_family: 'llama-server', device_class: 'gpu', backend: 'rocm', state: 'unavailable' },
    })
    await openRuntimes(page)

    const degraded = page.getByTestId('runtimes-degraded')
    await expect(degraded).toBeVisible()
    await expect(degraded).toContainText(/probe is unavailable/i)
    await expect(page.getByTestId('runtime-status-rocmfpx')).toContainText(/probe unavailable/i)
  })
})
