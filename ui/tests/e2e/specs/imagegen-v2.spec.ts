/**
 * imagegen-v2 — Playwright coverage for the V2 "Render hero" ImageGen pane.
 *
 * Tests: render-hero, queue rows, telemetry gauge, workflows strip, container
 * footer, empty-queue (no overlay lockup, recall PR #845), reduced-motion
 * freezes pulse animation.
 *
 * Harness: VITE_MOCK_HAL0=1 build. /api/comfyui/status is NOT in MOCK_ALLOWLIST
 * so page.route drives the pane (same convention as comfyui-arbiter-v3.spec.ts).
 */

import { test, expect, json } from '../fixtures/apiMock'

// V2 fixture — generating state (handoff demo values)
function comfyV2Status(overrides: Record<string, any> = {}) {
  return {
    mode: 'generation',
    reachable: true,
    engine: 'generating',
    container: { name: 'comfyui', state: 'running' },
    endpoint: ':8188',
    memory: { gtt_used_gb: 54, gtt_ceil_gb: 80, ram_used_gb: 61, ram_ceil_gb: 96, pressure: false },
    queue: { running: 1, pending: 2 },
    inference: { hermes: false },
    inventory: { checkpoints: 6, video: 4, loras: 11, vae: 3 },
    switchover: { active: false, target: null, error: null },
    arbiter: { mode: 'img', pinned: false, saved_llm_slots: ['primary', 'agent'], idle_restore_at: null },
    // V2 render-hero specific fields (mock)
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

// Empty-queue variant — no active render, no pending jobs
function comfyV2Empty() {
  return comfyV2Status({
    engine: 'running',
    queue: { running: 0, pending: 0 },
    active_render: null,
    queue_jobs: [],
  })
}

async function gotoImageTab(page: any) {
  await page.route('**/api/comfyui/status', (route: any) => json(route, comfyV2Status()))
  await page.goto('/#slots')
  await page.waitForSelector('.slot-tab.comfy', { timeout: 10_000 })
  await page.click('.slot-tab.comfy')
  // V2 pane root has class .comfy-v2-pane
  await page.waitForSelector('.comfy-v2-pane', { timeout: 10_000 })
}

test.describe('ImageGen V2 render-hero pane', () => {
  // ── 1. Render hero renders ──────────────────────────────────────────────
  test('render-hero: job name, progress bar, step info, it/s, eta, controls', async ({ page }) => {
    await gotoImageTab(page)
    const pane = page.locator('.comfy-v2-pane')

    // job name in hero
    await expect(pane.locator('.preview')).toBeVisible()
    await expect(pane).toContainText('wan2.2-i2v')

    // active-render progress block
    const jobBlock = pane.locator('.job').first()
    await expect(jobBlock).toBeVisible()
    await expect(jobBlock).toContainText('KSampler (low-noise)')
    // progress bar (gbar) renders
    await expect(pane.locator('.gbar').first()).toBeVisible()
    // step pips present
    await expect(pane.locator('.steps-pips')).toBeVisible()
    // it/s and eta present
    await expect(pane).toContainText('it/s')
    await expect(pane).toContainText('~38s')

    // Cancel + Open controls
    await expect(pane.locator('button', { hasText: 'Cancel render' })).toBeVisible()
    // Open ComfyUI may be a <button> or <a> depending on whether the live endpoint is known
    await expect(pane.locator('button, a').filter({ hasText: 'Open ComfyUI' }).first()).toBeVisible()
  })

  // ── 2. Queue rows ───────────────────────────────────────────────────────
  test('queue: running row + 2 pending rows render', async ({ page }) => {
    await gotoImageTab(page)
    const pane = page.locator('.comfy-v2-pane')

    // running row: ldot.generating
    await expect(pane.locator('.qcard.row.running')).toBeVisible()
    await expect(pane.locator('.qcard.row.running .ldot.generating')).toBeVisible()

    // 2 pending rows
    const pending = pane.locator('.qcard.row.pending')
    await expect(pending).toHaveCount(2)
    await expect(pending.nth(0)).toContainText('qwen-image')
    await expect(pending.nth(1)).toContainText('sdxl')
  })

  // ── 3. Telemetry gauge ──────────────────────────────────────────────────
  test('telemetry: GTT gauge renders with correct label + values', async ({ page }) => {
    await gotoImageTab(page)
    const pane = page.locator('.comfy-v2-pane')

    const gauge = pane.locator('.gauge').first()
    await expect(gauge).toBeVisible()
    // shows GTT label and used/ceil (54/80)
    await expect(gauge).toContainText('gtt')
    await expect(gauge).toContainText('54')

    // 2×2 device metric grid
    await expect(pane.locator('.mx2')).toBeVisible()
    await expect(pane.locator('.mx2')).toContainText('util')
    await expect(pane.locator('.mx2')).toContainText('temp')
    await expect(pane.locator('.mx2')).toContainText('clock')
  })

  // ── 4. Workflows strip ──────────────────────────────────────────────────
  test('workflows strip renders 6 flow buttons', async ({ page }) => {
    await gotoImageTab(page)
    const pane = page.locator('.comfy-v2-pane')

    await expect(pane.locator('.flows')).toBeVisible()
    await expect(pane.locator('.flow')).toHaveCount(6)
  })

  // ── 5. Models on share ─────────────────────────────────────────────────
  test('models block: inv pills render (checkpoints, loras, vae…)', async ({ page }) => {
    await gotoImageTab(page)
    const pane = page.locator('.comfy-v2-pane')

    await expect(pane.locator('.inv')).toBeVisible()
    // "6 checkpoints" pill
    await expect(pane.locator('.inv-pill').first()).toContainText('6')
  })

  // ── 6. Container footer ─────────────────────────────────────────────────
  test('footer: container identity + controls render', async ({ page }) => {
    await gotoImageTab(page)
    const pane = page.locator('.comfy-v2-pane')

    const foot = pane.locator('.wfoot')
    await expect(foot).toBeVisible()
    await expect(foot).toContainText('comfyui')
    await expect(foot).toContainText(':8188')
    // stop, restart, logs controls
    await expect(foot.locator('.sctrl.stop')).toBeVisible()
    await expect(foot.locator('.sctrl.restart')).toBeVisible()
  })

  // ── 7. Empty-queue state: NO click-blocking overlay ─────────────────────
  // Recall PR #845 lockup: position:absolute;inset:0 overlay ate all clicks.
  // Verify: a clickable element in/below the empty-queue area is reachable.
  test('empty-queue: no full-bleed overlay — clickable element underneath', async ({ page }) => {
    // Inject the empty-queue mock before navigation via window seam
    // (same pattern as window.__hal0UpdateStateOverride in mock.ts).
    const emptyMock = {
      engine: { name: 'ComfyUI', endpoint: ':8188', image: 'ghcr.io/hal0ai/comfyui@sha256:9f3c…b21a', restart: 'no' },
      run: null,
      queue: [],
      gtt: { used: 54, ceil: 80 },
      ram: { used: 61, ceil: 96 },
      stats: { util: 0, temp: 60, clk: 2.5, its: 0 },
    }
    await page.route('**/api/comfyui/status', (route: any) => json(route, comfyV2Empty()))
    await page.addInitScript((mock: any) => {
      (window as any).__comfyuiV2MockOverride = mock
    }, emptyMock)
    await page.goto('/#slots')
    await page.waitForSelector('.slot-tab.comfy', { timeout: 10_000 })
    await page.click('.slot-tab.comfy')
    await page.waitForSelector('.comfy-v2-pane', { timeout: 10_000 })

    const pane = page.locator('.comfy-v2-pane')
    // Empty-queue state shows an in-flow message (not a bleed overlay)
    await expect(pane.locator('.queue-empty-state')).toBeVisible()

    // Verify the Open ComfyUI button (below/near empty state) is clickable.
    // A full-bleed overlay would intercept this click and cause an error or wrong element.
    // Open ComfyUI may be <button> or <a> — check either
    const openBtn = pane.locator('button, a').filter({ hasText: 'Open ComfyUI' }).first()
    await expect(openBtn).toBeVisible()
    // isEnabled asserts no invisible overlay blocking it
    await expect(openBtn).toBeEnabled()
    // Actually clicking: if an overlay intercepts, Playwright strict mode raises
    await openBtn.click({ trial: true })
  })

  // ── 8. Reduced-motion: pulse animation frozen ───────────────────────────
  test('reduced-motion: ldot.generating has no animation', async ({ page }) => {
    // Emulate prefers-reduced-motion: reduce
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await gotoImageTab(page)
    const pane = page.locator('.comfy-v2-pane')

    // The generating dot should exist but its computed animation should be none/paused
    const dot = pane.locator('.ldot.generating').first()
    await expect(dot).toBeVisible()

    // Check animation-name is none (reduced-motion rule in CSS)
    const animName = await dot.evaluate((el) => getComputedStyle(el).animationName)
    expect(animName).toBe('none')
  })
})
