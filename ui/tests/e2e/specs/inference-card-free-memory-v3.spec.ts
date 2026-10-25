/**
 * inference-card-free-memory-v3 — #1900 regression, on the memory budget.
 *
 * The Slots-page Inference Engine card footer ("N serving · X GB free")
 * and the page-level telemetry ruler ("memory · … free Y GB", same
 * screen, telemetry-header.jsx ThRuler) MUST show the same free memory:
 * both read the backend memory budget (ADR-0039, /api/stats/hardware
 * `memory`) through useMemoryMapModel() — `gpu.free_gb` for the GPU pool,
 * `ram.free_gb` when the box has no GPU pool. The footer rounds to whole GB,
 * the ruler to one decimal; they agree within that rounding.
 *
 * Two ways the two numbers used to disagree, both still covered here:
 *   1. `inference-pane.jsx`'s footer subtracted a raw host-wide GTT stat
 *      (`gtt_used_mb`) instead of the figure the ruler renders — a wrong
 *      basis. Test 2 keeps that stat far above anything the budget says, so a
 *      footer that read it would miss.
 *   2. `memory-map.jsx` gated the whole GPU-pool framing on the probe's
 *      `compute_capable` alone, so on a Vulkan-only box (no ROCm stack,
 *      `vulkan_capable: true`) the pool fell back to plain system RAM and the
 *      footer read free == the ENTIRE pool, ignoring every loaded model.
 *      Whether a box has a GPU pool is now the backend's call (`memory.gpu`
 *      present or null), so the UI must not branch on the probe flags: test 1
 *      keeps the Vulkan-only probe and asserts the pool framing survives.
 *
 * Mock plumbing note (why the tests are shaped this way): the γ-suite runs
 * forced-mock (VITE_MOCK_HAL0=1), and `/api/hardware` is a non-networkFirst
 * MOCK_ALLOWLIST row (src/api/mock.ts) — it is substituted from
 * `window.HAL0_DATA.host` (data.jsx seed: compute_capable AND
 * vulkan_capable both true) BEFORE any fetch is issued, so a plain
 * `page.route('**' + '/api/hardware')` override never fires. To actually
 * drive the hardware shape a test must either claim the path via the
 * `__hal0MockPassthrough` escape hatch (#1498/#1527) and then route it, or
 * rewrite `window.HAL0_DATA` itself before the app boots. Test 1 uses the
 * passthrough; test 2 uses a HAL0_DATA setter shim. `/api/stats/hardware`
 * is NOT allowlisted, so plain routes work for it.
 *
 * Footer and `.th-ruler-h` agree BY CONSTRUCTION now (one hook, one figure),
 * so an agreement assertion alone is weak. These tests also pin the value —
 * 72.6 GB, the budget's free, which is neither the pool total minus the
 * drawn blocks (69.6) nor anything derivable from the raw counters — and the
 * POOL FRAMING ("GPU pool · shared" vs a silent fallback to system RAM).
 */
import { test, expect, json, type Page } from '../fixtures/apiMock'

// The reference box (see memory-map-v3.spec.ts): a shared 116 GB pool, 43.4 GB
// in use by the kernel's count, this box's slots + the image engine attributed
// 46.4 GB (3.0 over the counter). Free is the budget's 72.6, not 116 - 46.4.
const POOL_TOTAL_GB = 116
const BUDGET_FREE_GB = 72.6
const referenceBox = () => ({
  gpu: {
    kind: 'shared',
    total_gb: POOL_TOTAL_GB,
    used_gb: 43.4,
    this_box: [
      { slot: 'embed', gb: 2.8, state: 'ready' },
      { slot: 'tts', gb: 6.2, state: 'ready' },
      { slot: 'primary', gb: 30.8, state: 'serving' },
    ],
    image_engine_gb: 6.6,
    other_guests_gb: 0,
    attribution_excess_gb: 3.0,
    free_gb: BUDGET_FREE_GB,
  },
  ram: { limit_gb: 105.5, used_gb: 16.1, free_gb: 89.4, source: 'container' },
  render_free_gb: 107.4,
})

const footerFreeGb = async (page: Page) => {
  const status = page.locator('.infer-pane .body-status')
  // The free clause only appears once the stats query lands with a memory
  // block (mm.pool.totalGb > 0) — wait for it rather than racing the first
  // "N serving" render.
  await expect(status).toContainText(/GB free/)
  const text = (await status.textContent()) || ''
  const m = text.match(/([\d.]+)\s*GB free/)
  expect(m, `footer must render "N GB free": "${text}"`).not.toBeNull()
  return Number(m![1])
}

const rulerLocator = (page: Page) =>
  page.getByTestId('telemetry-header').locator('.th-ruler-h')

const rulerFreeGb = async (page: Page) => {
  const ruler = rulerLocator(page)
  await expect(ruler).toContainText(/free/)
  const text = (await ruler.textContent()) || ''
  const m = text.match(/free\s*([\d.]+)\s*GB/)
  expect(m, `ruler must render "free N GB": "${text}"`).not.toBeNull()
  return Number(m![1])
}

test.describe('Inference Engine card footer reconciles with the memory ruler (#1900)', () => {
  test('vulkan-only box (no rocm-smi): GPU pool framing survives, footer agrees with the ruler', async ({
    page,
  }) => {
    // A genuinely Vulkan-only host (compute_capable: false) — the
    // rc-validate repro shape. The default forced-mock seed is NOT this
    // box (it is compute+vulkan capable), so claim /api/hardware via the
    // passthrough escape hatch and drive it ourselves.
    await page.addInitScript(() => {
      ;(window as any).__hal0MockPassthrough = ['/api/hardware']
    })
    await page.route('**/api/hardware', (route) =>
      json(route, {
        ram_total_mb: 96_000,
        unified_memory_mb: 131_072,
        gtt_total_mb: 107_520,
        memory_kind: 'unified',
        gpus: [
          {
            vendor: 'amd',
            compute_capable: false,
            vulkan_capable: true,
            vram_mb: 81_920,
          },
        ],
        npu: { present: false },
      }),
    )
    await page.route('**/api/stats/hardware', (route) =>
      json(route, {
        // Raw counters the UI no longer reads for memory; the budget's figures
        // below differ from every one of them.
        ram_total_mb: 96_000,
        gpu_vram_total_mb: 107_520,
        gtt_used_mb: 40_000, // host-wide GTT — real, non-zero on a vulkan box
        memory: referenceBox(),
        host: { configured: false, detected: false },
      }),
    )
    await page.goto('/#slots')
    // Pool-basis assertion — the observable that catches a probe-flag gate on
    // the GPU framing (see header note). Pre-fix, vulkan-only ⇒ the pool
    // silently fell back to system RAM and the ruler read "memory · system …".
    await expect(rulerLocator(page)).toContainText('memory · GPU pool · shared')
    const footer = await footerFreeGb(page)
    const ruler = await rulerFreeGb(page)
    // Loaded models must be subtracted: free strictly below the pool
    // ceiling (fixture-derived, not a magic constant).
    expect(footer).toBeLessThan(POOL_TOTAL_GB)
    // The value is the budget's free: whole GB in the footer, one decimal in
    // the ruler. Not 116 - 46.4 = 69.6, which the drawn blocks leave.
    expect(ruler).toBe(BUDGET_FREE_GB)
    expect(footer).toBe(Math.round(BUDGET_FREE_GB))
    // Same basis → agree within 1 GB.
    expect(Math.abs(footer - ruler)).toBeLessThanOrEqual(1)
  })

  test('rocm-only box: footer free matches the ruler, not the raw host-wide GTT stat', async ({
    page,
  }) => {
    // ROCm-only box (vulkan_capable: false — a real contrast to test 1,
    // not a restatement of the seed) where the host-wide gtt_used_mb (all
    // GPU processes) diverges sharply from the budget's attribution — the
    // ~30 GB mismatch from the issue. A footer that read the raw
    // stat would show about 28 GB free here.
    // /api/stats/hardware is not allowlisted, so this route IS effective.
    await page.route('**/api/stats/hardware', (route) =>
      json(route, {
        ram_total_mb: 131_072,
        ram_used_mb: 100_000,
        ram_available_mb: 31_072,
        gpu_vram_total_mb: 107_520,
        gtt_used_mb: 90_000, // host-wide — far above the loaded-model sum
        vram_used_mb: 0,
        npu_status: { ok: true, model_mb: 1100 },
        memory: referenceBox(),
        host: { configured: false, detected: false },
      }),
    )
    // /api/hardware IS allowlisted (forced-mock substitutes it from
    // window.HAL0_DATA before any fetch), so shape the box by shimming the
    // HAL0_DATA seed itself rather than routing the request.
    await page.addInitScript(() => {
      let stored: any = undefined
      Object.defineProperty(window, 'HAL0_DATA', {
        set(v: any) {
          stored = {
            ...v,
            host: {
              ...v.host,
              gpus: [
                {
                  ...(v.host?.gpus?.[0] || {}),
                  compute_capable: true,
                  vulkan_capable: false,
                },
              ],
            },
          }
        },
        get() {
          return stored
        },
        configurable: true,
      })
    })
    await page.goto('/#slots')
    // ROCm-only keeps the GPU-pool framing too.
    await expect(rulerLocator(page)).toContainText('memory · GPU pool · shared')
    const footer = await footerFreeGb(page)
    const ruler = await rulerFreeGb(page)
    expect(ruler).toBe(BUDGET_FREE_GB)
    expect(footer).toBe(Math.round(BUDGET_FREE_GB))
    expect(Math.abs(footer - ruler)).toBeLessThanOrEqual(1)
  })

  test('box with no GPU pool: footer and ruler both read the free RAM', async ({ page }) => {
    // The backend reports no GPU lane (`gpu: null`), though the forced-mock
    // /api/hardware seed is compute+vulkan capable: the UI trusts the budget,
    // not the probe. Both figures fall back to the budget's RAM free, 89.4 GB
    // (limit 105.5, used 16.1) — never to the pool, never to 0.
    await page.route('**/api/stats/hardware', (route) =>
      json(route, {
        ram_total_mb: 131_072,
        ram_used_mb: 100_000,
        gtt_used_mb: 90_000,
        memory: { ...referenceBox(), gpu: null, render_free_gb: null },
        host: { configured: false, detected: false },
      }),
    )
    await page.goto('/#slots')
    await expect(rulerLocator(page)).toContainText('memory · RAM')
    await expect(rulerLocator(page)).not.toContainText('GPU pool')
    const footer = await footerFreeGb(page)
    const ruler = await rulerFreeGb(page)
    expect(ruler).toBe(89.4)
    expect(footer).toBe(89)
  })
})
