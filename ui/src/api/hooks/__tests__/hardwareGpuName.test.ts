// #3156: the probe's raw GPU string is an lspci line ("… Device 1586 (rev c1)")
// on a box whose pci.ids predates the part. /api/hardware also carries
// `gpu_display_name`, derived server-side by hal0.hardware.gpu_name; the UI
// shows that and keeps the raw string for older servers that do not send it.
import { describe, expect, it } from 'vitest'
import { normalizeHardware } from '../useHardware'

const PCI = 'Advanced Micro Devices, Inc. [AMD/ATI] Device 1586 (rev c1)'

describe('normalizeHardware gpuDisplayName', () => {
  it('takes the server display name and keeps the raw probe string in gpu', () => {
    const hw = normalizeHardware({ gpu_name: PCI, gpu_display_name: 'Radeon 8060S' })
    expect(hw.gpuDisplayName).toBe('Radeon 8060S')
    expect(hw.gpu).toBe(PCI)
  })

  it('falls back to the raw probe name when the server sends no display name', () => {
    expect(normalizeHardware({ gpu_name: PCI }).gpuDisplayName).toBe(PCI)
  })

  it('treats an empty display name as absent', () => {
    expect(normalizeHardware({ gpu_name: PCI, gpu_display_name: '' }).gpuDisplayName).toBe(PCI)
  })

  it('falls back through the nested gpu entry like gpu does', () => {
    expect(normalizeHardware({ gpus: [{ name: 'Radeon 8060S' }] }).gpuDisplayName).toBe('Radeon 8060S')
  })

  it('is empty on a box with no GPU name at all', () => {
    expect(normalizeHardware({}).gpuDisplayName).toBe('')
  })
})
