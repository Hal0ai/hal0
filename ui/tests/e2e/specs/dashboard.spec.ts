/**
 * dashboard.spec.ts — v2 Dashboard / view (slice #169).
 *
 * Covers the chat-first dashboard surface added in slice #169:
 *   - 5 composer states render via the tweaks store (idle / sending /
 *     streaming / swap / no-tools / offline)
 *   - SnapshotStrip rows route to /slots/:name on click
 *   - PersonaPicker swap to an NPU slot raises the npu-swap banner
 *   - Tool-call <details> block toggles open / closed
 *   - Hero ✕ dismiss persists across reloads (sessionStorage)
 *   - skip-path-empty hero variant hides the chat composer entirely
 *
 * Mocks ride on the shared apiMock fixture (slice #166). The first
 * spec also seeds `useSystemStore.slots` via the /api/slots route
 * so the snapshot strip has rows to click.
 */
import { test, expect, json } from '../fixtures/apiMock'
import { installSseHarness } from '../fixtures/sseHarness'

const SLOTS_FIXTURE = [
  {
    name: 'primary',
    kind: 'local',
    type: 'llm',
    device: 'gpu-vulkan',
    backend: 'llamacpp',
    provider: 'llamacpp',
    model: 'qwen3-chat.gguf',
    model_id: 'qwen3-chat',
    port: 8081,
    status: 'ready',
    lemonade_state: 'loaded',
    is_default: true,
  },
  {
    name: 'agent',
    kind: 'local',
    type: 'llm',
    device: 'npu',
    backend: 'flm',
    provider: 'flm',
    model: 'gemma3:1b',
    model_id: 'gemma3-1b',
    port: 8082,
    status: 'idle',
    lemonade_state: 'loaded',
    coresident_group: 'npu-flm-trio',
  },
  {
    name: 'embed',
    kind: 'local',
    type: 'embedding',
    device: 'gpu-rocm',
    backend: 'llamacpp',
    provider: 'llamacpp',
    model: 'nomic-embed.gguf',
    model_id: 'nomic-embed',
    port: 8083,
    status: 'ready',
    lemonade_state: 'loaded',
  },
]

test.beforeEach(async ({ page, mockState, cleanState }) => {
  void cleanState
  await page.setViewportSize({ width: 1366, height: 900 })
  await installSseHarness(page)
  // Empty SSE events so the bell + footer don't sit on a real EventSource.
  await page.route('**/api/events?**', (route) =>
    json(route, { events: [], next_since: 0 }),
  )
  await page.route('**/api/events', (route) =>
    json(route, { events: [], next_since: 0 }),
  )
  await page.route('**/api/events/stream*', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  )
  await page.route('**/api/agent/approvals/events*', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  )
  await page.route('**/api/lemonade/events/stream*', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
  )

  // Healthy lemonade so the composer's `offline` derivation stays off
  // unless a spec explicitly overrides it.
  await page.route('**/v1/health', (route) =>
    json(route, {
      loaded: [
        { model_name: 'qwen3-chat', backend_url: 'http://127.0.0.1:8081' },
      ],
      max_loaded: 4,
      version: 'v10.6.0',
    }),
  )
  // Seed the system store with 3 slots so SnapshotStrip + PersonaPicker
  // have rows. Mirrors /api/slots, which wins over /api/status.slots in
  // useSystemStore.
  mockState.status = { ...mockState.status, slots: SLOTS_FIXTURE }
  await page.route('**/api/slots', (route) => json(route, SLOTS_FIXTURE))
})

/* ── Hero ────────────────────────────────────────────────────────── */

test('Hero ✕ dismiss persists across reload via sessionStorage', async ({ page }) => {
  await page.goto('/')
  const hero = page.locator('[data-testid="dash-hero"]')
  await expect(hero).toBeVisible()
  await page.locator('[data-testid="hero-dismiss"]').click()
  await expect(hero).toHaveCount(0)

  await page.reload()
  await expect(page.locator('[data-testid="dash-hero"]')).toHaveCount(0)
})

test('Hero variant `post-install` shows tour CTA', async ({ page }) => {
  // Pre-seed the tweak override so the hero picks the post-install copy
  // independent of the (mocked) slot count.
  await page.addInitScript(() => {
    localStorage.setItem(
      'hal0:tweaks:v2',
      JSON.stringify({ heroVariant: 'post-install' }),
    )
  })
  await page.goto('/')
  const hero = page.locator('[data-testid="dash-hero"]')
  await expect(hero).toHaveAttribute('data-variant', 'post-install')
  await expect(page.locator('[data-testid="hero-tour"]')).toBeVisible()
})

/* ── Snapshot strip ─────────────────────────────────────────────── */

test('SnapshotStrip rows route to /slots/:name on click', async ({ page }) => {
  await page.goto('/')
  const strip = page.locator('[data-testid="snapshot-strip"]')
  await expect(strip).toBeVisible()
  // Wait for the system store to settle the slot list.
  await expect(page.locator('[data-testid="snap-row-primary"]')).toBeVisible()
  await page.locator('[data-testid="snap-row-agent"]').click()
  await expect(page).toHaveURL(/\/slots\/agent$/)
})

/* ── Composer states (5 of 5) ───────────────────────────────────── */

const COMPOSER_STATES: Array<'idle' | 'sending' | 'streaming' | 'swap' | 'no-tools' | 'offline'> = [
  'idle', 'sending', 'streaming', 'swap', 'no-tools', 'offline',
]

for (const state of COMPOSER_STATES) {
  test(`Composer state ${state} renders via tweak`, async ({ page }) => {
    await page.addInitScript((s) => {
      localStorage.setItem('hal0:tweaks:v2', JSON.stringify({ composerState: s }))
    }, state)
    await page.goto('/')

    const composer = page.locator('[data-testid="composer"]')
    await expect(composer).toBeVisible()
    await expect(composer).toHaveAttribute('data-state', state)

    if (state === 'streaming') {
      await expect(page.locator('[data-testid="composer-stop"]')).toBeVisible()
    } else if (state === 'sending') {
      await expect(page.locator('[data-testid="composer-meta-sending"]')).toBeVisible()
    } else if (state === 'swap') {
      await expect(page.locator('[data-testid="composer-banner-swap"]')).toBeVisible()
    } else if (state === 'offline') {
      await expect(page.locator('[data-testid="composer-banner-offline"]')).toBeVisible()
    } else if (state === 'no-tools') {
      await expect(page.locator('[data-testid="composer-attach"]')).toBeDisabled()
      await expect(page.locator('[data-testid="composer-mic"]')).toBeDisabled()
    } else {
      // idle — Send is interactive
      await expect(page.locator('[data-testid="composer-send"]')).toBeVisible()
    }
  })
}

/* ── Persona swap → NPU triggers banner ─────────────────────────── */

test('Persona swap to NPU slot raises the composer swap banner', async ({ page }) => {
  await page.goto('/')
  // Open the picker.
  await page.locator('[data-testid="persona-trigger"]').click()
  await expect(page.locator('[data-testid="persona-menu"]')).toBeVisible()
  // Pick the NPU agent slot. The composer drops to its `swap` state
  // for ~3.5s while voice + embed pause — surfaced inline on the
  // composer (not the catalog-driven BannerStack, which is scoped to
  // /slots and wouldn't render on /).
  await page.locator('[data-testid="persona-item-agent"]').click()
  const composer = page.locator('[data-testid="composer"]')
  await expect(composer).toHaveAttribute('data-state', 'swap', { timeout: 2_000 })
  await expect(page.locator('[data-testid="composer-banner-swap"]')).toBeVisible()
})

/* ── Tool-call <details> collapses + expands ────────────────────── */

test('Tool-call <details> block collapses and expands', async ({ page }) => {
  // Force the chat surface into `active` and seed a synthetic assistant
  // message that carries a tool-call. Avoids re-implementing OpenAI SSE
  // semantics in a mock — slice #169 only owns the inline
  // tool-call rendering, not stream correctness (which lives in
  // Composer / Dashboard's submit handler and is exercised elsewhere).
  await page.addInitScript(() => {
    localStorage.setItem('hal0:tweaks:v2', JSON.stringify({ chatVariant: 'active' }))
  })
  await page.goto('/')

  await page.waitForFunction(() => !!window.__hal0DashTest)
  await page.evaluate(() => {
    window.__hal0DashTest.clearMessages()
    window.__hal0DashTest.pushMessage({
      id: 'u-1', role: 'user', content: 'read a.py',
    })
    window.__hal0DashTest.pushMessage({
      id: 'a-1',
      role: 'assistant',
      persona: 'primary',
      content: 'Sure, reading a.py.',
      tool_calls: [
        {
          id: 'tc-1',
          name: 'read_file',
          args: { path: 'a.py' },
          result: 'print("hi")\n',
          duration_ms: 130,
        },
      ],
    })
  })

  // Tool-call block appears.
  const tc = page.locator('[data-testid="tool-call-tc-1"]')
  await expect(tc).toBeVisible({ timeout: 4_000 })

  // <details> starts collapsed (no `open` attr).
  await expect(tc).not.toHaveAttribute('open', '')
  await tc.locator('summary').click()
  await expect(tc).toHaveAttribute('open', '')
  await tc.locator('summary').click()
  await expect(tc).not.toHaveAttribute('open', '')
})

/* ── Skip-path-empty hides composer ────────────────────────────── */

test('Hero `skip-path-empty` hides the composer entirely', async ({ page, mockState }) => {
  // Empty slot list → derivation picks `skip-path-empty`.
  mockState.status = { ...mockState.status, slots: [] }
  await page.route('**/api/slots', (route) => json(route, []))

  await page.goto('/')
  await expect(page.locator('[data-testid="dash-hero"]')).toHaveAttribute('data-variant', 'skip-path-empty')
  await expect(page.locator('[data-testid="composer"]')).toHaveCount(0)
  await expect(page.locator('[data-testid="dash-empty"]')).toBeVisible()
})
