/**
 * footer-update-chip-v3 — issue #325 (epic #322 Phase 3 — folded scope).
 *
 * Phase 2 (PR #329) wires the TOP banner to `useUpdateState()`. The
 * Footer chip was still hardcoded to render "hal0 v0.2.2 available"
 * regardless of real release state — driven only by the global
 * BannerStack dismiss prop. This spec pins the Footer chip to live
 * data the same way:
 *
 *   1. When `useUpdateState()` returns no available release (or
 *      current === available), the chip is hidden.
 *   2. When `useUpdateState()` returns `{current, available}` mismatch,
 *      the chip renders with the live `${available}` string.
 *   3. The literal "v0.2.2" no longer appears in chrome.jsx — proven
 *      by source-grepping the file directly (the bundle itself still
 *      carries the string from the BANNER_CATALOG prototype entry,
 *      which Phase 2 owns and is intentionally out of #325's scope).
 *
 * The mock seam `window.__hal0UpdateStateOverride` lets us drive
 * forced-mock dev with arbitrary update-state payloads without ripping
 * out the mockFetch short-circuit.
 */
import { test, expect } from '../fixtures/apiMock'

async function withUpdateState(
  page: import('@playwright/test').Page,
  override: unknown,
) {
  await page.addInitScript((payload) => {
    ;(window as any).__hal0UpdateStateOverride = payload
  }, override)
}

const UPDATE_CHIP = (page: import('@playwright/test').Page) =>
  page.locator('.footer .foot-chip.accent', { hasText: /hal0 .* available/ })

test.describe('Footer update chip (#325)', () => {
  test('chip hidden when useUpdateState returns no available release', async ({ page }) => {
    await withUpdateState(page, {
      hal0: { current: '0.3.0-alpha.1', available: null, channel: 'stable' },
      flm: { current: 'v0.9.42', source: 'manual-deb' },
      autoCheck: true,
    })
    await page.goto('/')
    await expect(page.locator('.footer')).toBeVisible()
    // Wait for the rollup poll to land — the runtime-chip text proves
    // useRuntimeRollup has settled, which means useUpdateState has had
    // time to run too.
    await expect(page.locator('.footer .foot-chip', { hasText: 'runtime' })).toBeVisible({
      timeout: 6_000,
    })
    await expect(UPDATE_CHIP(page)).toHaveCount(0)
  })

  test('chip hidden when current === available', async ({ page }) => {
    await withUpdateState(page, {
      hal0: { current: '0.3.0-alpha.1', available: '0.3.0-alpha.1', channel: 'stable' },
      flm: { current: 'v0.9.42', source: 'manual-deb' },
      autoCheck: true,
    })
    await page.goto('/')
    await expect(page.locator('.footer')).toBeVisible()
    await expect(page.locator('.footer .foot-chip', { hasText: 'runtime' })).toBeVisible({
      timeout: 6_000,
    })
    await expect(UPDATE_CHIP(page)).toHaveCount(0)
  })

  test('chip renders with live available version when an update is offered', async ({ page }) => {
    await withUpdateState(page, {
      hal0: { current: '0.3.0-alpha.1', available: '0.3.0', channel: 'stable' },
      flm: { current: 'v0.9.42', source: 'manual-deb' },
      autoCheck: true,
    })
    await page.goto('/')
    await expect(page.locator('.footer')).toBeVisible()
    const chip = UPDATE_CHIP(page)
    await expect(chip).toBeVisible({ timeout: 6_000 })
    // The chip text MUST reflect the live `available` string — not the
    // prototype's hardcoded "v0.2.2".
    await expect(chip).toContainText('hal0 0.3.0 available')
    await expect(chip).not.toContainText('v0.2.2')
  })
})

test.describe('Footer update chip — source hygiene (#325)', () => {
  test('chrome.jsx no longer hardcodes "v0.2.2"', async () => {
    // The Footer used to render `<span className="v">hal0 v0.2.2 available</span>`
    // as a literal — the chip's bug-of-record. After #325 the chip
    // composes its text from `useUpdateState()` at render time, so the
    // string "v0.2.2" should be gone from the file. Note: a single
    // explanatory comment line in main.jsx still references the old
    // string and is intentionally allowed (the comment justifies the
    // change). This spec scopes the assertion to chrome.jsx, which is
    // the Footer component's own home.
    const fs = await import('fs')
    const path = await import('path')
    const { fileURLToPath } = await import('url')
    const here = path.dirname(fileURLToPath(import.meta.url))
    const chromePath = path.resolve(here, '../../..', 'src/dash/chrome.jsx')
    const body = fs.readFileSync(chromePath, 'utf8')
    // Allow the explanatory comment in the Footer's docblock that
    // explicitly cites the old "v0.2.2 available" string as the bug
    // being fixed. Strip block + line comments and re-check.
    const codeOnly = body
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/.*$/gm, '')
    expect(codeOnly).not.toContain('v0.2.2')
  })
})
