/**
 * comfyui-arbiter-v3 — V2 live-wired pane integration tests (Task 5.2).
 *
 * Replaces the removed V1 switchover/pin UI tests. Tests the V2 pane's
 * live API binding: page.route mocks /api/comfyui/* responses and asserts
 * that status fields render into hero/queue/telemetry and that control
 * buttons fire the correct POST endpoints.
 *
 * Convention (mirrors profiles-crud-v3.spec.ts):
 *   - VITE_MOCK_HAL0=1 build; /api/comfyui/* is NOT in MOCK_ALLOWLIST
 *   - page.route intercepts all comfyui routes
 *   - The V2 pane is on .comfy-v2-pane (slots page Image Gen tab)
 */

import { test, expect, json } from '../fixtures/apiMock'

// Full /api/comfyui/status shape with V2 extension fields
function comfyV2Status(overrides: Record<string, any> = {}) {
  return {
    mode: 'generation',
    reachable: true,
    engine: 'generating',
    container: { name: 'comfyui', state: 'running' },
    endpoint: ':8188',
    memory: {
      gtt_used_gb: 54,
      gtt_ceil_gb: 80,
      ram_used_gb: 61,
      ram_ceil_gb: 96,
      pressure: false,
    },
    queue: { running: 1, pending: 2 },
    util: 63,
    temp: 68.5,
    clock: 2.7,
    it_s: null,
    eta: null,
    step: null,
    inference: { hermes: false },
    inventory: { checkpoints: 6, video: 4, loras: 11, vae: 3 },
    switchover: { active: false, target: null, error: null },
    arbiter: {
      mode: 'img',
      pinned: false,
      saved_llm_slots: ['primary', 'agent'],
      idle_restore_at: null,
    },
    // V2 render-hero extension fields (not in base /status spec but accepted
    // by transformComfyuiStatus when present — same shape as mock fixture)
    active_render: {
      name: 'wan2.2-i2v',
      kind: '480p · 81 frames',
      pct: 72,
      eta: '~38s',
      node: 'KSampler (low-noise)',
      step: 3,
      total: 4,
      its: 1.9,
      loaded: 'wan-hi + wan-lo · umt5-xxl · wan-vae · 2 loras',
    },
    queue_jobs: [
      { name: 'qwen-image', kind: 'txt2img · 1328²' },
      { name: 'sdxl', kind: 'upscale 4×' },
    ],
    ...overrides,
  }
}

// Idle-engine variant — no active render, no pending jobs
function comfyV2Idle() {
  return comfyV2Status({
    engine: 'running',
    queue: { running: 0, pending: 0 },
    util: 0,
    active_render: null,
    queue_jobs: [],
  })
}

async function gotoImageTab(page: any) {
  await page.goto('/#slots')
  await page.waitForSelector('.slot-tab.comfy', { timeout: 10_000 })
  await page.click('.slot-tab.comfy')
  // V2 pane root has class .comfy-v2-pane
  await page.waitForSelector('.comfy-v2-pane', { timeout: 10_000 })
}

test.describe('ComfyUI V2 live-wired pane (Task 5.2)', () => {

  // ── 1. status renders into hero ──────────────────────────────────────────

  test('status: generating state renders job name + progress in hero', async ({ page }) => {
    await page.route('**/api/comfyui/status', (route: any) => json(route, comfyV2Status()))
    await gotoImageTab(page)

    const pane = page.locator('.comfy-v2-pane')
    await expect(pane).toContainText('wan2.2-i2v')
    await expect(pane.locator('.gbar').first()).toBeVisible()
    await expect(pane).toContainText('~38s')
    await expect(pane).toContainText('it/s')
  })

  // ── 2. status renders into queue ─────────────────────────────────────────

  test('status: queue rows bind — 1 running + 2 pending', async ({ page }) => {
    await page.route('**/api/comfyui/status', (route: any) => json(route, comfyV2Status()))
    await gotoImageTab(page)

    const pane = page.locator('.comfy-v2-pane')
    await expect(pane.locator('.qcard.row.running')).toBeVisible()
    await expect(pane.locator('.qcard.row.running .ldot.generating')).toBeVisible()

    const pending = pane.locator('.qcard.row.pending')
    await expect(pending).toHaveCount(2)
    await expect(pending.nth(0)).toContainText('qwen-image')
    await expect(pending.nth(1)).toContainText('sdxl')
  })

  // ── 3. status renders into telemetry gauges ──────────────────────────────

  test('status: GTT gauge shows memory values from /status memory block', async ({ page }) => {
    await page.route('**/api/comfyui/status', (route: any) => json(route, comfyV2Status()))
    await gotoImageTab(page)

    const pane = page.locator('.comfy-v2-pane')
    const gauge = pane.locator('.gauge').first()
    await expect(gauge).toBeVisible()
    await expect(gauge).toContainText('gtt')
    // gtt_used_gb: 54 should appear in sub label
    await expect(gauge).toContainText('54')
  })

  test('status: device telemetry shows util, temp, and clock from /status', async ({ page }) => {
    await page.route('**/api/comfyui/status', (route: any) => json(route, comfyV2Status()))
    await gotoImageTab(page)

    const pane = page.locator('.comfy-v2-pane')
    await expect(pane).toContainText('63%')
    await expect(pane).toContainText('68.5°C')
    await expect(pane).toContainText('2.7GHz')
  })

  // ── 4. Cancel render fires POST /api/comfyui/render/cancel ───────────────

  test('Cancel render button fires POST /render/cancel', async ({ page }) => {
    const posts: string[] = []
    await page.route('**/api/comfyui/status', (route: any) => json(route, comfyV2Status()))
    await page.route('**/api/comfyui/render/cancel', (route: any) => {
      posts.push(route.request().method())
      return json(route, { status: 'cancel_requested' }, 202)
    })

    await gotoImageTab(page)

    const cancelBtn = page.locator('.comfy-v2-pane button', { hasText: 'Cancel render' }).first()
    await expect(cancelBtn).toBeVisible()
    await cancelBtn.click()

    await expect.poll(() => posts.length, { timeout: 5_000 }).toBeGreaterThan(0)
    expect(posts[0]).toBe('POST')
  })

  // ── 5. Workflow chip is a link that opens ComfyUI ────────────────────────
  // The tags open ComfyUI's editor (matching the "opens in ComfyUI ↗" label).
  // True per-workflow auto-open via URL is blocked upstream
  // (comfyanonymous/ComfyUI#9858); the ?workflow=<file> param is a
  // forward-compatible breadcrumb that current ComfyUI ignores.
  test('workflow chip is an anchor that opens ComfyUI in a new tab', async ({ page }) => {
    await page.route('**/api/comfyui/status', (route: any) => json(route, comfyV2Status()))

    await gotoImageTab(page)

    const chips = page.locator('.comfy-v2-pane .flow')
    await expect(chips).toHaveCount(6)

    const first = chips.first()
    // Renders as a link, opens in a new tab.
    expect((await first.evaluate((el: Element) => el.tagName)).toLowerCase()).toBe('a')
    await expect(first).toHaveAttribute('target', '_blank')
    // Points at ComfyUI (:8188) and carries the curated workflow breadcrumb.
    const href = await first.getAttribute('href')
    expect(href).toContain(':8188')
    expect(href).toContain('workflow=')
  })

  // ── 6. Restart button fires POST /api/comfyui/restart ────────────────────

  test('footer Restart button fires POST /restart', async ({ page }) => {
    const posts: string[] = []
    await page.route('**/api/comfyui/status', (route: any) => json(route, comfyV2Status()))
    await page.route('**/api/comfyui/restart', (route: any) => {
      posts.push(route.request().method())
      return json(route, { status: 'restart_requested' }, 202)
    })

    await gotoImageTab(page)

    const restartBtn = page.locator('.comfy-v2-pane .wfoot .sctrl.restart')
    await expect(restartBtn).toBeVisible()
    await restartBtn.click()

    await expect.poll(() => posts.length, { timeout: 5_000 }).toBeGreaterThan(0)
    expect(posts[0]).toBe('POST')
  })

  // ── 7. Idle state: empty queue renders in-flow (no overlay lockup) ────────

  test('idle: empty-queue state renders in-flow, no click-blocking overlay', async ({ page }) => {
    await page.route('**/api/comfyui/status', (route: any) => json(route, comfyV2Idle()))
    await gotoImageTab(page)

    const pane = page.locator('.comfy-v2-pane')
    await expect(pane.locator('.queue-empty-state')).toBeVisible()

    // Open ComfyUI button must be clickable (would fail if overlay intercepts)
    const openBtn = pane.locator('button, a', { hasText: 'Open ComfyUI' }).first()
    await expect(openBtn).toBeVisible()
    await expect(openBtn).toBeEnabled()
    await openBtn.click({ trial: true })
  })

  // ── 8. Graceful degrade: absent active_render uses placeholder skeleton ───

  test('degrade: no active_render field → placeholder skeleton renders without crash', async ({ page }) => {
    // Status says 1 running but no active_render detail
    const statusNoDetail = comfyV2Status({ active_render: undefined, queue_jobs: undefined })
    delete statusNoDetail.active_render
    delete statusNoDetail.queue_jobs

    await page.route('**/api/comfyui/status', (route: any) => json(route, statusNoDetail))
    await gotoImageTab(page)

    const pane = page.locator('.comfy-v2-pane')
    // Pane must not crash — the card header should be visible
    await expect(pane.locator('.wcard-h')).toBeVisible()
    // running row is shown (1 running)
    await expect(pane.locator('.qcard.row.running')).toBeVisible()
  })
})
