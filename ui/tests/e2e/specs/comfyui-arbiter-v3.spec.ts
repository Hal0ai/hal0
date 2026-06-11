/**
 * comfyui-arbiter-v3 — Playwright coverage for the GPU arbiter surface
 * (Phase D8): global "GPU: image mode" banner, arbiter mode chip, pin
 * toggle, and idle auto-restore countdown on the ComfyUI pane.
 *
 * Harness mirrors profiles-crud-v3.spec.ts: VITE_MOCK_LEMONADE=1 build,
 * but /api/comfyui/* is NOT in the mockFetch allowlist, so page.route
 * intercepts both the status GET (read path) and the pin POST (raw:true
 * write path).
 */

import { test, expect, json } from '../fixtures/apiMock'

// Full /api/comfyui/status shape (D1-D7 backend): `mode` is arbiter-truth,
// `arbiter` is the new block (null when the arbiter is unavailable).
function comfyStatus(overrides: Record<string, any> = {}) {
  return {
    mode: 'inference',
    reachable: false,
    engine: 'stopped',
    container: { name: 'comfyui', state: 'exited' },
    endpoint: null,
    memory: null,
    queue: { running: 0, pending: 0 },
    inference: { lemonade: true, hermes: true },
    inventory: { checkpoints: 3, diffusion: 2, loras: 8, vae: 2 },
    switchover: { active: false, target: null, error: null },
    arbiter: null,
    ...overrides,
  }
}

const IMG_STATUS = () =>
  comfyStatus({
    mode: 'generation',
    reachable: true,
    engine: 'running',
    container: { name: 'comfyui', state: 'running' },
    endpoint: 'http://127.0.0.1:8188',
    inference: { lemonade: false, hermes: false },
    arbiter: {
      mode: 'img',
      pinned: false,
      saved_llm_slots: ['primary', 'agent'],
      // ~10 minutes out so the countdown renders a stable "~10m".
      idle_restore_at: Math.floor(Date.now() / 1000) + 600,
    },
  })

const LLM_STATUS = () =>
  comfyStatus({
    arbiter: { mode: 'llm', pinned: false, saved_llm_slots: [], idle_restore_at: null },
  })

async function gotoImageTab(page: any) {
  await page.goto('/#slots')
  await page.waitForSelector('.slot-tab.comfy', { timeout: 10_000 })
  await page.click('.slot-tab.comfy')
  await page.waitForSelector('.comfy-pane', { timeout: 10_000 })
}

test.describe('ComfyUI GPU arbiter — Phase D8', () => {
  // ── 1. img mode: global banner + image-mode chip + countdown ──────────────

  test('arbiter.mode img → global banner + image-mode chip + countdown', async ({ page }) => {
    await page.route('**/api/comfyui/status', (route) => json(route, IMG_STATUS()))

    await page.goto('/#slots')

    // Global banner renders in the view-banners strip (any route).
    const banner = page.locator('.banner', { hasText: 'GPU: image mode' })
    await expect(banner).toBeVisible({ timeout: 10_000 })
    await expect(banner).toContainText('LLM slots are stopped')

    // Pane: arbiter chip shows image mode + the auto-restore countdown.
    await page.click('.slot-tab.comfy')
    await page.waitForSelector('.comfy-pane', { timeout: 10_000 })
    const chip = page.locator('[data-testid="comfy-arbiter-chip"]')
    await expect(chip).toBeVisible()
    await expect(chip).toContainText('image mode')

    const countdown = page.locator('[data-testid="comfy-restore-countdown"]')
    await expect(countdown).toBeVisible()
    await expect(countdown).toContainText(/auto-restore in ~\d+m/)
  })

  // ── 2. pin toggle → POST /api/comfyui/pin {"pinned":true} ────────────────

  test('pin toggle POSTs {"pinned":true}; pinned reflected, countdown hidden', async ({ page }) => {
    const posts: any[] = []
    let pinned = false

    await page.route('**/api/comfyui/status', (route) => {
      const st = IMG_STATUS()
      st.arbiter!.pinned = pinned
      return json(route, st)
    })
    await page.route('**/api/comfyui/pin', (route) => {
      if (route.request().method() === 'POST') {
        try { posts.push(JSON.parse(route.request().postData() || '{}')) } catch { posts.push({}) }
        pinned = posts[posts.length - 1].pinned === true
        return json(route, { pinned })
      }
      return json(route, { pinned })
    })

    await gotoImageTab(page)

    const pinBtn = page.locator('[data-testid="comfy-pin-toggle"]')
    await expect(pinBtn).toBeVisible()
    await expect(page.locator('[data-testid="comfy-restore-countdown"]')).toBeVisible()

    await pinBtn.click()

    // POST body asserted.
    await expect.poll(() => posts.length).toBeGreaterThan(0)
    expect(posts[0]).toEqual({ pinned: true })

    // Pinned state reflected after refetch: pin active, countdown hidden.
    await expect(pinBtn).toContainText('pinned', { timeout: 10_000 })
    await expect(page.locator('[data-testid="comfy-restore-countdown"]')).not.toBeVisible()
  })

  // ── 3. llm mode: banner gone, inference chip ─────────────────────────────

  test('arbiter.mode llm → no banner, inference chip', async ({ page }) => {
    await page.route('**/api/comfyui/status', (route) => json(route, LLM_STATUS()))

    await gotoImageTab(page)

    await expect(page.locator('.banner', { hasText: 'GPU: image mode' })).not.toBeVisible()

    const chip = page.locator('[data-testid="comfy-arbiter-chip"]')
    await expect(chip).toBeVisible()
    await expect(chip).toContainText('inference')

    // No countdown in llm mode.
    await expect(page.locator('[data-testid="comfy-restore-countdown"]')).not.toBeVisible()
  })

  // ── 4. arbiter null: fail-soft — no chip/banner/pin, legacy pane intact ──

  test('arbiter null → no chip/banner/pin; legacy pane intact', async ({ page }) => {
    await page.route('**/api/comfyui/status', (route) => json(route, comfyStatus()))

    await gotoImageTab(page)

    await expect(page.locator('.banner', { hasText: 'GPU: image mode' })).not.toBeVisible()
    await expect(page.locator('[data-testid="comfy-arbiter-chip"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="comfy-pin-toggle"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="comfy-restore-countdown"]')).toHaveCount(0)

    // Legacy display: engine card + mode toggle + footer identity still render.
    await expect(page.locator('.comfy-pane .engine')).toBeVisible()
    await expect(page.locator('.comfy-pane .sw-wrap')).toBeVisible()
    await expect(page.locator('.comfy-pane .engine-foot')).toContainText('inference')
  })
})
