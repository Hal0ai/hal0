/**
 * memory-map-v3 — the dashboard's memory surface.
 *
 * Dashboard-redesign: the compact MemoryMap sidebar widget on /#dashboard
 * was superseded by the full-width UNIFIED MEMORY hero (dashboard-redesign
 * .rd-mem-*): slot allocations render as colored blocks INSIDE the pool
 * bar, with a striped system block and a free remainder. The attribution
 * model is unchanged — useMemoryMapModel() (mem_mb contract, per-slot
 * Okabe–Ito colors) feeds both the retired sidebar and the new hero — so
 * the semantic guards carry over:
 *   - per-slot mem_mb attribution renders non-zero blocks + legend rows
 *   - co-resident slots keep DISTINCT stable colours (never one device hue)
 *   - container slots with mem_mb attribute like any other slot (#660)
 *   - no Proxmox nudge on the dashboard surface (expanded-variant only)
 *
 * The old sidebar-only guards (GTT-pool header label, headroom-line
 * removal) retired with the sidebar: the hero deliberately frames the FULL
 * unified pool (RAM total; host DIMM total in Proxmox mode) per the
 * redesign handoff, and never had a headroom line. isSlotLive/limitedBy
 * logic remains covered by slot-live-equivalence-v3.spec.ts and the
 * useMemoryMapModel consumers.
 */
import { test, expect, json } from '../fixtures/apiMock'
import type { Page } from '@playwright/test'

function mockStatsHardware(page: Page, host: object, overrides: object = {}) {
  return page.route('**/api/stats/hardware', (route) =>
    json(route, {
      ram_total_mb: 96000,
      ram_used_mb: 40000,
      ram_available_mb: 56000,
      gtt_used_mb: 6200,
      vram_used_mb: 0,
      npu_status: { ok: true, model_mb: 1100 },
      host,
      ...overrides,
    }),
  )
}

test.describe('Unified memory hero (dashboard)', () => {
  test('renders the pool bar with slot blocks, free remainder and legend', async ({ page }) => {
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#dashboard')
    const card = page.locator('.rd-mem-card')
    await expect(card).toBeVisible()
    // Pool bar with at least one attributed slot block (default mock slots
    // carry mem_mb) plus the free remainder.
    const bar = card.locator('.rd-mem-bar')
    await expect(bar).toBeVisible()
    await expect(bar.locator('.rd-mem-seg')).not.toHaveCount(0)
    await expect(bar.locator('.rd-mem-free')).toContainText('free')
    // Legend: one row per attributed slot + the click affordance hint.
    await expect(card.locator('.rd-mem-leg').first()).toBeVisible()
    await expect(card).toContainText('click a block → slot')
  })

  test('co-resident slots render distinct legend swatch colours', async ({ page }) => {
    // Each loaded model slot gets its OWN stable colour so co-resident
    // models are visually distinguishable — several default-mock slots share
    // device=gpu-rocm, which must NOT collapse to one device hue.
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#dashboard')
    const swatches = page.locator('.rd-mem-card .rd-mem-leg i')
    await expect(swatches.first()).toBeVisible()
    const count = await swatches.count()
    expect(count).toBeGreaterThanOrEqual(2)
    const c0 = await swatches.nth(0).evaluate((el) => getComputedStyle(el).backgroundColor)
    const c1 = await swatches.nth(1).evaluate((el) => getComputedStyle(el).backgroundColor)
    expect(c0).not.toBe(c1)
  })

  test('container slot mem_mb attributed as a block in the pool bar (#660)', async ({ page }) => {
    // With VITE_MOCK_HAL0=1 the mock shim reads HAL0_DATA directly;
    // inject the container slot via addInitScript before data.jsx runs.
    const containerSlot = {
      name: 'primary-container', type: 'llm', device: 'gpu-rocm',
      device_class: 'gpu', backend: 'rocm',
      model: 'qwen3.6-35b-a3b-q4_k_m', model_id: 'qwen3.6-35b-a3b',
      group: 'chat', state: 'ready', port: 8096,
      runtime: 'container',
      profile: 'rocm-mtp',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server',
      image_status: 'present',
      container_status: 'running',
      container_health: true,
      mem_mb: 22400,
    }
    await page.addInitScript((slot) => {
      let stored: any = undefined
      Object.defineProperty(window, 'HAL0_DATA', {
        set(v: any) {
          stored = { ...v, slots: [slot, ...(v.slots || [])] }
        },
        get() { return stored },
        configurable: true,
      })
    }, containerSlot)
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#dashboard')
    const card = page.locator('.rd-mem-card')
    await expect(card).toBeVisible()
    // The 22.4 GB container slot must register in the legend with its name…
    await expect(card.locator('.rd-mem-legend')).toContainText('primary-container')
    // …and attribute as a non-zero block in the bar, alongside the free
    // remainder. (With mem_mb present the model attributes ONLY measured
    // slots — un-measured mock slots correctly drop to zero, not a guess.)
    const segs = card.locator('.rd-mem-bar .rd-mem-seg:not(.rd-mem-seg-system)')
    const count = await segs.count()
    expect(count).toBeGreaterThanOrEqual(1)
    await expect(card.locator('.rd-mem-free')).toContainText('free')
  })

  test('no Proxmox nudge on the dashboard memory surface', async ({ page }) => {
    // Detected-but-unconfigured PVE: the "⚠ Hosted on Proxmox" nudge lives
    // only in the expanded hardware-page variant, never on the dashboard.
    await mockStatsHardware(page, {
      configured: false,
      detected: true,
      detection: 'detected',
      hint: 'Configure /etc/hal0/proxmox.json to see host pressure.',
    })
    await page.goto('/#dashboard')
    const card = page.locator('.rd-mem-card')
    await expect(card).toBeVisible()
    await expect(card).not.toContainText('Hosted on Proxmox')
    await expect(page.locator('.memmap-pve-nudge')).toHaveCount(0)
  })

  test('health strip mirrors the unified-memory used/total reading', async ({ page }) => {
    // The 5-cell health strip's "unified memory" cell reads the same
    // ram_used/ram_total counters the hero frames — never a fabricated 0.
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#dashboard')
    const cell = page.locator('.rd-health-cell', { hasText: 'unified memory' })
    await expect(cell).toBeVisible()
    // 40000 MB used / 96000 MB total → "39.1/94 GB" (mb→GB, round1/round).
    await expect(cell.locator('.rd-health-v')).toContainText('39.1')
    await expect(cell.locator('.rd-health-v')).toContainText('/94 GB')
  })
})
