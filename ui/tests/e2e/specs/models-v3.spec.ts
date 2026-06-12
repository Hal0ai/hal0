/**
 * models-v3 — `#models` route renders the 2-pane layout (list-with-toolbar
 * / detail) and exposes the "Add by HF coords" trigger. The left filter
 * sidebar was folded into a toolbar above the catalog rows; search +
 * type/device filters live there now.
 *
 * Wireup (#220 brief): the catalog drives off `useModels()` and the
 * AddByHF modal calls `POST /api/models/inspect` →
 * `usePullJob().start()`. Tests in this file mock the new endpoints
 * (inspect, PUT defaults, DELETE cascade) via `page.route`; the
 * listing itself is served by the FORCED VITE_MOCK_HAL0 path
 * (HAL0_DATA-backed) so we get a populated catalog without poking the
 * browser's mock cache.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Models v3 (/models)', () => {
  test('renders catalog layout (toolbar + list + detail)', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.view .vh h1')).toHaveText('Models')
    await expect(page.locator('.models-layout')).toBeVisible()
    await expect(page.locator('.mdl-toolbar')).toBeVisible()
    await expect(page.locator('.mdl-search')).toBeVisible()
    await expect(page.locator('.mdl-list')).toBeVisible()
  })

  test('exposes Add-by-HF + Search-HF CTAs', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.view .vh button:has-text("Add by HF coords")')).toBeVisible()
    await expect(page.locator('.view .vh button:has-text("Search HF")')).toBeVisible()
  })

  test('type/device filter chips in the toolbar are clickable', async ({ page }) => {
    await page.goto('/#models')
    const llmChip = page.locator('.mdl-toolbar button.mdl-chip', { hasText: 'llm' }).first()
    await expect(llmChip).toBeVisible()
    await llmChip.click()
    await expect(llmChip).toHaveClass(/on/)
    // Device chip uses the normalized backend vocab (rocm, not gpu-rocm).
    const rocmChip = page.locator('.mdl-toolbar button.mdl-chip', { hasText: 'rocm' }).first()
    await expect(rocmChip).toBeVisible()
  })

  test('search input filters the catalog and shows an empty state on no match', async ({
    page,
  }) => {
    await page.goto('/#models')
    // Sanity: at least one row before filtering.
    await expect(page.locator('.mdl-row').first()).toBeVisible()
    await page.locator('.mdl-search').fill('zzz-no-such-model-zzz')
    await expect(page.locator('.mdl-row')).toHaveCount(0)
    await expect(page.locator('.mdl-list')).toContainText('No models match')
  })

  test('namespace chips render from backend ns field (blessed + pulled)', async ({ page }) => {
    // HAL0_DATA's fixture catalog carries `ns` on every row (blessed
    // for the vendor-curated list, pulled for the user.* prefix). The
    // backend now derives the same field path-shape-style — see
    // tests/api/test_models_routes.py for the locked rule (#220) — so
    // this is the front-end half of the same contract.
    await page.goto('/#models')
    await expect(
      page.locator('.mdl-section-label', { hasText: 'blessed' }).first(),
    ).toBeVisible()
  })

  test('AddByHF Inspect populates variants from /api/models/inspect', async ({ page }) => {
    await page.route('**/api/models/inspect', async (route) => {
      const body = JSON.parse(route.request().postData() || '{}')
      const repo = body.hf_repo || body.hf_url || 'unknown'
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          repo,
          cached: false,
          variants: [
            {
              id: 'qwen3-8b-q4_k_m.gguf',
              size_bytes: 4_900_000_000,
              size: '4.56 GB',
              info: '4.56 GB · single file',
            },
            {
              id: 'qwen3-8b-q8_0.gguf',
              size_bytes: 8_500_000_000,
              size: '7.91 GB',
              info: '7.91 GB · single file',
            },
          ],
          tags: ['text-generation', 'gguf'],
          metadata: { license: 'apache-2.0', readme_excerpt: 'Hello world.' },
        }),
      })
    })

    await page.goto('/#models')
    await page.locator('.view .vh button:has-text("Add by HF coords")').click()
    await page.locator('input[placeholder*="unsloth/Qwen3-8B-GGUF"]').fill('unsloth/Qwen3-8B-GGUF')
    await page.locator('button:has-text("Inspect")').click()
    // Variant rows render from the mocked response.
    await expect(page.locator('.variant-row', { hasText: 'qwen3-8b-q4_k_m.gguf' })).toBeVisible()
    await expect(page.locator('.variant-row', { hasText: 'qwen3-8b-q8_0.gguf' })).toBeVisible()
    // License surface renders the HF metadata payload.
    await expect(page.locator('.form-section', { hasText: 'License' })).toBeVisible()
  })

  test('Inspect surface shows the backend error envelope on 502', async ({ page }) => {
    await page.route('**/api/models/inspect', (route) =>
      route.fulfill({
        status: 502,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'hf.unreachable',
            message: 'failed to reach huggingface.co',
            details: { repo: 'foo/bar' },
          },
        }),
      }),
    )
    await page.goto('/#models')
    await page.locator('.view .vh button:has-text("Add by HF coords")').click()
    await page.locator('input[placeholder*="unsloth/Qwen3-8B-GGUF"]').fill('foo/bar')
    await page.locator('button:has-text("Inspect")').click()
    await expect(page.locator('.err').first()).toContainText('Inspect failed')
  })

  test('Recipe editor opens, pre-fills defaults, writes PUT /api/models/{id}', async ({ page }) => {
    // HAL0_DATA's first installed row is "qwen3.6-27b-mtp" — we PUT
    // against that id and assert the body carries our edited defaults.
    let putBody: any = null
    await page.route('**/api/models/qwen3.6-27b-mtp', async (route) => {
      if (route.request().method() === 'PUT') {
        putBody = JSON.parse(route.request().postData() || '{}')
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'qwen3.6-27b-mtp',
            defaults: putBody.defaults,
          }),
        })
      }
      return route.fallback()
    })

    await page.goto('/#models')
    // Open Edit Options on the auto-selected (first installed) row.
    await page.locator('button:has-text("Edit options")').click()
    // Fill context_size and save. The modal's input placeholder hints
    // at "8192" — fill whatever's there and assert the PUT body.
    const ctx = page.locator('input[placeholder*="8192"]')
    await ctx.fill('16384')
    await page.locator('button:has-text("Save options")').click()
    await expect.poll(() => putBody?.defaults?.context_size).toBe(16384)
  })

  test('Delete cascade reads affected_slots from DELETE response', async ({ page }) => {
    let deleted = false
    await page.route('**/api/models/qwen3.6-27b-mtp', async (route) => {
      if (route.request().method() === 'DELETE') {
        deleted = true
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'qwen3.6-27b-mtp',
            deleted: true,
            affected_slots: ['primary'],
          }),
        })
      }
      return route.fallback()
    })

    await page.goto('/#models')
    // The first installed row is auto-selected; trigger Delete from
    // its detail pane.
    await page.locator('button.danger:has-text("Delete")').click()
    // Confirm dialog renders. ConfirmDialog gates Delete on a
    // type-to-confirm input when the model has referrers; the HAL0_DATA
    // fixture's primary slot points at this model so the gate is on.
    const confirmInput = page.locator('input.input.mono').last()
    await confirmInput.fill('qwen3.6-27b-mtp')
    await page.locator('button:has-text("Delete model")').click()
    await expect.poll(() => deleted).toBe(true)
  })
})
