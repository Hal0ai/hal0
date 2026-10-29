// @vitest-environment happy-dom
//
// Overview v2 shell: OvCard / OvPill / OvSpark window globals (shell.jsx).
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

await import('../shell.jsx')
const g = globalThis as unknown as Record<string, React.ComponentType<any>>
const { OvCard, OvPill, OvSpark } = g

function mount(el: React.ReactElement) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => root.render(el))
  return host
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('OvCard', () => {
  it('renders header title and note, body and footer', () => {
    const host = mount(
      <OvCard title="Usage" note="last 24h" foot="x">
        body
      </OvCard>,
    )
    expect(host.querySelector('section.ov-card > header.ov-card-h .ov-card-t')?.textContent).toBe('Usage')
    expect(host.querySelector('.ov-card-n')?.textContent).toBe('last 24h')
    expect(host.querySelector('.ov-card-b')?.textContent).toBe('body')
    expect(host.querySelector('footer.ov-card-f')?.textContent).toBe('x')
  })

  it('omits the note and footer when not given, and marks a flush body', () => {
    const host = mount(
      <OvCard title="T" flush className="extra" right={<b className="r">R</b>}>
        b
      </OvCard>,
    )
    expect(host.querySelector('.ov-card-n')).toBeNull()
    expect(host.querySelector('footer')).toBeNull()
    expect(host.querySelector('.ov-card-b.flush')).not.toBeNull()
    expect(host.querySelector('section')?.className).toBe('ov-card extra')
    expect(host.querySelector('.ov-card-h .sp + .r')).not.toBeNull()
  })
})

describe('OvPill', () => {
  it('applies the tone class', () => {
    const host = mount(<OvPill tone="warn">slow</OvPill>)
    const pill = host.querySelector('.ov-pill')!
    expect(pill.classList.contains('warn')).toBe(true)
    expect(pill.textContent).toBe('slow')
  })
})

describe('OvSpark', () => {
  it('renders nothing for fewer than two values', () => {
    const host = mount(<OvSpark values={[1]} />)
    expect(host.innerHTML).toBe('')
  })

  it('draws area, line and endpoint circle for a series', () => {
    const host = mount(<OvSpark values={[1, 2, 3]} />)
    expect(host.querySelectorAll('svg')).toHaveLength(1)
    expect(host.querySelectorAll('svg circle')).toHaveLength(1)
    expect(host.querySelectorAll('svg path')).toHaveLength(2)
  })
})
