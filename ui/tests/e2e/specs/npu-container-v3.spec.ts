/**
 * npu-container-v3 — Phase A NPU container mode UI coverage.
 *
 * Verifies that when the npu chat slot has runtime="container" + npu: {asr, embed}
 * the NpuFlmStack:
 *   1. Renders asr toggle CHECKED and embed toggle UNCHECKED from slot.npu
 *      (the TOML-backed [npu] section — the only source of trio state).
 *   2. Toggle click issues PUT /api/slots/npu/config with {npu:{embed:true}}
 *      followed by POST /api/slots/npu/restart.
 *
 * READ path: VITE_MOCK_HAL0=1 short-circuits GET /api/slots in mockFetch
 * (client-side, before network). We inject slots via page.addInitScript to
 * override window.HAL0_DATA.slots — the same seam used by slot-card-container-v3.
 *
 * WRITE path: mutations use api(..., {raw:true}) which bypasses mockFetch and
 * hits the network. page.route intercepts those calls — the established pattern
 * from slots-wireup-v3.spec.ts.
 */
import { test, expect } from '../fixtures/apiMock'

// Container-runtime NPU slot fixture.
// asr=true, embed=false — exercises split-toggle state.
const NPU_CONTAINER_SLOT = {
  name: 'npu',
  type: 'llm',
  device: 'npu',
  device_class: 'npu',
  backend: null,
  model: 'qwen3-0.6b-npu',
  model_id: 'qwen3-0.6b-npu',
  group: 'npu',
  state: 'ready',
  port: 8098,
  runtime: 'container',
  profile: 'flm',
  image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:flm-npu-server',
  image_status: 'present',
  container_status: 'running',
  container_health: true,
  mem_mb: 1_200,
  npu: { asr: true, embed: false },
}

test.describe('NPU container mode — NpuFlmStack (#Phase-A)', () => {
  // ── Read-path: toggle state sourced from slot.npu ──────────────────────

  test('renders asr toggle CHECKED and embed toggle UNCHECKED from slot.npu', async ({ page }) => {
    // Inject the container NPU slot into HAL0_DATA before the app mounts.
    // mockFetch reads window.HAL0_DATA.slots for /api/slots (FORCED mode).
    await page.addInitScript((slot) => {
      // data.jsx sets HAL0_DATA at module load; addInitScript runs before
      // that, so we set a sentinel and let data.jsx merge/overwrite — but
      // data.jsx only sets HAL0_DATA ONCE. We patch it after DOMContentLoaded
      // so we win regardless of data.jsx timing.
      document.addEventListener('DOMContentLoaded', () => {
        if ((window as any).HAL0_DATA) {
          // Replace only the npu slots to keep the rest of the page rendering.
          const existing = (window as any).HAL0_DATA.slots || []
          // Remove any existing device=npu slots, inject ours.
          const withoutNpu = existing.filter((s: any) => s.device !== 'npu')
          ;(window as any).HAL0_DATA.slots = [...withoutNpu, slot]
        }
      })
    }, NPU_CONTAINER_SLOT)

    await page.goto('/#slots')
    // Wait until the NPU stack is visible (needs HAL0_DATA with our slot).
    const stack = page.locator('.npu-stack')
    await expect(stack).toBeVisible()

    // ASR switch must be on (aria-checked="true")
    const asrSwitch = stack.locator('.npu-trio .npu-mod').nth(1).locator('.npu-switch')
    await expect(asrSwitch).toHaveAttribute('aria-checked', 'true')

    // Embed switch must be off (aria-checked="false")
    const embedSwitch = stack.locator('.npu-trio .npu-mod').nth(2).locator('.npu-switch')
    await expect(embedSwitch).toHaveAttribute('aria-checked', 'false')

    // Footer echoes the TOML [npu] section state.
    await expect(stack.locator('.npu-stack-foot .npu-args')).toHaveText('npu = asr:on · embed:off')
  })

  // ── Write-path: toggle issues PUT /config + POST /restart ─────────────

  test('clicking embed toggle issues PUT /api/slots/npu/config then POST /api/slots/npu/restart', async ({ page }) => {
    const configPuts: any[] = []
    const restarts: string[] = []

    // Mutations bypass mockFetch (raw:true) → page.route intercepts them.
    await page.route('**/api/slots/npu/config', async (route) => {
      if (route.request().method() === 'PUT') {
        try {
          configPuts.push(JSON.parse(route.request().postData() || '{}'))
        } catch { configPuts.push({}) }
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/npu/restart', async (route) => {
      restarts.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })

    // READ seed via HAL0_DATA (same as read-path test).
    await page.addInitScript((slot) => {
      document.addEventListener('DOMContentLoaded', () => {
        if ((window as any).HAL0_DATA) {
          const existing = (window as any).HAL0_DATA.slots || []
          const withoutNpu = existing.filter((s: any) => s.device !== 'npu')
          ;(window as any).HAL0_DATA.slots = [...withoutNpu, slot]
        }
      })
    }, NPU_CONTAINER_SLOT)

    await page.goto('/#slots')
    const stack = page.locator('.npu-stack')
    await expect(stack).toBeVisible()

    // The NPU/FLM stack now renders inside a collapsible "engine" pane
    // (parallel to the ComfyUI + Inference panes); the trio lives in the
    // expandable body, so open it via the caret before interacting with a
    // toggle. Read-path assertions above work on the clipped DOM, but a
    // click needs the control un-clipped.
    await page.getByTestId('npu-qcaret').click()

    // Confirm embed starts unchecked (state from slot.npu.embed=false)
    const embedSwitch = stack.locator('.npu-trio .npu-mod').nth(2).locator('.npu-switch')
    await expect(embedSwitch).toHaveAttribute('aria-checked', 'false')

    // Click the embed toggle (currently off → toggling on)
    await embedSwitch.click()

    // PUT /config must carry npu.embed: true
    await expect.poll(() => configPuts.length, { timeout: 5_000 }).toBeGreaterThan(0)
    expect(configPuts[0]).toMatchObject({ npu: { embed: true } })

    // POST /restart must follow
    await expect.poll(() => restarts.length, { timeout: 5_000 }).toBeGreaterThan(0)
    expect(restarts[0]).toContain('/api/slots/npu/restart')
  })

  // ── Defaults: slot without an [npu] TOML section ──────────────────────

  test('npu slot without an npu config section renders both toggles OFF', async ({ page }) => {
    const NPU_NO_TOGGLES = {
      name: 'npu-bare',
      type: 'llm',
      device: 'npu',
      device_class: 'npu',
      backend: null,
      model: 'gemma3:1b',
      model_id: 'gemma3-1b-npu',
      group: 'npu',
      state: 'ready',
      port: 8093,
      runtime: 'container',
      profile: 'flm',
      container_status: 'running',
      container_health: true,
      // No `npu` field → trio modalities default to off.
    }

    // Inject the bare NPU slot — replace all npu-device slots with just this one.
    await page.addInitScript((slot) => {
      document.addEventListener('DOMContentLoaded', () => {
        if ((window as any).HAL0_DATA) {
          const existing = (window as any).HAL0_DATA.slots || []
          const withoutNpu = existing.filter((s: any) => s.device !== 'npu')
          ;(window as any).HAL0_DATA.slots = [...withoutNpu, slot]
        }
      })
    }, NPU_NO_TOGGLES)

    await page.goto('/#slots')
    const stack = page.locator('.npu-stack')
    await expect(stack).toBeVisible()

    // No [npu] section → asr + embed both off (never inferred from flags).
    const asrSwitch = stack.locator('.npu-trio .npu-mod').nth(1).locator('.npu-switch')
    await expect(asrSwitch).toHaveAttribute('aria-checked', 'false')
    const embedSwitch = stack.locator('.npu-trio .npu-mod').nth(2).locator('.npu-switch')
    await expect(embedSwitch).toHaveAttribute('aria-checked', 'false')
    await expect(stack.locator('.npu-stack-foot .npu-args')).toHaveText('npu = asr:off · embed:off')
  })
})
