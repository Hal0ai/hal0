/**
 * lemonade-voice-chip.spec.ts — PR-15 voice slot [CPU] disclosure chip.
 *
 * Covers (plan §11 PR-15 + ADR-0008 §2):
 *   - Voice slot TTS section with provider=kokoro renders the [CPU]
 *     chip with the correct aria-label.
 *   - Voice slot TTS section with a non-kokoro provider (hypothetical
 *     future GPU TTS) does NOT render the chip.
 *   - Tooltip text matches the briefed wording verbatim.
 *
 * No live backend — uses ``apiMock`` for HTTP. Chip is hard-coded to
 * provider === 'kokoro' per plan §1 #2 (no device-detection logic).
 *
 * The voice "slot card" in v0.2 is the VoiceCard capability card under
 * the Capabilities section on /slots; the `tts` slot itself is filtered
 * out of the main slot grid (CAPABILITY_OWNED_SLOTS in Slots.vue) and
 * its UX lives entirely in VoiceCard.
 */
import { test, expect, json } from '../fixtures/apiMock'

const KOKORO_TOOLTIP =
  'Kokoro TTS runs on CPU in v0.2. GPU-accelerated TTS is planned for v0.3.'

function capabilitiesPayload(ttsProvider: string) {
  return {
    backends: [
      { id: 'kokoro', label: 'Kokoro', short: 'kokoro', provider: 'kokoro' },
      { id: 'whispercpp', label: 'whisper.cpp', short: 'whisper', provider: 'whispercpp' },
    ],
    catalogs: {
      voice: {
        stt: [
          {
            id: 'whisper-tiny',
            backends: [{ id: 'whispercpp', provider: 'whispercpp', downloaded: true }],
          },
        ],
        tts: [
          {
            id: 'kokoro-v1',
            backends: [{ id: 'kokoro', provider: ttsProvider, downloaded: true }],
          },
        ],
      },
    },
    selections: {
      voice: {
        stt: {
          backend: 'whispercpp',
          provider: 'whispercpp',
          model: 'whisper-tiny',
          enabled: true,
          slot: 'stt',
          status: 'serving',
        },
        tts: {
          backend: 'kokoro',
          provider: ttsProvider,
          model: 'kokoro-v1',
          enabled: true,
          slot: 'tts',
          status: 'serving',
        },
      },
    },
  }
}

test('kokoro TTS sub-section renders [CPU] chip with aria-label', async ({
  page,
  mockState,
  cleanState,
}) => {
  await page.route('**/api/capabilities', (route) =>
    json(route, capabilitiesPayload('kokoro')),
  )
  await page.route('**/api/slots', (route) => json(route, []))
  mockState.status.slots = []

  await page.goto('/slots')

  const chip = page.getByTestId('cpu-only-chip')
  await expect(chip).toBeVisible({ timeout: 5_000 })
  await expect(chip).toHaveText('CPU')
  // aria-label includes the full tooltip text so screen-reader users get
  // the same disclosure as sighted users hovering the chip.
  await expect(chip).toHaveAttribute('aria-label', `CPU-only backend — ${KOKORO_TOOLTIP}`)
})

test('non-kokoro TTS provider has no [CPU] chip', async ({
  page,
  mockState,
  cleanState,
}) => {
  // Hypothetical future GPU-TTS provider — same capability, different
  // provider. PR-15 only marks kokoro:cpu; other providers stay clean.
  await page.route('**/api/capabilities', (route) =>
    json(route, capabilitiesPayload('piper-gpu')),
  )
  await page.route('**/api/slots', (route) => json(route, []))
  mockState.status.slots = []

  await page.goto('/slots')

  // VoiceCard rendered — assert its TTS sub-section is on the page.
  await expect(page.locator('.cap-title', { hasText: 'voice' })).toBeVisible()
  await expect(page.getByTestId('cpu-only-chip')).toHaveCount(0)
})

test('[CPU] chip tooltip text matches the brief verbatim', async ({
  page,
  mockState,
  cleanState,
}) => {
  await page.route('**/api/capabilities', (route) =>
    json(route, capabilitiesPayload('kokoro')),
  )
  await page.route('**/api/slots', (route) => json(route, []))
  mockState.status.slots = []

  await page.goto('/slots')

  const chip = page.getByTestId('cpu-only-chip')
  await expect(chip).toBeVisible({ timeout: 5_000 })
  // The native title= attribute carries the verbatim disclosure copy.
  await expect(chip).toHaveAttribute('title', KOKORO_TOOLTIP)
})
