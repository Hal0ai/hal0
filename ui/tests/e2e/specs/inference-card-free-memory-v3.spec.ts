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
 * The default mock host (mock-data.ts) IS a Vulkan-only box
 * (`compute_capable: false, vulkan_capable: true`) with several loaded
 * models carrying real `mem_mb` — the exact rc-validate repro shape.
 */
import { test, expect, json, type Page } from '../fixtures/apiMock'

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

const rulerFreeGb = async (page: Page) => {
  const ruler = page.getByTestId('telemetry-header').locator('.th-ruler-h')
  await expect(ruler).toContainText(/free/)
  const text = (await ruler.textContent()) || ''
  const m = text.match(/free\s*([\d.]+)\s*GB/)
  expect(m, `ruler must render "free N GB": "${text}"`).not.toBeNull()
  return Number(m![1])
}

test.describe('Inference Engine card footer reconciles with the memory ruler (#1900)', () => {
  test('vulkan-only box (no rocm-smi): footer free != the whole pool, agrees with the ruler', async ({
    page,
  }) => {
    // Default mock host is already vulkan-only (compute_capable: false,
    // vulkan_capable: true) with several loaded models — the rc-validate
    // repro shape verbatim. No stats/hardware override needed: the bug
    // reproduces off the plain default mock.
    await page.goto('/#slots')
    const footer = await footerFreeGb(page)
    const ruler = await rulerFreeGb(page)
    // Must reflect the loaded models, not the whole pool — several are
    // loaded and mem_mb-attributed (mock-data.ts), so free must be
    // meaningfully below the pool total (previously footer == total,
    // ignoring every loaded model entirely).
    expect(footer).toBeLessThan(120)
    // Same basis → agree within 1 GB (footer rounds to whole GB, ruler to
    // one decimal).
    expect(Math.abs(footer - ruler)).toBeLessThanOrEqual(1)
  })

  test('rocm box: footer free matches the ruler, not the raw host-wide GTT stat', async ({
    page,
  }) => {
    // ROCm-capable box where the host-wide gtt_used_mb (all GPU processes)
    // diverges sharply from the reconciled per-slot mem_mb sum the ruler
    // uses — the ct105-prod ~30 GB mismatch from the issue.
    await page.route('**/api/stats/hardware', (route) =>
      json(route, {
        ram_total_mb: 131_072,
        ram_used_mb: 100_000,
        ram_available_mb: 31_072,
        gpu_vram_total_mb: 107_520,
        gtt_used_mb: 90_000, // host-wide — far above the loaded-model sum
        vram_used_mb: 0,
        npu_status: { ok: true, model_mb: 1100 },
        host: { configured: false, detected: false },
      }),
    )
    await page.addInitScript(() => {
      let stored: any = undefined
      Object.defineProperty(window, 'HAL0_DATA', {
        set(v: any) {
          stored = {
            ...v,
            host: {
              ...v.host,
              gpus: [{ ...(v.host?.gpus?.[0] || {}), compute_capable: true, vulkan_capable: true }],
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
    const footer = await footerFreeGb(page)
    const ruler = await rulerFreeGb(page)
    expect(Math.abs(footer - ruler)).toBeLessThanOrEqual(1)
  })
})
