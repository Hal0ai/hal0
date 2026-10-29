/**
 * overview-v2 — `#dashboard` renders OverviewView, the operator's briefing: a Now band with the
 * unified memory bar, the Needs-you inbox, Serving now lanes, and the Agents / Usage / Hardware /
 * Recent / Image engine / Services / Memory cards over a version strip. It replaced the fixed-band
 * dashboard (`.rd-*`) and its swap-in widgets, whose specs are gone with it.
 *
 * Harness notes (the suite runs under VITE_MOCK_HAL0, see playwright.config.ts):
 *   - `/api/slots`, `/api/hardware` and `/api/status` are answered in-bundle, before `page.route`
 *     is consulted. Slots are seeded with `seedSlots`; memory is gated off with
 *     `window.__hal0MockMemoryEnabled = false`; the hardware body is routed after opting the path
 *     out of substitution with `window.__hal0MockPassthrough`.
 *   - Everything else the page polls is a plain `page.route` (apiMock's defaults, overridden here).
 */
import { test, expect, json, seedSlots, MOCK_DATA } from '../fixtures/apiMock'
import type { Page } from '@playwright/test'

/* ── slot fixtures: a CT105-like box, one slot per lane plus a few more ── */

const base = {
  type: 'llm',
  group: 'chat',
  container_status: 'running',
  container_health: true,
  last_used_at: null,
  metrics: { toks: 0, ttft: null, ctx: 0 },
}

/** An actively serving slot. Fresh `last_used_at` keeps it green rather than stuck-demoted. */
function servingSlot() {
  return {
    ...base,
    name: 'primary',
    state: 'serving',
    backend: 'rocm',
    device: 'gpu-rocm',
    model: 'qwen3-30b',
    model_id: 'qwen3-30b',
    model_default: 'qwen3-30b',
    port: 8100,
    isDefault: true,
    last_used_at: Date.now() / 1000,
    ctx_max: 32000,
    metrics: { toks: 42.5, ttft: 280, ctx: 1200 },
  }
}

/** One loaded slot in each of the four lanes (vulkan, rocm, npu, cpu), all idle. */
function idleBox() {
  return [
    { ...base, name: 'primary', state: 'ready', backend: 'rocm', device: 'gpu-rocm', model: 'qwen3-30b', port: 8100, isDefault: true, ctx_max: 32000 },
    { ...base, name: 'embed', state: 'ready', backend: 'vulkan', device: 'gpu-vulkan', model: 'bge-m3', type: 'embedding', group: 'embed', port: 8101 },
    { ...base, name: 'flm', state: 'ready', backend: 'flm', device: 'npu', model: 'qwen3-8b-flm', port: 8102 },
    { ...base, name: 'utility', state: 'ready', backend: 'cpu', device: 'cpu', model: 'qwen3-0.6b', port: 8103 },
  ]
}

/* ── network fixtures ── */

/** GET /api/doctor with one failing diagnosis the inbox shows as an `err` row. */
function failingDoctor() {
  return {
    verdict: 'warn',
    diagnoses: [
      {
        id: 'disk-low',
        severity: 'fail',
        confidence: 'high',
        summary: 'Model store is almost full',
        detail: '2 GB free of 500 GB',
        evidence: [],
        next_steps: [],
      },
    ],
  }
}

/** A /api/hardware body with no GPU at all: neither compute nor Vulkan access. */
function cpuOnlyHardware() {
  return { ...MOCK_DATA.host, gpus: [], gpu_name: '', gpu_vendor: '' }
}

/** /api/stats/hardware with the budget's memory block (the Now band's bar and legend). */
function populatedHardwareStats() {
  return {
    ram_total_mb: 96000,
    ram_used_mb: 40000,
    ram_available_mb: 56000,
    gtt_used_mb: 6200,
    vram_used_mb: 0,
    gpu_util: 0.31,
    cpu_util: 0.12,
    npu_status: { ok: true, model_mb: 1100 },
    memory: {
      gpu: {
        kind: 'shared',
        total_gb: 116,
        used_gb: 43.4,
        this_box: [
          { slot: 'embed', gb: 2.8, state: 'ready' },
          { slot: 'flm', gb: 6.2, state: 'ready' },
          { slot: 'primary', gb: 30.8, state: 'serving' },
        ],
        image_engine_gb: 6.6,
        image_engine_slot: 'img',
        image_engine_estimated: true,
        other_guests_gb: 4,
        attribution_excess_gb: 0,
        free_gb: 68.6,
      },
      ram: { limit_gb: 105.5, used_gb: 16.1, free_gb: 89.4, source: 'container' },
      render_free_gb: 107.4,
    },
    host: { configured: false, detected: false },
  }
}

/** A /api/stats body with three models (one with a long id) and a bucket strip. */
function populatedUsage() {
  const model = (id: string, ok: number, errors: number) => ({
    model_id: id,
    ttft_ms: { p50: 310, p95: 1200 },
    tps_decode: { avg: 38.2, p50: 37.9 },
    tps_prefill: { avg: 910 },
    spec_accept_rate: 0.64,
    ok,
    errors,
  })
  return {
    window: { from: '2026-10-28T12:00:00Z', to: '2026-10-29T12:00:00Z', bucket: '1h' },
    totals: { requests: 1530, ok: 1500, errors: 30, tokens_completed: 2_480_000 },
    by_model: [
      model('qwen3-30b-a3b-instruct-2507-q4_k_m-mtp-drafted', 1200, 20),
      model('bge-m3', 250, 0),
      model('qwen3-0.6b', 50, 10),
    ],
    buckets: Array.from({ length: 24 }, (_, i) => ({
      ts: `2026-10-28T${String((12 + i) % 24).padStart(2, '0')}:00:00Z`,
      requests: 40 + i,
      tokens_completed: 60_000 + i * 3000,
    })),
  }
}

/** Opt a path out of the in-bundle mock so a `page.route` body for it is authoritative. */
async function passthrough(page: Page, ...paths: string[]) {
  await page.addInitScript((list) => {
    ;(window as unknown as { __hal0MockPassthrough?: string[] }).__hal0MockPassthrough = list
  }, paths)
}

/**
 * The forced-mock update state advertises a newer release, which is itself a Needs-you row. A spec
 * that wants the inbox to hold only what it seeds says the box is up to date, through the seam
 * mockFixtures' buildUpdateState reads.
 */
async function upToDate(page: Page) {
  await page.addInitScript(() => {
    ;(window as unknown as { __hal0UpdateStateOverride?: unknown }).__hal0UpdateStateOverride = {
      hal0: { current: '0.3.0-alpha.1', available: null, channel: 'stable' },
      flm: { current: 'v0.9.42', source: 'manual-deb' },
      autoCheck: true,
    }
  })
}

function card(page: Page, title: string) {
  return page.locator('.ov-card').filter({ has: page.locator('.ov-card-t', { hasText: new RegExp(`^${title}$`) }) })
}

test.describe('Overview v2 (#dashboard)', () => {
  test('idle box renders every section', async ({ page }) => {
    await seedSlots(page, idleBox())
    await upToDate(page)
    await page.goto('/#dashboard')

    // Now band: the arbiter's mode, then the memory bar under it.
    const now = page.locator('.ov-now')
    await expect(now).toBeVisible()
    await expect(now).toContainText('chat mode')

    // Nothing needs the operator: the inbox collapses to its all-clear line.
    await expect(page.locator('.ov-inbox-clear')).toContainText('All clear')
    await expect(page.locator('.ov-att')).toHaveCount(0)

    // Serving now: one lane per device group.
    await expect(page.locator('.ov-lane')).toHaveCount(4)

    // The cards, by title.
    await expect(card(page, 'Agents')).toBeVisible()
    const usage = card(page, 'Usage')
    await expect(usage).toBeVisible()
    await expect(usage.locator('.ov-tabs button[aria-pressed="true"]')).toHaveText('24h')
    await expect(card(page, 'Hardware')).toBeVisible()
    await expect(card(page, 'Recent')).toBeVisible()
    await expect(card(page, 'Image engine')).toBeVisible()
    await expect(card(page, 'Services')).toBeVisible()
    await expect(card(page, 'Memory')).toBeVisible()
    await expect(page.locator('.ov-verstrip')).toContainText('up to date')

    // The fixed-band dashboard and its swap-in affordance are gone.
    await expect(page.locator('.rd-card')).toHaveCount(0)
    expect(await page.locator('.main').innerText()).not.toContain('⇄')
  })

  test('busy box fills needs-you, errors first', async ({ page }) => {
    await seedSlots(page, [...idleBox(), { ...base, name: 'brain', state: 'error', backend: 'rocm', device: 'gpu-rocm', model: 'qwen3-32b', port: 8104, container_status: 'crashed' }])
    await upToDate(page)
    await page.route('**/api/doctor', (route) => json(route, failingDoctor()))
    await page.goto('/#dashboard')

    const rows = page.locator('.ov-att')
    await expect(rows).toHaveCount(2)
    await expect(page.locator('.ov-inbox-clear')).toHaveCount(0)

    // The doctor's `fail` is an err row; the crashed slot is a warn row. Errors sort first.
    await expect(rows.nth(0)).toHaveClass(/\berr\b/)
    await expect(rows.nth(0)).toContainText('Model store is almost full')
    await expect(rows.nth(1)).toHaveClass(/\bwarn\b/)
    await expect(rows.nth(1)).toContainText('brain')
    // The slot row offers Restart and View slot (src/dash/notifications.jsx). Logs live on the
    // Serving now tile, whose actions show on hover or focus.
    await expect(rows.nth(1).getByRole('button', { name: 'Restart' })).toBeVisible()
    await expect(rows.nth(1).getByRole('button', { name: 'View slot' })).toBeVisible()
    const tile = page.locator('.ov-tile', { hasText: 'brain' })
    await tile.hover()
    await expect(tile.locator('.ov-acts button', { hasText: /^logs$/ })).toBeVisible()
  })

  test('usage window persists across a reload and drives the request', async ({ page }) => {
    await seedSlots(page, idleBox())
    const windows: string[] = []
    await page.route(/\/api\/stats(\?|$)/, (route) => {
      windows.push(new URL(route.request().url()).searchParams.get('window') ?? '')
      return json(route, {
        window: { from: '2026-10-22T12:00:00Z', to: '2026-10-29T12:00:00Z', bucket: '1d' },
        totals: { requests: 0, ok: 0, errors: 0, tokens_completed: 0 },
        by_model: [],
      })
    })
    await page.goto('/#dashboard')

    const tabs = card(page, 'Usage').locator('.ov-tabs')
    await expect(tabs.locator('button[aria-pressed="true"]')).toHaveText('24h')
    await tabs.getByRole('button', { name: '7d', exact: true }).click()
    await expect(tabs.locator('button[aria-pressed="true"]')).toHaveText('7d')
    await expect.poll(() => windows).toContain('7d')

    windows.length = 0
    await page.reload()
    await expect(card(page, 'Usage').locator('.ov-tabs button[aria-pressed="true"]')).toHaveText('7d')
    await expect.poll(() => windows).toContain('7d')
    expect(windows).not.toContain('24h')
  })

  test('memory card follows the gate', async ({ page }) => {
    await seedSlots(page, idleBox())
    // Control: memory on (the default) shows the card.
    await page.goto('/#dashboard')
    await expect(card(page, 'Memory')).toBeVisible()

    // /api/status is in-bundle under forced mock; the flag is how a spec turns memory off.
    await page.addInitScript(() => {
      ;(window as unknown as { __hal0MockMemoryEnabled?: boolean }).__hal0MockMemoryEnabled = false
    })
    await page.reload()
    // Wait for the rest of the page so an absent card is not just an unmounted one.
    await expect(card(page, 'Services')).toBeVisible()
    await expect(card(page, 'Memory')).toHaveCount(0)
  })

  test('cpu-only box shows its own truth', async ({ page }) => {
    await seedSlots(page, [{ ...base, name: 'utility', state: 'ready', backend: 'cpu', device: 'cpu', model: 'qwen3-0.6b', port: 8103, isDefault: true }])
    await passthrough(page, '/api/hardware')
    await page.route('**/api/hardware', (route) => json(route, cpuOnlyHardware()))
    await page.goto('/#dashboard')

    const gpuRow = card(page, 'Hardware').locator('.ov-hw').filter({ hasText: /^\s*gpu/ })
    await expect(gpuRow).toContainText('no GPU access')
    // No GPU figure anywhere: the Now band's facts carry no GPU percentage.
    const facts = page.locator('.ov-now-facts').first()
    await expect(facts).toBeVisible()
    await expect(facts).not.toContainText(/gpu\s*\d+\s*%/i)
    await expect(page.locator('.ov-now')).not.toContainText(/gpu\s*\d+\s*%/i)
  })

  test('stacks at phone width', async ({ page }) => {
    // A populated box, not a quiet one: the widest content is what overflows a phone (a full
    // memory bar legend, a model table, long model names, serving tiles), so the page must hold
    // it all at 400 px.
    await seedSlots(page, [servingSlot(), ...idleBox().slice(1)])
    await page.route('**/api/stats/hardware', (route) => json(route, populatedHardwareStats()))
    await page.route(/\/api\/stats(\?|$)/, (route) => json(route, populatedUsage()))
    await page.setViewportSize({ width: 400, height: 900 })
    await page.goto('/#dashboard')
    await expect(page.locator('.ov-now')).toBeVisible()
    await expect(page.locator('.ov-mem-leg i').first()).toBeVisible()
    await expect(page.locator('.ov-mtable tbody tr').first()).toBeVisible()
    await expect(card(page, 'Memory')).toBeVisible()

    // The page, and the app column the page sits in: a document-level check alone passes while
    // `.main` is wider than the viewport, because the shell clips the overflow.
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth)
    expect(scrollWidth).toBeLessThanOrEqual(400)
    const main = await page.locator('.main').evaluate((e) => ({ client: e.clientWidth, scroll: e.scrollWidth }))
    expect(main.client).toBeLessThanOrEqual(400)
    expect(main.scroll).toBeLessThanOrEqual(main.client)

    // Every column spans the page: the 8/4, 7/5 and 4/4/4 bands collapse to one column.
    const cols = page.locator('.c8, .c7, .c5, .c4')
    expect(await cols.count()).toBe(7)
    const widths = await cols.evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().width)))
    const content = await page.locator('.ov-view').evaluate((e) => {
      const cs = getComputedStyle(e)
      return Math.round(e.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight))
    })
    for (const w of widths) expect(w).toBeGreaterThanOrEqual(content - 2)
  })

  test('serving slot is green with a sparkline', async ({ page }) => {
    await seedSlots(page, [servingSlot()])
    await page.route('**/api/system-stats', (route) =>
      json(route, {
        ts: '2026-10-29T12:00:00Z',
        fleet: { gpu_util: 0.6, vram_bytes: null, gtt_bytes: null, power_w: null, temp_c: null },
        slots: [{ slot_id: 'primary', state: 'serving', vram_bytes: null, inflight: 2, kv_used: 120000, ts: '2026-10-29T12:00:00Z' }],
      }),
    )
    await page.route(/\/api\/stats\/throughput\/history(\?|$)/, (route) =>
      json(route, {
        window_s: 100,
        bucket_s: 5,
        samples: [],
        per_slot: { primary: [10, 22, 31, 38, 41, 42.5] },
      }),
    )
    await page.goto('/#dashboard')

    const tile = page.locator('.ov-tile', { hasText: 'primary' })
    await expect(tile).toBeVisible({ timeout: 10_000 })
    await expect(tile).toHaveClass(/\bserving\b/)

    const style = await tile.locator('.ov-dot.serving').evaluate((el) => {
      const cs = getComputedStyle(el)
      return { animationName: cs.animationName, boxShadow: cs.boxShadow }
    })
    // The design system's only looping animation is `pulse`, reserved for in-flight work.
    expect(style.animationName).toContain('pulse')
    expect(style.boxShadow).not.toBe('none')

    await expect(tile.locator('svg').first()).toBeVisible()
  })
})
