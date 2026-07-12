/**
 * memory-provider-v3 — Playwright coverage for the two-provider-card Memory
 * pane (Hindsight + Honcho) and the per-agent provider routing strip.
 *
 * Companion to memory-view-v3 (which covers the Hindsight card's
 * bank/timeseries/operations surface in isolation, now targeting
 * `mem-provider-card-hindsight` post-refactor). This spec is the
 * Honcho-side + cross-provider surface:
 *   - both provider cards render with correct stats from fixtures
 *   - Honcho card's disabled state (dimmed + "disabled" chip)
 *   - Honcho "Sync graph now" → POST /honcho/sync/run, busy→ok label
 *   - Honcho sync timer toggle → PUT /honcho/sync
 *   - routing strip renders agents from /api/memory/provider, apply → PUT,
 *     409 (unavailable target engine) surfaces an inline error
 *   - Consolidate (now on the Hindsight card) still works
 *   - Migrate reveals the CLI hint with no network call
 *
 * The Hindsight card's own identity fields (version/banks/reachable) come
 * from the forced-mock (VITE_MOCK_HAL0) baked /api/memory/engine + /banks
 * data (see src/api/mock.ts) — that surface short-circuits page.route, so
 * this spec doesn't attempt to override it. The provider-routing +
 * Honcho-stats surface is NOT in that allowlist, so `installMemoryProviderMocks`
 * (ui/tests/e2e/fixtures/apiMock.ts) is authoritative for it via page.route.
 */

import { test, expect, json, installMemoryProviderMocks, HONCHO_STATS_DISABLED } from '../fixtures/apiMock'

async function gotoMemory(page: any) {
  await page.goto('/#memory')
  await page.waitForFunction(() => typeof (window as any).MemoryView === 'function')
  await page.waitForSelector('[data-testid="mem-provider-card-hindsight"]', { timeout: 10_000 })
}

test.describe('Memory view — provider cards + routing strip', () => {
  test('both provider cards render with correct stats from fixtures', async ({ page }) => {
    await installMemoryProviderMocks(page)
    await gotoMemory(page)

    const hindsight = page.locator('[data-testid="mem-provider-card-hindsight"]')
    await expect(hindsight).toContainText('hindsight')
    await expect(hindsight.locator('.chip.ok')).toContainText(/reachable/i)

    const honcho = page.locator('[data-testid="mem-provider-card-honcho"]')
    await expect(honcho).toBeVisible()
    await expect(honcho).not.toHaveClass(/dimmed/)
    await expect(honcho.locator('.chip.ok')).toContainText(/reachable/i)
    await expect(honcho).toContainText('v3.1.0')
    await expect(honcho).toContainText('hal0') // workspace
    await expect(honcho).toContainText('3') // peers stat
    await expect(honcho).toContainText('1267') // observations(1200) + conclusions(67)
    await expect(honcho).toContainText('3') // deriver queue: pending(2) + processing(1)
  })

  test('Honcho card is dimmed with a disabled chip when the engine is off', async ({ page }) => {
    await installMemoryProviderMocks(page, { honchoStats: HONCHO_STATS_DISABLED })
    await gotoMemory(page)

    const honcho = page.locator('[data-testid="mem-provider-card-honcho"]')
    await expect(honcho).toBeVisible()
    await expect(honcho).toHaveClass(/dimmed/)
    await expect(honcho.locator('.chip')).toContainText('disabled')
    await expect(honcho).toContainText(/enable it under Settings|isn't enabled/i)
    // No stats grid, no actions row, when disabled.
    await expect(honcho.locator('[data-testid="mem-btn-sync-now"]')).toHaveCount(0)
  })

  test('Sync now POSTs sync/run and shows busy then ok', async ({ page }) => {
    const syncRuns: string[] = []
    await installMemoryProviderMocks(page)
    await page.route('**/api/memory/honcho/sync/run', async (route) => {
      syncRuns.push(route.request().url())
      await route.fallback()
    })
    await gotoMemory(page)

    const btn = page.locator('[data-testid="mem-btn-sync-now"]')
    await expect(btn).toHaveText('Sync graph now')
    await btn.click()
    await expect.poll(() => syncRuns.length).toBeGreaterThan(0)
    await expect(btn).toHaveText('Synced', { timeout: 3_000 })
  })

  test('Sync now shows an error state and surfaces the note when the backend fails to start it', async ({ page }) => {
    // POST /honcho/sync/run is fail-soft server-side: a systemctl failure
    // still returns HTTP 200 with {started: false, note}. The UI must not
    // treat that 2xx as success.
    const note = 'systemctl start hal0-honcho-sync.service failed: unit not found'
    await installMemoryProviderMocks(page, { syncRun: { started: false, note } })
    await gotoMemory(page)

    const btn = page.locator('[data-testid="mem-btn-sync-now"]')
    await expect(btn).toHaveText('Sync graph now')
    await btn.click()
    await expect(btn).toHaveText('Sync failed', { timeout: 3_000 })
    await expect(btn).toHaveAttribute('title', note)
  })

  test('sync toggle PUTs the timer state', async ({ page }) => {
    const puts: any[] = []
    await installMemoryProviderMocks(page)
    await page.route('**/api/memory/honcho/sync', async (route) => {
      if (route.request().method() === 'PUT') {
        puts.push(route.request().postDataJSON?.() ?? {})
      }
      await route.fallback()
    })
    await gotoMemory(page)

    const honcho = page.locator('[data-testid="mem-provider-card-honcho"]')
    await expect(honcho).toContainText('on') // timer_enabled: true by default
    await page.click('[data-testid="mem-sync-toggle"]')
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toEqual({ enabled: false })
  })

  test('routing strip renders agents from the fixture', async ({ page }) => {
    await installMemoryProviderMocks(page)
    await gotoMemory(page)

    const strip = page.locator('[data-testid="mem-provider-routing"]')
    await expect(strip).toBeVisible()

    const hermesRow = page.locator('[data-testid="mem-agent-provider-row-hermes"]')
    await expect(hermesRow).toBeVisible()
    await expect(page.locator('[data-testid="mem-agent-provider-select-hermes"]')).toHaveValue('hindsight')
    await expect(page.locator('[data-testid="mem-agent-private-hermes"]')).not.toBeChecked()

    const piRow = page.locator('[data-testid="mem-agent-provider-row-pi-coder"]')
    await expect(piRow).toBeVisible()
    await expect(page.locator('[data-testid="mem-agent-provider-select-pi-coder"]')).toHaveValue('honcho')
    await expect(page.locator('[data-testid="mem-agent-private-pi-coder"]')).toBeChecked()
  })

  test('changing select + Apply PUTs the correct body', async ({ page }) => {
    const puts: any[] = []
    await installMemoryProviderMocks(page)
    await page.route('**/api/memory/provider', async (route) => {
      if (route.request().method() === 'PUT') {
        puts.push(route.request().postDataJSON?.() ?? {})
      }
      await route.fallback()
    })
    await gotoMemory(page)

    await page.selectOption('[data-testid="mem-agent-provider-select-hermes"]', 'honcho')
    await page.click('[data-testid="mem-agent-apply-hermes"]')
    await page.click('[data-testid="mem-agent-provider-row-hermes"] >> text=Confirm')
    await expect.poll(() => puts.length).toBeGreaterThan(0)
    expect(puts[0]).toEqual({ agent: 'hermes', provider: 'honcho', private: false, restart: true })
  })

  test('409 on an unavailable target provider shows an inline error', async ({ page }) => {
    await installMemoryProviderMocks(page, { unavailableAgents: ['hermes'] })
    await gotoMemory(page)

    await page.selectOption('[data-testid="mem-agent-provider-select-hermes"]', 'honcho')
    await page.click('[data-testid="mem-agent-apply-hermes"]')
    await page.click('[data-testid="mem-agent-provider-row-hermes"] >> text=Confirm')

    const err = page.locator('[data-testid="mem-agent-error-hermes"]')
    await expect(err).toBeVisible()
    await expect(err).toContainText(/honcho engine is not reachable/i)
    // The switch was rejected server-side — the draft select stays on the
    // attempted (unpersisted) value; nothing was applied.
    await expect(page.locator('[data-testid="mem-agent-provider-select-hermes"]')).toHaveValue('honcho')
  })

  test('Consolidate (on the Hindsight card) queues a consolidation for the selected bank', async ({ page }) => {
    const posts: string[] = []
    await installMemoryProviderMocks(page)
    await page.route('**/api/memory/banks/*/consolidate', (route) => {
      posts.push(route.request().url())
      return json(route, { operation_id: 'op-mock-consolidate', status: 'pending' })
    })
    await gotoMemory(page)

    // Consolidate is disabled until a bank is selected.
    const btn = page.locator('[data-testid="mem-provider-card-hindsight"] [data-testid="mem-btn-consolidate"]')
    await expect(btn).toBeDisabled()

    await page.click('[data-testid="mem-bank-primary"]')
    await expect(btn).toBeEnabled()
    await btn.click()
    await expect.poll(() => posts.length).toBeGreaterThan(0)
    expect(posts[0]).toContain('/api/memory/banks/primary/consolidate')
  })

  test('Migrate reveals the CLI hint without making a network call', async ({ page }) => {
    const calls: string[] = []
    await installMemoryProviderMocks(page)
    await page.route('**/api/memory/honcho/migrate*', (route) => {
      calls.push(route.request().url())
      return json(route, { ok: true })
    })
    await gotoMemory(page)

    await page.click('[data-testid="mem-btn-migrate"]')
    const hint = page.locator('[data-testid="mem-migrate-confirm"]')
    await expect(hint).toBeVisible()
    await expect(hint).toContainText('hal0 memory migrate --from honcho --to hindsight')
    await expect(hint).toContainText('hal0 memory migrate --from hindsight --to honcho')
    expect(calls.length).toBe(0)
  })
})

test.describe('Memory view — Honcho surfaces on settings and graph pages', () => {
  test('Settings has a Honcho panel', async ({ page }) => {
    await installMemoryProviderMocks(page)
    await page.goto('/#settings')
    await page.click('.nav-item:has-text("Memory")')
    const panel = page.locator('[data-testid="settings-honcho-panel"]')
    await expect(panel).toBeVisible()
    await expect(panel).toContainText(/honcho/i)
  })

  test('Memory graph tab shows a Honcho chip', async ({ page }) => {
    await installMemoryProviderMocks(page)
    await gotoMemory(page)
    await page.click('[data-testid="mem-tab-graph"]')
    await expect(page.locator('[data-testid="mem-graph-honcho-chip"]')).toBeVisible()
  })
})
