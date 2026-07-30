/**
 * board-chat-error-surfacing-v3 — pre-stream + network failures on the
 * board/brain chat surface (issue #1452).
 *
 * Prior behaviour: useBoard.ts's `send()` swallowed every HTTP-level
 * failure silently —
 *   `if (!res.ok || !res.body) { setStreaming(false); return }`
 * after the POST to ENDPOINTS.boardChat, and the network-error `.catch`
 * was equally silent for non-abort errors. Only in-stream SSE `error`
 * frames got a bubble. A pre-stream 503 (slot warming), 502 (crash-loop),
 * 401, or a dead gateway vanished — the operator's composed message
 * disappeared with no explanation and no way to retry without retyping.
 *
 * This spec pins the fix: `!res.ok` and the fetch `.catch` (non-abort)
 * paths now lift the backend's `{error:{code,message,details}}` envelope
 * (see src/api/client.ts's `readErrorEnvelope`, shared with useBoard.ts)
 * and append an assistant/error bubble mirroring the existing in-stream
 * SSE `error` frame path, including a `retry_after_s` hint and a Retry
 * affordance that resends the operator's original text.
 *
 * An aborted turn (operator hits Stop) must NOT produce an error bubble —
 * that is an intentional cancellation, not a failure.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const FIVE_S = 5_500

/** The backend's structured error envelope (hal0.api.middleware.error_codes). */
function envelope(code: string, message: string, details: Record<string, unknown> = {}) {
  return JSON.stringify({ error: { code, message, details } })
}

// BoardIcon stub required — see board-chat-v3.spec.ts header for the full
// chrome.jsx/import-order explanation this is proven necessary by.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    ;(window as any).BoardIcon = () => null
  })
})

async function gotoBoardAndWait(page: Page) {
  await page.goto('/#board')
  await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
}

async function openChat(page: Page) {
  await page.locator('[data-testid="board-action-chat"]').click()
  await expect(page.locator('[data-testid="board-chat"]')).toBeVisible({ timeout: FIVE_S })
}

test.describe('BoardView — agent chat error surfacing (#1452)', () => {
  test('A — pre-stream 503 slot.loading envelope surfaces an error bubble with the retry hint, and the composed message is preserved', async ({
    page,
  }) => {
    await page.route('**/api/board/chat', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: envelope(
          'slot.loading',
          "container slot 'brain' is not ready (starting)",
          { slot: 'brain', state: 'starting', retry_after_s: 15 },
        ),
      })
    })

    await gotoBoardAndWait(page)
    await openChat(page)

    const input = page.locator('[data-testid="board-chat-input"]')
    await input.fill('what is blocked right now?')
    await page.locator('[data-testid="board-chat-send"]').click()

    const msgs = page.locator('[data-testid="board-chat-msg"]')
    // The operator's turn stays visible — not answered by silence.
    await expect(msgs.first()).toContainText('what is blocked right now?', { timeout: FIVE_S })

    // An error bubble appears carrying the envelope message + retry hint.
    const errorMsg = msgs.nth(1)
    await expect(errorMsg).toBeVisible({ timeout: FIVE_S })
    await expect(errorMsg).toContainText("container slot 'brain' is not ready (starting)")
    await expect(errorMsg).toContainText('15')

    // A Retry affordance is offered so the operator doesn't have to retype.
    await expect(errorMsg.locator('[data-testid="board-chat-retry"]')).toBeVisible()

    // Streaming indicator must not be stuck on.
    await expect(page.locator('[data-testid="board-chat-stop"]')).not.toBeVisible()
  })

  test('B — a network-level failure (connection reset) also surfaces an error bubble, not silence', async ({
    page,
  }) => {
    await page.route('**/api/board/chat', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback()
        return
      }
      await route.abort('connectionreset')
    })

    await gotoBoardAndWait(page)
    await openChat(page)

    const input = page.locator('[data-testid="board-chat-input"]')
    await input.fill('is the gateway up?')
    await page.locator('[data-testid="board-chat-send"]').click()

    const msgs = page.locator('[data-testid="board-chat-msg"]')
    await expect(msgs.first()).toContainText('is the gateway up?', { timeout: FIVE_S })

    const errorMsg = msgs.nth(1)
    await expect(errorMsg).toBeVisible({ timeout: FIVE_S })
    // Generic but non-empty — the point is silence is gone, not the exact wording.
    await expect(errorMsg).not.toHaveText('')
    await expect(errorMsg.locator('[data-testid="board-chat-retry"]')).toBeVisible()
  })

  test('C — clicking Retry on an error bubble resends the original composed text', async ({ page }) => {
    let attempts = 0
    await page.route('**/api/board/chat', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback()
        return
      }
      attempts += 1
      if (attempts === 1) {
        await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: envelope('slot.loading', 'brain slot still warming', { retry_after_s: 5 }),
        })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'data: {"type":"token","text":"back online"}\n\ndata: {"type":"done"}\n\n',
      })
    })

    await gotoBoardAndWait(page)
    await openChat(page)

    await page.locator('[data-testid="board-chat-input"]').fill('status check')
    await page.locator('[data-testid="board-chat-send"]').click()

    const msgs = page.locator('[data-testid="board-chat-msg"]')
    const errorMsg = msgs.nth(1)
    await expect(errorMsg).toBeVisible({ timeout: FIVE_S })

    await errorMsg.locator('[data-testid="board-chat-retry"]').click()

    // Retry resends as a genuine new turn: a fresh user bubble (the same
    // text) followed by the successful reply.
    await expect(msgs.nth(2)).toContainText('status check', { timeout: FIVE_S })
    await expect(msgs.nth(3)).toContainText('back online', { timeout: FIVE_S })
    expect(attempts).toBe(2)
  })

  test('D — stopping an in-flight turn does NOT produce an error bubble', async ({ page }) => {
    await page.route('**/api/board/chat', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback()
        return
      }
      // Simulate a slow backend: delay long enough for the operator to hit Stop.
      await new Promise((resolve) => setTimeout(resolve, 2000))
      try {
        await route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: 'data: {"type":"token","text":"too late"}\n\ndata: {"type":"done"}\n\n',
        })
      } catch {
        // Request was aborted client-side before this resolved — expected.
      }
    })

    await gotoBoardAndWait(page)
    await openChat(page)

    await page.locator('[data-testid="board-chat-input"]').fill('long running query')
    await page.locator('[data-testid="board-chat-send"]').click()

    const stopBtn = page.locator('[data-testid="board-chat-stop"]')
    await expect(stopBtn).toBeVisible({ timeout: FIVE_S })
    await stopBtn.click()

    // Give the aborted fetch's rejection handler time to (not) run.
    await page.waitForTimeout(2500)

    const msgs = page.locator('[data-testid="board-chat-msg"]')
    // Only the operator's own message — no error bubble, no phantom reply.
    await expect(msgs).toHaveCount(1)
    await expect(page.locator('[data-testid="board-chat-stop"]')).not.toBeVisible()
  })
})
