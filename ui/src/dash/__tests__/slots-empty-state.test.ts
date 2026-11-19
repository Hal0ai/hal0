// The Slots page's zero-slots empty state must not send users to `hal0 setup`: that entry point is
// hidden from `hal0 --help` and its bundle wizard is retired (#3793). It should point at New slot.
import { describe, expect, it } from 'vitest'
import slotsSrc from '../slots.jsx?raw'

describe('Slots empty state copy', () => {
  const start = slotsSrc.indexOf('No slots configured')
  const block = slotsSrc.slice(start, slotsSrc.indexOf('dash-empty-cta', start))

  it('finds the empty-state block', () => {
    expect(start).toBeGreaterThan(-1)
    expect(block.length).toBeGreaterThan(0)
  })

  it('does not mention the hidden hal0 setup command', () => {
    expect(block).not.toContain('hal0 setup')
  })

  it('points at New slot and does not claim a model is merely unloaded', () => {
    expect(block).toContain('New slot')
    expect(block).not.toContain('model loaded')
  })
})
