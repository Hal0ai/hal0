// @vitest-environment happy-dom
import { createElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReasonedButton } from './ReasonedButton.jsx'
import { cleanup, click, mount } from './test-harness'

afterEach(cleanup)

describe('ReasonedButton', () => {
  it('is a plain button when there is no reason', () => {
    const onClick = vi.fn()
    const { host } = mount(createElement(ReasonedButton, { className: 'btn ghost sm', onClick }, 'Refresh'))
    const button = host.querySelector('button')!
    expect(button.disabled).toBe(false)
    expect(button.className).toBe('btn ghost sm')
    expect(button.getAttribute('type')).toBe('button')
    expect(button.hasAttribute('aria-describedby')).toBe(false)
    expect(host.querySelector('.ma-reason')).toBeNull()
    click(button)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('is disabled by a reason, shows it, and points at it', () => {
    const onClick = vi.fn()
    const { host } = mount(
      createElement(ReasonedButton, { reason: 'Waiting on the extraction connection.', onClick }, 'Refresh'),
    )
    const button = host.querySelector('button')!
    const reason = host.querySelector('.ma-reason')!
    expect(button.disabled).toBe(true)
    expect(button.className).toBe('btn')
    expect(reason.textContent).toBe('Waiting on the extraction connection.')
    expect(reason.id).not.toBe('')
    expect(button.getAttribute('aria-describedby')).toBe(reason.id)
    click(button)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('gives two buttons two different reason ids', () => {
    const { host } = mount(
      createElement(
        'div',
        null,
        createElement(ReasonedButton, { reason: 'First.' }, 'A'),
        createElement(ReasonedButton, { reason: 'Second.' }, 'B'),
      ),
    )
    const ids = [...host.querySelectorAll('.ma-reason')].map((p) => p.id)
    expect(new Set(ids).size).toBe(2)
  })

  it('still honours a plain disabled with no reason', () => {
    const { host } = mount(createElement(ReasonedButton, { disabled: true }, 'Refresh'))
    expect(host.querySelector('button')!.disabled).toBe(true)
    expect(host.querySelector('.ma-reason')).toBeNull()
  })
})
