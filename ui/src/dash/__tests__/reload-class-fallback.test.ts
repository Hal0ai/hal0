// The unified AI Capabilities page renders ApplyBadge for per-slot keys the
// backend apply-plan registry can't classify (they aren't Hal0Config paths).
// Lock the fallback classifications so a badge can never silently vanish.
import { describe, expect, it } from 'vitest'
import { reloadClassFor, SERVICE_HAL0_API, SERVICE_SLOTS } from '../settings/data/reloadClass.js'

describe('RELOAD_CLASS_FALLBACK capability keys', () => {
  it.each([
    'slot.tts.default_voice',
    'slot.tts.default_speed',
    'slot.tts.default_response_format',
    'slot.image.default_size',
    'slot.image.default_steps',
  ])('%s is immediate (injected per-request)', (key) => {
    expect(reloadClassFor(key, {})).toEqual({ apply_class: 'immediate', services: [] })
  })

  it('slot.image.idle_restore_minutes needs a hal0-api restart', () => {
    expect(reloadClassFor('slot.image.idle_restore_minutes', {})).toEqual({
      apply_class: 'service-restart',
      services: [SERVICE_HAL0_API],
    })
  })

  it('npu keys stay service-restart on the slots service', () => {
    expect(reloadClassFor('slot.npu.asr', {})).toEqual({
      apply_class: 'service-restart',
      services: [SERVICE_SLOTS],
    })
  })
})
