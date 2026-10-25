// @vitest-environment happy-dom
//
// ImagePanel: saving ANY generation default must not pin the slot's
// `default_size`. An empty `default_size` means "the model's native size"
// (SD 1.5 and SDXL Turbo are 512x512); the panel used to write the empty field
// back as 1024x1024, which re-pinned every model to 1024 on an unrelated save
// (a steps edit, say). "Model native" now round-trips as "".
//
// Default size is a select (Model native, four listed sizes, Custom...), so the controls
// are addressed by test id, not by position among the inputs.
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
    // The Model row reads these; a non-empty catalog renders a <select>, so the
    // text inputs this file indexes stay the three generation defaults.
    catalogItems: [{ id: 'sdxl-lightning' }],
    model: 'sdxl-lightning',
    setModel: () => {},
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

const selectValueSetter = Object.getOwnPropertyDescriptor(
  window.HTMLSelectElement.prototype,
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

function byId<T extends HTMLElement>(host: HTMLElement, id: string): T {
  const el = host.querySelector(`[data-testid="${id}"]`)
  if (!el) throw new Error(`no element with test id ${id}`)
  return el as T
}

function sizeSelect(host: HTMLElement): HTMLSelectElement {
  return byId<HTMLSelectElement>(host, 'img-default-size')
}
function customSize(host: HTMLElement): HTMLInputElement {
  return byId<HTMLInputElement>(host, 'img-default-size-custom')
}
function stepsInput(host: HTMLElement): HTMLInputElement {
  return byId<HTMLInputElement>(host, 'img-default-steps')
}
function stepsHint(host: HTMLElement): HTMLElement | null {
  return host.querySelector('[data-testid="img-default-steps-hint"]')
}
function saveButton(host: HTMLElement): HTMLButtonElement {
  return Array.from(host.querySelectorAll('button')).find((b) =>
    /Save image generation/.test(b.textContent || ''),
  ) as HTMLButtonElement
}

function type(input: HTMLInputElement, value: string) {
  act(() => {
    inputValueSetter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

function pick(select: HTMLSelectElement, value: string) {
  act(() => {
    selectValueSetter.call(select, value)
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

async function save(host: HTMLElement) {
  const button = saveButton(host)
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
      image: { default_size: '', default_steps: 0 },
    }
    const host = mount()
    const size = sizeSelect(host)

    expect(size.value).toBe('')
    expect(size.selectedOptions[0].textContent).toBe('Model native')
    // Model native is not a custom size: no W x H field.
    expect(host.querySelector('[data-testid="img-default-size-custom"]')).toBeNull()

    type(stepsInput(host), '30')
    await save(host)

    expect(editCalls).toHaveLength(1)
    expect(editCalls[0].body.image).toEqual({
      default_size: '',
      default_steps: 30,
    })
  })

  it('model native saves empty default_size', async () => {
    configBox.current = {
      image: { default_size: '768x768', default_steps: 0 },
    }
    const host = mount()
    const size = sizeSelect(host)
    expect(size.value).toBe('768x768')

    pick(size, '')
    await save(host)

    expect(editCalls).toHaveLength(1)
    expect(editCalls[0].body.image.default_size).toBe('')
  })

  it('offers Model native, the four listed sizes and Custom', () => {
    configBox.current = { image: { default_size: '', default_steps: 0 } }
    const host = mount()
    const options = Array.from(sizeSelect(host).options).map((o) => [o.value, o.textContent])

    expect(options).toEqual([
      ['', 'Model native'],
      ['512x512', '512x512'],
      ['768x768', '768x768'],
      ['1024x1024', '1024x1024'],
      ['1328x1328', '1328x1328'],
      ['custom', 'Custom\u2026'],
    ])
  })

  it('a listed size saves as picked', async () => {
    configBox.current = { image: { default_size: '', default_steps: 0 } }
    const host = mount()

    pick(sizeSelect(host), '1328x1328')
    await save(host)

    expect(editCalls[0].body.image.default_size).toBe('1328x1328')
  })

  it('custom size saves WxH', async () => {
    configBox.current = { image: { default_size: '', default_steps: 0 } }
    const host = mount()

    pick(sizeSelect(host), 'custom')
    type(customSize(host), ' 640x384 ')
    await save(host)

    expect(editCalls).toHaveLength(1)
    expect(editCalls[0].body.image.default_size).toBe('640x384')
  })

  it('a saved size that is not listed shows as Custom with its value', async () => {
    configBox.current = { image: { default_size: '640x640', default_steps: 0 } }
    const host = mount()

    expect(sizeSelect(host).value).toBe('custom')
    expect(customSize(host).value).toBe('640x640')

    // Going back to a listed size drops the custom field and saves that size.
    pick(sizeSelect(host), '512x512')
    expect(host.querySelector('[data-testid="img-default-size-custom"]')).toBeNull()
    await save(host)
    expect(editCalls[0].body.image.default_size).toBe('512x512')
  })

  it.each(['1030x1030', '32x512', '4096x512', '1024', 'big', ''])(
    'a custom size of %j is refused: Save is off and nothing is written',
    async (bad) => {
      configBox.current = { image: { default_size: '', default_steps: 0 } }
      const host = mount()

      pick(sizeSelect(host), 'custom')
      type(customSize(host), bad)
      type(stepsInput(host), '12') // something else is dirty, so only the size gates Save

      expect(saveButton(host).disabled).toBe(true)
      await save(host)
      expect(editCalls).toHaveLength(0)
    },
  )

  it('says which sizes a custom field accepts once it holds a bad one', () => {
    configBox.current = { image: { default_size: '', default_steps: 0 } }
    const host = mount()

    pick(sizeSelect(host), 'custom')
    type(customSize(host), '1030x1030')

    expect(host.textContent).toMatch(/64.{1,3}2048/)
    expect(host.textContent).toMatch(/multiple of 8/)
  })
})

describe('ImagePanel default steps', () => {
  it('steps zero shows model default hint', () => {
    configBox.current = { image: { default_size: '', default_steps: 0 } }
    const host = mount()

    expect(stepsInput(host).value).toBe('0')
    expect(stepsHint(host)?.textContent).toBe('model default')

    type(stepsInput(host), '30')
    expect(stepsHint(host)).toBeNull()

    type(stepsInput(host), '0')
    expect(stepsHint(host)?.textContent).toBe('model default')
  })

  it('a set step count shows no hint', () => {
    configBox.current = { image: { default_size: '', default_steps: 25 } }
    const host = mount()

    expect(stepsInput(host).value).toBe('25')
    expect(stepsHint(host)).toBeNull()
  })
})
