/**
 * models.spec.ts — Models view smoke (slice #171 v2 adaptation).
 *
 * Adapted from the v1 table-layout spec. Preserves the original
 * `pull → load → delete` intent on the new 3-pane catalog:
 *   - open the v2 Add-by-HF modal (replaces "Add model → HF tab")
 *   - inspect a repo, pick a variant, submit Pull (POSTs the pull endpoint)
 *   - select the existing primary slot's model, fire delete from the
 *     detail pane, confirm
 *
 * v1's "assign-via-row dropdown" disappears in v2 — load-into-slot
 * happens via the detail pane's [Load now] CTA. The spec exercises
 * that path instead of the v1 select-option.
 */
import { test, expect, json, MOCK_DATA } from '../fixtures/apiMock'

const HF_ID = 'Qwen/Qwen3-4B-GGUF'
const ENC = encodeURIComponent('user.Qwen3-4B')   // v2 modal prefixes user.

test('v2 catalog: pull HF model, load into slot, delete', async ({
  page,
  mockState,
  cleanState,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 })

  // Seed primary slot so the Used-by detail panel + Load button have
  // a target.
  mockState.status.slots.push({
    name: 'primary',
    type: 'llm',
    kind: 'llm',
    device: 'gpu-rocm',
    model: null,
    state: 'idle',
    port: 8081,
  })

  // Route catalog list to include the pre-existing model we'll later
  // delete.
  await page.route('**/api/models', (route) => {
    if (route.request().method() !== 'GET') return json(route, {})
    return json(route, { models: [MOCK_DATA.models.find((m) => m.id === 'qwen3.6-27b-mtp')!] })
  })

  // /v1/pull/variants — 404 → modal mock fallback.
  await page.route(/\/v1\/pull\/variants/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }),
  )

  // POST /api/models/<id>/pull
  let pullBody: any = null
  await page.route(/\/api\/models\/.*\/pull$/, (route) => {
    if (route.request().method() !== 'POST') return json(route, {})
    pullBody = JSON.parse(route.request().postData() || '{}')
    return json(route, { id: 'job-1', model_id: 'user.Qwen3-4B' })
  })
  await page.route(/\/api\/models\/.*\/pull\/stream$/, (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  )
  await page.route(/\/api\/models\/.*\/pull\/status$/, (route) =>
    json(route, { state: 'idle' }),
  )

  // POST /api/slots/<name>/swap — used by Load Now
  let lastSwapBody: any = null
  await page.route(/\/api\/slots\/[^/]+\/swap$/, (route) => {
    lastSwapBody = JSON.parse(route.request().postData() || '{}')
    const slot = mockState.status.slots.find((s) => s.name === 'primary')
    if (slot && lastSwapBody?.model) slot.model = lastSwapBody.model
    return json(route, { ok: true })
  })

  // DELETE /api/models/<id>
  await page.route(/\/api\/models\/[^/]+$/, (route) => {
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ status: 204 })
    }
    return json(route, {})
  })

  await page.goto('/models')

  // ── Open Add by HF coords modal, submit a repo ───────────────
  await page.locator('[data-test="add-by-hf"]').click()
  await expect(page.locator('.modal-shell')).toBeVisible()
  await page.locator('#hf-repo').fill(HF_ID)
  await page.getByRole('button', { name: /Inspect/ }).click()
  await page.locator('[data-variant="Q4_K_M"]').click()

  // Model name auto-prefilled as user.Qwen3-4B (strip -GGUF, prefix user.)
  await expect(page.locator('#hf-model-name')).toHaveValue('user.Qwen3-4B')

  await page.locator('[data-test="hf-pull-submit"]').click()
  await expect.poll(() => pullBody, { timeout: 3000 }).not.toBeNull()
  expect(pullBody?.hf_url).toBe(HF_ID)
  expect(pullBody?.variant).toBe('Q4_K_M')

  // Modal closes after pull
  await expect(page.locator('.modal-shell')).toBeHidden({ timeout: 2000 })

  // ── Select existing installed model, fire Load Now ───────────
  await page.locator('[data-model-id="qwen3.6-27b-mtp"]').click()
  await page.locator('[data-test="load-now"]').click()
  await expect.poll(() => lastSwapBody, { timeout: 3000 }).not.toBeNull()
  expect(lastSwapBody?.model).toBe('qwen3.6-27b-mtp')

  // ── Delete the selected model ────────────────────────────────
  await page.locator('[data-test="delete-btn"]').click()
  // Slot now references the model → type-to-confirm required
  await page.locator('[data-test="del-type-confirm"]').fill('qwen3.6-27b-mtp')
  await page.locator('[data-test="del-confirm"]').click()

  // Model row gone from the list
  await expect(page.locator('[data-model-id="qwen3.6-27b-mtp"]')).toHaveCount(0)
})
