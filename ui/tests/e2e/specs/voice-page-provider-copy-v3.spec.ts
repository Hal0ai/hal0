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
 *
 * #1944 — two defects in this file, one cause. Every control was addressed
 * by its ordinal inside the panel (`ttsPanel.locator('select option').first()`)
 * and every assertion ran without waiting for the capabilities probe:
 *
 *   - The Model row renders a free-text <input> until GET /api/capabilities
 *     delivers the catalog, and a <select> afterwards. So that one locator
 *     pointed at the Default-voice picker before the probe landed (first
 *     option "— use engine default (af_bella) —") and at the Model picker
 *     after it landed (first option "— unset —"). The kokoro guard therefore
 *     passed or failed purely on whether Playwright's first poll beat the
 *     mocked probe — the intermittent CI red on #1923 and #1943.
 *   - The same swap made the qwen3 guard permanently vacuous: post-probe it
 *     only ever asked whether "— unset —" contains 'af_bella'. Reverting the
 *     #1470 default-voice fix left the suite green.
 *   - Two more qwen3 assertions were vacuous for a different reason: since
 *     #1683 the sub-copy lives in a popup portalled to <body>, so a row's
 *     own text can never contain "Kokoro" / "Kokoro clamps". Reverting those
 *     two hints to hardcoded Kokoro copy also left the suite green.
 *
 * Rules this file now follows: address a control through its labelled row,
 * never by ordinal; sync on the probe having been applied before asserting;
 * read sub-copy through fieldInfoPopup; and pair every "must not claim X"
 * with the positive statement of what the copy must say instead, so a blank
 * or unrendered element cannot satisfy it.
 */
import { test, expect, json } from '../fixtures/apiMock'
import type { Locator, Page } from '@playwright/test'

// #1683: FieldInfoIcon's description now portals to document.body (so
// overflow:hidden panels can't clip it), so it's no longer a DOM descendant
// of its row — `row.textContent` (what `toContainText` reads) doesn't see it
// anymore. Look the popup up via the Info button's aria-describedby instead.
async function fieldInfoPopup(row: Locator) {
  const btn = row.getByRole('button', { name: 'Info' })
  const id = await btn.getAttribute('aria-describedby')
  return row.page().locator(`[id="${id}"]`)
}

const capPanel = (page: Page, title: string) =>
  page.locator('.s-panel').filter({ has: page.locator('.k > span', { hasText: new RegExp(`^${title}$`) }) })

// #1944: rows are identified by their label, never by position. The set of
// <select> elements in a panel changes as the capabilities probe resolves.
const panelRow = (panel: Locator, label: string) =>
  panel.locator('.s-row').filter({ has: panel.page().locator('.k > span', { hasText: new RegExp(`^${label}$`) }) })

// #1944: the sync point every assertion in this file needs. The Model picker
// only becomes a <select> holding the mocked id once GET /api/capabilities
// has resolved AND useCapabilitySelection has pushed the selection into form
// state — i.e. once `resolvedProvider` is the provider under test. Asserting
// provider-keyed copy before this is meaningless: the provider is still ""
// and none of the engine-specific strings are on screen yet.
async function capabilitiesApplied(panel: Locator, model: string) {
  await expect(panelRow(panel, 'Model').locator('select')).toHaveValue(model)
}

function capabilityRow(device: string, provider: string, model: string | null, slot: string, status: string) {
  return { device, backend: device, provider, model, enabled: !!model, slot, status }
}

type Sel = { model: string; provider: string; device: string }

// Mirrors the real /api/capabilities envelope (orchestrator.py get_state /
// catalogs_by_slot — catalog rows carry a flat `provider` field, see
// catalog.py _entry_to_row / _tts_rows_for_capability).
function capsWith(tts: Sel, stt: Sel = { model: 'moonshine-base', provider: 'moonshine', device: 'cpu' }) {
  return {
    backends: [
      { id: 'cpu', label: 'CPU', short: 'CPU', provider: 'llamacpp', multiplex: false },
      { id: 'gpu-rocm', label: 'GPU · ROCm', short: 'ROCm', provider: 'llamacpp', multiplex: false },
    ],
    catalogs: {
      voice: {
        stt: [
          { id: 'moonshine-base', capabilities: ['stt'], size_gb: 0.4, backend: 'cpu', provider: 'moonshine' },
          { id: 'whisper-large-v3', capabilities: ['stt'], size_gb: 1.5, backend: 'npu', provider: 'flm' },
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
        stt: capabilityRow(stt.device, stt.provider, stt.model, 'stt', 'serving'),
        tts: capabilityRow(tts.device, tts.provider, tts.model, 'tts', 'serving'),
      },
      img: { img: capabilityRow('', '', null, 'img', 'offline') },
    },
  }
}

const KOKORO: Sel = { model: 'kokoro-v1', provider: 'kokoro', device: 'cpu' }
const QWEN3: Sel = { model: 'qwen3-tts', provider: 'qwen3tts', device: 'gpu-rocm' }

test.describe('Voice page copy keyed on provider (#1470)', () => {
  test('qwen3tts selection gets qwen3tts-appropriate copy, not hardcoded Kokoro facts', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, capsWith(QWEN3)))
    await page.route('**/api/slots/tts/config', (route) => json(route, {}))
    await page.route('**/api/slots/tts/voices', (route) => json(route, { source: 'offline', voices: [] }))
    await page.goto('/#settings/voice')
    await expect(page.locator('.settings-content h2').first()).toHaveText('AI Capabilities')

    const ttsPanel = capPanel(page, 'TTS')
    await capabilitiesApplied(ttsPanel, 'qwen3-tts')

    // Cold non-Kokoro slot: the Kokoro seed pack is not a legal fallback, so
    // the row must degrade to the free-text id field rather than offering
    // Bella & friends to an engine that has never heard of them.
    const voiceRow = panelRow(ttsPanel, 'Default voice')
    await expect(voiceRow.locator('input')).toHaveAttribute('placeholder', 'empty = engine default')
    await expect(voiceRow.locator('option')).toHaveCount(0)
    await expect(await fieldInfoPopup(voiceRow)).toContainText('model-specific voice id')
    await expect(await fieldInfoPopup(voiceRow)).not.toContainText('Kokoro')

    // Sample-rate row must not claim a fixed 24 kHz — Qwen3-TTS reports
    // whatever the loaded model's codec produces at startup.
    const sampleRateRow = panelRow(ttsPanel, 'Sample rate')
    await expect(sampleRateRow).toContainText('engine-dependent')
    await expect(sampleRateRow).not.toContainText('24 kHz')
    await expect(await fieldInfoPopup(sampleRateRow)).toContainText('Qwen3-TTS model at startup')
    await expect(await fieldInfoPopup(sampleRateRow)).not.toContainText('Kokoro')

    // Speed hint must not attribute the clamp specifically to Kokoro.
    const speedRow = panelRow(ttsPanel, 'Default speed')
    await expect(await fieldInfoPopup(speedRow)).toContainText('the engine clamps to 0.5–2.0')
    await expect(await fieldInfoPopup(speedRow)).not.toContainText('Kokoro clamps')
  })

  test('qwen3tts live voice list is labelled with the qwen3 engine default, not af_bella', async ({ page }) => {
    // The placeholder <option> only exists when the picker renders a <select>,
    // which for a non-Kokoro provider means the slot reported a live list.
    // Without this case nothing asserts the #1470 default-voice label at all.
    await page.route('**/api/capabilities', (route) => json(route, capsWith(QWEN3)))
    await page.route('**/api/slots/tts/config', (route) => json(route, {}))
    await page.route('**/api/slots/tts/voices', (route) => json(route, { source: 'live', voices: ['Ryan', 'Chelsie'] }))
    await page.goto('/#settings/voice')

    const ttsPanel = capPanel(page, 'TTS')
    await capabilitiesApplied(ttsPanel, 'qwen3-tts')

    const voiceOptions = panelRow(ttsPanel, 'Default voice').locator('option')
    await expect(voiceOptions).toHaveCount(3)
    await expect(voiceOptions.first()).toHaveText('— use engine default (Ryan) —')
    await expect(voiceOptions.nth(1)).toHaveText('Ryan')
  })

  test('kokoro selection keeps the original Kokoro-specific copy (regression guard)', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, capsWith(KOKORO)))
    await page.route('**/api/slots/tts/config', (route) => json(route, {}))
    await page.route('**/api/slots/tts/voices', (route) => json(route, { source: 'offline', voices: [] }))
    await page.goto('/#settings/voice')
    await expect(page.locator('.settings-content h2').first()).toHaveText('AI Capabilities')

    const ttsPanel = capPanel(page, 'TTS')
    await capabilitiesApplied(ttsPanel, 'kokoro-v1')

    const voiceRow = panelRow(ttsPanel, 'Default voice')
    await expect(voiceRow.locator('option').first()).toHaveText('— use engine default (af_bella) —')
    await expect(await fieldInfoPopup(voiceRow)).toContainText('bundled voices (Kokoro v1)')

    const sampleRateRow = panelRow(ttsPanel, 'Sample rate')
    await expect(sampleRateRow).toContainText('24 kHz')
    await expect(await fieldInfoPopup(sampleRateRow)).toContainText('Kokoro')
  })

  test('moonshine STT selection keeps the English-only language copy', async ({ page }) => {
    await page.route('**/api/capabilities', (route) => json(route, capsWith(KOKORO)))
    await page.route('**/api/slots/tts/config', (route) => json(route, {}))
    await page.route('**/api/slots/tts/voices', (route) => json(route, { source: 'offline', voices: [] }))
    await page.goto('/#settings/voice')

    const sttPanel = capPanel(page, 'STT')
    await capabilitiesApplied(sttPanel, 'moonshine-base')

    const languageRow = panelRow(sttPanel, 'Language')
    await expect(languageRow).toContainText('English')
    await expect(await fieldInfoPopup(languageRow)).toContainText('moonshine is English-only')
  })

  test('a non-moonshine STT provider does not claim the English-only fact', async ({ page }) => {
    // The other half of the #1470 property, which the moonshine case above
    // used to claim in its title but never exercised.
    await page.route('**/api/capabilities', (route) =>
      json(route, capsWith(KOKORO, { model: 'whisper-large-v3', provider: 'flm', device: 'npu' })))
    await page.route('**/api/slots/tts/config', (route) => json(route, {}))
    await page.route('**/api/slots/tts/voices', (route) => json(route, { source: 'offline', voices: [] }))
    await page.goto('/#settings/voice')

    const sttPanel = capPanel(page, 'STT')
    await capabilitiesApplied(sttPanel, 'whisper-large-v3')

    const languageRow = panelRow(sttPanel, 'Language')
    await expect(languageRow).toContainText('engine-dependent')
    await expect(languageRow).not.toContainText('English')
    await expect(await fieldInfoPopup(languageRow)).toContainText('flm — language support is engine-specific')
  })

  test('the TTS voice picker claims no engine facts while the capabilities probe is in flight (#1944)', async ({ page }) => {
    // TtsPanel's `kokoroish = !sel.model || isKokoro` conflated "no TTS model
    // configured" with "the probe has not answered yet", so a qwen3tts box
    // rendered Kokoro's seed pack and its "(af_bella)" default for the whole
    // load window — #1470's defect, scoped to the gap before the probe lands.
    // Gate the route so the in-flight state is a held, deterministic state
    // rather than a race.
    let releaseProbe: () => void = () => {}
    const probeGate = new Promise<void>((resolve) => { releaseProbe = resolve })
    await page.route('**/api/capabilities', async (route) => {
      await probeGate
      await json(route, capsWith(QWEN3))
    })
    await page.route('**/api/slots/tts/config', (route) => json(route, {}))
    await page.route('**/api/slots/tts/voices', (route) => json(route, { source: 'offline', voices: [] }))
    await page.goto('/#settings/voice')

    const ttsPanel = capPanel(page, 'TTS')
    const voiceRow = panelRow(ttsPanel, 'Default voice')
    // Probe still held: no catalog, so the Model row is the free-text input.
    await expect(panelRow(ttsPanel, 'Model').locator('select')).toHaveCount(0)
    const options = voiceRow.locator('option')
    await expect(options).toHaveCount(1)
    await expect(options.first()).toHaveText('— use engine default —')
    await expect(await fieldInfoPopup(voiceRow)).toContainText('loading the tts slot')

    releaseProbe()
    await capabilitiesApplied(ttsPanel, 'qwen3-tts')
    await expect(voiceRow.locator('input')).toHaveAttribute('placeholder', 'empty = engine default')
  })
})
