// image-dedupe (slot drawer Advanced): the drawer used to render the
// resolved Image row (slot-hw-advanced-image) AND a separate "Image status"
// row (slot-image-status) that repeated the same ref plus a " · #id"
// suffix — two stacked rows, same digest, both overflowing the drawer.
// The fix merges them into one row: the resolved ref stays, and the old
// status row's job (what's the container ACTUALLY running, right now)
// becomes a small state chip instead of a second text line.
//
// imageLiveState() is the pure comparison behind that chip, pinned here the
// same way breakerChip's classifier is pinned in breaker-chip.test.ts —
// the JSX only renders whatever this returns.
import { describe, expect, it } from 'vitest'

import { imageLiveState } from '../slot-status.js'

const REF = 'ghcr.io/hal0ai/hal0-promptforge@sha256:abc123'
const OLD_REF = 'ghcr.io/hal0ai/hal0-promptforge@sha256:def456'

describe('imageLiveState', () => {
  it('is null when the slot is not running — nothing to compare against', () => {
    expect(imageLiveState(REF, REF, 'off')).toBeNull()
    expect(imageLiveState(OLD_REF, REF, 'off')).toBeNull()
    expect(imageLiveState(REF, REF, 'transitional')).toBeNull()
  })

  it('running + actual matches resolved → live chip (ok tone)', () => {
    const chip = imageLiveState(REF, REF, 'running')
    expect(chip).not.toBeNull()
    expect(chip!.cls).toBe('chip ok')
    expect(chip!.label).toBe('live')
  })

  it('running + actual differs from resolved → restart-pending chip (warn tone)', () => {
    const chip = imageLiveState(OLD_REF, REF, 'running')
    expect(chip).not.toBeNull()
    expect(chip!.cls).toBe('chip warn')
    expect(chip!.label).toBe('restart pending')
    // the tooltip must explain WHY: the running container hasn't picked up
    // the resolved ref yet, not that something is broken.
    expect(chip!.tooltip.toLowerCase()).toContain('restart')
    expect(chip!.tooltip).toContain(OLD_REF)
  })

  it('running but the actual image is unknown (null/undefined) → no chip, not a guess', () => {
    expect(imageLiveState(null, REF, 'running')).toBeNull()
    expect(imageLiveState(undefined, REF, 'running')).toBeNull()
    expect(imageLiveState('', REF, 'running')).toBeNull()
  })
})
