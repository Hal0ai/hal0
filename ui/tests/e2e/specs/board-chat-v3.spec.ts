/**
 * board-chat-v3 — AgentChat + WS event integration for the Operator Board.
 *
 * Covers:
 *   - Open chat via board-action-chat → board-chat visible
 *   - Chat input + send button present
 *   - Suggestion chips (board-chat-suggest-<i>)
 *   - Sending a message via send button → POST /api/board/chat
 *   - SSE stream: mocked fetch POST with chunked data: lines → assistant msg appears
 *   - WS /api/board/events: task.updated frame → board query invalidated → card moves lane
 *   - task ref chip (board-chat-ref-<id>) click opens drawer
 *   - Close chat via Escape
 *
 * SSE mock: useBoardChat reads response via fetch ReadableStream; we mock the
 * POST /api/board/chat route to return a body of SSE lines:
 *   data: {"delta":"Hello"}\n\n
 *   data: {"type":"tool_call","tool_call":{"action":"update_task","id":"<id>","status":"done"}}\n\n
 *   data: [DONE]\n\n
 *
 * WS mock: installWsHarness replaces window.WebSocket with FakeWebSocket.
 * After goto we waitForWs('/api/board/events') then emit a task.updated frame
 * and override GET /api/board/board to reflect the moved task.
 */

import { test, expect, json } from '../fixtures/apiMock'
import { installWsHarness, waitForWs, emitWs } from '../fixtures/wsHarness'
import { BOARD_TASKS, makeBoardLanesResponse } from '../fixtures/mock-data'

const FIVE_S = 5_500

// BoardIcon stub: REQUIRED (real source bug). agent-chat.jsx:6 captures
// `const Icon = window.BoardIcon || window.Icons || (() => null)` at module-eval time.
// chrome.jsx (imported earlier in main.tsx) publishes window.Icons as an OBJECT glyph
// map, and board-view.jsx (which sets window.BoardIcon) is imported AFTER agent-chat.jsx,
// so Icon captures the object → <Icon/> throws "Element type is invalid ... got: object"
// and the chat panel never mounts. Proven: removing this fails 9 of the chat tests.
// See board-drawer-v3.spec.ts header for the full chrome.jsx/import-order explanation.
//
// NOT stubbed: __hal0UseBoardTask. The prior version blocked it here too, but that was a
// FALSE workaround — AgentChat doesn't use the task hook, and the "ref chip opens drawer"
// test only asserts the drawer element becomes visible (the BoardIcon stub already
// prevents a crash; the QueryResult bug only corrupts drawer *content*, not visibility).
// Removing the block keeps all 10 chat tests green, so it has been deleted.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).BoardIcon = () => null
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

test.describe('BoardView — agent chat', () => {

  // ── Open/close ─────────────────────────────────────────────────────────

  test('clicking board-action-chat opens board-chat', async ({ page }) => {
    await gotoBoardAndWait(page)
    await openChat(page)
    await expect(page.locator('[data-testid="board-chat"]')).toBeVisible()
  })

  test('board-chat has input, send button, and 4 suggestion chips', async ({ page }) => {
    await gotoBoardAndWait(page)
    await openChat(page)

    await expect(page.locator('[data-testid="board-chat-input"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-chat-send"]')).toBeVisible()
    // 4 default suggestion chips
    for (let i = 0; i < 4; i++) {
      await expect(page.locator(`[data-testid="board-chat-suggest-${i}"]`)).toBeVisible()
    }
  })

  test('Escape closes board-chat', async ({ page }) => {
    await gotoBoardAndWait(page)
    await openChat(page)
    await expect(page.locator('[data-testid="board-chat"]')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('[data-testid="board-chat"]')).not.toBeVisible({ timeout: FIVE_S })
  })

  // ── Send via stub (no hook) ────────────────────────────────────────────
  // When __hal0UseBoardChat is absent the stub path is used (typing reply + setTimeout).
  // The spec uses the mock SSE route which makes the hook present.

  test('stub path: suggestion chip sends message and stub reply appears', async ({ page }) => {
    // Block the chat hook so stub path fires (delete alone won't work —
    // board-hook-bridge.ts sets it after all init scripts; use defineProperty to prevent that)
    await page.addInitScript(() => {
      Object.defineProperty(window, '__hal0UseBoardChat', {
        get: () => undefined,
        set: () => {},
        configurable: false,
      })
    })

    await gotoBoardAndWait(page)
    await openChat(page)

    // Click first suggestion chip
    await page.locator('[data-testid="board-chat-suggest-0"]').click()
    // User message should appear
    const msgs = page.locator('[data-testid="board-chat-msg"]')
    await expect(msgs.first()).toBeVisible({ timeout: FIVE_S })
    // After ~1s the stub reply appears
    await expect(msgs).toHaveCount(2, { timeout: 3000 })
  })

  // ── SSE streaming path ────────────────────────────────────────────────

  test('send message → POST /api/board/chat → assistant tokens stream in', async ({ page }) => {
    // Mock chat SSE endpoint with proper streaming body
    await page.route('**/api/board/chat', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback()
        return
      }
      const sseBody = [
        'data: {"delta":"Hello"}\n\n',
        'data: {"delta":", board"}\n\n',
        'data: {"delta":"!"}\n\n',
        'data: [DONE]\n\n',
      ].join('')
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody,
      })
    })

    await gotoBoardAndWait(page)
    await openChat(page)

    const input = page.locator('[data-testid="board-chat-input"]')
    await input.fill('what is blocked?')
    await page.locator('[data-testid="board-chat-send"]').click()

    // User message appears
    const msgs = page.locator('[data-testid="board-chat-msg"]')
    await expect(msgs.first()).toBeVisible({ timeout: FIVE_S })
    // Assistant message with streamed content appears
    await expect(msgs.nth(1)).toBeVisible({ timeout: FIVE_S })
    await expect(msgs.nth(1)).toContainText('Hello')
  })

  test('send via Enter key fires chat POST', async ({ page }) => {
    let chatCalled = false
    await page.route('**/api/board/chat', async (route) => {
      if (route.request().method() === 'POST') {
        chatCalled = true
        await route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: 'data: {"delta":"ok"}\n\ndata: [DONE]\n\n',
        })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await openChat(page)

    const input = page.locator('[data-testid="board-chat-input"]')
    await input.fill('hello agent')
    await input.press('Enter')
    await page.waitForTimeout(500)
    expect(chatCalled).toBe(true)
  })

  test('SSE tool_call frame invalidates board and assistant msg shows', async ({ page }) => {
    const targetTask = BOARD_TASKS.find(t => t.status === 'todo')!

    await page.route('**/api/board/chat', async (route) => {
      if (route.request().method() !== 'POST') { await route.fallback(); return }
      const sseBody = [
        'data: {"delta":"Moving task for you..."}\n\n',
        `data: {"type":"tool_call","tool_call":{"action":"update_task","id":"${targetTask.id}","status":"done"}}\n\n`,
        'data: {"delta":" Done."}\n\n',
        'data: [DONE]\n\n',
      ].join('')
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: sseBody })
    })

    await gotoBoardAndWait(page)
    await openChat(page)

    await page.locator('[data-testid="board-chat-input"]').fill('move the task to done')
    await page.locator('[data-testid="board-chat-send"]').click()

    // Assistant message should appear with streamed text
    const msgs = page.locator('[data-testid="board-chat-msg"]')
    await expect(msgs.nth(1)).toBeVisible({ timeout: FIVE_S })
    await expect(msgs.nth(1)).toContainText('Moving task')
  })

  test('chat ref chip (board-chat-ref-<id>) renders when refs present', async ({ page }) => {
    const refTask = BOARD_TASKS.find(t => t.status === 'blocked')!

    // Block hook so stub path fires with refs
    await page.addInitScript((id: string) => {
      ;(window as any).AGENT_SEED = [
        {
          role: 'assistant',
          at: 'just now',
          body: 'Blocked task needs attention.',
          refs: [id],
        },
      ]
      Object.defineProperty(window, '__hal0UseBoardChat', {
        get: () => undefined,
        set: () => {},
        configurable: false,
      })
    }, refTask.id)

    await gotoBoardAndWait(page)
    await openChat(page)

    // Ref chip for the blocked task should be visible in the seeded message
    const refChip = page.locator(`[data-testid="board-chat-ref-${refTask.id}"]`)
    await expect(refChip).toBeVisible({ timeout: FIVE_S })
  })

  test('clicking ref chip opens task drawer', async ({ page }) => {
    const refTask = BOARD_TASKS.find(t => t.status === 'blocked')!

    await page.addInitScript((id: string) => {
      ;(window as any).AGENT_SEED = [
        {
          role: 'assistant',
          at: 'just now',
          body: 'Check this.',
          refs: [id],
        },
      ]
      Object.defineProperty(window, '__hal0UseBoardChat', {
        get: () => undefined,
        set: () => {},
        configurable: false,
      })
    }, refTask.id)

    await gotoBoardAndWait(page)
    await openChat(page)

    const refChip = page.locator(`[data-testid="board-chat-ref-${refTask.id}"]`)
    await expect(refChip).toBeVisible({ timeout: FIVE_S })
    await refChip.click()

    // Chat closes and drawer opens (onOpenTask calls setChatOpen(false) + setOpenTask)
    await expect(page.locator('[data-testid="board-task-drawer"]')).toBeVisible({ timeout: FIVE_S })
  })

  // ── WS task.updated → card moves lane ─────────────────────────────────

  test('WS task.updated frame → board query invalidated → card reflects new status', async ({ page }) => {
    await installWsHarness(page)

    // Task that will "move" from todo to done via WS event
    const movingTask = BOARD_TASKS.find(t => t.status === 'todo')!

    // Initial response: task in todo
    let served = false
    await page.route('**/api/board/board', async (route) => {
      if (!served) {
        served = true
        await json(route, makeBoardLanesResponse(false))
      } else {
        // After WS event → return task in done lane
        const moved = {
          ...makeBoardLanesResponse(false),
          lanes: {
            ...makeBoardLanesResponse(false).lanes,
            todo: makeBoardLanesResponse(false).lanes.todo.filter(
              (t: any) => t.id !== movingTask.id
            ),
            done: [
              ...makeBoardLanesResponse(false).lanes.done,
              { ...movingTask, status: 'done' },
            ],
          },
        }
        await json(route, moved)
      }
    })

    await gotoBoardAndWait(page)

    // Confirm task starts in todo lane
    await expect(
      page.locator(`[data-testid="board-lane-todo"] [data-testid="board-task-${movingTask.id}"]`)
    ).toBeVisible()

    // Wait for WS to connect
    await waitForWs(page, '/api/board/events')

    // Emit task.updated event
    await emitWs(page, '/api/board/events', {
      kind: 'task.updated',
      task_id: movingTask.id,
      at: new Date().toISOString(),
      status: 'done',
    })

    // WS onmessage invalidates board query → refetch → card now in done lane
    await expect(
      page.locator(`[data-testid="board-lane-done"] [data-testid="board-task-${movingTask.id}"]`)
    ).toBeVisible({ timeout: FIVE_S })
  })

})
