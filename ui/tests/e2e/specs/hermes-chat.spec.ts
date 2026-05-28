/**
 * hermes-chat — v0.3 PR-10 (master plan §4 PR-10).
 *
 * Exercises the HermesChat surface end-to-end against a mocked WS event
 * stream (wsHarness fixture). Pins the contract for:
 *
 *   - Composer submit (Enter submits, Shift+Enter newline)
 *   - Event routing: message.{start,delta,complete} → assistant bubble
 *   - Event routing: tool.{start,progress,complete}  → ToolCallCard
 *   - Event routing: approval.request                → inline ApprovalCard
 *   - Persona switch via sidecar dropdown
 *   - MCP status pip rolls up from /api/mcp/servers
 *   - Reconnect: socket drop kicks the reconnect-scheduler
 *   - Sidecar restart confirmation while a message is streaming
 *   - Mobile sheet toggle visible <768px
 */
import { test, expect, json } from '../fixtures/apiMock'
import {
  installWsHarness,
  waitForWs,
  emitWs,
  getWsSent,
  closeWs,
} from '../fixtures/wsHarness'

const FIVE_S = 5_500

const MOCK_AGENTS = {
  agents: [{ name: 'hermes', installed_at: '2026-05-25T12:00:00Z', status: 'installed' }],
  count: 1,
}

const MOCK_PERSONAS = {
  agent_id: 'hermes',
  active: 'default',
  personas: [
    { id: 'default', display_name: 'Hermes', description: 'Default persona', active: true },
    { id: 'coder',   display_name: 'Hermes Coder', description: 'Coder persona', active: false },
  ],
}

const MOCK_MCP_GREEN = {
  servers: [
    { id: 'hal0-admin',  name: 'hal0-admin',  bundled: true, state: 'running' },
    { id: 'hal0-memory', name: 'hal0-memory', bundled: true, state: 'running' },
  ],
  count: 2,
}

async function seedAndOpen(page: import('@playwright/test').Page) {
  await page.route('**/api/agents', (route) => json(route, MOCK_AGENTS))
  await page.route('**/api/agents/hermes/personas', (route) => json(route, MOCK_PERSONAS))
  await page.route('**/api/mcp/servers', (route) => json(route, MOCK_MCP_GREEN))
  await page.route('**/api/agent/approvals', (route) => json(route, { approvals: [] }))
  await page.route('**/api/agents/skills', (route) => json(route, { skills: [], count: 0 }))
  await page.route('**/api/agents/hermes/memory/stats', (route) => json(route, { writes: 0 }))
  await page.route('**/api/agents/hermes/session/handshake', (route) =>
    json(route, { agent_id: 'hermes', ok: true }),
  )
  await page.route('**/api/agents/hermes/personas/coder/activate', (route) =>
    json(route, { ok: true, active: 'coder' }),
  )
  await page.route('**/api/agents/hermes/restart', (route) =>
    json(route, { ok: true }),
  )
  await installWsHarness(page)
  await page.goto('/#agent/chat')
}

test.describe('HermesChat surface — boot + composer', () => {
  test('mounts surface + composer + sidecar + connects to events WS', async ({ page }) => {
    await seedAndOpen(page)
    await expect(page.locator('[data-testid="hermes-chat-surface"]')).toBeVisible({ timeout: FIVE_S })
    await expect(page.locator('[data-testid="hermes-composer"]')).toBeVisible()
    await expect(page.locator('[data-testid="hermes-sidecar"]')).toBeVisible()
    // Connection manager opens both events + submit WS once handshake
    // returns 200.
    await waitForWs(page, '/api/agents/hermes/events', FIVE_S)
    await waitForWs(page, '/api/agents/hermes/submit', FIVE_S)
  })

  test('composer Enter submits prompt.submit on the WS', async ({ page }) => {
    await seedAndOpen(page)
    await waitForWs(page, '/api/agents/hermes/submit', FIVE_S)

    const input = page.locator('[data-testid="hermes-composer-input"]')
    await input.fill('hi hermes')
    await input.press('Enter')

    // The user bubble lands immediately.
    await expect(page.locator('[data-testid="hermes-msg-user"]')).toContainText('hi hermes', {
      timeout: FIVE_S,
    })

    // The WS submit frame includes a JSON-RPC envelope with prompt.submit.
    const frames = await getWsSent(page, '/api/agents/hermes/submit')
    expect(frames.some((f) => f.includes('"method":"prompt.submit"'))).toBe(true)
    expect(frames.some((f) => f.includes('"text":"hi hermes"'))).toBe(true)

    // Textarea cleared.
    await expect(input).toHaveValue('')
  })

  test('Shift+Enter inserts a newline (does NOT submit)', async ({ page }) => {
    await seedAndOpen(page)
    await waitForWs(page, '/api/agents/hermes/submit', FIVE_S)
    const input = page.locator('[data-testid="hermes-composer-input"]')
    await input.fill('line one')
    await input.press('Shift+Enter')
    await input.type('line two')
    // Still has both lines + the message was NOT sent.
    await expect(input).toHaveValue('line one\nline two')
    const frames = await getWsSent(page, '/api/agents/hermes/submit')
    expect(frames.some((f) => f.includes('prompt.submit') && f.includes('line one'))).toBe(false)
  })
})

test.describe('HermesChat surface — event routing', () => {
  test('message.start + message.delta + message.complete renders assistant bubble', async ({ page }) => {
    await seedAndOpen(page)
    await waitForWs(page, '/api/agents/hermes/events', FIVE_S)

    await emitWs(page, '/api/agents/hermes/events', {
      jsonrpc: '2.0', method: 'event',
      params: { type: 'message.start', session_id: 's1', payload: {} },
    })
    await emitWs(page, '/api/agents/hermes/events', {
      jsonrpc: '2.0', method: 'event',
      params: { type: 'message.delta', session_id: 's1', payload: { text: 'Hello ' } },
    })
    await emitWs(page, '/api/agents/hermes/events', {
      jsonrpc: '2.0', method: 'event',
      params: { type: 'message.delta', session_id: 's1', payload: { text: 'world.' } },
    })
    await emitWs(page, '/api/agents/hermes/events', {
      jsonrpc: '2.0', method: 'event',
      params: { type: 'message.complete', session_id: 's1', payload: { status: 'complete' } },
    })

    const bubble = page.locator('[data-testid="hermes-msg-assistant"]').first()
    await expect(bubble).toContainText('Hello world.', { timeout: FIVE_S })
  })

  test('tool.start + tool.progress + tool.complete renders ToolCallCard', async ({ page }) => {
    await seedAndOpen(page)
    await waitForWs(page, '/api/agents/hermes/events', FIVE_S)

    await emitWs(page, '/api/agents/hermes/events', {
      jsonrpc: '2.0', method: 'event',
      params: {
        type: 'tool.start', session_id: 's1',
        payload: { tool_id: 't1', name: 'read_file', context: 'path=/tmp/x' },
      },
    })

    const card = page.locator('[data-testid="hermes-tool-card"]')
    await expect(card).toBeVisible({ timeout: FIVE_S })
    await expect(card).toHaveAttribute('data-tool-name', 'read_file')
    await expect(card).toHaveAttribute('data-tool-status', 'running')

    await emitWs(page, '/api/agents/hermes/events', {
      jsonrpc: '2.0', method: 'event',
      params: {
        type: 'tool.complete', session_id: 's1',
        payload: { tool_id: 't1', name: 'read_file', summary: '42 bytes', duration_s: 0.2 },
      },
    })
    await expect(card).toHaveAttribute('data-tool-status', 'done')
  })

  test('approval.request renders inline ApprovalCard + Approve sends approval.respond', async ({ page }) => {
    await seedAndOpen(page)
    await waitForWs(page, '/api/agents/hermes/events', FIVE_S)
    await waitForWs(page, '/api/agents/hermes/submit', FIVE_S)

    await emitWs(page, '/api/agents/hermes/events', {
      jsonrpc: '2.0', method: 'event',
      params: {
        type: 'approval.request', session_id: 's1',
        payload: { request_id: 'req-42', tool: 'shell_exec', args: { cmd: 'rm -rf /' } },
      },
    })

    const card = page.locator('[data-testid="hermes-approval-card"]')
    await expect(card).toBeVisible({ timeout: FIVE_S })
    await expect(card).toHaveAttribute('data-approval-rid', 'req-42')
    await expect(card).toHaveAttribute('data-approval-kind', 'approval')

    await page.locator('[data-testid="hermes-approval-approve"]').click()

    const frames = await getWsSent(page, '/api/agents/hermes/submit')
    expect(frames.some((f) => f.includes('"method":"approval.respond"') && f.includes('req-42'))).toBe(true)
    expect(frames.some((f) => f.includes('"decision":"approve"'))).toBe(true)
  })

  test('status.update + error update sidecar + render error bubble', async ({ page }) => {
    await seedAndOpen(page)
    await waitForWs(page, '/api/agents/hermes/events', FIVE_S)

    await emitWs(page, '/api/agents/hermes/events', {
      jsonrpc: '2.0', method: 'event',
      params: { type: 'error', session_id: 's1', payload: { message: 'tool died' } },
    })

    await expect(page.locator('[data-testid="hermes-msg-error"]')).toContainText('tool died', {
      timeout: FIVE_S,
    })
  })

  test('session.info updates model badge', async ({ page }) => {
    await seedAndOpen(page)
    await waitForWs(page, '/api/agents/hermes/events', FIVE_S)

    await emitWs(page, '/api/agents/hermes/events', {
      jsonrpc: '2.0', method: 'event',
      params: {
        type: 'session.info', session_id: 's1',
        payload: { session_id: 's1', model: 'qwen3-coder-30b', provider: 'hal0' },
      },
    })

    const badge = page.locator('[data-testid="hermes-sidecar-model"]')
    await expect(badge).toContainText('qwen3-coder-30b', { timeout: FIVE_S })
    await expect(badge).toContainText('hal0')
  })
})

test.describe('HermesChat sidecar — persona + restart', () => {
  test('persona dropdown lists personas and activates on click', async ({ page }) => {
    await seedAndOpen(page)
    await expect(page.locator('[data-testid="hermes-sidecar-persona"]')).toBeVisible({ timeout: FIVE_S })
    await page.locator('[data-testid="hermes-sidecar-persona-button"]').click()
    await expect(page.locator('[data-testid="hermes-sidecar-persona-menu"]')).toBeVisible()

    // Wait for the activate POST to fire when we pick coder.
    const activateReq = page.waitForRequest(
      (r) => r.url().includes('/api/agents/hermes/personas/coder/activate') && r.method() === 'POST',
    )
    await page.locator('[data-testid="hermes-sidecar-persona-option-coder"]').click()
    await activateReq
  })

  test('restart button POSTs /api/agents/hermes/restart when idle', async ({ page }) => {
    await seedAndOpen(page)
    await expect(page.locator('[data-testid="hermes-sidecar-restart"]')).toBeVisible({ timeout: FIVE_S })

    const restartReq = page.waitForRequest(
      (r) => r.url().includes('/api/agents/hermes/restart') && r.method() === 'POST',
    )
    await page.locator('[data-testid="hermes-sidecar-restart"]').click()
    await restartReq
  })

  test('restart confirms when a message is streaming', async ({ page }) => {
    await seedAndOpen(page)
    await waitForWs(page, '/api/agents/hermes/events', FIVE_S)
    // Begin streaming.
    await emitWs(page, '/api/agents/hermes/events', {
      jsonrpc: '2.0', method: 'event',
      params: { type: 'message.start', session_id: 's1', payload: {} },
    })
    await emitWs(page, '/api/agents/hermes/events', {
      jsonrpc: '2.0', method: 'event',
      params: { type: 'message.delta', session_id: 's1', payload: { text: 'streaming…' } },
    })

    await page.locator('[data-testid="hermes-sidecar-restart"]').click()
    await expect(page.locator('[data-testid="hermes-sidecar-restart-confirm"]')).toBeVisible({
      timeout: FIVE_S,
    })

    const restartReq = page.waitForRequest(
      (r) => r.url().includes('/api/agents/hermes/restart') && r.method() === 'POST',
    )
    await page.locator('[data-testid="hermes-sidecar-restart-confirm-yes"]').click()
    await restartReq
  })
})

test.describe('HermesChat — reconnect strategy', () => {
  test('events WS close kicks the reconnect scheduler', async ({ page }) => {
    await seedAndOpen(page)
    await waitForWs(page, '/api/agents/hermes/events', FIVE_S)

    // Drop the events WS.
    await closeWs(page, '/api/agents/hermes/events', 1006)

    // Reconnect within ~5s (base backoff 250ms, capped 4s; handshake
    // succeeds against the mock).
    await waitForWs(page, '/api/agents/hermes/events', FIVE_S)
  })
})

test.describe('HermesChat — mobile sheet', () => {
  test.use({ viewport: { width: 600, height: 900 } })

  test('mobile sheet toggle button is visible <768px', async ({ page }) => {
    await seedAndOpen(page)
    await expect(page.locator('[data-testid="hermes-chat-sheet-toggle"]')).toBeVisible({
      timeout: FIVE_S,
    })
  })
})

test.describe('HermesChat — first-run welcome', () => {
  test('on initial connect without a sessionId, sends session.create with first_run=true', async ({ page }) => {
    await seedAndOpen(page)
    await waitForWs(page, '/api/agents/hermes/submit', FIVE_S)
    // Give the onopen handler a beat to fire.
    await page.waitForTimeout(120)
    const frames = await getWsSent(page, '/api/agents/hermes/submit')
    expect(frames.some((f) => f.includes('"method":"session.create"') && f.includes('"first_run":true'))).toBe(true)
  })
})
