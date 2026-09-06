/**
 * auth-challenge-v3 (#1822) — the posture-coupled ADMIN gate's dashboard UX.
 *
 * A LAN-bound box with `require_auth` OFF still requires an admin session
 * for ADMIN-class mutations from off-box callers (hal0.api.auth's
 * posture-coupled gate) — model pulls, slot deletes, config writes, and
 * approval execution alike. `auth_required` genuinely reads false in this
 * scenario, so the full-page login (AuthGate/LoginView) never fires; the
 * FIRST mutation that hits the 401 (`auth.required`) is what has to surface
 * the prompt. `lib/queryClient.ts`'s global `MutationCache.onError` catches
 * it and routes it to `AuthChallengeDrawer` via `useAuthChallengeStore` —
 * this spec drives that end-to-end through the approvals flow named in the
 * brief: mutation → 401 → sign-in → retried OK.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Auth challenge drawer (#1822 posture-coupled gate)', () => {
  test('approve 401s once, the drawer prompts sign-in, and the retry succeeds', async ({
    page,
    mockState,
  }) => {
    mockState.approvals = [
      {
        id: 'ap-1',
        tool: 'model_pull',
        args: { model: 'llama-3.1-8b' },
        client_id: 'hermes',
        enqueued_at: Date.now() / 1000 - 60,
        state: 'pending',
      },
    ]

    let approveAttempts = 0
    await page.route('**/api/agent/approvals/*/approve', async (route) => {
      approveAttempts += 1
      if (approveAttempts === 1) {
        return route.fulfill({
          status: 401,
          contentType: 'application/json',
          body: JSON.stringify({
            error: { code: 'auth.required', message: 'authentication required', details: {} },
          }),
        })
      }
      // Second attempt (the retry, now "logged in"): succeed and clear the
      // entry so the modal's own re-render reflects a resolved approval.
      mockState.approvals = mockState.approvals.filter((a: any) => a.id !== 'ap-1')
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ approval: { id: 'ap-1', state: 'executed' } }),
      })
    })

    let loginAttempts = 0
    await page.route('**/api/auth/login', async (route) => {
      loginAttempts += 1
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, tier: 'admin' }),
      })
    })

    await page.goto('/')
    await expect(page.getByTestId('tb-bell-badge')).toHaveText('1', { timeout: 6_000 })
    await page.getByTestId('tb-bell').click()
    await page.getByTestId('notif-sec-attention').getByRole('button', { name: 'Review' }).click()
    await expect(page.locator('.approval-modal')).toBeVisible()

    // Approve → 401 → the sign-in drawer appears instead of a silent failure.
    // AuthChallengeDrawer renders NOTHING until a challenge is raised: the
    // Drawer primitive keeps its <aside class="drawer" role="dialog"> mounted
    // whether open or not, so an always-mounted instance at the app root would
    // put a second drawer on every page and every spec that addresses "the
    // drawer" by class or role would hit a strict-mode violation. "Closed" is
    // therefore asserted as absence, not as a missing `.open` class.
    await page.locator('.approval-card').getByRole('button', { name: 'Approve' }).click()
    const drawer = page.locator('aside.drawer', { hasText: 'Sign-in required' })
    await expect(drawer).toHaveClass(/\bopen\b/)
    await expect(drawer).toHaveAttribute('aria-hidden', 'false')
    await expect(drawer).toContainText('reachable from your network')

    // Sign in — the drawer retries the ORIGINAL approve call automatically.
    await page.getByTestId('auth-challenge-key-input').fill('the-admin-key')
    await page.getByTestId('auth-challenge-submit').click()

    await expect(drawer).toHaveCount(0)
    expect(loginAttempts).toBe(1)
    expect(approveAttempts).toBe(2)

    // The retried approve succeeded: the entry is gone from the pending list.
    await expect(page.getByTestId('approvals-empty')).toBeVisible()
  })

  test('dismissing the drawer leaves the original mutation failed (no retry)', async ({
    page,
    mockState,
  }) => {
    mockState.approvals = [
      {
        id: 'ap-2',
        tool: 'slot_delete',
        args: { slot: 'qwen' },
        client_id: 'hermes',
        enqueued_at: Date.now() / 1000 - 30,
        state: 'pending',
      },
    ]

    let approveAttempts = 0
    await page.route('**/api/agent/approvals/*/approve', async (route) => {
      approveAttempts += 1
      return route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({
          error: { code: 'auth.required', message: 'authentication required', details: {} },
        }),
      })
    })

    await page.goto('/')
    await page.getByTestId('tb-bell').click()
    await page.getByTestId('notif-sec-attention').getByRole('button', { name: 'Review' }).click()
    await page.locator('.approval-card').getByRole('button', { name: 'Approve' }).click()

    const drawer = page.locator('aside.drawer', { hasText: 'Sign-in required' })
    await expect(drawer).toHaveClass(/\bopen\b/)
    await page.keyboard.press('Escape')
    await expect(drawer).toHaveCount(0)

    // Only the one refused attempt happened — dismissing never retries.
    expect(approveAttempts).toBe(1)
  })
})
