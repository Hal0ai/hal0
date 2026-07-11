/**
 * board-chat-tool-use-v3 — the steward's tool-use UX (2026-07-11 hardening).
 *
 * Covers the operator-facing chat behaviors added alongside the Brain's
 * autonomous slot/model grant:
 *   - tool card status logic: job-shaped results with `error: null` read
 *     "done" (regression: pull-status cards showed a false "error");
 *     truthy `error` reads "error"
 *   - gated calls: pending_approval result + approval_required frame →
 *     amber "awaiting approval" card with inline Approve / Deny
 *   - Approve → POST /api/agent/approvals/{id}/approve → card "approved";
 *     a follow-up tool_result (the resumed turn) flips it to "done"
 *   - Deny → card "denied"
 *   - auto-approve toggle: approval_required auto-POSTs approve
 *   - "new session" clears the thread; Stop appears while streaming
 *
 * SSE frames follow the REAL backend contract (board_chat.py `type` field).
 */

import { test, expect } from '../fixtures/apiMock'

const FIVE_S = 5_500

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    ;(window as any).BoardIcon = () => null
  })
})

async function gotoBoardAndWait(page: any) {
  await page.goto('/#board')
  await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
}

async function openChat(page: any) {
  await page.locator('[data-testid="board-action-chat"]').click()
  await expect(page.locator('[data-testid="board-chat"]')).toBeVisible({ timeout: FIVE_S })
}

function sse(frames: object[]): string {
  return frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join('')
}

async function mockChat(page: any, frames: object[]) {
  await page.route('**/api/board/chat', async (route: any) => {
    if (route.request().method() !== 'POST') {
      await route.fallback()
      return
    }
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body: sse(frames) })
  })
}

async function send(page: any, text: string) {
  await page.locator('[data-testid="board-chat-input"]').fill(text)
  await page.locator('[data-testid="board-chat-send"]').click()
}

const toolCard = (page: any) => page.locator('[data-testid="board-chat-tool"]')

// ── result → status mapping ────────────────────────────────────────────────

test('job-shaped result with error:null reads done, not error', async ({ page }) => {
  await mockChat(page, [
    { type: 'tool_call', id: 'c1', name: 'model_pull_status', arguments: { model_id: 'm' } },
    {
      type: 'tool_result',
      id: 'c1',
      name: 'model_pull_status',
      result: { state: 'running', bytes_downloaded: 1, error: null, error_code: null },
    },
    { type: 'token', text: 'downloading' },
    { type: 'done' },
  ])
  await gotoBoardAndWait(page)
  await openChat(page)
  await send(page, 'check the pull')

  // Assert the STATUS chip, not the whole card — the folded result JSON
  // legitimately contains the literal text `"error": null`.
  await expect(toolCard(page).locator('.tool-status')).toHaveText('done', { timeout: FIVE_S })
})

test('truthy error in result reads error', async ({ page }) => {
  await mockChat(page, [
    { type: 'tool_call', id: 'c1', name: 'model_pull_status', arguments: {} },
    { type: 'tool_result', id: 'c1', name: 'model_pull_status', result: { error: 'boom' } },
    { type: 'done' },
  ])
  await gotoBoardAndWait(page)
  await openChat(page)
  await send(page, 'check')

  await expect(toolCard(page)).toContainText('error', { timeout: FIVE_S })
})

// ── approval gates in the thread ───────────────────────────────────────────

const GATED_FRAMES = [
  { type: 'tool_call', id: 'c1', name: 'model_delete', arguments: { model_id: 'doomed' } },
  {
    type: 'tool_result',
    id: 'c1',
    name: 'model_delete',
    result: { status: 'pending_approval', approval_id: 'ap-123', detail: 'queued' },
  },
  { type: 'approval_required', id: 'c1', name: 'model_delete', approval_id: 'ap-123' },
  { type: 'token', text: 'waiting on you' },
  { type: 'done' },
]

test('gated call renders awaiting-approval card with Approve/Deny', async ({ page }) => {
  await mockChat(page, GATED_FRAMES)
  await gotoBoardAndWait(page)
  await openChat(page)
  await send(page, 'delete it')

  await expect(toolCard(page)).toContainText('awaiting approval', { timeout: FIVE_S })
  await expect(page.locator('[data-testid="board-chat-approve"]')).toBeVisible()
  await expect(page.locator('[data-testid="board-chat-deny"]')).toBeVisible()
})

test('Approve posts to the bell endpoint and marks the card approved', async ({ page }) => {
  await mockChat(page, GATED_FRAMES)
  let approved = false
  await page.route('**/api/agent/approvals/ap-123/approve', async (route: any) => {
    approved = true
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' })
  })
  await gotoBoardAndWait(page)
  await openChat(page)
  await send(page, 'delete it')

  await page.locator('[data-testid="board-chat-approve"]').click()
  await expect(toolCard(page)).toContainText('approved', { timeout: FIVE_S })
  expect(approved).toBe(true)
})

test('Deny marks the card denied', async ({ page }) => {
  await mockChat(page, GATED_FRAMES)
  await page.route('**/api/agent/approvals/ap-123/deny', async (route: any) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' })
  })
  await gotoBoardAndWait(page)
  await openChat(page)
  await send(page, 'delete it')

  await page.locator('[data-testid="board-chat-deny"]').click()
  await expect(toolCard(page)).toContainText('denied', { timeout: FIVE_S })
})

test('auto-approve toggle approves the gate without a click', async ({ page }) => {
  await mockChat(page, GATED_FRAMES)
  let approved = false
  await page.route('**/api/agent/approvals/ap-123/approve', async (route: any) => {
    approved = true
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' })
  })
  await gotoBoardAndWait(page)
  await openChat(page)
  await page.locator('[data-testid="board-chat-auto-approve"] input').check()
  await send(page, 'delete it')

  await expect(toolCard(page)).toContainText('approved', { timeout: FIVE_S })
  expect(approved).toBe(true)
})

test('follow-up tool_result after approval flips the card to done', async ({ page }) => {
  // The paused-turn contract: pending result → approval_required → (operator
  // approves) → SECOND tool_result with the executed payload.
  await mockChat(page, [
    ...GATED_FRAMES.slice(0, 3),
    { type: 'tool_result', id: 'c1', name: 'model_delete', result: { deleted: true } },
    { type: 'token', text: 'gone' },
    { type: 'done' },
  ])
  await gotoBoardAndWait(page)
  await openChat(page)
  await send(page, 'delete it')

  await expect(toolCard(page)).toContainText('done', { timeout: FIVE_S })
  await expect(page.locator('[data-testid="board-chat-approve"]')).toHaveCount(0)
})

// ── session controls ───────────────────────────────────────────────────────

test('new session clears the thread', async ({ page }) => {
  await mockChat(page, [{ type: 'token', text: 'hello there' }, { type: 'done' }])
  await gotoBoardAndWait(page)
  await openChat(page)
  await send(page, 'hi')
  await expect(page.locator('[data-testid="board-chat-msg"]')).toHaveCount(2, {
    timeout: FIVE_S,
  })

  await page.locator('[data-testid="board-chat-new-session"]').click()
  await expect(page.locator('[data-testid="board-chat-msg"]')).toHaveCount(0)
  await expect(page.locator('[data-testid="board-chat-new-session"]')).toHaveCount(0)
})

test('Stop appears while streaming and aborts the turn', async ({ page }) => {
  // Stall the response: streaming stays true while the fetch is pending,
  // which is the window the Stop button exists for.
  await page.route('**/api/board/chat', async (route: any) => {
    if (route.request().method() !== 'POST') {
      await route.fallback()
      return
    }
    await new Promise((resolve) => setTimeout(resolve, 20_000))
    await route.abort().catch(() => {})
  })
  await gotoBoardAndWait(page)
  await openChat(page)
  await send(page, 'go')

  const stop = page.locator('[data-testid="board-chat-stop"]')
  await expect(stop).toBeVisible({ timeout: FIVE_S })
  await stop.click()
  await expect(stop).toHaveCount(0)
  // The thread is kept (the user message), the composer stays usable.
  await expect(page.locator('[data-testid="board-chat-msg"]')).toHaveCount(1)
  await expect(page.locator('[data-testid="board-chat-input"]')).toBeEnabled()
})
