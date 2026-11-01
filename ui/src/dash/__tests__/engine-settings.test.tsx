// @vitest-environment happy-dom
//
// EngineSettings — the profile drawer's engine block for a halogen-family
// runner: one typed control per `backends[<runner>].knobs` row, written into
// the profile's flags text as HALOGEN_NAME=value tokens.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { EngineSettings } from '../engine-settings.jsx'
import { HALOGEN_KNOBS } from './fixtures/halogen-knobs'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function mount(props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const onChange = vi.fn()
  const render = (extra: Record<string, unknown> = {}) =>
    act(() => {
      root.render(
        React.createElement(EngineSettings, { knobs: HALOGEN_KNOBS, flags: '', onChange, ...props, ...extra }),
      )
    })
  render()
  return { host, root, onChange, render }
}

const q = (host: HTMLElement, id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null

function type(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('EngineSettings', () => {
  it('renders one control per knob (4 numbers, 2 enums, 2 switches)', () => {
    const { host, root } = mount({})
    const rows = host.querySelectorAll('[data-testid^="engine-knob-"]')
    expect(rows).toHaveLength(8)
    expect(q(host, 'engine-input-HALOGEN_KV_SLOTS')?.tagName).toBe('INPUT')
    expect(q(host, 'engine-input-HALOGEN_KV_POOL_POSITIONS')?.tagName).toBe('INPUT')
    expect(q(host, 'engine-input-HALOGEN_MAX_TOKENS_DEFAULT')?.tagName).toBe('INPUT')
    expect(q(host, 'engine-input-HALOGEN_HOST_RESERVE_GIB')?.tagName).toBe('INPUT')
    // enum → one segment per choice
    expect(q(host, 'engine-opt-HALOGEN_MAX_TOK-16384')).not.toBeNull()
    expect(q(host, 'engine-opt-HALOGEN_MAX_TOK-32768')).not.toBeNull()
    expect(q(host, 'engine-opt-HALOGEN_PROMPT_CACHE-2')).not.toBeNull()
    // bool → a switch
    expect(q(host, 'engine-switch-HALOGEN_KV8')?.getAttribute('role')).toBe('switch')
    expect(q(host, 'engine-switch-HALOGEN_WEIGHTS_LOCK')?.getAttribute('role')).toBe('switch')
    act(() => root.unmount())
  })

  it('gives each number input its min and max, and each row its label and help line', () => {
    const { host, root } = mount({})
    const slots = q(host, 'engine-input-HALOGEN_KV_SLOTS') as HTMLInputElement
    expect(slots.getAttribute('min')).toBe('1')
    expect(slots.getAttribute('max')).toBe('64')
    const pool = q(host, 'engine-input-HALOGEN_KV_POOL_POSITIONS') as HTMLInputElement
    expect(pool.getAttribute('min')).toBe('1024')
    expect(pool.hasAttribute('max')).toBe(false)
    const row = q(host, 'engine-knob-HALOGEN_KV_SLOTS') as HTMLElement
    expect(row.textContent).toContain('KV slots')
    expect(row.textContent).toContain('Conversations generating at the same moment.')
    act(() => root.unmount())
  })

  it('marks the memory knobs with a badge and no other', () => {
    const { host, root } = mount({})
    const memory = HALOGEN_KNOBS.filter((k) => k.memory).map((k) => k.env)
    for (const k of HALOGEN_KNOBS) {
      expect(q(host, `engine-memory-${k.env}`) !== null).toBe(memory.includes(k.env))
    }
    act(() => root.unmount())
  })

  it('shows the engine default as the placeholder when a number is unset', () => {
    const { host, root } = mount({})
    expect((q(host, 'engine-input-HALOGEN_KV_SLOTS') as HTMLInputElement).placeholder).toBe('2')
    expect((q(host, 'engine-input-HALOGEN_MAX_TOKENS_DEFAULT') as HTMLInputElement).placeholder).toBe('engine default')
    act(() => root.unmount())
  })

  it('emits HALOGEN_KV_SLOTS=4 on change', () => {
    const { host, root, onChange } = mount({})
    type(q(host, 'engine-input-HALOGEN_KV_SLOTS') as HTMLInputElement, '4')
    expect(onChange).toHaveBeenLastCalledWith('HALOGEN_KV_SLOTS=4')
    act(() => root.unmount())
  })

  it('writes into the existing flags text and keeps its other tokens', () => {
    const flags = 'HALOGEN_MSG="Time is up." HALOGEN_KV_SLOTS=2'
    const { host, root, onChange } = mount({ flags })
    expect((q(host, 'engine-input-HALOGEN_KV_SLOTS') as HTMLInputElement).value).toBe('2')
    type(q(host, 'engine-input-HALOGEN_KV_SLOTS') as HTMLInputElement, '8')
    expect(onChange).toHaveBeenLastCalledWith('HALOGEN_MSG="Time is up." HALOGEN_KV_SLOTS=8')
    act(() => root.unmount())
  })

  it('clearing a number removes the token (back to the engine default)', () => {
    const { host, root, onChange } = mount({ flags: 'HALOGEN_KV_SLOTS=4 HALOGEN_KV8=1' })
    type(q(host, 'engine-input-HALOGEN_KV_SLOTS') as HTMLInputElement, '')
    expect(onChange).toHaveBeenLastCalledWith('HALOGEN_KV8=1')
    act(() => root.unmount())
  })

  it('an enum segment writes its choice, and the active segment follows the flags', () => {
    const { host, root, onChange, render } = mount({})
    // unset → the engine default reads as selected
    expect(q(host, 'engine-opt-HALOGEN_MAX_TOK-16384')?.getAttribute('aria-pressed')).toBe('true')
    act(() => q(host, 'engine-opt-HALOGEN_MAX_TOK-32768')!.click())
    expect(onChange).toHaveBeenLastCalledWith('HALOGEN_MAX_TOK=32768')
    render({ flags: 'HALOGEN_MAX_TOK=32768' })
    expect(q(host, 'engine-opt-HALOGEN_MAX_TOK-32768')?.getAttribute('aria-pressed')).toBe('true')
    expect(q(host, 'engine-opt-HALOGEN_MAX_TOK-16384')?.getAttribute('aria-pressed')).toBe('false')
    act(() => root.unmount())
  })

  it('a switch flips between 1 and 0, starting from the engine default when unset', () => {
    const { host, root, onChange, render } = mount({})
    // WEIGHTS_LOCK defaults to 1 (on); KV8 defaults to 0 (off)
    expect(q(host, 'engine-switch-HALOGEN_WEIGHTS_LOCK')?.getAttribute('aria-checked')).toBe('true')
    expect(q(host, 'engine-switch-HALOGEN_KV8')?.getAttribute('aria-checked')).toBe('false')
    act(() => q(host, 'engine-switch-HALOGEN_KV8')!.click())
    expect(onChange).toHaveBeenLastCalledWith('HALOGEN_KV8=1')
    act(() => q(host, 'engine-switch-HALOGEN_WEIGHTS_LOCK')!.click())
    expect(onChange).toHaveBeenLastCalledWith('HALOGEN_WEIGHTS_LOCK=0')
    render({ flags: 'HALOGEN_KV8=1' })
    expect(q(host, 'engine-switch-HALOGEN_KV8')?.getAttribute('aria-checked')).toBe('true')
    act(() => q(host, 'engine-switch-HALOGEN_KV8')!.click())
    expect(onChange).toHaveBeenLastCalledWith('HALOGEN_KV8=0')
    act(() => root.unmount())
  })

  it('offers a reset only on a knob whose token is set, and it removes the token', () => {
    const { host, root, onChange } = mount({ flags: 'HALOGEN_KV8=1 HALOGEN_KV_SLOTS=4' })
    expect(q(host, 'engine-reset-HALOGEN_MAX_TOK')).toBeNull()
    act(() => q(host, 'engine-reset-HALOGEN_KV8')!.click())
    expect(onChange).toHaveBeenLastCalledWith('HALOGEN_KV_SLOTS=4')
    act(() => root.unmount())
  })

  it('reads the last value of a repeated name, as the server does', () => {
    const { host, root } = mount({ flags: 'HALOGEN_KV_SLOTS=1 HALOGEN_KV_SLOTS=6' })
    expect((q(host, 'engine-input-HALOGEN_KV_SLOTS') as HTMLInputElement).value).toBe('6')
    act(() => root.unmount())
  })

  it('shows a value the control cannot express as typed, so the error text has something to point at', () => {
    const { host, root } = mount({ flags: 'HALOGEN_MAX_TOK=8192 HALOGEN_KV_SLOTS=999' })
    expect((q(host, 'engine-input-HALOGEN_KV_SLOTS') as HTMLInputElement).value).toBe('999')
    // no enum segment is pressed for an unlisted choice
    expect(q(host, 'engine-opt-HALOGEN_MAX_TOK-16384')?.getAttribute('aria-pressed')).toBe('false')
    expect(q(host, 'engine-opt-HALOGEN_MAX_TOK-32768')?.getAttribute('aria-pressed')).toBe('false')
    act(() => root.unmount())
  })

  it('readOnly disables every control and emits nothing', () => {
    const { host, root, onChange } = mount({ readOnly: true, flags: 'HALOGEN_KV8=1' })
    for (const el of host.querySelectorAll('input, button')) {
      expect((el as HTMLInputElement | HTMLButtonElement).disabled).toBe(true)
    }
    act(() => q(host, 'engine-switch-HALOGEN_KV8')!.click())
    expect(onChange).not.toHaveBeenCalled()
    act(() => root.unmount())
  })

  it('renders nothing for a runner with no knobs', () => {
    const { host, root } = mount({ knobs: [] })
    expect(host.querySelector('[data-testid="pf-engine-settings"]')).toBeNull()
    act(() => root.unmount())
  })
})
