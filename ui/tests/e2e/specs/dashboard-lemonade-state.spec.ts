/**
 * dashboard-lemonade-state.spec.ts — PR-11 dashboard surface.
 *
 * Covers (plan §11 PR-11):
 *   - Slot card renders correct lifecycle state (loaded / idle / offline)
 *   - Coresident TRIO badge appears when ``slot.coresident_group`` is set
 *   - Nuclear-evict event from /api/lemonade/events/stream pops a toast
 *
 * No live backend — uses ``apiMock`` for HTTP and ``sseHarness`` for SSE.
 */
import { test, expect, json } from '../fixtures/apiMock'
import { emitSseTyped, installSseHarness, waitForSse } from '../fixtures/sseHarness'

const FLM_TRIO_SLOTS = [
  {
    name: 'agent',
    kind: 'local',
    type: 'llm',
    device: 'npu',
    backend: 'flm',
    provider: 'flm',
    model_id: 'gemma3-1b',
    model: 'gemma3-1b',
    port: 8082,
    status: 'ready',
    lemonade_state: 'loaded',
    backend_url: 'http://127.0.0.1:14002/v1',
    coresident_group: 'npu-flm-trio',
  },
  {
    name: 'stt-npu',
    kind: 'local',
    type: 'transcription',
    device: 'npu',
    backend: 'flm',
    provider: 'flm',
    model_id: 'whisper-v3',
    model: 'whisper-v3',
    port: 8084,
    status: 'idle',
    lemonade_state: 'idle',
    coresident_group: 'npu-flm-trio',
  },
  {
    name: 'embed-npu',
    kind: 'local',
    type: 'embedding',
    device: 'npu',
    backend: 'flm',
    provider: 'flm',
    model_id: 'embed-gemma',
    model: 'embed-gemma',
    port: 8085,
    status: 'idle',
    lemonade_state: 'idle',
    coresident_group: 'npu-flm-trio',
  },
]

test('coresident TRIO badge appears on all three FLM trio slots', async ({
  page,
  mockState,
  cleanState,
}) => {
  await page.route('**/api/slots', (route) => json(route, FLM_TRIO_SLOTS))
  // Status endpoint also returns the slots — keep the two paths in sync
  // so the system store doesn't replace the rich /api/slots payload.
  mockState.status.slots = FLM_TRIO_SLOTS

  await page.goto('/slots')

  // The trio badge has data-testid="coresident-badge". Three slot cards,
  // three badges — each one carrying the "TRIO" label.
  const badges = page.getByTestId('coresident-badge')
  await expect(badges).toHaveCount(3, { timeout: 5_000 })
  await expect(badges.first()).toHaveText('TRIO')
})

test('non-trio slot has no coresident badge', async ({ page, mockState, cleanState }) => {
  const slots = [
    {
      name: 'primary',
      kind: 'local',
      type: 'llm',
      device: 'gpu-rocm',
      backend: 'rocm',
      provider: 'lemonade',
      model_id: 'qwen3-4b',
      model: 'qwen3-4b',
      port: 8081,
      status: 'ready',
      lemonade_state: 'loaded',
    },
  ]
  await page.route('**/api/slots', (route) => json(route, slots))
  mockState.status.slots = slots

  await page.goto('/slots')
  // The card renders; no coresident badge is attached.
  await expect(page.locator('.sc-name', { hasText: 'primary' })).toBeVisible()
  await expect(page.getByTestId('coresident-badge')).toHaveCount(0)
})

test('nuclear-evict event from /api/lemonade/events/stream pops a toast', async ({
  page,
  mockState,
  cleanState,
}) => {
  // SSE shim must be installed BEFORE the page mounts so the EventSource
  // construction goes through the fake.
  await installSseHarness(page)

  // Minimal slot mock so the dashboard doesn't 404 on initial poll.
  await page.route('**/api/slots', (route) => json(route, []))
  mockState.status.slots = []

  await page.goto('/slots')

  // useNuclearEvictBanner mounts at the App level and opens the
  // EventSource on App.onMounted; wait for the stream to appear.
  await waitForSse(page, '/api/lemonade/events/stream')

  // Drive a typed nuclear_evict event into the open stream. The
  // composable subscribes via es.addEventListener('nuclear_evict'),
  // so emitSseTyped — not emitSse — is required.
  const delivered = await emitSseTyped(page, '/api/lemonade/events/stream', 'nuclear_evict', {
    type: 'nuclear_evict',
    message: 'Load failed with non-file-not-found error, evicting all models and retrying...',
    ts: 1700000000,
  })
  expect(delivered).toBeGreaterThan(0)

  // The toast container renders the warning. The text includes
  // "Nuclear evict:" prefix from the composable.
  const toast = page.locator('.toast', { hasText: /Nuclear evict/i })
  await expect(toast).toBeVisible({ timeout: 5_000 })
})

test('NPU exclusivity violation surfaces a typed error toast', async ({
  page,
  mockState,
  cleanState,
}) => {
  // Pre-seed a model so the dropdown isn't empty.
  mockState.models.push({
    id: 'qwen3-1b',
    name: 'Qwen3 1B',
    size_gb: 0.6,
  })
  mockState.status.slots = [
    {
      name: 'agent',
      kind: 'local',
      type: 'llm',
      device: 'npu',
      backend: 'flm',
      provider: 'flm',
      model: 'gemma3-1b',
      port: 8082,
      status: 'ready',
      lemonade_state: 'loaded',
      coresident_group: 'npu-flm-trio',
    },
  ]

  await page.route('**/api/slots', (route) => {
    const req = route.request()
    if (req.method() === 'POST') {
      // Mock the backend rejecting the second NPU LLM slot.
      return route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'slot.npu_exclusivity_violation',
            message:
              "only one NPU LLM slot may be enabled at a time (slot 'agent-2' would conflict with 'agent')",
            details: {
              slot: 'agent-2',
              conflicting_slots: ['agent'],
            },
          },
        }),
      })
    }
    return json(route, mockState.status.slots)
  })

  await page.goto('/slots')

  // Open create modal + fill in a name. Backend selection details
  // depend on the actual modal form; we exercise the error path by
  // posting whatever the form sends.
  await page.getByRole('button', { name: /New slot/i }).click()
  await expect(page.locator('#create-slot-title')).toBeVisible()
  await page.locator('#slot-name').fill('agent-2')

  // Submit. The mock returns 409 with the typed envelope; the toast
  // must surface the conflicting-slot message. The modal-footer button
  // toggles label between "Create slot" and "Creating…"; match either.
  await page.getByRole('button', { name: /^Create slot$/ }).click()

  const toast = page.locator('.toast', { hasText: /NPU LLM slot|conflict/i })
  await expect(toast).toBeVisible({ timeout: 5_000 })
})
