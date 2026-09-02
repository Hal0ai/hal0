// @vitest-environment happy-dom
//
// RichSelect trigger passthrough (Task 11 review fix): the shell used to
// hardcode `className="rsel-trigger"` and drop any `aria-label` a caller
// passed, which silently regressed the slot drawer's Runtime error-border
// styling and the Model select's screen-reader label when Task 11c swapped
// their native <select>s for RichSelect. Both are now optional props merged
// onto the trigger <button> — this pins that the merge actually happens.
//
// This repo has no @testing-library — mount with createRoot/act directly
// (mirrors runner-images-pull-terminal.test.tsx's own note on this).
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import { RichSelect } from '../rich-select'

let container: HTMLDivElement | null = null

afterEach(() => {
  if (container) {
    act(() => {
      createRoot(container!).unmount()
    })
    container.remove()
    container = null
  }
})

function mount(el: React.ReactElement) {
  container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => {
    root.render(el)
  })
  return container
}

const OPTIONS = [{ id: 'a', row: 'Option A' }]

describe('RichSelect trigger passthrough', () => {
  it('merges an optional className onto the hardcoded rsel-trigger class', () => {
    const el = mount(
      <RichSelect
        data-testid="probe"
        value="a"
        options={OPTIONS}
        onChange={() => {}}
        className="rsel-err"
      />,
    )
    const trigger = el.querySelector('[data-testid="probe"]') as HTMLButtonElement
    expect(trigger.classList.contains('rsel-trigger')).toBe(true)
    expect(trigger.classList.contains('rsel-err')).toBe(true)
  })

  it('renders no extra class when className is omitted', () => {
    const el = mount(
      <RichSelect data-testid="probe" value="a" options={OPTIONS} onChange={() => {}} />,
    )
    const trigger = el.querySelector('[data-testid="probe"]') as HTMLButtonElement
    expect(trigger.className).toBe('rsel-trigger')
  })

  it('forwards aria-label onto the trigger button', () => {
    const el = mount(
      <RichSelect
        data-testid="probe"
        value="a"
        options={OPTIONS}
        onChange={() => {}}
        aria-label="Model for agent"
      />,
    )
    const trigger = el.querySelector('[data-testid="probe"]') as HTMLButtonElement
    expect(trigger.getAttribute('aria-label')).toBe('Model for agent')
  })

  it('omits the aria-label attribute when none is passed', () => {
    const el = mount(
      <RichSelect data-testid="probe" value="a" options={OPTIONS} onChange={() => {}} />,
    )
    const trigger = el.querySelector('[data-testid="probe"]') as HTMLButtonElement
    expect(trigger.hasAttribute('aria-label')).toBe(false)
  })
})
