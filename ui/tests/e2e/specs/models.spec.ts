/**
 * models.spec.ts — γ-3 Model management (PLAN §10.3 path 3).
 *
 * Covers: from /models, open the Pull modal (HF tab), submit a custom
 * Hugging Face repo, confirm the new model appears in the table,
 * assign it to the primary slot via the row's "Assign…" dropdown,
 * then delete it.
 *
 * Wire shapes targeted (Wave-3 — see usePullJob.js):
 *   POST   /api/models/{encodeURIComponent(id)}/pull
 *     body: {hf_url, quant}
 *     200:  {job_id, model_id}
 *   GET    /api/models/{id}/pull/stream            text/event-stream
 *   GET    /api/models/{id}/pull/status            for reattach (n/a here)
 *   DELETE /api/models/{encodeURIComponent(id)}    204
 *   POST   /api/slots/{name}/load                  body: {model: id}
 *
 * HF ids carry slashes and case (e.g. `Qwen/Qwen3-4B-GGUF`). Models.vue
 * optimistically inserts a row with that exact id as both `id` and
 * `name`, so the row text contains the path and the DELETE / load
 * payloads carry the case-preserving id verbatim.
 *
 * Note: the UI's HF-pull flow at /models doesn't surface inline SSE
 * progress in the modal (FirstRun owns the progress beat). The "watch
 * progress" beat in the brief reduces here to: modal closes, toast
 * fires, the row appears, and the in-flight `usePullJob` is
 * functional (we mock the stream as an empty 200 stream so the
 * EventSource opens without exploding).
 */
import { test, expect, json } from '../fixtures/apiMock'

const HF_ID = 'Qwen/Qwen3-4B-GGUF'
const ENC = encodeURIComponent(HF_ID)  // 'Qwen%2FQwen3-4B-GGUF'

test('pulls a custom HF model, assigns it to primary, deletes it', async ({
  page,
  mockState,
  cleanState,
}) => {
  // Pre-seed the primary slot so the assign dropdown has something
  // to point at.
  mockState.status.slots.push({
    name: 'primary',
    backend: 'vulkan',
    model: null,
    port: 8081,
    status: 'offline',
  })

  // POST /api/models/{enc-id}/pull — the shipped wire. Body carries
  // {hf_url, quant}; the URL path carries the encoded id. Models.vue
  // does its own optimistic row insert on success, so the mock only
  // needs to ack with a job_id; the EventSource stream is fulfilled
  // separately so `usePullJob.attachStream` doesn't throw.
  let lastPullBody: any = null
  await page.route(new RegExp(`/api/models/${ENC}/pull$`), (route) => {
    if (route.request().method() !== 'POST') return json(route, {})
    lastPullBody = JSON.parse(route.request().postData() || '{}')
    return json(route, { job_id: 'job-1', model_id: HF_ID })
  })
  await page.route(new RegExp(`/api/models/${ENC}/pull/stream$`), (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  )
  // reattachInFlightPulls hits /pull/status for every row on mount; we
  // load the page before any row exists, but safe to mock for
  // defense-in-depth against subsequent navigations.
  await page.route(new RegExp(`/api/models/${ENC}/pull/status$`), (route) =>
    json(route, { state: 'idle' }),
  )

  // DELETE /api/models/{enc-id} — case-preserving, slash-encoded. The
  // backend cascades slot.model = null; mirror that so the spec's
  // post-delete assertion is meaningful. Also intercept GET on the
  // same path to keep the apiMock catch-all from getting first dibs.
  await page.route(new RegExp(`/api/models/${ENC}$`), (route) => {
    if (route.request().method() === 'DELETE') {
      for (const s of mockState.status.slots) {
        if (s.model === HF_ID) s.model = null
      }
      return route.fulfill({ status: 204 })
    }
    return json(route, {})
  })

  // /api/slots/<name>/load — record the model that was loaded.
  let lastLoadBody: any = null
  await page.route(/\/api\/slots\/[^/]+\/load$/, (route) => {
    const post = route.request().postData()
    lastLoadBody = post ? JSON.parse(post) : null
    const slot = mockState.status.slots.find((s) => s.name === 'primary')
    if (slot && lastLoadBody?.model) slot.model = lastLoadBody.model
    return json(route, { ok: true })
  })

  await page.goto('/models')

  // ── Open pull modal, switch to HF tab, submit a repo ─────────
  // Page-level trigger is now "Add model" (B3 rename in 360b1c8 — the
  // page Add button surfaces Curated/HuggingFace/Local tabs; the HF
  // tab's submit button is still "Pull model").
  await page.getByRole('button', { name: /^Add model$/ }).click()
  await expect(page.locator('#pull-title')).toBeVisible()
  await page.getByRole('tab', { name: /HuggingFace/ }).click()
  await page.locator('#hf-url').fill(HF_ID)
  await page.locator('#quant').selectOption('Q4_K_M')

  const pullReq = page.waitForRequest(
    (r) => r.url().endsWith(`/api/models/${ENC}/pull`) && r.method() === 'POST',
  )
  await page.locator('[aria-labelledby="pull-title"]')
    .getByRole('button', { name: /^Pull model$/ })
    .click()
  await pullReq
  expect(lastPullBody?.hf_url).toBe(HF_ID)
  expect(lastPullBody?.quant).toBe('Q4_K_M')

  // Modal closes after the pull and the optimistic row appears. The
  // row text contains the HF repo path (Models.vue:148-153 inserts
  // {id: hf_url, name: hf_url, _pending: true}).
  await expect(page.locator('#pull-title')).toBeHidden()
  const row = page.locator('tr', { hasText: 'Qwen3-4B-GGUF' })
  await expect(row).toBeVisible()

  // ── Assign to primary slot via the row's dropdown ────────────
  const assignResp = page.waitForResponse(
    (r) => r.url().endsWith('/api/slots/primary/load') && r.request().method() === 'POST',
  )
  await row.locator('select.assign-select').selectOption('primary')
  await assignResp

  expect(lastLoadBody?.model).toBe(HF_ID)
  expect(
    mockState.status.slots.find((s) => s.name === 'primary')?.model,
  ).toBe(HF_ID)

  // Refresh status from the page so the UI's "Used by" cell updates.
  await page.evaluate(async () => {
    const m = await import('/src/stores/system.js')
    await m.useSystemStore().fetchStatus()
  })
  await expect(row.locator('.slot-badge', { hasText: 'primary' })).toBeVisible()

  // ── Delete the model ─────────────────────────────────────────
  const deleteReq = page.waitForRequest(
    (r) => r.url().endsWith(`/api/models/${ENC}`) && r.method() === 'DELETE',
  )
  // Delete button's aria-label is `Delete <name>` (Models.vue:444).
  await row.getByRole('button', { name: `Delete ${HF_ID}` }).click()
  // ConfirmDialog: when 1 slot uses the model, no type-to-confirm —
  // a single "Delete model" button does it.
  await page.getByRole('button', { name: /^Delete model$/ }).click()
  await deleteReq

  // The row is gone; the primary slot's model is cleared.
  await expect(page.locator('tr', { hasText: 'Qwen3-4B-GGUF' })).toHaveCount(0)
  expect(
    mockState.status.slots.find((s) => s.name === 'primary')?.model,
  ).toBeNull()
})
