/**
 * updates-state-error-branches — a failed /api/updates/state read must not be
 * rendered as a known, plausible update posture (#1539 tranche 2).
 *
 * `updatesState` feeds seven call sites and none of them consults `isError`.
 * Six of those seven are honest about it: the footer chip and the update
 * banner simply stay hidden (their resting state anyway), About renders "—",
 * the notification bell drops the row, and main.jsx leaves the tab title on
 * its build-time stamp. Not a lie in any of them.
 *
 * Settings ▸ Updates is the exception, and it is the #1467 shape exactly:
 *
 *   - The channel <select> read `u.hal0?.channel || 'stable'`. A failed read
 *     therefore displayed "stable" — a specific, plausible, persisted-looking
 *     value the page never actually read. An operator on `nightly` was shown
 *     `stable` with nothing to suggest the read had failed.
 *   - The hal0 row fell through to `current {u.hal0?.current}` with `current`
 *     undefined, rendering the bare word "current" followed by nothing.
 *   - Nothing on the page said the read had failed, while "Roll back" — the
 *     one irreversible control here — stayed armed against unknown state.
 *
 * The tell that this is the defect class and not just a missing banner: the
 * Auto-check row in the SAME panel already guards on `stateQuery.data` and
 * renders "—" (that guard is #1467's fix). One row in the panel was honest
 * and the one next to it fabricated a value.
 *
 * Requires #1538's `__hal0MockPassthrough` — `updatesState` is a plain
 * allowlist row, so forced-mock substitutes it before any fetch is issued and
 * a `page.route` override never sees the URL.
 */
import { test, expect } from '../fixtures/apiMock'

type Win = Window & {
  __hal0MockPassthrough?: unknown
  __hal0UpdateStateOverride?: unknown
}

/** Claim /api/updates/state and fail it. Everything else stays mocked. */
async function breakUpdateState(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    ;(window as unknown as Win).__hal0MockPassthrough = ['/api/updates/state']
  })
  await page.route('**/api/updates/state', (route) =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        error: { code: 'updates.unavailable', message: 'update service is not responding' },
      }),
    }),
  )
}

/** Healthy path with a NON-default channel, so "shows stable" can't pass by
 *  coincidence — the forced-mock payload's channel is itself `stable`. */
async function seedChannel(page: import('@playwright/test').Page, channel: string) {
  await page.addInitScript((ch) => {
    ;(window as unknown as Win).__hal0UpdateStateOverride = {
      hal0: { current: '9.9.9', available: null, channel: ch, revoked: false },
      flm: { current: 'v0.9.42', source: 'manual-deb' },
      autoCheck: true,
    }
  }, channel)
}

test.describe('Settings ▸ Updates — an unreadable update state is not a known one (#1539)', () => {
  test('the panel says the read failed instead of staying silent', async ({ page }) => {
    await breakUpdateState(page)
    await page.goto('/#settings/updates')

    const err = page.getByTestId('updates-state-error')
    await expect(err).toBeVisible({ timeout: 15_000 })
    await expect(err).toContainText('Could not read update state')
  })

  test('the channel select does not fabricate "stable"', async ({ page }) => {
    await breakUpdateState(page)
    await page.goto('/#settings/updates')
    await expect(page.getByTestId('updates-state-error')).toBeVisible({ timeout: 15_000 })

    // The lie: a persisted setting rendered as a specific plausible value the
    // page never read. Unknown must read as unknown, and the control must not
    // invite a switch away from a baseline it doesn't have.
    const sel = page.getByTestId('updates-channel')
    await expect(sel).toBeDisabled()
    await expect(sel).toHaveValue('')
    await expect(sel).not.toHaveValue('stable')
  })

  test('the hal0 row reads "—", not a dangling "current"', async ({ page }) => {
    await breakUpdateState(page)
    await page.goto('/#settings/updates')
    await expect(page.getByTestId('updates-state-error')).toBeVisible({ timeout: 15_000 })

    const ver = page.getByTestId('updates-hal0-version')
    await expect(ver).toHaveText('—')
  })

  test('roll back is disarmed while the state is unknown', async ({ page }) => {
    // Rolling back is the one irreversible control on this page. Offering it
    // against a version we failed to read is the actionable half of the lie.
    await breakUpdateState(page)
    await page.goto('/#settings/updates')
    await expect(page.getByTestId('updates-state-error')).toBeVisible({ timeout: 15_000 })

    await expect(page.getByTestId('updates-rollback')).toBeDisabled()
  })

  test('healthy: the real channel is shown, selectable, and no outage notice', async ({ page }) => {
    // Regression guard, deliberately written against the markup that already
    // exists on main so it is GREEN before the fix as well as after — the
    // error branch is evaluated in front of the normal render, so its job is
    // to prove the fix stays scoped to real failures rather than swallowing
    // the values it replaced.
    await seedChannel(page, 'nightly')
    await page.goto('/#settings/updates')

    const sel = page.locator('.s-panel select')
    await expect(sel).toHaveValue('nightly', { timeout: 15_000 })
    await expect(sel).toBeEnabled()
    await expect(page.locator('.s-panel').first()).toContainText('current 9.9.9')
    await expect(page.getByTestId('updates-state-error')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Roll back' })).toBeEnabled()
  })
})
