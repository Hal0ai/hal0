// @vitest-environment happy-dom
// FLM model dropdown: shared RichSelect options with status icon, size and audio chips.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { FlmModelPicker, flmRichOptions } = await import('../npu/FlmModelPicker')

const models = [
  { model: 'gemma4-it:e4b', installed: true, capabilities: ['chat', 'stt'], family: 'gemma4', size_bytes: 4_000_000_000 },
  { model: 'qwen3.5:9b', installed: false, capabilities: ['chat'], family: 'qwen3.5', size_bytes: 6_400_000_000 },
  { model: 'whisper-v3:turbo', installed: true, capabilities: ['stt'], family: 'whisper' },
]
let host: HTMLDivElement
let root: Root
beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('flmRichOptions', () => {
  it('headers are disabled; models follow their group', () => {
    const o = flmRichOptions(models, 'chat', 'gemma4-it:e4b')
    expect(o.map((x: { id: string }) => x.id)).toEqual(['__hdr-installed', 'gemma4-it:e4b', '__hdr-download', 'qwen3.5:9b'])
    expect(o.filter((x: { disabled?: boolean }) => x.disabled).map((x: { id: string }) => x.id)).toEqual(['__hdr-installed', '__hdr-download'])
  })
})

describe('FlmModelPicker', () => {
  const open = (onChange = vi.fn(), cap = 'chat') => {
    act(() => root.render(<FlmModelPicker models={models as never} cap={cap as never} value="gemma4-it:e4b" onChange={onChange} testid="pick" ariaLabel="m" />))
    act(() => { (host.querySelector('[data-testid="pick"]') as HTMLElement).click() })
    return onChange
  }
  const opt = (id: string) => host.querySelector(`[data-option-id="${id}"]`) as HTMLElement

  it('shows audio, size and installed vs download icons', () => {
    open()
    expect(opt('gemma4-it:e4b').textContent).toContain('audio')
    expect(opt('gemma4-it:e4b').textContent).toContain('4.0 GB')
    expect(opt('gemma4-it:e4b').querySelector('.npt-flm-st.ok')).not.toBeNull()
    expect(opt('qwen3.5:9b').textContent).toContain('6.4 GB')
    expect(opt('qwen3.5:9b').querySelector('.npt-flm-st.dl')).not.toBeNull()
    expect(opt('qwen3.5:9b').textContent).not.toContain('audio')
  })
  it('choosing a model calls onChange; clicking a group header does not', () => {
    const onChange = open()
    act(() => opt('__hdr-download').click())
    expect(onChange).not.toHaveBeenCalled()
    act(() => opt('qwen3.5:9b').click())
    expect(onChange).toHaveBeenCalledWith('qwen3.5:9b')
  })
  it('the speech picker lists only speech models', () => {
    open(vi.fn(), 'stt')
    expect(opt('whisper-v3:turbo')).not.toBeNull()
    expect(opt('qwen3.5:9b')).toBeNull()
  })
})
