/**
 * quickchat-retry-v3 (GH #1469 item 5) — QuickChatCard error recovery.
 *
 * On a stream error, `send()` had already cleared the operator's typed
 * message (`setInput('')` runs unconditionally at the end of `send()`,
 * before the async `openChatStream` call can possibly fail) and offered no
 * way to resend it — despite the backend shipping a `retry_after_s` hint
 * in the structured error envelope (`hal0.errors.Hal0Error`'s `details`,
 * see `src/hal0/api/middleware/error_codes.py`) for exactly this case.
 *
 * Pinned here:
 *   1. the message survives a failed send — it reappears in the input box,
 *      not just vanishes;
 *   2. `retry_after_s`, when present, is surfaced to the operator;
 *   3. a Retry control resends without retyping.
 */
import { test, expect } from '../fixtures/apiMock'

const LAYOUT_WITH_QUICKCHAT = {
  v: 3,
  cells: {
    memory: 'memorybar',
    a1: 'throughput',
    a2: 'utilization',
    a3: 'requests',
    slots: 'slots',
    c1: 'activity',
    c2: 'quickchat',
    c3: 'attention',
  },
  quickActions: true,
}

function errorEnvelope(message: string, details: Record<string, unknown> = {}) {
  return JSON.stringify({ error: { code: 'slot.crash_looping', message, details } })
}

async function gotoDashboardWithQuickChat(page: import('@playwright/test').Page) {
  await page.route('**/api/user/dashboard-layout', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(LAYOUT_WITH_QUICKCHAT),
    })
  })
  await page.goto('/#dashboard')
  await expect(page.locator('.qc-textarea')).toBeVisible({ timeout: 10_000 })
}

test.describe('QuickChatCard — error recovery (#1469)', () => {
  test('a failed send restores the message instead of discarding it', async ({ page }) => {
    await page.route('**/v1/chat/completions', (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: errorEnvelope('slot is crash-looping', { retry_after_s: 30 }),
      }),
    )
    await gotoDashboardWithQuickChat(page)

    const textarea = page.locator('.qc-textarea')
    await textarea.fill('what is the meaning of slots?')
    await page.locator('.qc-send').click()

    await expect(page.locator('.qc-error')).toBeVisible({ timeout: 10_000 })
    // The message must come back — not be lost because the send failed.
    await expect(textarea).toHaveValue('what is the meaning of slots?')
  })

  test('retry_after_s from the error envelope is surfaced to the operator', async ({ page }) => {
    await page.route('**/v1/chat/completions', (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: errorEnvelope('slot is crash-looping', { retry_after_s: 30 }),
      }),
    )
    await gotoDashboardWithQuickChat(page)

    await page.locator('.qc-textarea').fill('ping')
    await page.locator('.qc-send').click()

    await expect(page.locator('.qc-error')).toContainText('30')
  })

  test('the Retry control resends the same message without retyping', async ({ page }) => {
    let calls = 0
    await page.route('**/v1/chat/completions', async (route) => {
      calls += 1
      if (calls === 1) {
        return route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: errorEnvelope('slot is crash-looping', { retry_after_s: 1 }),
        })
      }
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'data: {"choices":[{"delta":{"content":"pong"}}]}\n\ndata: [DONE]\n\n',
      })
    })
    await gotoDashboardWithQuickChat(page)

    await page.locator('.qc-textarea').fill('ping')
    await page.locator('.qc-send').click()
    await expect(page.locator('.qc-error')).toBeVisible({ timeout: 10_000 })

    await page.locator('.qc-retry').click()

    await expect(page.locator('.qc-message .qc-text')).toContainText('pong', { timeout: 10_000 })
    expect(calls).toBe(2)
  })
})
