/**
 * memory-budget-capped-guest-v3 — a second guest on a shared GPU pool, with a
 * RAM cap well below the pool.
 *
 * ADR-0039 gives every dashboard memory figure one backend owner:
 * /api/stats/hardware `memory` (MemoryBudget.as_dict()). This box is a 48 GB
 * container on a 116 GB shared pool whose other guests hold 43.4 GB of it; its
 * own slots hold nothing and the image engine is idle. The figures that used
 * to be re-derived on this screen from raw counters, and came out wrong here:
 *
 *   - the top bar's CPU cell divided RAM by the GPU pool (116 GB); the limit is
 *     the container's 48 GB, and the cell must say so ("/ 48 GB limit")
 *   - the 43.4 GB the pool counter shows is NOT this box's: it is drawn as the
 *     striped "other guests" block, never as system RAM or a slot's
 *   - the pool header names the pool ("GPU pool · shared"), and its headroom is
 *     the pool's free less the 2 GB safety margin (72.6 - 2 = 70.6). The 48 GB
 *     RAM cap does not bind the pool: GTT is not charged to the guest's cgroup.
 *   - the ruler's free label, the dashboard card's and the inference footer's
 *     are the budget's free_gb (72.6), one number across the screens
 *
 * The raw counters the mock also carries (ram_total_mb, gtt_used_mb, ...) are
 * set to values that would give different answers, so a screen that fell back
 * to them fails here. The fixture is the "capped second guest" of the unit
 * tests in src/dash/__tests__/memory-map-pure.test.ts.
 *
 * Headroom is rendered only in the top bar's ruler header (.th-ruler-h, on the
 * Slots page).
 */
import { test, expect, json, seedSlots, type Page } from '../fixtures/apiMock'

const cappedSecondGuest = () => ({
  gpu: {
    kind: 'shared',
    total_gb: 116,
    used_gb: 43.4,
    this_box: [],
    image_engine_gb: 0,
    other_guests_gb: 43.4,
    attribution_excess_gb: 0,
    free_gb: 72.6,
  },
  ram: { limit_gb: 48, used_gb: 2.2, free_gb: 45.8, source: 'container' },
  render_free_gb: 70.6,
})

// The guest has a slot configured, but nothing is loaded: the container is
// down and the model is not resident, so the budget attributes nothing to it.
// (A box with NO slots at all shows the empty "No slots configured" state, with
// no top bar.)
const idleSlot = {
  name: 'primary',
  type: 'llm',
  device: 'gpu-rocm',
  profile: 'rocm',
  runtime: 'container',
  container_status: 'exited',
  model: 'qwen3.6-27b-mtp',
  model_id: 'qwen3.6-27b-mtp',
  group: 'chat',
  state: 'offline',
  port: 8092,
  isDefault: true,
  metrics: {},
}

function mockCappedGuest(page: Page) {
  return page.route('**/api/stats/hardware', (route) =>
    json(route, {
      // Raw counters that disagree with the budget on purpose: 94 GB total,
      // 40000 MB (39.1 GB) used, 90000 MB (87.9 GB) of GTT.
      ram_total_mb: 96_000,
      ram_used_mb: 40_000,
      ram_available_mb: 56_000,
      gtt_used_mb: 90_000,
      vram_used_mb: 0,
      cpu_util: 0.25,
      npu_status: { ok: true, model_mb: 0 },
      memory: cappedSecondGuest(),
      host: { configured: false, detected: false },
    }),
  )
}

const header = (page: Page) => page.getByTestId('telemetry-header')
const ruler = (page: Page) => header(page).locator('.th-ruler')

test.describe('Memory budget: capped second guest on a shared GPU pool', () => {
  test.beforeEach(async ({ page }) => {
    await seedSlots(page, [idleSlot])
    await mockCappedGuest(page)
  })

  test('top bar CPU cell shows RAM used over the 48 GB container limit', async ({ page }) => {
    await page.goto('/#slots')
    const cell = header(page).locator('.th-cell', { hasText: 'CPU · RAM' })
    await expect(cell).toBeVisible()
    await expect(cell.locator('.th-gauge-cap')).toHaveText('/ 48 GB limit')
    await expect(cell.locator('.th-gauge-v')).toHaveText('2.2')
    // Neither the GPU pool nor the raw counters.
    await expect(cell).not.toContainText('/ 116 GB')
    await expect(cell).not.toContainText('/ 94 GB')
    await expect(cell).not.toContainText('39.1')
  })

  test('ruler names the shared pool and draws the other guests segment', async ({ page }) => {
    await page.goto('/#slots')
    const r = ruler(page)
    await expect(r.locator('.th-ruler-h')).toContainText('memory · GPU pool · shared')

    // The striped block is the 43.4 GB the pool counter shows that is not this
    // box's: labelled "other guests", 43.4 of the 116 GB pool wide.
    const other = r.locator('.th-ruler-seg-system')
    await expect(other).toHaveCount(1)
    await expect(other).toHaveAttribute('title', 'other guests · 43.4 GB')
    await expect(other).toContainText('other guests · 43.4G')
    const barBox = await r.locator('.th-ruler-bar').boundingBox()
    const otherBox = await other.boundingBox()
    expect(barBox).not.toBeNull()
    expect(otherBox).not.toBeNull()
    expect(otherBox!.width / barBox!.width).toBeCloseTo(43.4 / 116, 1)

    // No slot is resident and the image engine is idle: nothing else is drawn
    // but the free block.
    await expect(r.locator('.th-ruler-seg[role="link"]')).toHaveCount(0)
    await expect(r).not.toContainText('system')
    // GB ticks run to the pool total.
    await expect(r.locator('.th-ruler-ticks')).toContainText('116 GB')
  })

  test('ruler headroom reads 70.6 GB, limited by the pool, not by the RAM cap', async ({ page }) => {
    await page.goto('/#slots')
    const h = ruler(page).locator('.th-ruler-h')
    // 72.6 GB free in the pool, less the 2 GB safety margin.
    await expect(h.locator('b.ok')).toHaveText('70.6 GB')
    await expect(h).toContainText('headroom 70.6 GB')
    await expect(h.locator('.lim')).toHaveText('— limited by pool')
    // Not the 45.8 GB the 48 GB container has free, and no other-guests bytes
    // counted as this box's model memory.
    await expect(h).not.toContainText('45.8')
    await expect(h).toContainText('model 0.0 GB')
  })

  test('one free figure: ruler header, ruler block and inference footer all read 72.6', async ({ page }) => {
    await page.goto('/#slots')
    const r = ruler(page)
    await expect(r.locator('.th-ruler-free')).toHaveText('free · 72.6 GB')
    await expect(r.locator('.th-ruler-h')).toContainText('free 72.6 GB')
    // The footer rounds to whole GB.
    await expect(page.locator('.infer-pane .body-status')).toContainText('0 serving · 73 GB free')
  })

  test('dashboard card and health strip agree: other guests, 72.6 free, RAM over its limit', async ({
    page,
  }) => {
    await page.goto('/#dashboard')
    const card = page.locator('.rd-mem-card')
    await expect(card).toBeVisible()
    // The striped block is other guests, not a RAM-derived "system" block.
    const striped = card.locator('.rd-mem-seg-system')
    await expect(striped).toHaveCount(1)
    await expect(striped).toHaveAttribute('title', 'other guests · 43.4 GB')
    await expect(card.locator('.rd-mem-legend')).toContainText('other guests 43.4G')
    await expect(card.locator('.rd-mem-legend')).not.toContainText('system')
    await expect(card.locator('.rd-mem-free')).toHaveText('free · 72.6 GB')

    const ram = page
      .locator('.rd-health-cell')
      .filter({ has: page.locator('.rd-health-k', { hasText: /^RAM$/ }) })
    await expect(ram.locator('.rd-health-v')).toHaveText('2.2/48 GB')
    await expect(ram.locator('.rd-health-v')).toHaveAttribute('title', 'container limit')
  })
})
