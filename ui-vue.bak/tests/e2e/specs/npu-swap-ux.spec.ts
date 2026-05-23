/**
 * npu-swap-ux.spec.ts — PR-20 / plan §5.3 / ADR-0009.
 *
 * Covers the dashboard surface for the NPU trio chat-model swap:
 *
 *   1. Banner `npu-swap` appears when /api/npu/swap-status reports
 *      in_progress=true.
 *   2. Banner auto-dismisses when the status flips back to in_progress=false.
 *   3. Spinner + "Loading <to_model>…" line takes over the NPU chat sub-row
 *      while the swap is in progress.
 *   4. Editing the NPU LLM slot's model and saving → confirmation modal.
 *      Cancel keeps the form open; Confirm POSTs to /swap.
 *   5. Two-enabled NPU LLM refusal (PR-11 case) — covered by the
 *      backend slot-manager test suite; here we just assert the
 *      dashboard surfaces a 409 envelope without crashing.
 */
import { test, expect, json } from '../fixtures/apiMock'

/** Seed an NPU trio (chat + stt + embed) into mockState. */
function seedNpuTrio(mockState: any) {
  mockState.status.slots = [
    {
      name: 'agent',
      kind: 'flm',
      type: 'llm',
      backend: 'flm',
      device: 'npu',
      model: 'llama-3.2-3b-npu',
      port: 8092,
      status: 'ready',
      coresident_group: 'npu-flm-trio',
      group: 'npu',
    },
    {
      name: 'stt-npu',
      kind: 'transcription',
      backend: 'flm',
      device: 'npu',
      model: 'whisper-int8',
      status: 'ready',
      coresident_group: 'npu-flm-trio',
      group: 'npu',
    },
    {
      name: 'embed-npu',
      kind: 'embedding',
      backend: 'flm',
      device: 'npu',
      model: 'bge-npu',
      status: 'ready',
      coresident_group: 'npu-flm-trio',
      group: 'npu',
    },
  ]
}

test('banner + spinner render when swap-status reports in_progress', async ({
  page,
  mockState,
  cleanState,
}) => {
  seedNpuTrio(mockState)

  // Mid-swap: lemond still has gemma3 loaded, slot points at llama-3.2-3b-npu.
  await page.route('**/api/npu/swap-status', (route) =>
    json(route, {
      in_progress: true,
      from_model: 'gemma3:1b',
      to_model: 'llama-3.2-3b-npu',
    }),
  )

  await page.goto('/slots')

  // Banner surface — matches the `npu-swap` catalog entry.
  const banner = page.locator('[data-banner-id="npu-swap"]')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText('gemma3:1b')
  await expect(banner).toContainText('llama-3.2-3b-npu')

  // Spinner takes over the NPU chat sub-row.
  const chatRow = page.getByTestId('npu-chat-row')
  await expect(chatRow).toBeVisible()
  const progress = page.getByTestId('npu-swap-progress')
  await expect(progress).toBeVisible()
  await expect(progress).toContainText('Loading llama-3.2-3b-npu')
})

test('banner auto-dismisses when swap-status flips back to false', async ({
  page,
  mockState,
  cleanState,
}) => {
  seedNpuTrio(mockState)

  let inProgress = true
  await page.route('**/api/npu/swap-status', (route) =>
    json(route, {
      in_progress: inProgress,
      from_model: inProgress ? 'gemma3:1b' : null,
      to_model: 'llama-3.2-3b-npu',
    }),
  )

  await page.goto('/slots')

  const banner = page.locator('[data-banner-id="npu-swap"]')
  await expect(banner).toBeVisible()

  // Flip the mock back to "no swap" and wait for the next poll tick.
  inProgress = false

  // Poll cadence is 2s — give the next tick 4s headroom for CI jitter.
  await expect(banner).toHaveCount(0, { timeout: 6000 })
  // The chat sub-row's spinner should also be gone.
  await expect(page.getByTestId('npu-swap-progress')).toHaveCount(0)
})

test('intentional NPU chat-model swap pops the confirmation modal', async ({
  page,
  mockState,
  cleanState,
}) => {
  seedNpuTrio(mockState)
  mockState.models = [
    { id: 'llama-3.2-3b-npu', name: 'llama-3.2-3b-npu', backends: ['flm'] },
    { id: 'gemma3:1b',        name: 'gemma3:1b',        backends: ['flm'] },
  ]
  // Default: no swap.
  await page.route('**/api/npu/swap-status', (route) =>
    json(route, { in_progress: false, from_model: null, to_model: null }),
  )
  // Capture the /swap POST so the test can assert it only fires after Confirm.
  let swapCalls = 0
  await page.route('**/api/slots/agent/swap', (route) => {
    swapCalls += 1
    return json(route, { ok: true })
  })

  await page.goto('/slots/agent')

  const drawer = page.locator('[aria-labelledby="edit-slot-title"]')
  await expect(drawer).toBeVisible()

  // Change the model select to the new chat model.
  await page.locator('#edit-slot-model').selectOption('gemma3:1b')

  // Click Save — should NOT POST yet; should open the confirmation modal.
  await drawer.getByRole('button', { name: /^Save/ }).click()

  const modal = page.locator('[role="dialog"]', { hasText: /Swap NPU chat model/ })
  await expect(modal).toBeVisible()
  expect(swapCalls).toBe(0)
})

test('cancel on the swap modal leaves the form intact and does not POST', async ({
  page,
  mockState,
  cleanState,
}) => {
  seedNpuTrio(mockState)
  mockState.models = [
    { id: 'llama-3.2-3b-npu', name: 'llama-3.2-3b-npu', backends: ['flm'] },
    { id: 'gemma3:1b',        name: 'gemma3:1b',        backends: ['flm'] },
  ]
  await page.route('**/api/npu/swap-status', (route) =>
    json(route, { in_progress: false, from_model: null, to_model: null }),
  )
  let swapCalls = 0
  await page.route('**/api/slots/agent/swap', (route) => {
    swapCalls += 1
    return json(route, { ok: true })
  })

  await page.goto('/slots/agent')

  await page.locator('#edit-slot-model').selectOption('gemma3:1b')
  await page.locator('[aria-labelledby="edit-slot-title"]')
    .getByRole('button', { name: /^Save/ }).click()

  const modal = page.locator('[role="dialog"]', { hasText: /Swap NPU chat model/ })
  await expect(modal).toBeVisible()

  await modal.getByRole('button', { name: /Cancel/ }).click()
  await expect(modal).toHaveCount(0)
  expect(swapCalls).toBe(0)

  // Form should still hold the user's pending selection.
  await expect(page.locator('#edit-slot-model')).toHaveValue('gemma3:1b')
})

test('two-enabled NPU LLM rejection — 409 envelope surfaces as toast', async ({
  page,
  mockState,
  cleanState,
}) => {
  // PR-11 backend test suite covers the actual SlotManager refusal; here
  // we simulate the envelope coming back through the config-PUT path
  // and verify the dashboard renders it as a toast instead of crashing.
  seedNpuTrio(mockState)
  mockState.models = [
    { id: 'llama-3.2-3b-npu', name: 'llama-3.2-3b-npu', backends: ['flm'] },
    { id: 'gemma3:1b',        name: 'gemma3:1b',        backends: ['flm'] },
  ]
  await page.route('**/api/npu/swap-status', (route) =>
    json(route, { in_progress: false, from_model: null, to_model: null }),
  )

  // Force the swap POST to return the PR-11 envelope.
  await page.route('**/api/slots/agent/swap', (route) =>
    route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          code: 'slot.npu_exclusivity_violation',
          message:
            "only one NPU LLM slot may be enabled at a time " +
            "(slot 'agent' would conflict with 'agent-2')",
          details: { slot: 'agent', conflicting_slots: ['agent-2'] },
        },
      }),
    }),
  )

  await page.goto('/slots/agent')

  // Slot's current model is llama-3.2-3b-npu; pick a different one so the
  // model field is actually CHANGED and the save path is triggered.
  await page.locator('#edit-slot-model').selectOption('gemma3:1b')
  await page.locator('[aria-labelledby="edit-slot-title"]')
    .getByRole('button', { name: /^Save/ }).click()

  // Confirm the swap — should fire the POST → 409 → toast.
  const modal = page.locator('[role="dialog"]', { hasText: /Swap NPU chat model/ })
  await expect(modal).toBeVisible()
  await modal.getByRole('button', { name: /Swap now/ }).click()

  // The toast surface carries the conflict message — the dashboard
  // doesn't crash, the drawer closes, and the user can correct.
  const toast = page.locator('.toast, [role="status"]', {
    hasText: /NPU LLM slot|conflict|exclus/i,
  })
  await expect(toast.first()).toBeVisible({ timeout: 4000 })
})
