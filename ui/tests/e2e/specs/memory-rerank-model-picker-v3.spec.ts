/**
 * memory-rerank-model-picker-v3 — Settings → Memory → Fallback reranker.
 *
 * `memory.embedding.rerank_model` renders as a dropdown, not free text:
 * the schema default ("" = whatever the rerank slot serves) plus every
 * capability-tagged rerank model from the `catalogs.embed.rerank` catalog,
 * each annotated with its gpu/npu backends. A saved id outside both stays
 * pickable as "(saved)". Free text is not offered — an id the gateway
 * can't resolve makes Hal0Reranker fail-soft to fused vector ordering on
 * every recall, silently.
 */
import { test, expect, json } from '../fixtures/apiMock'

const CAPS_MOCK = {
  backends: [],
  catalogs: {
    embed: {
      embed: [],
      rerank: [
        {
          id: 'bge-reranker-v2-m3',
          capabilities: ['rerank'],
          size_gb: 0.56,
          backends: [
            { id: 'gpu-rocm', provider: 'llama-server', downloaded: true, pullable: true },
            { id: 'npu', provider: 'flm', downloaded: true, pullable: true },
          ],
        },
      ],
    },
    voice: { stt: [], tts: [] },
    img: { img: [] },
  },
  selections: { embed: {}, voice: {}, img: {} },
}

test.describe('Memory reranker model picker', () => {
  test('rerank_model is a dropdown: slot-model default + backend-annotated catalog rows', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, CAPS_MOCK))
    await page.goto('/#settings/memory')

    const row = page.locator('.s-row')
      .filter({ has: page.locator('.k span', { hasText: /^Rerank model$/ }) })
    const select = row.locator('select')
    await expect(select).toBeVisible()
    await expect(select.locator('option[value=""]')).toHaveText("the rerank slot's model (default)")
    await expect(select.locator('option[value="bge-reranker-v2-m3"]')).toHaveText('bge-reranker-v2-m3 · gpu-rocm / npu')
    // No free-text fallback for this key.
    await expect(row.locator('input[type="text"], input:not([type])')).toHaveCount(0)
  })

  test('picking a catalog model dirties the panel and lights Save reranker', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, CAPS_MOCK))
    await page.goto('/#settings/memory')

    const row = page.locator('.s-row')
      .filter({ has: page.locator('.k span', { hasText: /^Rerank model$/ }) })
    const save = page.getByRole('button', { name: 'Save fallback reranker' })
    await expect(row.locator('select')).toBeVisible()
    await expect(save).toBeDisabled()
    await row.locator('select').selectOption('bge-reranker-v2-m3')
    await expect(save).toBeEnabled()
  })
})
