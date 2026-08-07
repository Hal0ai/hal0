/**
 * capability-catalog-pickers-v3 — Settings → Inference → Voice / Image-gen
 * model pickers (#1454).
 *
 * GET /api/capabilities ships `{backends, catalogs, selections}` where
 * `catalogs.voice.stt`, `catalogs.voice.tts`, and `catalogs.img.img` are BARE
 * ARRAYS of picker rows (each row carries `id` + a `backends` breakdown) —
 * see mockFixtures.ts buildCapabilities() for the canonical shape. The pages
 * used to index `.items`/`.models` off those arrays, which don't exist on an
 * Array, so `catalogItems` was always `[]`: the `<select>` branch never
 * rendered and the "no installed … models" hint lied even with models
 * installed.
 *
 * These specs pin the real bare-array shape rendering into a populated
 * <select> (not the free-text fallback) and the false empty-state hint
 * being absent.
 */
import { test, expect, json } from '../fixtures/apiMock'

function capabilityRow(device: string, provider: string, model: string | null, slot: string, status: string) {
  return { device, backend: device, provider, model, enabled: !!model, slot, status }
}

// Mirrors the real /api/capabilities envelope (orchestrator.py get_state /
// catalogs_by_slot) — same shape mockFixtures.ts documents as ground truth.
const CAPS_MOCK = {
  backends: [
    { id: 'npu', label: 'NPU', short: 'NPU', provider: 'flm', multiplex: true },
    { id: 'cpu', label: 'CPU', short: 'CPU', provider: 'llamacpp', multiplex: false },
    { id: 'gpu-rocm', label: 'GPU · ROCm', short: 'ROCm', provider: 'llamacpp', multiplex: false },
  ],
  catalogs: {
    voice: {
      stt: [
        { id: 'Whisper-Large-v3-Turbo', capabilities: ['stt'], size_gb: 1.5, backends: [{ id: 'npu', provider: 'flm', downloaded: true, pullable: true }] },
        { id: 'gemma4-it:e2b', capabilities: ['stt'], size_gb: 2.0, backends: [{ id: 'npu', provider: 'flm', downloaded: true, pullable: true }] },
      ],
      tts: [
        { id: 'kokoro-v1', capabilities: ['tts'], size_gb: 0.3, backends: [{ id: 'cpu', provider: 'kokoro', downloaded: true, pullable: true }] },
        { id: 'qwen3-tts', capabilities: ['tts'], size_gb: 1.2, backends: [{ id: 'gpu-rocm', provider: 'sdcpp', downloaded: true, pullable: true }] },
      ],
    },
    img: {
      img: [
        { id: 'sd-turbo', capabilities: ['image'], size_gb: 2.1, backends: [{ id: 'gpu-rocm', provider: 'sdcpp', downloaded: true, pullable: true }] },
      ],
    },
  },
  selections: {
    voice: {
      stt: capabilityRow('npu', 'flm', 'Whisper-Large-v3-Turbo', 'stt', 'serving'),
      tts: capabilityRow('cpu', 'kokoro', 'kokoro-v1', 'tts', 'serving'),
    },
    img: {
      img: capabilityRow('', '', null, 'img', 'offline'),
    },
  },
}

test.describe('Capability catalog pickers (#1454)', () => {
  test('Voice page: STT/TTS Model pickers render as <select> populated from the bare-array catalog', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, CAPS_MOCK))
    await page.goto('/#settings/voice')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Voice')

    const modelRows = page.locator('.s-row').filter({ has: page.locator('.k span', { hasText: /^Model$/ }) })
    await expect(modelRows).toHaveCount(2)

    // STT row
    const sttRow = modelRows.nth(0)
    const sttSelect = sttRow.locator('select')
    await expect(sttSelect).toBeVisible()
    await expect(sttSelect.locator('option', { hasText: 'Whisper-Large-v3-Turbo' })).toHaveCount(1)
    await expect(sttSelect.locator('option', { hasText: 'gemma4-it:e2b' })).toHaveCount(1)
    await expect(sttRow).not.toContainText('no installed STT models')

    // TTS row
    const ttsRow = modelRows.nth(1)
    const ttsSelect = ttsRow.locator('select')
    await expect(ttsSelect).toBeVisible()
    await expect(ttsSelect.locator('option', { hasText: 'kokoro-v1' })).toHaveCount(1)
    await expect(ttsSelect.locator('option', { hasText: 'qwen3-tts' })).toHaveCount(1)
    await expect(ttsRow).not.toContainText('no installed TTS models')
  })

  test('Image-gen page: img Model picker renders as <select> populated from the bare-array catalog', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, CAPS_MOCK))
    await page.goto('/#settings/imagegen')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Image Generation')

    const modelRow = page.locator('.s-row').filter({ has: page.locator('.k span', { hasText: /^Model$/ }) })
    await expect(modelRow).toHaveCount(1)
    const select = modelRow.locator('select')
    await expect(select).toBeVisible()
    await expect(select.locator('option', { hasText: 'sd-turbo' })).toHaveCount(1)
    await expect(modelRow).not.toContainText('no installed image models')
  })

  test('Voice page: empty catalog still shows the honest "no installed" hint (not a false negative)', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, {
      backends: [],
      catalogs: { voice: { stt: [], tts: [] }, img: { img: [] } },
      selections: {
        voice: { stt: capabilityRow('', '', null, 'stt', 'offline'), tts: capabilityRow('', '', null, 'tts', 'offline') },
        img: { img: capabilityRow('', '', null, 'img', 'offline') },
      },
    }))
    await page.goto('/#settings/voice')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Voice')

    const modelRows = page.locator('.s-row').filter({ has: page.locator('.k span', { hasText: /^Model$/ }) })
    await expect(modelRows.nth(0)).toContainText('no installed STT models')
    await expect(modelRows.nth(1)).toContainText('no installed TTS models')
    // Genuinely-empty catalog falls back to the free-text input, not a <select>.
    await expect(modelRows.nth(0).locator('select')).toHaveCount(0)
  })
})

// #1467 item 4: neither page had an isError branch for GET /api/capabilities
// — a failed probe rendered blank/unchecked controls as if nothing were
// configured, and Save stayed clickable (gated only on `loading`, i.e.
// isLoading, which is false once the query has settled to an error). Fixed
// by mirroring AdvancedPage.jsx's isError pattern: an error banner + Save
// suppressed via `errored` alongside the existing dirty/loading/pending
// gates (VoicePage.jsx Save STT / Save TTS, ImageGenPage.jsx Save Image-gen).
//
// NOT e2e-covered: `/api/capabilities` is `networkFirst` in
// src/api/mock.ts's MOCK_ALLOWLIST (needed so THIS spec's page.route
// fixtures above stay authoritative under the suite's forced-mock build —
// see the allowlist comment). But `mockFetch`'s FORCED+networkFirst branch
// substitutes the baked payload for ANY non-ok response, network exception
// included (`if (FORCED && hit.row.networkFirst && !res.ok) { …fallback… }`)
// — so a page.route 500/abort on this endpoint is silently papered over
// with a 200 mock payload before it ever reaches react-query, and
// capsQuery.isError is unreachable from a Playwright spec against this
// harness. Verified by direct code inspection instead (matches the
// AdvancedPage/SecretsPage isError precedent exactly); flagged in the
// #1467 report as a test-infra gap, not a shortcut in the fix itself.
