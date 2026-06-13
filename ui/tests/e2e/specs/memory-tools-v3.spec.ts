/**
 * memory-tools-v3 — Playwright coverage for the #memory/tools surface.
 *
 * The Tools panel was re-skinned to mt-* classes but keeps every live action +
 * testid (recall, reflect, documents w/ delete-confirm, mental-models refresh,
 * directives create/toggle/delete). The bank selector lives in a .mt-bankbar
 * (data-testid="mem-tools-bank") and persists to localStorage 'hal0.mem.bank'.
 *
 * NOTE on data: the Playwright webServer runs with VITE_MOCK_HAL0=1 (forced
 * mock). The mock fetch harness (src/api/mock.ts) short-circuits the allowlisted
 * GET endpoints (recall, reflect, documents, mental-models, directives) BEFORE
 * the network layer, so per-spec page.route stubs for those exact paths are
 * bypassed — the panel renders the baked forced-mock dataset. Sub-resource
 * mutations whose path is NOT in the allowlist (e.g. /documents/{id} DELETE,
 * /mental-models/{id}/refresh) DO fall through to the network and are
 * interceptable via page.route. Tests assert against the baked dataset shape
 * and intercept only the mutations that escape forced-mock.
 *
 * Baked dataset (src/api/mock.ts):
 *   - banks: primary (default) / hermes / scratch / ingest
 *   - documents: 6 incl doc-install-log
 *   - mental models: 3 incl mm-operator-style (is_stale: true)
 *   - directives: 4 incl dir-citations
 *   - reflect: long summary text + based_on { facts, documents, mental_models }
 */

import { test, expect, json } from '../fixtures/apiMock'

async function gotoTools(page: any) {
  await page.goto('/#memory/tools')
  await page.waitForFunction(() => typeof (window as any).MemoryView === 'function')
  await page.waitForSelector('[data-testid="mem-tools"]', { timeout: 10_000 })
}

test.describe('Memory tools', () => {
  test('recall console runs and renders ranked results', async ({ page }) => {
    await gotoTools(page)

    await page.fill('[data-testid="mem-recall-q"]', 'what changed recently')
    await page.selectOption('[data-testid="mem-recall-budget"]', 'high')
    await page.click('[data-testid="mem-recall-run"]')

    const results = page.locator('[data-testid="mem-recall-results"]')
    await expect(results).toBeVisible()
    // baked recall returns the strongest hits as .mt-result rows.
    await expect.poll(async () => await results.locator('.mt-result').count()).toBeGreaterThan(0)
  })

  test('reflect playground renders answer and based_on counts', async ({ page }) => {
    await gotoTools(page)

    await page.fill('[data-testid="mem-reflect-q"]', 'summarize the platform state')
    await page.click('[data-testid="mem-reflect-run"]')

    const out = page.locator('[data-testid="mem-reflect-out"]')
    await expect(out).toBeVisible()
    // baked reflect summary text + the "based on …" provenance line.
    await expect(out).toContainText('strix-halo-01 operator')
    await expect(out).toContainText('based on')
  })

  test('documents list renders and Delete fires DELETE', async ({ page }) => {
    // /documents/{id} is NOT in the forced-mock allowlist → page.route works.
    const docDeletes: string[] = []
    await page.route('**/api/memory/banks/*/documents/*', (route: any) => {
      if (route.request().method() === 'DELETE') {
        docDeletes.push(route.request().url())
        return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
      }
      return json(route, {})
    })

    await gotoTools(page)

    const row = page.locator('[data-testid="mem-doc-doc-install-log"]')
    await expect(row).toBeVisible()
    await row.locator('[data-testid="mem-doc-delete"]').click()
    // confirm step
    await page.click('[data-testid="mem-doc-delete-confirm"]')
    await expect.poll(() => docDeletes.length).toBeGreaterThan(0)
    expect(docDeletes[0]).toContain('/documents/doc-install-log')
  })

  test('mental model stale badge + Refresh POST', async ({ page }) => {
    // /mental-models/{id}/refresh is NOT allowlisted → page.route works.
    const mmRefresh: string[] = []
    await page.route('**/api/memory/banks/*/mental-models/*/refresh', (route: any) => {
      mmRefresh.push(route.request().url())
      return json(route, { operation_id: 'op-9', status: 'pending' })
    })

    await gotoTools(page)

    // baked mm-operator-style is the stale one.
    const row = page.locator('[data-testid="mem-mm-mm-operator-style"]')
    await expect(row).toBeVisible()
    await expect(row.locator('.mo-badge.warn')).toContainText('stale')
    await row.locator('[data-testid="mem-mm-refresh"]').click()
    await expect.poll(() => mmRefresh.length).toBeGreaterThan(0)
    expect(mmRefresh[0]).toContain('/mental-models/mm-operator-style/refresh')
  })

  test('directive create form submits name/content', async ({ page }) => {
    await gotoTools(page)

    // baked directives render; opening the create form exposes name + content.
    await expect(page.locator('[data-testid="mem-dir-dir-citations"]')).toBeVisible()

    await page.click('[data-testid="mem-dir-new"]')
    await page.fill('[data-testid="mem-dir-name"]', 'tone')
    await page.fill('[data-testid="mem-dir-content"]', 'be terse')
    await page.click('[data-testid="mem-dir-submit"]')

    // The create POST (/directives) is forced-mocked, so we can't capture the
    // body; on success the inline form closes (its inputs disappear).
    await expect(page.locator('[data-testid="mem-dir-name"]')).toHaveCount(0)
  })
})
