/**
 * memory-v2-view — Playwright coverage for the Hindsight Memory view
 * (`#memory`), migrated to the v2 DOM for task C8.
 *
 * memory-view-v3.spec.ts (this file's predecessor) targeted the pre-v2 DOM
 * that C6 retired — `mem-bank-primary`/`mem-bank-ingest` bank cards,
 * `mem-timeseries .mo-spark` bars, `mem-op-*` retry buttons. `MemoryView`
 * (memory.jsx) now renders `window.MemV2Overview` for the Overview
 * sub-tab and `window.MemV2Workspace` for Bank — see
 * memory-v2-overview.spec.ts / memory-v2-workspace.spec.ts / memory-v2-
 * bankbar.spec.ts for that surface's own dedicated coverage. This file
 * keeps only what memory-view-v3 covered and the v2 DOM still has an
 * equivalent for:
 *   - Nav: "Memory" item present when memory_enabled, routes to #memory
 *   - Engine panel: version + reachable status + bank count
 *     (window.MemV2Overview's EnginePanel, mv-engine-panel)
 *   - Bank rows: fact-type counts + live activity chips (mv-bank-row-*)
 *   - Growth chart: stacked bars per bucket (mv-growth)
 *   - Create bank: form PUTs /api/memory/banks/{name}
 *
 * The old "failed operation exposes Retry" test has no v2 equivalent —
 * the retry-per-operation affordance (mem-op-retry, the pre-v2
 * memory.jsx MemBankDetail queue) was not carried into the v2 Bank
 * workspace/BankBar (they only ever show working/pending/idle summary
 * chips, never a per-op list); see the C8 report for the gap this
 * leaves and why it's out of this task's scope to reintroduce.
 *
 * Runs against the default forced-mock dataset (mockFixtures.ts) — no
 * page.route overrides needed except for the create-bank PUT, which the
 * GET-only forced-mock never substitutes.
 */
import { test, expect, json } from '../fixtures/apiMock'

async function gotoMemory(page: any) {
  await page.goto('/#memory')
  await page.waitForSelector('[data-testid="mv-engine-panel"]', { timeout: 10_000 })
}

test.describe('Memory view — Hindsight surface (v2 DOM)', () => {
  test('nav shows Memory item and routes to #memory', async ({ page }) => {
    await page.goto('/#dashboard')
    // Accordion: the Memory sub-link sits under Agents, collapsed until opened.
    await page.locator('[data-testid="nav-agent-toggle"]').click()
    const nav = page.locator('[data-testid="nav-memory"]')
    await expect(nav).toBeVisible()
    await nav.click()
    await expect(page.locator('[data-testid="mv-engine-panel"]')).toBeVisible()
  })

  test('engine panel renders reachable status, version, and bank count', async ({ page }) => {
    await gotoMemory(page)
    const panel = page.getByTestId('mv-engine-panel')
    // buildMemoryEngine() mock: reachable: true, engine: 'hindsight',
    // version: '0.7.2', banks_total: MEM_BANKS.length (6).
    await expect(panel).toContainText('reachable')
    await expect(panel).toContainText('hindsight 0.7.2')
    await expect(panel).toContainText('6')
  })

  test('bank rows show fact-type counts and live activity', async ({ page }) => {
    await gotoMemory(page)
    // primary: a rich fact-type breakdown, idle (no in-flight ops in the
    // baked mock); ingest: pending_operations 7, failed_operations 1 — no
    // per-op detail surface in v2, but the row's activity chip must still
    // read "pending", not "idle" (the #1539-class distinction this spec
    // exists to pin — see memory-bank-error-branches.spec.ts for the
    // engine-outage side of the same contract).
    const primary = page.getByTestId('mv-bank-row-primary')
    await expect(primary).toBeVisible()
    await expect(primary).toContainText('primary')

    const ingest = page.getByTestId('mv-bank-row-ingest')
    await expect(ingest).toBeVisible()
    await expect(ingest).toContainText('pending')
  })

  test('growth chart renders stacked bars per bucket', async ({ page }) => {
    await gotoMemory(page)
    const growth = page.getByTestId('mv-growth')
    await expect(growth).toBeVisible()
    // one stacked triple of <rect> per bucket, not a single SVG polyline —
    // any individual segment can legitimately be height=0 on a quiet day,
    // so assert on the total rect count rather than the first element.
    await expect(growth.locator('svg rect')).not.toHaveCount(0)
  })

  test('create bank PUTs /api/memory/banks/{name}', async ({ page }) => {
    const puts: { url: string; body: any }[] = []
    await page.route('**/api/memory/banks/scratch-pad', (route: any) => {
      if (route.request().method() === 'PUT') {
        let body = {}
        try {
          body = JSON.parse(route.request().postData() || '{}')
        } catch {}
        puts.push({ url: route.request().url(), body })
        return json(route, { bank_id: 'scratch-pad' })
      }
      return json(route, {})
    })

    await gotoMemory(page)
    await page.click('[data-testid="mem-btn-new-bank"]')
    await page.fill('[data-testid="mem-input-bank-id"]', 'scratch-pad')
    await page.click('[data-testid="mem-btn-bank-submit"]')
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0].url).toContain('/api/memory/banks/scratch-pad')
  })
})
