/**
 * lemonade-voice-chip.spec.ts — PR-15 voice slot [CPU] disclosure chip.
 *
 * Covers (plan §11 PR-15 + ADR-0008 §2):
 *   - Voice slot with provider=kokoro renders the [CPU] chip with the
 *     correct aria-label.
 *   - Voice slot with a non-kokoro provider (hypothetical future GPU TTS)
 *     does NOT render the chip.
 *   - Tooltip text matches the briefed wording verbatim.
 *
 * Slice #170 (v2 Slots view rewrite): the chip moved from VoiceCard
 * (capability card, deleted) to the v2 SlotCard. Tests now seed a `tts`
 * slot with the relevant provider in mockState.status.slots so the chip
 * renders inside the Voice section's grid card.
 */
import { test, expect, json } from '../fixtures/apiMock'

const KOKORO_TOOLTIP =
  'Kokoro TTS runs on CPU in v0.2. GPU-accelerated TTS is planned for v0.3.'

function ttsSlot(provider: string) {
  return {
    name: 'tts',
    type: 'tts',
    kind: provider,        // SlotCard's isKokoroCpu reads from provider OR kind
    provider,
    backend: 'cpu',
    device: 'cpu',
    model: 'kokoro-v1',
    port: 8085,
    status: 'ready',
  }
}

test('kokoro TTS slot renders [CPU] chip with aria-label', async ({
  page,
  mockState,
  cleanState,
}) => {
  mockState.status.slots = [ttsSlot('kokoro')]

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
  mockState.status.slots = [ttsSlot('piper-gpu')]

  await page.goto('/slots')

  // Voice section heading renders for the seeded tts slot.
  await expect(page.locator('.sec h2', { hasText: /^Voice/ })).toBeVisible()
  await expect(page.getByTestId('cpu-only-chip')).toHaveCount(0)
})

test('[CPU] chip tooltip text matches the brief verbatim', async ({
  page,
  mockState,
  cleanState,
}) => {
  mockState.status.slots = [ttsSlot('kokoro')]

  await page.goto('/slots')

  const chip = page.getByTestId('cpu-only-chip')
  await expect(chip).toBeVisible({ timeout: 5_000 })
  // The native title= attribute carries the verbatim disclosure copy.
  await expect(chip).toHaveAttribute('title', KOKORO_TOOLTIP)
})
