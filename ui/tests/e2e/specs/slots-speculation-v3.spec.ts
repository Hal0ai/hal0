/**
 * slots-speculation-v3 — ADR-0031 §5: the slot drawer's Speculation line.
 * (The row bolt is unit-tested in dash/__tests__/slot-list.test.tsx.)
 */
import { test, expect } from '../fixtures/apiMock'

const spec = (over: Record<string, unknown>) => ({
  active: false, draft_tokens: 0, accepted_tokens: 0, drafts: 0, acceptance: null,
  mean_accepted_len: null, live_acceptance: null, mode: 'none', kind: null,
  variant: null, unavailable_reason: null, ...over,
})

const slot = (name: string, model_id: string, s: unknown) => ({
  name, type: 'llm', device: 'gpu-rocm', profile: 'rocm', model: model_id, model_id,
  group: 'chat', state: 'serving', port: 8092,
  metrics: { ctx: 8192, toks: 42, ttft: 180, kv: 35, spec: s },
})

const SLOTS = [
  slot('lit', 'with-draft', spec({ active: true, mode: 'draft', kind: 'dflash', variant: 'lilicorr', acceptance: 0.78, mean_accepted_len: 3.4 })),
  slot('off', 'with-draft', spec({ mode: 'none' })),
  slot('warn', 'with-draft', spec({ mode: 'none', kind: 'dflash', variant: 'lilicorr', unavailable_reason: 'needs llama.cpp b11669' })),
  slot('plain', 'no-draft', spec({ mode: 'none' })),
]

const MODELS = [
  { id: 'with-draft', name: 'with-draft', longName: 'With Draft', installed: true, ns: 'pulled', capabilities: ['chat'],
    draft: '/models/d.gguf', draft_kind: 'dflash', metadata: { draft: { variant: 'lilicorr' } } },
  { id: 'no-draft', name: 'no-draft', longName: 'No Draft', installed: true, ns: 'pulled', capabilities: ['chat'] },
]

test.beforeEach(async ({ page }) => {
  // Forced-mock would answer GET /api/slots and /api/models itself; opt out so
  // the routes below are authoritative (see api/mock.ts passthroughRequested).
  await page.addInitScript(() => {
    ;(window as any).__hal0MockPassthrough = ['/api/slots', '/api/models']
  })
  await page.route('**/api/slots', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ slots: SLOTS }) }))
  await page.route('**/api/models', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: MODELS }) }))
  await page.route('**/api/slots/*/resolved', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        name: 'lit', argv: ['llama-server'], provenance: [], removed: 0,
        speculation: { mode: 'draft', kind: 'dflash', variant: 'lilicorr', unavailable_reason: null },
      }),
    }))
})

test.describe('Slot drawer speculation line', () => {
  test('slot drawer states what speculation is in effect', async ({ page }) => {
    await page.goto('/#slots/lit')
    await page.getByTestId('slot-hw-advanced').click()
    await expect(page.getByTestId('slot-spec-line')).toHaveText(
      'Speculation: LiLiCorr · 78% accepted · 3.4 tok/step')
  })
})
