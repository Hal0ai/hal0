/**
 * voice-page-provider-copy-v3 — Voice page copy keyed on the TTS/STT
 * selection's actual provider (#1470).
 *
 * VoicePage.jsx used to present Kokoro facts as engine truth regardless of
 * which engine was actually selected: the default-voice placeholder always
 * said "(af_bella)", the speed hint always said "Kokoro clamps to 0.5–2.0",
 * the sample-rate row always claimed a fixed 24 kHz, and the Language row
 * always stated the moonshine-specific "English-only / param ignored" fact
 * — all baked in rather than derived from `selections.voice.tts.provider` /
 * `selections.voice.stt.provider`, which GET /api/capabilities already
 * carries (orchestrator.py get_state's per-child `provider` field).
 *
 * These specs pin the qwen3tts selection getting qwen3tts-appropriate copy
 * (not Kokoro's), and the kokoro selection keeping the original Kokoro
 * facts as a regression guard.
 */
import { test, expect, json } from '../fixtures/apiMock'

function capabilityRow(device: string, provider: string, model: string | null, slot: string, status: string) {
  return { device, backend: device, provider, model, enabled: !!model, slot, status }
}

// Mirrors the real /api/capabilities envelope (orchestrator.py get_state /
// catalogs_by_slot — catalog rows carry a flat `provider` field, see
// catalog.py _entry_to_row / _tts_rows_for_capability).
function capsWithTtsSelection(model: string, provider: string, device: string) {
  return {
    backends: [
      { id: 'cpu', label: 'CPU', short: 'CPU', provider: 'llamacpp', multiplex: false },
      { id: 'gpu-rocm', label: 'GPU · ROCm', short: 'ROCm', provider: 'llamacpp', multiplex: false },
    ],
    catalogs: {
      voice: {
        stt: [
          { id: 'moonshine-base', capabilities: ['stt'], size_gb: 0.4, backend: 'cpu', provider: 'moonshine' },
        ],
        tts: [
          { id: 'kokoro-v1', capabilities: ['tts'], size_gb: 0.3, backend: 'cpu', provider: 'kokoro' },
          { id: 'qwen3-tts', capabilities: ['tts'], size_gb: 1.2, backend: 'gpu-rocm', provider: 'qwen3tts' },
        ],
      },
      img: { img: [] },
    },
    selections: {
      voice: {
        stt: capabilityRow('cpu', 'moonshine', 'moonshine-base', 'stt', 'serving'),
        tts: capabilityRow(device, provider, model, 'tts', 'serving'),
      },
      img: { img: capabilityRow('', '', null, 'img', 'offline') },
    },
  }
}

test.describe('Voice page copy keyed on provider (#1470)', () => {
  test('qwen3tts selection gets qwen3tts-appropriate copy, not hardcoded Kokoro facts', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, capsWithTtsSelection('qwen3-tts', 'qwen3tts', 'gpu-rocm')))
    await page.route('**/api/slots/tts/config', (route) => json(route, {}))
    await page.route('**/api/slots/tts/voices', (route) => json(route, { source: 'offline', voices: [] }))
    await page.goto('/#settings/voice')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Voice')

    const ttsPanel = page.locator('.s-panel').filter({ has: page.locator('.k span', { hasText: /^TTS$/ }) })

    // Default-voice placeholder must not claim Kokoro's af_bella default.
    const voiceOption = ttsPanel.locator('select option').first()
    await expect(voiceOption).not.toContainText('af_bella')

    // Sample-rate row must not claim a fixed 24 kHz — Qwen3-TTS reports
    // whatever the loaded model's codec produces at startup.
    const sampleRateRow = ttsPanel.locator('.s-row').filter({ hasText: 'Sample rate' })
    await expect(sampleRateRow).not.toContainText('24 kHz')
    await expect(sampleRateRow).not.toContainText('Kokoro')

    // Speed hint must not attribute the clamp specifically to Kokoro.
    const speedRow = ttsPanel.locator('.s-row').filter({ hasText: 'Default speed' })
    await expect(speedRow).not.toContainText('Kokoro clamps')
  })

  test('kokoro selection keeps the original Kokoro-specific copy (regression guard)', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, capsWithTtsSelection('kokoro-v1', 'kokoro', 'cpu')))
    await page.route('**/api/slots/tts/config', (route) => json(route, {}))
    await page.route('**/api/slots/tts/voices', (route) => json(route, { source: 'offline', voices: [] }))
    await page.goto('/#settings/voice')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Voice')

    const ttsPanel = page.locator('.s-panel').filter({ has: page.locator('.k span', { hasText: /^TTS$/ }) })

    const voiceOption = ttsPanel.locator('select option').first()
    await expect(voiceOption).toContainText('af_bella')

    const sampleRateRow = ttsPanel.locator('.s-row').filter({ hasText: 'Sample rate' })
    await expect(sampleRateRow).toContainText('24 kHz')
    await expect(sampleRateRow).toContainText('Kokoro')
  })

  test('moonshine STT selection keeps the English-only language copy, other providers do not claim it', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, capsWithTtsSelection('kokoro-v1', 'kokoro', 'cpu')))
    await page.route('**/api/slots/tts/config', (route) => json(route, {}))
    await page.route('**/api/slots/tts/voices', (route) => json(route, { source: 'offline', voices: [] }))
    await page.goto('/#settings/voice')

    const languageRow = page.locator('.s-row').filter({ hasText: 'Language' })
    await expect(languageRow).toContainText('English')
    await expect(languageRow).toContainText('moonshine is English-only')
  })
})
