// @vitest-environment happy-dom
import { createElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { InlineConfirm, confirmPhrase } from './InlineConfirm.jsx'
import { cleanup, click, mount, press } from './test-harness'

afterEach(cleanup)

const LOSS = 'What agents sent in these 105 operations will never become facts. This cannot be undone.'

function open(over: Record<string, unknown> = {}) {
  const onConfirm = vi.fn()
  const onDismiss = vi.fn()
  const mounted = mount(
    createElement(
      InlineConfirm,
      { verb: 'Discard', count: 105, noun: 'failed operations', onConfirm, onDismiss, ...over },
      LOSS,
    ),
  )
  const group = mounted.host.querySelector<HTMLElement>('[role="group"]')!
  const [confirm, keep] = [...group.querySelectorAll<HTMLButtonElement>('button')]
  return { ...mounted, group, confirm, keep, onConfirm, onDismiss }
}

describe('confirmPhrase', () => {
  it.each([
    [{ verb: 'Discard', count: 105, noun: 'failed operations' }, 'Discard 105 failed operations'],
    [{ verb: 'Delete', count: 52955, noun: 'facts' }, 'Delete 52,955 facts'],
    [{ verb: 'Delete', count: 1, noun: 'facts', nounSingular: 'fact' }, 'Delete 1 fact'],
    [{ verb: 'Delete', count: 1, noun: 'facts' }, 'Delete 1 facts'],
    [{ verb: 'Delete', noun: 'the bank' }, 'Delete the bank'],
  ])('%j → %s', (input, expected) => {
    expect(confirmPhrase(input)).toBe(expected)
  })
})

describe('InlineConfirm', () => {
  it('states what and how many, in a group named by its title', () => {
    const { group } = open()
    const title = group.querySelector('h3')!
    expect(title.textContent).toBe('Discard 105 failed operations?')
    expect(group.getAttribute('aria-labelledby')).toBe(title.id)
    expect(group.querySelector('p')!.textContent).toBe(LOSS)
    // In place, not a dialog.
    expect(group.getAttribute('aria-modal')).toBeNull()
    expect(group.closest('[role="dialog"]')).toBeNull()
  })

  it('puts the object and the count on the confirm button, never a bare "Confirm"', () => {
    const { confirm, group } = open()
    expect(confirm.textContent).toBe('Discard 105 failed operations')
    expect(confirm.className).toBe('btn danger')
    // The button is described by the sentence that says what is lost.
    expect(confirm.getAttribute('aria-describedby')).toBe(group.querySelector('p')!.id)
  })

  it('names the other button for what is kept, and styles the two differently', () => {
    const { keep, confirm } = open()
    expect(keep.textContent).toBe('Keep them')
    expect(keep.textContent).not.toMatch(/cancel/i)
    expect(keep.className).toBe('btn ghost')
    expect(keep.className).not.toBe(confirm.className)
  })

  it('orders the buttons confirm, then keep, and focuses keep first', () => {
    const { group, confirm, keep } = open()
    expect([...group.querySelectorAll('button')]).toEqual([confirm, keep])
    expect(document.activeElement).toBe(keep)
  })

  it('accepts another keep label for a single thing', () => {
    const { keep, confirm, group } = open({ verb: 'Delete', count: undefined, noun: 'the bank', dismissLabel: 'Keep it' })
    expect(keep.textContent).toBe('Keep it')
    expect(confirm.textContent).toBe('Delete the bank')
    expect(group.querySelector('h3')!.textContent).toBe('Delete the bank?')
  })

  it('calls onDismiss from the keep button and from Escape, and never onConfirm', () => {
    const { keep, group, onConfirm, onDismiss } = open()
    click(keep)
    expect(onDismiss).toHaveBeenCalledTimes(1)
    press(keep, 'Escape')
    expect(onDismiss).toHaveBeenCalledTimes(2)
    press(group, 'Enter')
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('calls onConfirm only from the confirm button', () => {
    const { confirm, onConfirm, onDismiss } = open()
    click(confirm)
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onDismiss).not.toHaveBeenCalled()
  })

  it('disables confirm while busy and leaves keep usable', () => {
    const { confirm, keep, onConfirm } = open({ busy: true })
    expect(confirm.disabled).toBe(true)
    expect(keep.disabled).toBe(false)
    click(confirm)
    expect(onConfirm).not.toHaveBeenCalled()
  })
})
