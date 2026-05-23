/**
 * extras-v2.spec.ts — slice #174 Extras (Hardware + Backends + Logs +
 * Agent) end-to-end.
 *
 * Covers the four routes' v2 surfaces against mocked endpoints:
 *   • each route renders without /api/* leaks past the fixture
 *   • Backend modals (Install / Uninstall / FLM-deb) wire correctly
 *   • Logs source-filter pivots to LemonadeJournalPanel (PR-14)
 *   • Persona Edit modal save flow round-trips
 *   • NoBundledAgentCard installs Hermes via /api/agents/install
 *   • Banners wire through useBannerStore (no-agent scope)
 *
 * Test order is intentional: each test re-mounts a clean page via the
 * `cleanState` fixture, so cross-test state leaks are scoped to the
 * Pinia singletons that survive `page.goto` (we re-route everything
 * the survivors touch in beforeEach).
 */
import { test, expect, json, MOCK_DATA } from '../fixtures/apiMock'
import { installSseHarness, emitSse, waitForSse } from '../fixtures/sseHarness'

test.describe('Extras v2 — routes', () => {
  test.beforeEach(async ({ page }) => {
    await installSseHarness(page)
    await page.route('**/api/agents', (route) => {
      if (route.request().method() === 'POST') {
        return json(route, {
          name: 'hermes',
          installed_at: new Date().toISOString(),
          status: 'installed',
        })
      }
      return json(route, { agents: [], count: 0 })
    })
    await page.route('**/api/agents/install', (route) =>
      json(route, {
        name: 'hermes',
        installed_at: new Date().toISOString(),
        status: 'installed',
      }),
    )
    await page.route('**/api/agent/approvals', (route) =>
      json(route, { approvals: [] }),
    )
    await page.route('**/api/agent/approvals/events', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: '',
      }),
    )
    await page.route('**/api/personas', (route) => {
      if (route.request().method() === 'POST') {
        return json(route, { ok: true })
      }
      return json(route, { personas: MOCK_DATA.personas })
    })
    await page.route(/\/api\/personas\/[^/]+$/, (route) => json(route, { ok: true }))
    await page.route('**/api/backends', (route) =>
      json(route, {
        backends: MOCK_DATA.backends,
        lemonade: { version: MOCK_DATA.lemonade.version, pinned: true, sha: 'abc' },
      }),
    )
    await page.route(/\/api\/backends\/[^/]+\/install$/, (route) =>
      json(route, { ok: true }),
    )
    await page.route(/\/api\/backends\/[^/]+$/, (route) => {
      if (route.request().method() === 'DELETE') return json(route, { ok: true })
      return json(route, {})
    })
    // Lemonade /v1/health — store polls this; default a happy snapshot
    // so the offline-stale overlay doesn't dim Hardware.
    await page.route('**/v1/health', (route) =>
      json(route, { loaded: [], max_loaded: 4, version: 'v10.6.0' }),
    )
  })

  test('hardware renders all 6 panels + refresh re-hits /api/hardware', async ({
    page,
    cleanState: _cleanState,
  }) => {
    await page.goto('/hardware')
    // PageHeader heading. The sidebar also shows "Hardware" so target
    // the page heading directly via test-id-friendly h1.
    await expect(page.locator('h1').filter({ hasText: 'Hardware' })).toBeVisible()
    await expect(page.getByTestId('hw-card')).toHaveCount(6)

    const probe = page.waitForResponse((r) => r.url().endsWith('/api/hardware'))
    await page.getByTestId('hw-refresh').click()
    await probe
  })

  test('backends — install + FLM-deb + uninstall modals fire', async ({
    page,
    cleanState: _cleanState,
  }) => {
    // The default fixture installs a catch-all **/api/** route that
    // shadows our beforeEach /api/backends mock (Playwright matches
    // in reverse-registration order; the catch-all goes LAST in
    // installDefaultMocks). Re-register here so it wins for this test.
    await page.route('**/api/backends', (route) =>
      json(route, {
        backends: MOCK_DATA.backends,
        lemonade: { version: MOCK_DATA.lemonade.version, pinned: true, sha: 'abc' },
      }),
    )
    await page.goto('/backends')
    await expect(page.getByTestId('lemonade-self-card')).toBeVisible()

    // FLM row — reinstall routes through the .deb guide modal.
    await page.getByTestId('backend-reinstall-flm:npu').click()
    await expect(page.getByTestId('flm-deb-modal')).toBeAttached()
    await page.getByTestId('flm-deb-copy').click()
    // Two "Close" buttons exist (header X aria-label + footer text);
    // pick the footer one explicitly.
    await page.locator('button.btn-primary.sm').filter({ hasText: 'Close' }).click()
    await expect(page.getByTestId('flm-deb-modal')).not.toBeAttached()

    // Generic uninstall (kokoro).
    await page.getByTestId('backend-uninstall-kokoro').click()
    await expect(page.getByTestId('backend-uninstall-modal')).toBeAttached()
    await page.getByRole('button', { name: /^Cancel$/ }).click()
    await expect(page.getByTestId('backend-uninstall-modal')).not.toBeAttached()

    // Generic install (whispercpp reinstall).
    await page.getByTestId('backend-reinstall-whispercpp').click()
    await expect(page.getByTestId('backend-install-modal')).toBeAttached()
    const installReq = page.waitForRequest(
      (r) =>
        r.url().endsWith('/api/backends/whispercpp/install') &&
        r.method() === 'POST',
    )
    await page.getByTestId('backend-install-confirm').click()
    await installReq
  })

  test('logs — source filter pivots to LemonadeJournalPanel (PR-14)', async ({
    page,
    cleanState: _cleanState,
  }) => {
    await page.route('**/api/logs/stream', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: '',
      }),
    )
    await page.route('**/api/lemonade/logs/stream', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: '',
      }),
    )

    await page.goto('/logs')
    // Default source is merged → main viewport.
    await expect(page.getByTestId('log-viewport')).toBeVisible()
    await expect(page.getByTestId('log-filter-bar')).toBeVisible()

    // Filter bar segmented buttons exist.
    await expect(page.getByTestId('log-source-merged')).toBeVisible()
    await expect(page.getByTestId('log-source-hal0')).toBeVisible()
    await expect(page.getByTestId('log-source-lemond')).toBeVisible()

    // Pause / Export buttons wire.
    await page.getByTestId('log-pause').click()
    // Pivot to lemond → PR-14 LemonadeJournalPanel renders.
    await page.getByTestId('log-source-lemond').click()
    await expect(page.locator('[data-testid="lemonade-journal"]')).toBeVisible()

    // Pivot back → main viewport.
    await page.getByTestId('log-source-merged').click()
    await expect(page.getByTestId('log-viewport')).toBeVisible()
  })

  test('agent — NoBundledAgentCard installs Hermes', async ({
    page,
    cleanState: _cleanState,
  }) => {
    await page.goto('/agent')
    // PageHeader h1 (sidebar also has "Agents" group label — target h1).
    await expect(page.locator('h1').filter({ hasText: 'Agent' })).toBeVisible()
    await expect(page.getByTestId('no-bundled-agent')).toBeVisible()

    const installReq = page.waitForRequest(
      (req) =>
        req.url().endsWith('/api/agents/install') && req.method() === 'POST',
    )
    await page.getByTestId('no-agent-install').click()
    const req = await installReq
    // Default pick is hermes.
    expect(JSON.parse(req.postData() || '{}').name).toBe('hermes')
  })

  test('agent — Persona Edit modal saves', async ({
    page,
    cleanState: _cleanState,
  }) => {
    // Re-register /api/personas after cleanState so the default
    // catch-all doesn't shadow our seed (Playwright matches in
    // reverse-registration order).
    const personas = [
      { id: 'default', name: 'default', slot: 'primary', model: 'qwen3.6-27b-mtp', tone: 'operator', desc: 'Default persona', active: true },
      { id: 'coder', name: 'coder', slot: 'coder', model: 'qwen3-coder-30b', tone: 'code-focused', desc: 'Coder persona' },
    ]
    await page.route('**/api/personas', (route) => {
      if (route.request().method() === 'POST') return json(route, { ok: true })
      return json(route, { personas })
    })
    await page.route(/\/api\/personas\/[^/]+$/, (route) => json(route, { ok: true }))
    await page.goto('/agent?tab=personas')
    await expect(page.getByTestId('agent-tab-personas')).toHaveClass(/active/)

    // MOCK_DATA.personas surfaces ids: default / coder / agent.
    // First match: whichever Edit chip lands first (mock vs fallback).
    const editBtn = page
      .locator('[data-testid^="persona-edit-"]')
      .first()
    await expect(editBtn).toBeVisible()
    await editBtn.click()

    await expect(page.getByTestId('persona-edit-modal')).toBeAttached()
    await expect(page.getByTestId('persona-name')).toBeVisible()
    await expect(page.getByTestId('persona-prompt')).toBeVisible()

    await page.getByTestId('persona-tool-shell_exec').check({ force: true })
    await page.getByTestId('persona-save').click()
    await expect(page.getByTestId('persona-edit-modal')).not.toBeAttached()
  })

  test('agent — no-agent banner shows when no agent installed', async ({
    page,
    cleanState: _cleanState,
  }) => {
    await page.goto('/agent')
    // BannerStack with scope=agent renders the no-agent catalog entry
    // copy. Wait for it to settle since the banner is shown onMounted.
    await expect(
      page.locator('[data-testid="banner-stack"]'),
    ).toContainText(/No bundled agent installed yet/, { timeout: 5000 })
  })
})
