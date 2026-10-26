/**
 * memory-map-v3 — the dashboard's memory surface.
 *
 * The dashboard's memory surface is the full-width UNIFIED MEMORY hero
 * (dashboard-redesign .rd-mem-*): slot allocations render as colored blocks INSIDE the pool
 * bar, with a striped "other guests" block and a free remainder.
 *
 * Every memory figure on these screens comes from the backend memory budget
 * (ADR-0039): /api/stats/hardware `memory`, read through useMemoryMapModel().
 * The raw counters the mock also carries (ram_total_mb, ram_used_mb,
 * gtt_used_mb) are deliberately DIFFERENT from the budget's figures, so a
 * screen that fell back to them would fail here. The semantic guards:
 *   - the budget's per-slot rows render non-zero blocks + legend rows, each
 *     under its own name; the image engine is named as such
 *   - co-resident slots keep DISTINCT stable colours (never one device hue)
 *   - container slots attribute like any other slot (#660)
 *   - the free label is the budget's `free_gb`, not the pool total minus the
 *     drawn blocks (the attribution can overshoot the kernel's live counter)
 *   - the health strip and the top bar show this box's RAM over its limit,
 *     not over the GPU pool
 *   - no Proxmox nudge on the dashboard surface (expanded-variant only)
 *
 * isSlotLive/limitedBy logic remains
 * covered by slot-live-equivalence-v3.spec.ts and the useMemoryMapModel
 * consumers. A box where the pool is shared with a capped guest is covered
 * by memory-budget-capped-guest-v3.spec.ts.
 */
import { test, expect, json } from '../fixtures/apiMock'
import type { Page } from '@playwright/test'

// The reference box, as /api/stats/hardware serves it under `memory`
// (MemoryBudget.as_dict()): a shared 116 GB pool, 43.4 GB of it in use by the
// kernel's own count. This box's slots are attributed 39.8 GB and the image
// engine 6.6 GB — 46.4 GB in all, 3.0 GB more than the counter — so the
// budget's free (72.6) is larger than the 69.6 the drawn blocks leave. The
// slot names are the e2e seed's (data.jsx), so legend rows and ruler links
// resolve against real slots; the figures are those of the unit fixtures in
// src/dash/__tests__/memory-map-pure.test.ts. `this_box` excludes the img
// slot: its memory is `image_engine_gb`, and the backend names the slot that
// hosts the engine (`image_engine_slot`). ComfyUI is not answering on this box,
// so the engine's figure is the img slot's own row: `image_engine_estimated`.
const referenceBox = (thisBox?: Array<{ slot: string; gb: number; state: string }>) => ({
  gpu: {
    kind: 'shared',
    total_gb: 116,
    used_gb: 43.4,
    this_box: thisBox ?? [
      { slot: 'embed', gb: 2.8, state: 'ready' },
      { slot: 'tts', gb: 6.2, state: 'ready' },
      { slot: 'primary', gb: 30.8, state: 'serving' },
    ],
    image_engine_gb: 6.6,
    image_engine_slot: 'img',
    image_engine_estimated: true,
    other_guests_gb: 0,
    attribution_excess_gb: 3.0,
    free_gb: 72.6,
  },
  ram: { limit_gb: 105.5, used_gb: 16.1, free_gb: 89.4, source: 'container' },
  render_free_gb: 107.4,
})

function mockStatsHardware(page: Page, host: object, overrides: object = {}) {
  return page.route('**/api/stats/hardware', (route) =>
    json(route, {
      // Raw counters the dashboard no longer reads for memory figures; they
      // disagree with `memory` on purpose (40000 MB = 39.1 GB, not 16.1).
      ram_total_mb: 96000,
      ram_used_mb: 40000,
      ram_available_mb: 56000,
      gtt_used_mb: 6200,
      vram_used_mb: 0,
      npu_status: { ok: true, model_mb: 1100 },
      memory: referenceBox(),
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
    // Pool bar with at least one attributed slot block (the budget's per-slot
    // rows) plus the free remainder.
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

  test('free is the budget free, the image engine is named, and the blocks are the budget rows', async ({
    page,
  }) => {
    // 30.8 + 6.2 + 2.8 slot GB + the 6.6 GB image engine = 46.4 GB drawn, but
    // the kernel's own count is 43.4 GB, so the budget's free is 72.6 — not the
    // 69.6 (116 - 46.4) the drawn blocks leave. One screen, one free number:
    // the card's label is the budget's, the same figure the top bar shows.
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#dashboard')
    const card = page.locator('.rd-mem-card')
    await expect(card).toBeVisible()
    await expect(card.locator('.rd-mem-free')).toHaveText('free · 72.6 GB')
    await expect(card).not.toContainText('69.6')
    // The img slot's memory is the image engine's, shown under its own name;
    // the per-slot figures are estimates and say so, and so is the engine's
    // while the img slot's row stands in for ComfyUI's own reading.
    const legend = card.locator('.rd-mem-legend')
    await expect(legend).toContainText('image engine 6.6G')
    await expect(legend).toContainText('primary 30.8G')
    await expect(legend).toContainText('tts 6.2G')
    await expect(card.locator('.rd-mem-seg[title^="primary"]')).toHaveAttribute(
      'title',
      /^primary · 30\.8 GB \(estimated\) · /,
    )
    await expect(card.locator('.rd-mem-seg[title^="image engine"]')).toHaveAttribute(
      'title',
      /^image engine · 6\.6 GB \(estimated\) · /,
    )
    // Nothing else holds the pool: no striped block.
    await expect(card.locator('.rd-mem-seg-system')).toHaveCount(0)
  })

  test('container slot attributed as a block in the pool bar (#660)', async ({ page }) => {
    // With VITE_MOCK_HAL0=1 the mock shim reads HAL0_DATA directly;
    // inject the container slot via addInitScript before data.jsx runs.
    const containerSlot = {
      name: 'primary-container', type: 'llm', device: 'gpu-rocm',
      device_class: 'gpu', backend: 'rocm',
      model: 'qwen3.6-35b-a3b-q4_k_m', model_id: 'qwen3.6-35b-a3b',
      group: 'chat', state: 'ready', port: 8096,
      runtime: 'container',
      profile: 'rocm-mtp',
      image: 'ghcr.io/hal0ai/hal0-runner-standard:2026.10.11',
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
    // The attribution is the budget's: the container slot is a row of
    // `this_box`, 22.4 GB, like any other slot.
    await mockStatsHardware(page, { configured: false, detected: false }, {
      memory: referenceBox([{ slot: 'primary-container', gb: 22.4, state: 'serving' }]),
    })
    await page.goto('/#dashboard')
    const card = page.locator('.rd-mem-card')
    await expect(card).toBeVisible()
    // The 22.4 GB container slot must register in the legend with its name…
    await expect(card.locator('.rd-mem-legend')).toContainText('primary-container 22.4G')
    // …and attribute as a non-zero block in the bar, alongside the free
    // remainder. (The model attributes ONLY the budget's rows — the mock
    // slots' own mem_mb is not read, and a slot the budget omits is not drawn.)
    await expect(card.locator('.rd-mem-bar .rd-mem-seg[title^="primary-container"]')).toHaveCount(1)
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

  test('health strip shows RAM used over the RAM limit, not over the GPU pool', async ({ page }) => {
    // The 5-cell health strip's RAM cell reads the budget's RAM block: used
    // over the container's limit. It used to divide RAM used by the GPU pool
    // ("unified memory", 39.1/128 GB), which misstated a box whose RAM is capped
    // below the pool, and it read the raw ram_used_mb counter.
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#dashboard')
    const cell = page
      .locator('.rd-health-cell')
      .filter({ has: page.locator('.rd-health-k', { hasText: /^RAM$/ }) })
    await expect(cell).toBeVisible()
    // 16.1 GB used of the 105.5 GB container limit, shown as 106.
    await expect(cell.locator('.rd-health-v')).toHaveText('16.1/106 GB')
    await expect(cell.locator('.rd-health-v')).toHaveAttribute('title', 'container limit')
    // Not the raw 40000 MB counter (39.1 GB), not the 116 GB pool.
    await expect(cell).not.toContainText('39.1')
    await expect(cell).not.toContainText('/116')
    await expect(page.locator('.rd-health-k', { hasText: 'unified memory' })).toHaveCount(0)
  })

  // Live screenshot feedback (2026-08-22): the operator spotted the
  // Slots-page header's own CPU memory gauge cell (telemetry-header.jsx's
  // ThCellCpuMem, a DIFFERENT file than #1978 touched) still reading the raw
  // ram_total_mb (~94 GB) right next to ThRuler's memory-map band on the SAME
  // page showing the pool total — the class of divergence #1978 fixed for the
  // dashboard's three other "memory" figures, just missed here. The cell is now
  // "CPU · RAM": this box's RAM from the budget, over the RAM limit — neither
  // the raw counter nor the GPU pool.
  test('Slots-page "CPU · RAM" gauge reads the budget RAM over its limit, not raw ram_total_mb or the pool', async ({
    page,
  }) => {
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#slots')
    const hero = page.getByTestId('telemetry-header')
    const cell = hero.locator('.th-cell', { hasText: 'CPU · RAM' })
    await expect(cell).toBeVisible()
    await expect(cell.locator('.th-gauge-v')).toHaveText('16.1')
    // The 105.5 GB limit is the container's, so the caption says so.
    await expect(cell.locator('.th-gauge-cap')).toHaveText('/ 106 GB limit')
    await expect(cell).not.toContainText('39.1')
    await expect(cell).not.toContainText('/ 94 GB')
    await expect(cell).not.toContainText('/ 116 GB')
    await expect(cell).not.toContainText('/ 128 GB')
    await expect(hero.locator('.th-cell', { hasText: 'CPU · Memory' })).toHaveCount(0)
  })
})
