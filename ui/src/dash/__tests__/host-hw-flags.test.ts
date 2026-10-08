// hostHwFlags (hw-cascade.js) — unknown-never-vetoes regression coverage.
//
// hw-cascade.js's runnerOptions() hides a runtime only when a lane's flag
// reads explicitly `false`; it never hides one on an absent/undefined flag
// ("unknown"). hostHwFlags is the sole translator from the raw /api/
// system-info `hardware` payload to that flag shape, so it must never
// manufacture an explicit `false` for a box it was never actually asked
// about. Caught by the Task 12 e2e mocks (a bare `hardware: {}` — no
// gpus[0] — silently vetoed every GPU runtime); see task-12-report.md.
//
// It lives in hw-cascade.js (a pure-functions module, no window globals)
// rather than in slot-modals.jsx because both drawers consume it: the slot
// drawer AND the profile drawer's Runtime select. The profile-drawer end of
// that parity is asserted in profiles.test.tsx.
import { describe, expect, it } from 'vitest'

import { hostHwFlags } from '../hw-cascade.js'

describe('hostHwFlags', () => {
  it('no hardware payload at all (still loading) → {} (unknown, no veto)', () => {
    expect(hostHwFlags(undefined)).toEqual({})
    expect(hostHwFlags(null)).toEqual({})
  })

  it('hardware present but no gpus[0] (degraded probe / partial payload) → {} (unknown, no veto)', () => {
    expect(hostHwFlags({})).toEqual({})
    expect(hostHwFlags({ gpus: [] })).toEqual({})
  })

  it('gpu0 present, fully capable → rocm + vulkan true, no cuda flag', () => {
    expect(
      hostHwFlags({ gpus: [{ compute_capable: true, vulkan_capable: true }] }),
    ).toEqual({ rocm: true, vulkan: true })
  })

  it('gpu0 present, no compute capability → rocm explicitly false, vulkan true', () => {
    expect(
      hostHwFlags({ gpus: [{ compute_capable: false, vulkan_capable: true }] }),
    ).toEqual({ rocm: false, vulkan: true })
  })

  // CUDA is not supported in this release: the old `cuda: compute_capable`
  // derivation marked the CUDA lane feasible on every ROCm-capable AMD box
  // (compute_capable is vendor-neutral). No cuda flag is emitted at all.
  it('AMD gpu0 with compute capability never reports a cuda lane', () => {
    const flags = hostHwFlags({
      gpus: [{ vendor: 'amd', compute_capable: true, vulkan_capable: true }],
    })
    expect(flags).toEqual({ rocm: true, vulkan: true })
    expect('cuda' in flags).toBe(false)
  })

  // compute_capable is set from nvidia-smi presence too — that is not ROCm
  // evidence. An NVIDIA box is served by the Vulkan lane.
  it('NVIDIA gpu0 → rocm false, vulkan from the probe', () => {
    expect(
      hostHwFlags({
        kfd_present: false,
        gpus: [{ vendor: 'nvidia', compute_capable: true, vulkan_capable: true }],
      }),
    ).toEqual({ rocm: false, vulkan: true })
  })

  // kfd_present: FE/BE ROCm gate mismatch (host-truth fix). A box with
  // /dev/kfd but no host rocminfo (containers bring their own ROCm
  // userland) actively runs ROCm slots — the backend's own feasibility
  // gate (config_write._reconcile_device_profile via
  // hal0.providers._gpu.kfd_present) allows it, so the drawer must not
  // veto it just because the host-rocminfo-backed compute_capable is false.

  it('gpu0 present, compute_capable false but top-level kfd_present true → rocm true', () => {
    expect(
      hostHwFlags({
        kfd_present: true,
        gpus: [{ compute_capable: false, vulkan_capable: false }],
      }),
    ).toEqual({ rocm: true, vulkan: false })
  })

  it('gpu0 present, compute_capable true and kfd_present false → rocm true (compute_capable alone still sufficient)', () => {
    expect(
      hostHwFlags({
        kfd_present: false,
        gpus: [{ compute_capable: true, vulkan_capable: false }],
      }),
    ).toEqual({ rocm: true, vulkan: false })
  })

  it('no gpus[0] but top-level kfd_present true → still {} (unknown shape preserved, no veto)', () => {
    expect(hostHwFlags({ kfd_present: true, gpus: [] })).toEqual({})
    expect(hostHwFlags({ kfd_present: true })).toEqual({})
  })
})
