// @vitest-environment happy-dom
//
// ImagePanel: saving ANY generation default must not pin the slot's
// `default_size`. An empty `default_size` means "the model's native size"
// (SD 1.5 and SDXL Turbo are 512x512); the panel used to write the empty field
// back as 1024x1024, which re-pinned every model to 1024 on an unrelated save
// (an idle-restore edit, say). The empty field now round-trips as "".
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
// FieldInfoIcon is a dash/primitives.jsx window global, used unimported by the panel.
;(globalThis as unknown as { FieldInfoIcon: () => null }).FieldInfoIcon = () => null

const { editCalls, configBox } = vi.hoisted(() => ({
  editCalls: [] as { name: string; body: { image: Record<string, unknown> } }[],
  configBox: { current: {} as Record<string, unknown> },
}))

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: [{ name: 'img', type: 'image', group: 'img' }] }),
  useSlotConfig: () => ({ data: configBox.current }),
  useSlotEdit: () => ({
    isPending: false,
    mutateAsync: async (args: { name: string; body: { image: Record<string, unknown> } }) => {
      editCalls.push(args)
    },
  }),
}))

vi.mock('../settings/pages/capabilities/useCapabilitySelection.js', () => ({
  useCapabilitySelection: () => ({
    capsQuery: { isError: false, isLoading: false },
    applyCapability: { isPending: false },
    provider: 'comfyui',
    setProvider: () => {},
    enabled: true,
    setEnabled: () => {},
    status: 'ready',
    dirty: false,
    reset: () => {},
    save: async () => {},
    loading: false,
    errored: false,
  }),
}))

const { ImagePanel } = await import('../settings/pages/capabilities/ImagePanel.jsx')

const inputValueSetter = Object.getOwnPropertyDescriptor(
  window.HTMLInputElement.prototype,
  'value',
)!.set!

function mount(): HTMLElement {
  const host = document.createElement('div')
  document.body.appendChild(host)
  act(() => {
    createRoot(host).render(<ImagePanel registry={{}} />)
  })
  return host
}

function inputs(host: HTMLElement): HTMLInputElement[] {
  return Array.from(host.querySelectorAll('input')).filter(
    (i) => i.type !== 'checkbox',
  ) as HTMLInputElement[]
}

function type(input: HTMLInputElement, value: string) {
  act(() => {
    inputValueSetter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function save(host: HTMLElement) {
  const button = Array.from(host.querySelectorAll('button')).find((b) =>
    /Save image generation/.test(b.textContent || ''),
  ) as HTMLButtonElement
  await act(async () => {
    button.click()
  })
}

beforeEach(() => {
  editCalls.length = 0
  document.body.innerHTML = ''
})

describe('ImagePanel default size', () => {
  it('saving another default leaves an empty size empty (model native)', async () => {
    configBox.current = {
      image: { default_size: '', default_steps: 0, idle_restore_minutes: 60 },
    }
    const host = mount()
    const [size, , idle] = inputs(host)

    expect(size.value).toBe('')
    expect(size.placeholder).toBe('model native')

    type(idle, '30')
    await save(host)

    expect(editCalls).toHaveLength(1)
    expect(editCalls[0].body.image).toEqual({
      default_size: '',
      default_steps: 0,
      idle_restore_minutes: 30,
    })
  })

  it('clearing the size field saves an empty size, not 1024x1024', async () => {
    configBox.current = {
      image: { default_size: '768x768', default_steps: 0, idle_restore_minutes: 60 },
    }
    const host = mount()
    const [size] = inputs(host)
    expect(size.value).toBe('768x768')

    type(size, '')
    await save(host)

    expect(editCalls[0].body.image.default_size).toBe('')
  })

  it('a size the operator typed is saved as typed', async () => {
    configBox.current = { image: { default_size: '', default_steps: 0, idle_restore_minutes: 60 } }
    const host = mount()
    const [size] = inputs(host)

    type(size, ' 640x640 ')
    await save(host)

    expect(editCalls[0].body.image.default_size).toBe('640x640')
  })
})
