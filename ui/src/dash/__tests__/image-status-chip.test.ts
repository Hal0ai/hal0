// #1939 — `image_status: "unknown"` must render as an honest indeterminate.
//
// The backend now distinguishes "podman was asked and the image is not there"
// (`missing`) from "the container image store could not be asked at all"
// (`unknown` — wrapper rc 66, no sudoers grant, seam drift, probe timeout).
// A UI that paints the second one like the first — or like a fault — throws
// away the entire point of the tri-state, so the classifier is pinned here
// rather than left to the JSX.
import { describe, expect, it } from 'vitest'

import type { Slot } from '@/api/hooks/useSlots'

import { imageStatusChip } from '../slot-status.js'

// Compile-time half of chain site 5: this file IS typechecked (tsconfig
// includes src/**/*.ts, and `npm run typecheck` runs before the build), so a
// union that loses the `unknown` member fails CI here even though every
// runtime assertion below would still pass.
const UNKNOWN_SLOT: Slot = {
  name: 'gpu-chat',
  type: 'llm',
  device: 'gpu-rocm',
  model: 'qwen3.6-35b',
  state: 'ready',
  metrics: {},
  runtime: 'container',
  container_status: 'running',
  image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-server',
  image_status: 'unknown',
}

describe('imageStatusChip', () => {
  it('renders an indeterminate chip for unknown', () => {
    const chip = imageStatusChip(UNKNOWN_SLOT)
    expect(chip).not.toBeNull()
    expect(chip!.label).toBe('image ?')
  })

  it('uses a NEUTRAL class — never the error/warn vocabulary', () => {
    // The colour rule in slot-status.js: RED = error, AMBER/YELLOW =
    // transitional or degraded. "We could not read the image store" is
    // neither; painting it as either one re-tells the lie in CSS.
    const cls = imageStatusChip(UNKNOWN_SLOT)!.cls
    expect(cls).toContain('image-unknown')
    expect(cls).not.toMatch(/\b(err|error|warn|ok)\b/)
  })

  it('says what it does not know, and what to do about it', () => {
    const tooltip = imageStatusChip(UNKNOWN_SLOT)!.tooltip
    // Must NOT claim absence…
    expect(tooltip).not.toMatch(/\bnot (present|installed|found)\b/i)
    expect(tooltip).not.toMatch(/\bmissing\b/i)
    // …and must point at the seam, which is the thing an operator can fix.
    expect(tooltip).toMatch(/could not/i)
    expect(tooltip).toMatch(/hal0 doctor/)
  })

  it.each([
    ['present', 'present'],
    ['missing', 'missing'],
    ['pulling', 'pulling'],
    ['not-configured', 'not-configured'],
  ] as const)('renders no chip for %s (definitive or already-surfaced)', (_l, status) => {
    expect(imageStatusChip({ image_status: status })).toBeNull()
  })

  it('renders no chip when the field is absent or null', () => {
    expect(imageStatusChip({})).toBeNull()
    expect(imageStatusChip({ image_status: null })).toBeNull()
    expect(imageStatusChip(null)).toBeNull()
    expect(imageStatusChip(undefined)).toBeNull()
  })
})
