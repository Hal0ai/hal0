// hostHwFlags (slot-modals.jsx) — unknown-never-vetoes regression coverage.
//
// hw-cascade.js's runnerOptions() hides a runtime only when a lane's flag
// reads explicitly `false`; it never hides one on an absent/undefined flag
// ("unknown"). hostHwFlags is the sole translator from the raw /api/
// system-info `hardware` payload to that flag shape, so it must never
// manufacture an explicit `false` for a box it was never actually asked
// about. Caught by the Task 12 e2e mocks (a bare `hardware: {}` — no
// gpus[0] — silently vetoed every GPU runtime); see task-12-report.md.
//
// slot-modals.jsx is a window-globals dash module (`const {...} = React` at
// module top, `Object.assign(window, {...})` at the bottom), so the globals
// must be installed before the dynamic import — same pattern as
// runner-images-view.test.tsx / memoryOverviewV2.smoke.test.tsx.
import React from 'react'
import { describe, expect, it } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

const { hostHwFlags } = await import('../slot-modals.jsx')

describe('hostHwFlags', () => {
  it('no hardware payload at all (still loading) → {} (unknown, no veto)', () => {
    expect(hostHwFlags(undefined)).toEqual({})
    expect(hostHwFlags(null)).toEqual({})
  })

  it('hardware present but no gpus[0] (degraded probe / partial payload) → {} (unknown, no veto)', () => {
    expect(hostHwFlags({})).toEqual({})
    expect(hostHwFlags({ gpus: [] })).toEqual({})
  })

  it('gpu0 present, fully capable → all three lanes true', () => {
    expect(
      hostHwFlags({ gpus: [{ compute_capable: true, vulkan_capable: true }] }),
    ).toEqual({ rocm: true, vulkan: true, cuda: true })
  })

  it('gpu0 present, no compute capability → rocm/cuda explicitly false, vulkan true', () => {
    expect(
      hostHwFlags({ gpus: [{ compute_capable: false, vulkan_capable: true }] }),
    ).toEqual({ rocm: false, vulkan: true, cuda: false })
  })
})
