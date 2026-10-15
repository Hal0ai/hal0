// #2674: a vulkan-only box (render node passed through, no /dev/kfd) is still
// a GPU box. The Overview and Settings panels gate their GPU rows on
// hasGpuAccess(); the NPU rows gate on their own hasNpuAccess().
import { describe, expect, it } from 'vitest'
import { hasGpuAccess, hasNpuAccess, normalizeHardware } from '../useHardware'

const vulkanOnly = {
  gpus: [{ name: 'Radeon 8060S', compute_capable: false, vulkan_capable: true }],
  kfd_present: false,
}
const kfdPresent = {
  gpus: [{ name: 'Radeon 8060S', compute_capable: false, vulkan_capable: false }],
  kfd_present: true,
}
const cpuOnly = { gpus: [{ compute_capable: false, vulkan_capable: false }], kfd_present: false }

describe('hasGpuAccess', () => {
  it('is true on a vulkan-only box', () => {
    expect(hasGpuAccess(normalizeHardware(vulkanOnly))).toBe(true)
  })
  it('is true when kfd is present (unchanged)', () => {
    expect(hasGpuAccess(normalizeHardware(kfdPresent))).toBe(true)
  })
  it('is false on a CPU-only box and while loading', () => {
    expect(hasGpuAccess(normalizeHardware(cpuOnly))).toBe(false)
    expect(hasGpuAccess(undefined)).toBe(false)
  })
})

describe('hasNpuAccess', () => {
  it('does not ride on the GPU gate', () => {
    expect(hasNpuAccess(normalizeHardware(vulkanOnly))).toBe(false)
    expect(hasNpuAccess(normalizeHardware({ ...kfdPresent, npu: { present: false } }))).toBe(false)
  })
  it('is true when an NPU is present', () => {
    expect(hasNpuAccess(normalizeHardware({ ...cpuOnly, npu: { present: true } }))).toBe(true)
  })
})
