/**
 * security-enforcement-v3 — Settings ▸ Security enforcement toggle + logout (O19).
 *
 * The Security page's auth controls are now REAL, not stubs: the enforcement
 * toggle drives PUT /api/auth/require and the logout action POST /api/auth/logout,
 * both reflecting live posture from GET /api/auth/status. This suite drives:
 *   - off + admin key → Enable is live; clicking it flips state to on.
 *   - off + NO admin key → Enable is blocked (lockout guard) with a reason.
 *   - on + admin session → Log out is offered and posts to /logout.
 *
 * /api/auth/{status,require,logout} are not in the default fixture mocks, so
 * the per-test page.route registrations win over the `/api/` catch-all.
 */
import { test, expect, json, type Page } from '../fixtures/apiMock'

async function openSecurity(page: Page) {
  await page.goto('/#settings')
  await page.locator('.settings-nav .nav-item', { hasText: 'Security' }).click()
  await expect(page.getByTestId('security-page')).toBeVisible()
}

/** Stateful posture: /require flips require_auth; /status reflects it. tier is
 *  held fixed (admin) so toggling enforcement never bounces this admin session
 *  to the login gate mid-test. */
async function installAuth(
  page: Page,
  opts: { requireAuth?: boolean; hasAdminKey?: boolean; tier?: string } = {},
) {
  const state = {
    auth_required: opts.requireAuth ?? false,
    has_admin_key: opts.hasAdminKey ?? true,
    tier: opts.tier ?? 'admin',
  }
  await page.route('**/api/auth/status', (route) => json(route, state))
  await page.route('**/api/auth/require', (route) => {
    if (route.request().method() !== 'PUT') return json(route, {})
    const body = route.request().postDataJSON?.() ?? {}
    if (body.require_auth && !state.has_admin_key) {
      return json(route, { error: { code: 'auth.no_admin_key', message: 'no admin key' } }, 400)
    }
    state.auth_required = !!body.require_auth
    return json(route, { require_auth: state.auth_required, applies_live: true })
  })
  let loggedOut = false
  await page.route('**/api/auth/logout', (route) => {
    loggedOut = true
    return json(route, { ok: true })
  })
  return { get logoutCalled() { return loggedOut }, state }
}

test.describe('Settings → Security enforcement', () => {
  test('auth off + admin key: Enable is live and flips state to on', async ({ page }) => {
    await installAuth(page, { requireAuth: false, hasAdminKey: true, tier: 'admin' })
    await openSecurity(page)

    await expect(page.getByTestId('security-enforcement-state')).toContainText(/off/i)
    const enable = page.getByTestId('security-enforcement-enable')
    await expect(enable).toBeEnabled()
    await enable.click()

    // Posture re-read → state flips to on, and the Disable control appears.
    await expect(page.getByTestId('security-enforcement-state')).toContainText(/on/i)
    await expect(page.getByTestId('security-enforcement-disable')).toBeVisible()
  })

  test('auth off + no admin key: Enable is blocked with a lockout reason', async ({ page }) => {
    await installAuth(page, { requireAuth: false, hasAdminKey: false, tier: 'anon' })
    await openSecurity(page)

    await expect(page.getByTestId('security-enforcement-enable')).toBeDisabled()
    await expect(page.getByTestId('security-enforcement-blocked')).toContainText(/lock/i)
  })

  test('admin session offers a working Log out', async ({ page }) => {
    const auth = await installAuth(page, { requireAuth: true, hasAdminKey: true, tier: 'admin' })
    await openSecurity(page)

    const logoutBtn = page.getByTestId('security-logout')
    await expect(logoutBtn).toBeVisible()
    await logoutBtn.click()
    await expect.poll(() => auth.logoutCalled).toBe(true)
  })
})
