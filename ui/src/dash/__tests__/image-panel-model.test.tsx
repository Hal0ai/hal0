// @vitest-environment happy-dom
//
// ImagePanel: the Model row. The img selection (GET/POST /api/capabilities, group
// img, child img) is what /v1/images defaults and Open WebUI's image model follow,
// and it is the one place the dashboard picks it (the slot drawer and swap popover
// link here for an image slot). The row lists the image picker rows the API serves
// (catalog models plus the operator's own `ckpt/<path>` checkpoints), marks a model
// whose weights are not on disk, and saves the pick as `model`.
//
// The real useCapabilitySelection runs; only the capabilities query/mutation and the
// slot hooks are mocked at the module boundary.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
// FieldInfoIcon is a dash/primitives.jsx window global, used unimported by the panel.
;(globalThis as unknown as { FieldInfoIcon: () => null }).FieldInfoIcon = () => null

const { applyCalls, capsBox } = vi.hoisted(() => ({
  applyCalls: [] as { slot: string; child: string; body: Record<string, unknown> }[],
  capsBox: { current: undefined as Record<string, unknown> | undefined },
}))

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: [{ name: 'img', type: 'image', group: 'img' }] }),
  useSlotConfig: () => ({ data: { image: {} } }),
  useSlotEdit: () => ({ isPending: false, mutateAsync: async () => {} }),
}))

vi.mock('@/api/hooks/useCapabilities', () => ({
  useCapabilities: () => ({ data: capsBox.current, isLoading: false, isError: false, refetch: () => {} }),
  useCapabilityApply: () => ({
    isPending: false,
    mutateAsync: async (args: { slot: string; child: string; body: Record<string, unknown> }) => {
      applyCalls.push(args)
    },
  }),
}))

const { ImagePanel } = await import('../settings/pages/capabilities/ImagePanel.jsx')

const CATALOG = [
  {
    id: 'sdxl-lightning',
    capabilities: ['image'],
    size_gb: 7.6,
    display_name: 'SDXL Lightning (8-step)',
    license: 'CreativeML-OpenRAIL++',
    recommended: true,
    backends: [{ id: 'gpu-vulkan', provider: 'comfyui', downloaded: true, pullable: true }],
  },
  {
    id: 'qwen-image',
    capabilities: ['image'],
    size_gb: 40,
    display_name: 'Qwen-Image',
    license: 'Apache-2.0',
    recommended: false,
    backends: [{ id: 'gpu-vulkan', provider: 'comfyui', downloaded: false, pullable: true }],
  },
  {
    id: 'ckpt/mine/sdxl-custom',
    capabilities: ['image'],
    size_gb: 6.43,
    display_name: 'mine/sdxl-custom.safetensors',
    license: '',
    recommended: false,
    backends: [{ id: 'gpu-vulkan', provider: 'comfyui', downloaded: true, pullable: false }],
  },
]

function caps(items: unknown[], model: string | null) {
  return {
    backends: [],
    catalogs: { img: { img: items } },
    selections: {
      img: { img: { device: 'gpu-vulkan', backend: 'gpu-vulkan', provider: 'comfyui', model, enabled: true, slot: 'img', status: 'ready' } },
    },
  }
}

const selectValueSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')!.set!

function mount(): HTMLElement {
  const host = document.createElement('div')
  document.body.appendChild(host)
  act(() => {
    createRoot(host).render(<ImagePanel registry={{}} />)
  })
  return host
}

// The Model row's control: the one <select> whose "— unset —" option the row owns.
function modelSelect(host: HTMLElement): HTMLSelectElement | null {
  return (Array.from(host.querySelectorAll('select')).find((s) =>
    Array.from(s.options).some((o) => o.textContent === '— unset —'),
  ) ?? null) as HTMLSelectElement | null
}

function saveButton(host: HTMLElement): HTMLButtonElement {
  return Array.from(host.querySelectorAll('button')).find((b) =>
    /Save image generation/.test(b.textContent || ''),
  ) as HTMLButtonElement
}

beforeEach(() => {
  applyCalls.length = 0
  document.body.innerHTML = ''
})

describe('ImagePanel Model row', () => {
  it('lists the image catalog and the operator checkpoints by label, marking a model not on disk', () => {
    capsBox.current = caps(CATALOG, 'sdxl-lightning')
    const host = mount()
    const select = modelSelect(host)!
    expect(select.value).toBe('sdxl-lightning')
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      '— unset —',
      'SDXL Lightning (8-step) (sdxl-lightning)',
      'Qwen-Image (qwen-image) · ⬇ not downloaded',
      'mine/sdxl-custom.safetensors (ckpt/mine/sdxl-custom)',
    ])
    // The old "selected per-workflow" pointer is gone: this row is where it is picked.
    expect(host.textContent).not.toContain('selected per-workflow')
  })

  it('saves the picked model in the capability apply body', async () => {
    capsBox.current = caps(CATALOG, 'sdxl-lightning')
    const host = mount()
    expect(saveButton(host).disabled).toBe(true)

    const select = modelSelect(host)!
    act(() => {
      selectValueSetter.call(select, 'ckpt/mine/sdxl-custom')
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(saveButton(host).disabled).toBe(false)
    await act(async () => saveButton(host).click())

    expect(applyCalls).toHaveLength(1)
    expect(applyCalls[0]).toMatchObject({
      slot: 'img',
      child: 'img',
      body: { model: 'ckpt/mine/sdxl-custom', enabled: true, provider: 'comfyui' },
    })
  })

  it('with no catalog rows falls back to a free-text id and links to the Image slot page', () => {
    capsBox.current = caps([], null)
    const host = mount()
    expect(modelSelect(host)).toBeNull()
    const link = host.querySelector('[data-testid="img-model-empty-link"]') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('#slots/image')
  })

  it('shows no empty-state link while the catalog has rows', () => {
    capsBox.current = caps(CATALOG, null)
    const host = mount()
    expect(host.querySelector('[data-testid="img-model-empty-link"]')).toBeNull()
  })
})
