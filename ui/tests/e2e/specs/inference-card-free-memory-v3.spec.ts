/**
 * inference-card-free-memory-v3 — #1900 regression.
 *
 * The Slots-page Inference Engine card footer ("N serving · X GB free")
 * and the page-level telemetry ruler ("memory · … free Y GB", same
 * screen, telemetry-header.jsx ThRuler) MUST derive free memory from the
 * same basis and agree within rounding — both figures come from
 * useMemoryMapModel() (memory-map.jsx).
 *
 * Two ways the two numbers used to disagree, both covered here:
 *   1. `inference-pane.jsx`'s footer subtracted `mm.self.gttUsedGb` (a raw
 *      host-wide GTT stat) instead of `mm.self.modelUsedGb` (the reconciled
 *      per-slot sum the ruler itself renders against) — wrong basis even
 *      on a compute-capable (ROCm) box.
 *   2. `memory-map.jsx` gated the whole GTT-pool framing on
 *      `computeCapable` (rocm-smi presence) alone, so on a Vulkan-only box
 *      (no ROCm stack, `vulkan_capable: true`) `hasGpu` was false: the pool
 *      fell back to plain system RAM and `gttUsedGb` hard-zeroed — the
 *      footer read free == the ENTIRE pool, ignoring every loaded model.
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
 * Once the footer fix is in, footer and `.th-ruler-h` agree BY
 * CONSTRUCTION (both are pool.totalGb - self.modelUsedGb off one hook), so
 * an agreement assertion alone can never detect a `hasGpu` regression. The
 * observable symptom of that half is the POOL BASIS — "GPU pool (GTT)"
 * framing vs a silent fallback to system RAM — which test 1 asserts
 * directly.
 */
import { test, expect, json, type Page } from '../fixtures/apiMock'

// Fixture-derived pool ceiling both tests drive via /api/stats/hardware.
const GPU_POOL_TOTAL_MB = 107_520
const GPU_POOL_TOTAL_GB = GPU_POOL_TOTAL_MB / 1024 // 105

const footerFreeGb = async (page: Page) => {
  const status = page.locator('.infer-pane .body-status')
  // The GTT-cap-gated clause only appears once the hardware + stats queries
  // land (mm.pool.totalGb > 0) — wait for it rather than racing the first
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
  test('vulkan-only box (no rocm-smi): GTT pool framing survives, footer agrees with the ruler', async ({
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
        gtt_total_mb: GPU_POOL_TOTAL_MB,
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
        ram_total_mb: 96_000,
        gpu_vram_total_mb: GPU_POOL_TOTAL_MB,
        gtt_used_mb: 40_000, // host-wide GTT — real, non-zero on a vulkan box
        host: { configured: false, detected: false },
      }),
    )
    await page.goto('/#slots')
    // Pool-basis assertion — the ONLY observable that catches a `hasGpu`
    // regression (see header note). Pre-fix, vulkan-only ⇒ hasGpu false ⇒
    // pool silently falls back to system RAM and the ruler reads
    // "memory · system …" with gttUsedGb hard-zeroed.
    await expect(rulerLocator(page)).toContainText('GPU pool (GTT)')
    const footer = await footerFreeGb(page)
    const ruler = await rulerFreeGb(page)
    // Loaded models must be subtracted: free strictly below the pool
    // ceiling (fixture-derived, not a magic constant).
    expect(footer).toBeLessThan(GPU_POOL_TOTAL_GB)
    // Same basis → agree within 1 GB (footer rounds to whole GB, ruler to
    // one decimal).
    expect(Math.abs(footer - ruler)).toBeLessThanOrEqual(1)
  })

  test('rocm-only box: footer free matches the ruler, not the raw host-wide GTT stat', async ({
    page,
  }) => {
    // ROCm-only box (vulkan_capable: false — a real contrast to test 1,
    // not a restatement of the seed) where the host-wide gtt_used_mb (all
    // GPU processes) diverges sharply from the reconciled per-slot mem_mb
    // sum the ruler uses — the ct105-prod ~30 GB mismatch from the issue.
    // /api/stats/hardware is not allowlisted, so this route IS effective.
    await page.route('**/api/stats/hardware', (route) =>
      json(route, {
        ram_total_mb: 131_072,
        ram_used_mb: 100_000,
        ram_available_mb: 31_072,
        gpu_vram_total_mb: GPU_POOL_TOTAL_MB,
        gtt_used_mb: 90_000, // host-wide — far above the loaded-model sum
        vram_used_mb: 0,
        npu_status: { ok: true, model_mb: 1100 },
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
    // ROCm-only must keep the GPU-pool framing too (computeCapable alone
    // satisfies the widened gate).
    await expect(rulerLocator(page)).toContainText('GPU pool (GTT)')
    const footer = await footerFreeGb(page)
    const ruler = await rulerFreeGb(page)
    expect(Math.abs(footer - ruler)).toBeLessThanOrEqual(1)
  })
})
