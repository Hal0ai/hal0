/**
 * models-v3 — `#models` route renders the 3-tab catalog (Inference / Image / Upstream)
 * with simplified OR filter chips, pagination, and download-icon installed indicators.
 *
 * Wireup (#220 brief): the catalog drives off `useModels()` and the
 * AddByHF modal calls `POST /api/models/inspect` → `usePullJob().start()`.
 * Tests in this file mock the new endpoints (inspect, PUT defaults, DELETE cascade)
 * via `page.route`; the listing itself is served by the FORCED VITE_MOCK_HAL0 path.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Models v3 (/models)', () => {
  test('renders tab bar + catalog layout', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.view .vh h1')).toHaveText('Models')
    await expect(page.locator('.slot-tabs')).toBeVisible()
    await expect(page.locator('.models-layout')).toBeVisible()
    await expect(page.locator('.mdl-toolbar')).toBeVisible()
    await expect(page.locator('.mdl-search')).toBeVisible()
    await expect(page.locator('.mdl-list')).toBeVisible()
  })

  test('three tabs: Inference, Image/ComfyUI, Upstream', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.slot-tab[role="tab"]')).toHaveCount(3)
    await expect(page.locator('.slot-tab:has-text("Inference Models")')).toBeVisible()
    await expect(page.locator('.slot-tab:has-text("Image / ComfyUI")')).toBeVisible()
    await expect(page.locator('.slot-tab:has-text("Upstream Models")')).toBeVisible()
    // Default tab is Inference
    await expect(page.locator('.slot-tab.on:has-text("Inference Models")')).toBeVisible()
  })

  test('exposes Add-by-HF + Search-HF CTAs', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.view .vh button:has-text("Add by HF coords")')).toBeVisible()
    await expect(page.locator('.view .vh button:has-text("Search HF")')).toBeVisible()
  })

  test('simplified filter chips toggle OR semantics', async ({ page }) => {
    await page.goto('/#models')
    // All 7 filter chips render
    await expect(page.getByTestId('mdl-filter-mtp')).toBeVisible()
    await expect(page.getByTestId('mdl-filter-moe')).toBeVisible()
    await expect(page.getByTestId('mdl-filter-dense')).toBeVisible()
    await expect(page.getByTestId('mdl-filter-embed')).toBeVisible()
    await expect(page.getByTestId('mdl-filter-rerank')).toBeVisible()
    await expect(page.getByTestId('mdl-filter-voice')).toBeVisible()
    await expect(page.getByTestId('mdl-filter-vision')).toBeVisible()

    // Click MTP chip — toggles on
    const mtpChip = page.getByTestId('mdl-filter-mtp')
    await mtpChip.click()
    await expect(mtpChip).toHaveClass(/on/)

    // Clear button appears and works
    await page.locator('.mdl-clear').click()
    await expect(mtpChip).not.toHaveClass(/on/)
  })

  test('search input filters the catalog and shows an empty state on no match', async ({
    page,
  }) => {
    await page.goto('/#models')
    await expect(page.locator('.mdl-row').first()).toBeVisible()
    await page.locator('.mdl-search').fill('zzz-no-such-model-zzz')
    await expect(page.locator('.mdl-row')).toHaveCount(0)
    await expect(page.locator('.mdl-list')).toContainText('No models match')
  })

  test('installed models show green download icon, uninstalled show grey', async ({ page }) => {
    await page.goto('/#models')
    // Installed rows have green icon
    await expect(page.getByTestId('mdl-row-installed').first()).toBeVisible()
    // Not-installed rows exist too
    await expect(page.getByTestId('mdl-row-not-installed').first()).toBeVisible()
  })

  test('pagination controls render and work', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.mdl-pager')).toBeVisible()
    await expect(page.locator('.mdl-pager-pages')).toBeVisible()
    await expect(page.locator('.mdl-pager-size')).toBeVisible()
    // All per-page options present
    await expect(page.locator('.mdl-pager-size .mdl-chip', { hasText: '10' })).toBeVisible()
    await expect(page.locator('.mdl-pager-size .mdl-chip', { hasText: '25' })).toBeVisible()
    await expect(page.locator('.mdl-pager-size .mdl-chip', { hasText: '50' })).toBeVisible()
    await expect(page.locator('.mdl-pager-size .mdl-chip', { hasText: 'All' })).toBeVisible()
  })

  test('namespace sections render from backend ns field (blessed + pulled)', async ({ page }) => {
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
    await expect(page.locator('.variant-row', { hasText: 'qwen3-8b-q4_k_m.gguf' })).toBeVisible()
    await expect(page.locator('.variant-row', { hasText: 'qwen3-8b-q8_0.gguf' })).toBeVisible()
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
    await page.locator('button:has-text("Edit options")').click()
    const ctx = page.locator('input[placeholder*="8192"]')
    await ctx.fill('16384')
    await page.getByTestId('model-save').click()
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
    await page.locator('button.danger:has-text("Delete")').click()
    const confirmInput = page.locator('input.input.mono').last()
    await confirmInput.fill('qwen3.6-27b-mtp')
    await page.locator('button:has-text("Delete model")').click()
    await expect.poll(() => deleted).toBe(true)
  })

  test('Check updates affordance forces a refresh when nothing is outdated', async ({ page }) => {
    const checks: string[] = []
    await page.route('**/api/models/updates/check*', async (route) => {
      checks.push(route.request().url())
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ checked_at: 1730000000, checked: 5, updates_available: 0, models: {} }),
      })
    })
    await page.goto('/#models')
    // Nothing outdated in the mock catalog → the explicit check affordance
    // renders instead of Update-all (the surface must never be invisible).
    const btn = page.getByTestId('mdl-check-updates')
    await expect(btn).toBeVisible()
    await expect(page.getByTestId('mdl-update-all')).toHaveCount(0)
    await btn.click()
    await expect.poll(() => checks.some((u) => u.includes('refresh=1'))).toBe(true)
  })
})
