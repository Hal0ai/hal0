/**
 * security-page-v3 — D4 Settings → Security (post-R3 surface rework; UI-API-2
 * wired the route-exposure table live).
 *
 * Status-only key management. The page is driven by GET /api/auth/status
 * ({ auth_required, has_admin_key, tier }) and NEVER renders a key value —
 * this suite asserts that absence directly. Client-key status and login
 * throttle counts stay disabled-with-reason (genuinely no backend route yet);
 * the rotate flow's destructive confirm is gated on typing the phrase. The
 * route-exposure table (GET /api/auth/exposure) is now live — a separate
 * describe block below drives its loaded/empty/permission-denied states.
 *
 * /api/auth/status and /api/auth/exposure are NOT in the in-bundle mock
 * allowlist, so page.route wins for both.
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

function mockAuthExposure(
  page: import('@playwright/test').Page,
  body: unknown,
  status = 200,
) {
  return page.route('**/api/auth/exposure', (route) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }),
  )
}

const EXPOSURE_FIXTURE = {
  classes: ['open', 'client', 'admin', 'bootstrap'],
  rules: [
    { label: 'auth status', auth_class: 'open', methods: ['GET'], pattern: '/api/auth/status', kind: 'exact' },
    { label: 'inference', auth_class: 'client', methods: ['POST'], pattern: '/v1/chat/completions', kind: 'prefix' },
    { label: 'settings', auth_class: 'admin', methods: null, pattern: '/api/settings', kind: 'prefix' },
  ],
  open_allowlist: [
    { method: 'GET', path: '/api/health' },
    { method: 'GET', path: '/v1/models' },
  ],
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

  test('exposure table shows the class taxonomy + the live per-route table', async ({ page }) => {
    await mockAuthStatus(page, { auth_required: true, has_admin_key: true, tier: 'admin' })
    await mockAuthExposure(page, EXPOSURE_FIXTURE)
    await openSecurity(page)

    await expect(page.getByTestId('exposure-table')).toBeVisible()
    for (const cls of ['open', 'client', 'admin', 'bootstrap']) {
      await expect(page.getByTestId(`exposure-class-${cls}`)).toBeVisible()
    }

    // Live rows come from GET /api/auth/exposure — one per fixture rule.
    await expect(page.getByTestId('exposure-rule-row')).toHaveCount(EXPOSURE_FIXTURE.rules.length)
    await expect(page.getByTestId('exposure-live-rules')).toContainText('inference')
    await expect(page.getByTestId('exposure-live-rules')).toContainText('/v1/chat/completions')
    await expect(page.getByTestId('exposure-live-rules')).toContainText('ADMIN')

    // The OPEN allowlist renders as its own list.
    await expect(page.getByTestId('exposure-allowlist-row')).toHaveCount(EXPOSURE_FIXTURE.open_allowlist.length)
    await expect(page.getByTestId('exposure-live-allowlist')).toContainText('/api/health')

    // No leftover stub-reason markup.
    await expect(page.getByTestId('exposure-live-stub')).toHaveCount(0)
  })

  test('exposure table shows a permission reason when GET /api/auth/exposure 403s', async ({ page }) => {
    await mockAuthStatus(page, { auth_required: true, has_admin_key: true, tier: 'client' })
    await mockAuthExposure(
      page,
      { error: { code: 'auth.forbidden', message: 'admin session required' } },
      403,
    )
    await openSecurity(page)

    await expect(page.getByTestId('exposure-live-error')).toContainText(/admin session/i)
    await expect(page.getByTestId('exposure-live-rules')).toHaveCount(0)
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
