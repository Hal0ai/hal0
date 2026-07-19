/**
 * auth-gate-v3 — app-shell login gate (O19).
 *
 * The dashboard now gates on GET /api/auth/status: when enforcement is on and
 * the session is anonymous the shell renders the login view INSTEAD of the app
 * (no flash of a locked dashboard). Auth off (the shipped default) → the app
 * renders untouched. This suite drives all three: locked box → login, wrong key
 * → error (no key echo), right key → app; plus the open-box no-op and the
 * rate-limit retry-after copy.
 *
 * /api/auth/status + /api/auth/login are not in the default fixture mocks, so
 * the per-test page.route registrations below win over the `/api/` catch-all.
 */
import { test, expect, json, type Page } from '../fixtures/apiMock'

type AuthOpts = {
  requireAuth?: boolean
  hasAdminKey?: boolean
  correctKey?: string
  rateLimited?: boolean
  retryAfterS?: number
}

/**
 * Stateful auth surface. GET /status reflects a closure-local `loggedIn` flag;
 * POST /login flips it (right key → 200 admin), or errors (wrong key → 401
 * auth.invalid_key; rateLimited → 429 auth.rate_limited with retry_after_s).
 * Mirrors the real backend shapes (routes/auth.py).
 */
async function installAuth(page: Page, opts: AuthOpts = {}) {
  const {
    requireAuth = true,
    hasAdminKey = true,
    correctKey = 'correct-admin-key',
    rateLimited = false,
    retryAfterS,
  } = opts
  let loggedIn = false

  await page.route('**/api/auth/status', (route) =>
    json(route, {
      auth_required: requireAuth,
      has_admin_key: hasAdminKey,
      tier: loggedIn ? 'admin' : 'anon',
    }),
  )

  await page.route('**/api/auth/login', (route) => {
    if (route.request().method() !== 'POST') return json(route, {})
    if (rateLimited) {
      return json(
        route,
        {
          error: {
            code: 'auth.rate_limited',
            message: 'too many login attempts',
            details: retryAfterS != null ? { retry_after_s: retryAfterS } : {},
          },
        },
        429,
      )
    }
    const body = route.request().postDataJSON?.() ?? {}
    if (body.key === correctKey) {
      loggedIn = true
      return json(route, { ok: true, tier: 'admin' })
    }
    return json(route, { error: { code: 'auth.invalid_key', message: 'invalid key' } }, 401)
  })
}

test.describe('App-shell auth gate', () => {
  test('locked box (auth on, anonymous) renders the login view, not the app', async ({ page }) => {
    await installAuth(page, { requireAuth: true })
    await page.goto('/')

    await expect(page.getByTestId('login-view')).toBeVisible()
    await expect(page.getByTestId('login-key-input')).toBeVisible()
    // The app shell must NOT be mounted behind the login (no flash of locked UI).
    await expect(page.locator('.app')).toHaveCount(0)
  })

  test('open box (auth off) renders the app untouched — zero change', async ({ page }) => {
    await installAuth(page, { requireAuth: false })
    await page.goto('/')

    await expect(page.locator('.app')).toBeVisible()
    await expect(page.getByTestId('login-view')).toHaveCount(0)
  })

  test('wrong key shows a clear error and never echoes the key', async ({ page }) => {
    await installAuth(page, { requireAuth: true, correctKey: 'the-right-key' })
    await page.goto('/')

    await page.getByTestId('login-key-input').fill('the-wrong-key')
    await page.getByTestId('login-submit').click()

    const err = page.getByTestId('login-error')
    await expect(err).toBeVisible()
    await expect(err).toContainText(/invalid key/i)
    // The submitted key must never appear in the error surface.
    await expect(err).not.toContainText('the-wrong-key')
    // Still gated — no app leaked through.
    await expect(page.locator('.app')).toHaveCount(0)
  })

  test('right key enters the app (login → cookie → app loads)', async ({ page }) => {
    await installAuth(page, { requireAuth: true, correctKey: 'the-right-key' })
    await page.goto('/')

    await expect(page.getByTestId('login-view')).toBeVisible()
    await page.getByTestId('login-key-input').fill('the-right-key')
    await page.getByTestId('login-submit').click()

    // Status re-reads as admin → the gate swaps in the app; login view is gone.
    await expect(page.locator('.app')).toBeVisible()
    await expect(page.getByTestId('login-view')).toHaveCount(0)
  })

  test('rate-limited login surfaces the retry-after seconds', async ({ page }) => {
    await installAuth(page, { requireAuth: true, rateLimited: true, retryAfterS: 17 })
    await page.goto('/')

    await page.getByTestId('login-key-input').fill('anything')
    await page.getByTestId('login-submit').click()

    const err = page.getByTestId('login-error')
    await expect(err).toBeVisible()
    await expect(err).toContainText(/17s/)
  })
})
