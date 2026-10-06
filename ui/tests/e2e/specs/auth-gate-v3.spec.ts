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
 * Posture-coupled gate (#1822): a LAN-bound box with an admin key refuses this
 * caller's ADMIN-class requests — reads included — while `auth_required` reads
 * false; the backend reports that as `admin_sign_in_required`. The second
 * describe block drives that front door: login view with its own copy, the
 * "View read-only" escape, signing in from the top-bar session chip, logging
 * out, and a session that lapses mid-use.
 *
 * /api/auth/status + /api/auth/login are not in the default fixture mocks, so
 * the per-test page.route registrations below win over the `/api/` catch-all.
 */
import { test, expect, json, type Page } from '../fixtures/apiMock'

type AuthOpts = {
  requireAuth?: boolean
  /** Posture-coupled gate: ADMIN requests from this caller need a session. */
  postureGated?: boolean
  /** Start with a valid session cookie already in place. */
  startLoggedIn?: boolean
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
    postureGated = false,
    startLoggedIn = false,
    hasAdminKey = true,
    correctKey = 'correct-admin-key',
    rateLimited = false,
    retryAfterS,
  } = opts
  let loggedIn = startLoggedIn

  await page.route('**/api/auth/status', (route) =>
    json(route, {
      auth_required: requireAuth,
      has_admin_key: hasAdminKey,
      lan_exposed: postureGated,
      admin_sign_in_required: (requireAuth || postureGated) && !loggedIn,
      tier: loggedIn ? 'admin' : 'anon',
    }),
  )

  await page.route('**/api/auth/logout', (route) => {
    loggedIn = false
    return json(route, { ok: true })
  })

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

  return {
    /** Simulate the 8h session cookie lapsing server-side. */
    expireSession: () => {
      loggedIn = false
    },
    isLoggedIn: () => loggedIn,
  }
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

test.describe('App-shell auth gate — posture-coupled gate (auth off, LAN-bound, keyed)', () => {
  const POSTURE = { requireAuth: false, postureGated: true, correctKey: 'the-right-key' }

  test('asks for the key at the front door, in its own words, with a read-only way in', async ({ page }) => {
    await installAuth(page, POSTURE)
    await page.goto('/')

    const login = page.getByTestId('login-view')
    await expect(login).toBeVisible()
    await expect(login).toContainText(/reachable from your network/i)
    // "Require authentication" is OFF on this box — the screen must not claim otherwise.
    await expect(login).not.toContainText(/authentication is enabled/i)
    await expect(page.getByTestId('login-view-read-only')).toBeVisible()
    await expect(page.locator('.app')).toHaveCount(0)
  })

  test('right key at the front door enters the app signed in', async ({ page }) => {
    await installAuth(page, POSTURE)
    await page.goto('/')

    await page.getByTestId('login-key-input').fill('the-right-key')
    await page.getByTestId('login-submit').click()

    await expect(page.locator('.app')).toBeVisible()
    await expect(page.getByTestId('tb-session-admin')).toBeVisible()
    await expect(page.getByTestId('tb-session-signin')).toHaveCount(0)
  })

  test('view read-only → sign in from the top bar → log out returns to the front door', async ({ page }) => {
    await installAuth(page, POSTURE)
    await page.goto('/')

    // Skip the login: the app renders, and the top bar says how to sign in.
    await page.getByTestId('login-view-read-only').click()
    await expect(page.locator('.app')).toBeVisible()
    const signIn = page.getByTestId('tb-session-signin')
    await expect(signIn).toBeVisible()

    // The read-only choice survives a reload of the tab.
    await page.reload()
    await expect(page.locator('.app')).toBeVisible()
    await expect(page.getByTestId('login-view')).toHaveCount(0)

    // Sign in from the chip: the drawer opens with nothing to retry.
    await signIn.click()
    const drawer = page.locator('aside.drawer', { hasText: 'Sign-in required' })
    await expect(drawer).toHaveClass(/\bopen\b/)
    await expect(page.getByTestId('auth-challenge-submit')).toHaveText('Sign in')
    await page.getByTestId('auth-challenge-key-input').fill('the-right-key')
    await page.getByTestId('auth-challenge-submit').click()

    await expect(page.locator('aside.drawer', { hasText: 'Sign-in required' })).toHaveCount(0)
    await expect(page.getByTestId('tb-session-admin')).toBeVisible()

    // Logging out lands on the login view, not silently back in read-only.
    await page.getByTestId('tb-session-logout').click()
    await expect(page.getByTestId('login-view')).toBeVisible()
    await expect(page.locator('.app')).toHaveCount(0)
  })

  test('a session that lapses mid-use brings the login view back instead of empty pages', async ({ page }) => {
    const auth = await installAuth(page, { ...POSTURE, startLoggedIn: true })
    // ADMIN-class reads answer only while the session is valid — the gate is
    // per route class, so a lapsed cookie 401s polled GETs, not just mutations.
    await page.route('**/api/agent/approvals', (route) =>
      auth.isLoggedIn()
        ? json(route, { approvals: [] })
        : json(
            route,
            { error: { code: 'auth.required', message: 'authentication required', details: {} } },
            401,
          ),
    )
    await page.goto('/')
    await expect(page.locator('.app')).toBeVisible()
    await expect(page.getByTestId('tb-session-admin')).toBeVisible()

    auth.expireSession()

    await expect(page.getByTestId('login-view')).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('.app')).toHaveCount(0)
  })

  test('a box that gates nothing shows no session chip', async ({ page }) => {
    await installAuth(page, { requireAuth: false, hasAdminKey: false })
    await page.goto('/')

    await expect(page.locator('.app')).toBeVisible()
    await expect(page.getByTestId('tb-session-signin')).toHaveCount(0)
    await expect(page.getByTestId('tb-session-admin')).toHaveCount(0)
  })
})
