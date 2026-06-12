/**
 * connections-v3 — `#connections` renders ConnectionsView.
 *
 * Covers the P1 γ-suite gap: zero coverage of
 * ConnectionsView / useProviders / useUpstreams / useTestUpstream.
 *
 * Three sections tested:
 *   1. View heading + count chip render from mocked provider+upstream data.
 *   2. Test button fires POST /api/upstreams/{name}/test via page.route and
 *      the success result (latency badge) renders inline.
 *   3. Test failure shows the error state inline.
 *
 * Scoping notes:
 *   MOCK_UPSTREAMS spreads MOCK_PROVIDERS, so openrouter appears in both
 *   the "Providers (remote)" card and the "All upstreams" card. Selectors
 *   for openrouter/openai-direct are scoped to the providers section to
 *   avoid Playwright strict-mode violations (2 matching elements).
 */
import { test, expect, json } from '../fixtures/apiMock'

// ── Mock data ────────────────────────────────────────────────────────

const MOCK_PROVIDERS = [
  {
    name: 'openrouter',
    kind: 'remote',
    url: 'https://openrouter.ai/api/v1',
    auth_style: 'bearer',
    auth_value_env: 'HAL0_OPENROUTER_KEY',
    auth_configured: false,
    timeout_seconds: 30,
    models: [],
    advertise_models: ['gpt-4o', 'claude-3-5-sonnet'],
  },
  {
    name: 'openai-direct',
    kind: 'remote',
    url: 'https://api.openai.com/v1',
    auth_style: 'bearer',
    auth_value_env: 'OPENAI_API_KEY',
    auth_configured: true,
    timeout_seconds: 30,
    models: ['gpt-4o'],
    advertise_models: ['gpt-4o'],
  },
]

const MOCK_UPSTREAMS = [
  ...MOCK_PROVIDERS,
  {
    name: 'primary',
    kind: 'slot',
    url: 'http://127.0.0.1:8081',
    auth_style: 'none',
    auth_value_env: null,
    auth_configured: true,
    slot_name: 'primary',
    timeout_seconds: 30,
    models: [],
    advertise_models: [],
  },
]

// ── Tests ─────────────────────────────────────────────────────────────

test.describe('Connections view (#connections)', () => {
  test.beforeEach(async ({ page }) => {
    // Register mocks before navigation so page.route fires on first fetch.
    await page.route('**/api/providers', (route) => json(route, MOCK_PROVIDERS))
    await page.route('**/api/upstreams', (route) => json(route, MOCK_UPSTREAMS))
    // Navigate + wait for BOTH API responses in parallel.
    // waitForResponse must be set up before goto so it catches the first request.
    // Timeout 15s covers cold-vite compilation + font CDN blocking.
    await Promise.all([
      page.waitForResponse('**/api/providers', { timeout: 15_000 }),
      page.waitForResponse('**/api/upstreams', { timeout: 15_000 }),
      page.goto('/#connections', { waitUntil: 'domcontentloaded' }),
    ])
    // Confirm rows are rendered (data is in React state).
    await expect(page.locator('.cn-row').first()).toBeVisible({ timeout: 5_000 })
  })

  test('renders Connections view heading', async ({ page }) => {
    await expect(page.locator('.view .vh h1')).toHaveText('Connections')
  })

  test('providers section shows remote provider rows', async ({ page }) => {
    // Section card with "Providers (remote)" label
    const section = page.locator('.card').filter({ hasText: 'Providers (remote)' })
    await expect(section).toBeVisible()
    // At least one cn-row for "openrouter"
    await expect(section.locator('.cn-row').filter({ hasText: 'openrouter' })).toBeVisible()
    await expect(section.locator('.cn-row').filter({ hasText: 'openai-direct' })).toBeVisible()
  })

  test('auth chip shows configured vs unconfigured', async ({ page }) => {
    // Scope to providers section: openrouter also appears in "All upstreams"
    // section (MOCK_UPSTREAMS spreads MOCK_PROVIDERS), so page-wide selectors
    // would resolve to 2 elements → strict-mode violation.
    const provSection = page.locator('.card').filter({ hasText: 'Providers (remote)' })
    // openrouter has auth_configured: false → "chip warn"
    await expect(
      provSection.locator('.cn-row').filter({ hasText: 'openrouter' }).locator('.chip.warn'),
    ).toBeVisible()
    // openai-direct has auth_configured: true → "chip ok"
    await expect(
      provSection.locator('.cn-row').filter({ hasText: 'openai-direct' }).locator('.chip.ok'),
    ).toBeVisible()
  })

  test('upstreams section shows all rows (providers + slot)', async ({ page }) => {
    // "All upstreams" section should have 3 rows (2 remote + 1 slot)
    const section = page.locator('.card').filter({ hasText: 'All upstreams' })
    await expect(section).toBeVisible()
    await expect(section.locator('.cn-row')).toHaveCount(3)
  })

  test('slot upstream shows slot kind chip', async ({ page }) => {
    const slotRow = page.locator('.cn-row').filter({ hasText: 'primary' })
    await expect(slotRow.locator('.chip', { hasText: 'slot' })).toBeVisible()
  })

  test('test button fires POST /api/upstreams/{name}/test and shows latency', async ({ page }) => {
    // Intercept the test probe before clicking
    await page.route('**/api/upstreams/openrouter/test', (route) =>
      json(route, { ok: true, latency_ms: 142, models_count: 2 }),
    )

    // Scope to providers section to avoid strict-mode on the duplicate openrouter row.
    const provSection = page.locator('.card').filter({ hasText: 'Providers (remote)' })
    const openrouterRow = provSection.locator('.cn-row').filter({ hasText: 'openrouter' })
    await openrouterRow.locator('button', { hasText: 'Test' }).click()

    // Inline ok result with latency should appear
    await expect(openrouterRow.locator('.cn-test-ok')).toBeVisible({ timeout: 5_000 })
    await expect(openrouterRow.locator('.cn-test-ok')).toContainText('142 ms')
  })

  test('failed test shows error state inline', async ({ page }) => {
    await page.route('**/api/upstreams/openrouter/test', (route) =>
      json(route, { ok: false, error: 'connection refused', status: 503 }),
    )

    const provSection = page.locator('.card').filter({ hasText: 'Providers (remote)' })
    const openrouterRow = provSection.locator('.cn-row').filter({ hasText: 'openrouter' })
    await openrouterRow.locator('button', { hasText: 'Test' }).click()

    await expect(openrouterRow.locator('.cn-test-err')).toBeVisible({ timeout: 5_000 })
    await expect(openrouterRow.locator('.cn-test-err')).toContainText('connection refused')
  })

  test('pending state shows testing indicator', async ({ page }) => {
    // Delay the test response to observe the pending state
    await page.route('**/api/upstreams/openrouter/test', async (route) => {
      await new Promise((r) => setTimeout(r, 300))
      return json(route, { ok: true, latency_ms: 88 })
    })

    const provSection = page.locator('.card').filter({ hasText: 'Providers (remote)' })
    const openrouterRow = provSection.locator('.cn-row').filter({ hasText: 'openrouter' })
    await openrouterRow.locator('button', { hasText: 'Test' }).click()

    // Pending state appears briefly (TestCell shows cn-test-pending while pending=true)
    await expect(openrouterRow.locator('.cn-test-pending')).toBeVisible({ timeout: 2_000 })
    // Then resolves to ok
    await expect(openrouterRow.locator('.cn-test-ok')).toBeVisible({ timeout: 5_000 })
  })
})
