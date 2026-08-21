/**
 * memory-v2-routing — Playwright coverage for task C6's root MemoryView +
 * routing integration: Overview/Bank sub-tabs, legacy #memory/graph and
 * #memory/tools redirects, the #agent/memory/<section> alias, deep-link
 * query parsing (`?bank=<id>&fact=<id>`), and the existing memory_enabled
 * gate still hiding the Memory tab.
 *
 * Runs against the default forced-mock dataset (VITE_MOCK_HAL0=1, set by
 * playwright.config.ts) — no per-spec page.route overrides needed for the
 * GET-only reads; mock.ts's allowlist + mockFixtures.ts's baked payloads
 * (six banks: primary/big/hermes/scratch/ingest/empty; MEM_FACTS f1..f26)
 * already cover every hook these views call.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('memory-v2 routing (task C6)', () => {
  test('#memory shows the Overview (engine panel + growth chart)', async ({ page }) => {
    await page.goto('/#memory')
    await expect(page.getByTestId('mem-tab-overview')).toHaveClass(/active/)
    await expect(page.getByTestId('mv-engine-panel')).toBeVisible()
    await expect(page.getByTestId('mv-growth')).toBeVisible()
  })

  test('#memory/bank shows the Bank workspace', async ({ page }) => {
    await page.goto('/#memory/bank')
    await expect(page.getByTestId('mem-tab-bank')).toHaveClass(/active/)
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
    await expect(page.getByTestId('mv-bankbar')).toBeVisible()
  })

  test('#memory/graph redirects to #memory/bank', async ({ page }) => {
    await page.goto('/#memory/graph')
    await expect(page).toHaveURL(/#memory\/bank/)
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
  })

  test('#memory/tools redirects to #memory/bank', async ({ page }) => {
    await page.goto('/#memory/tools')
    await expect(page).toHaveURL(/#memory\/bank/)
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
  })

  test('#agent/memory/bank also works (the accepted alias prefix)', async ({ page }) => {
    await page.goto('/#agent/memory/bank')
    await expect(page.getByTestId('agent-tab-memory')).toBeVisible()
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
  })

  test('a legacy redirect under the #agent/memory prefix preserves that prefix', async ({ page }) => {
    await page.goto('/#agent/memory/graph')
    await expect(page).toHaveURL(/#agent\/memory\/bank/)
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
  })

  test('deep-link ?bank=<id>&fact=<id> opens the inspector on that fact', async ({ page }) => {
    // f26 (2026-06-10) is the newest valid fact in the mock's 26-fact set —
    // guaranteed to land on the default (page 1, newest-first) listing so
    // the workspace's Inspector can resolve it from the currently-displayed
    // page (it has no separate "fetch a unit by id" fallback — see the C4
    // report's Inspector notes).
    await page.goto('/#memory/bank?bank=big&fact=f26')
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
    await expect(page.getByTestId('mv-inspector')).toBeVisible()
    await expect(page.getByTestId('mv-fact-row-f26')).toHaveClass(/on/)
  })

  test('memory gate OFF still hides the Memory tab (existing contract, unchanged)', async ({ page }) => {
    await page.addInitScript(() => {
      ;(window as unknown as { __hal0MockMemoryEnabled?: boolean }).__hal0MockMemoryEnabled = false
    })
    await page.goto('/#memory')
    await expect(page.getByTestId('agent-tab-nav')).toBeVisible()
    await expect(page.getByTestId('agent-tab-memory')).toHaveCount(0)
  })
})
