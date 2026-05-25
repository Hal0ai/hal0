/**
 * chat-reasoning — covers the reasoning/answer separation that landed in
 * feat/chat-thinking-separator.
 *
 * Scenarios:
 *   1. Reasoning block renders when SSE delta.reasoning_content arrives.
 *   2. Reasoning block does NOT render when the model only emits content.
 *   3. While reasoning streams but answer is empty, the block is expanded
 *      (the `.bubble-reasoning.open` class is on). When the answer starts
 *      streaming, the block auto-collapses back to the 3-line clamp.
 *   4. The user's manual click on the toggle wins over auto-collapse.
 *
 * Each test mounts a stubbed SSE stream for /v1/chat/completions; we don't
 * exercise lemond directly. The Composer is driven by the existing chat hook
 * (useChat → streamChatCompletion), so the only thing under test here is
 * "the right buffers go to the right DOM nodes".
 */
import { test, expect } from '../fixtures/apiMock'

// Build an OpenAI-style SSE body from an ordered list of (kind, text) chunks.
function sseStream(chunks: Array<{ kind: 'content' | 'reasoning'; text: string }>): string {
  const lines: string[] = []
  for (const c of chunks) {
    const delta =
      c.kind === 'content'
        ? { content: c.text }
        : { reasoning_content: c.text }
    const evt = {
      id: 'chat-mock',
      model: 'mock-model',
      choices: [{ index: 0, delta }],
    }
    lines.push(`data: ${JSON.stringify(evt)}\n\n`)
  }
  lines.push('data: [DONE]\n\n')
  return lines.join('')
}

async function stubChat(page: Parameters<typeof test.extend>[0] extends never ? any : any, body: string) {
  await page.route('**/v1/chat/completions', async (route: any) => {
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body,
    })
  })
}

// Common: drive the composer with a single user message and wait for the
// assistant bubble to mount.
async function sendPrompt(page: any, text: string) {
  const input = page.locator('.composer-input').first()
  await expect(input).toBeVisible()
  await input.fill(text)
  await input.press('Enter')
  // Wait for the assistant message row to mount.
  await expect(page.locator('.msg').filter({ hasText: 'assistant' }).first()).toBeVisible()
}

test.describe('chat reasoning surface', () => {
  test('reasoning block renders when reasoning_content arrives', async ({ page }) => {
    await stubChat(
      page,
      sseStream([
        { kind: 'reasoning', text: 'Step 1: understand the question. ' },
        { kind: 'reasoning', text: 'Step 2: form the answer.' },
        { kind: 'content', text: 'The sky is blue because of Rayleigh scattering.' },
      ]),
    )
    await page.goto('/')
    await sendPrompt(page, 'why is the sky blue?')
    // Final state: reasoning block is present, contains both reasoning chunks,
    // and the answer bubble holds the content text.
    const reasoning = page.locator('.bubble-reasoning')
    await expect(reasoning).toBeVisible()
    await expect(reasoning.locator('.text')).toContainText('Step 1')
    await expect(reasoning.locator('.text')).toContainText('Step 2')
    // The answer bubble (non-user, non-reasoning) shows the final content.
    const answerBubble = page
      .locator('.msg:not(.user) .bubble')
      .filter({ hasText: 'Rayleigh scattering' })
    await expect(answerBubble).toBeVisible()
  })

  test('reasoning block does NOT render when only content arrives', async ({ page }) => {
    await stubChat(
      page,
      sseStream([
        { kind: 'content', text: 'OK' },
      ]),
    )
    await page.goto('/')
    await sendPrompt(page, 'reply with the exact word OK')
    // Answer bubble is present…
    await expect(
      page.locator('.msg:not(.user) .bubble').filter({ hasText: 'OK' }),
    ).toBeVisible()
    // …but no reasoning block exists for this message.
    await expect(page.locator('.bubble-reasoning')).toHaveCount(0)
  })

  test('reasoning collapsed by default after stream completes (3-line clamp on)', async ({
    page,
  }) => {
    // Long reasoning text so the clamp is visible — multiple sentences/lines.
    const longReasoning =
      'Line A reasoning. Line B reasoning. Line C reasoning. Line D reasoning. ' +
      'Line E reasoning. Line F reasoning. Line G reasoning.'
    await stubChat(
      page,
      sseStream([
        { kind: 'reasoning', text: longReasoning },
        { kind: 'content', text: 'final answer here' },
      ]),
    )
    await page.goto('/')
    await sendPrompt(page, 'q')
    const reasoning = page.locator('.bubble-reasoning')
    await expect(reasoning).toBeVisible()
    // After the answer arrived, the block should NOT have the `.open` class
    // (auto-collapse fired). The toggle should read "thinking ▾".
    await expect(reasoning).not.toHaveClass(/\bopen\b/)
    const toggle = reasoning.locator('.toggle')
    await expect(toggle).toHaveText(/thinking\s+▾/)
    // CSS-level confirmation: the .text element clamps to 3 lines.
    await expect(reasoning.locator('.text')).toHaveCSS('-webkit-line-clamp', '3')
  })

  test('clicking toggle expands the reasoning block (and the choice sticks)', async ({
    page,
  }) => {
    await stubChat(
      page,
      sseStream([
        { kind: 'reasoning', text: 'expand me to see the full thought trace.' },
        { kind: 'content', text: 'done' },
      ]),
    )
    await page.goto('/')
    await sendPrompt(page, 'q')
    const reasoning = page.locator('.bubble-reasoning')
    await expect(reasoning).toBeVisible()
    // Starts collapsed (answer already streamed).
    await expect(reasoning).not.toHaveClass(/\bopen\b/)
    // Click → expands.
    await reasoning.locator('.toggle').click()
    await expect(reasoning).toHaveClass(/\bopen\b/)
    await expect(reasoning.locator('.toggle')).toHaveText(/thinking\s+▴/)
    // Click again → collapses.
    await reasoning.locator('.toggle').click()
    await expect(reasoning).not.toHaveClass(/\bopen\b/)
  })
})
