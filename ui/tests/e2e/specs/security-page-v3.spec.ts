/**
 * security-page-v3 — D4 Settings → Security (post-R3 surface rework).
 *
 * Status-only key management. The page is driven by GET /api/auth/status
 * ({ auth_required, has_admin_key, tier }) and NEVER renders a key value —
 * this suite asserts that absence directly. Everything the endpoint can't back
 * (client-key status, rotation, throttle counts, the live per-route exposure
 * table) is disabled-with-reason, and the rotate flow's destructive confirm is
 * gated on the missing rotation route.
 *
 * /api/auth/status is NOT in the in-bundle mock allowlist, so page.route wins.
 */
import { test, expect } from '../fixtures/apiMock'

function mockAuthStatus(
  page: import('@playwright/test').Page,
  body: { auth_required: boolean; has_admin_key: boolean; tier: string },
) {
  return page.route('**/api/auth/status', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
  )
}

async function openSecurity(page: import('@playwright/test').Page) {
  await page.goto('/#settings')
  await page.locator('.settings-nav .nav-item', { hasText: 'Security' }).click()
  await expect(page.getByTestId('security-page')).toBeVisible()
}

test.describe('Settings → Security', () => {
  test('admin key posture derives from has_admin_key (set)', async ({ page }) => {
    await mockAuthStatus(page, { auth_required: true, has_admin_key: true, tier: 'admin' })
    await openSecurity(page)

    await expect(page.getByTestId('security-key-admin-status')).toContainText(/set/i)
    await expect(page.getByTestId('security-tier')).toContainText('admin')
    // Client-key status is not reported by the endpoint → disabled + reason.
    await expect(page.getByTestId('security-set-client')).toBeDisabled()
    await expect(page.getByTestId('security-client-reason')).toContainText(/API-lane/i)
    // Throttle counters are not published.
    await expect(page.getByTestId('security-throttle-status')).toContainText(/unavailable/i)
  })

  test('admin key posture derives from has_admin_key (unset)', async ({ page }) => {
    await mockAuthStatus(page, { auth_required: false, has_admin_key: false, tier: 'open' })
    await openSecurity(page)
    await expect(page.getByTestId('security-key-admin-status')).toContainText(/unset/i)
  })

  test('never renders a key value — status only, no inputs on the page', async ({ page }) => {
    await mockAuthStatus(page, { auth_required: true, has_admin_key: true, tier: 'admin' })
    await openSecurity(page)

    // No free-standing inputs (the only input lives in the rotate dialog, and
    // it's closed here).
    await expect(page.getByTestId('security-page').locator('input, textarea, code')).toHaveCount(0)

    // Assert absence of any key-shaped token in the rendered text. A real key
    // is a long run of base64/hex; the page must contain none.
    const body = (await page.getByTestId('security-page').innerText()) || ''
    expect(body).not.toMatch(/[A-Za-z0-9_-]{24,}/)
  })

  test('exposure table shows the class taxonomy + a live-table stub reason', async ({ page }) => {
    await mockAuthStatus(page, { auth_required: true, has_admin_key: true, tier: 'admin' })
    await openSecurity(page)

    await expect(page.getByTestId('exposure-table')).toBeVisible()
    for (const cls of ['open', 'client', 'admin', 'bootstrap']) {
      await expect(page.getByTestId(`exposure-class-${cls}`)).toBeVisible()
    }
    await expect(page.getByTestId('exposure-live-stub')).toContainText(/API-lane/i)
  })

  test('rotate flow gates on type-to-confirm', async ({ page }) => {
    await mockAuthStatus(page, { auth_required: true, has_admin_key: true, tier: 'admin' })
    await openSecurity(page)

    await page.getByTestId('security-rotate-admin').click()
    await expect(page.getByTestId('rotate-confirm-input')).toBeVisible()
    // Confirm is disabled until the exact phrase is typed.
    await expect(page.getByTestId('rotate-confirm')).toBeDisabled()
    await page.getByTestId('rotate-confirm-input').fill('rotate admin')
    await expect(page.getByTestId('rotate-confirm')).toBeEnabled()
  })

  test('rotate calls POST /api/auth/rotate and shows status-only result, never a value', async ({
    page,
  }) => {
    await mockAuthStatus(page, { auth_required: true, has_admin_key: true, tier: 'admin' })
    // The endpoint returns STATUS ONLY — no key value ever crosses the wire.
    await page.route('**/api/auth/rotate', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tier: 'admin',
          rotated_at: '2026-07-19T06:00:00Z',
          key_len: 43,
          fingerprint: 'ab12cd34',
          applies_live: true,
          restart_required: false,
          session_preserved: true,
          note: 'New admin key written to /etc/hal0/api.env — retrieve it there; it is never shown in the dashboard.',
        }),
      }),
    )
    await openSecurity(page)

    await page.getByTestId('security-rotate-admin').click()
    await page.getByTestId('rotate-confirm-input').fill('rotate admin')
    await page.getByTestId('rotate-confirm').click()

    // Result panel shows the fingerprint + notice — never a key value.
    await expect(page.getByTestId('rotate-result')).toBeVisible()
    await expect(page.getByTestId('rotate-fingerprint')).toContainText('ab12cd34')
    await expect(page.getByTestId('rotate-note')).toContainText(/never shown/i)
    // No revealed value; the dialog renders no <code> value block or input now.
    await expect(page.getByTestId('rotate-result').locator('input')).toHaveCount(0)

    // After Done, the admin key row shows the fingerprint (status-only), no value.
    await page.getByTestId('rotate-done').click()
    await expect(page.getByTestId('security-key-admin')).toContainText('ab12cd34')
  })
})
