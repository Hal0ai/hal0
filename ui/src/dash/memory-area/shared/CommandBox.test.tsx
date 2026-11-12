// @vitest-environment happy-dom
import { createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, click, mount, settle } from './test-harness'

const { clipboard } = vi.hoisted(() => ({ clipboard: { ok: true, calls: [] as string[] } }))

vi.mock('@/lib/clipboard', () => ({
  copyTextToClipboard: async (text: string) => {
    clipboard.calls.push(text)
    return clipboard.ok
  },
}))

const { CommandBox } = await import('./CommandBox.jsx')

beforeEach(() => {
  clipboard.ok = true
  clipboard.calls.length = 0
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const parts = (host: HTMLElement) => ({
  code: host.querySelector('code')!,
  button: host.querySelector<HTMLButtonElement>('button')!,
  status: host.querySelector<HTMLElement>('[role="status"]')!,
})

describe('CommandBox', () => {
  it('shows the command in a code element, with a decorative prompt', () => {
    const { host } = mount(createElement(CommandBox, { command: 'hal0 doctor' }))
    const { code } = parts(host)
    expect(code.textContent).toBe('hal0 doctor')
    expect(code.className).toBe('ma-cmd-text')
    const prompt = host.querySelector('.ma-cmd-prompt')!
    expect(prompt.textContent).toBe('$')
    expect(prompt.getAttribute('aria-hidden')).toBe('true')
  })

  it('names its icon-only button with aria-label', () => {
    const { host } = mount(createElement(CommandBox, { command: 'hal0 doctor' }))
    const { button } = parts(host)
    expect(button.getAttribute('aria-label')).toBe('Copy command')
    expect(button.textContent).toBe('') // icon only
    expect(button.querySelector('svg')!.getAttribute('aria-hidden')).toBe('true')
    expect(button.getAttribute('type')).toBe('button')

    const other = mount(createElement(CommandBox, { command: 'x', copyLabel: 'Copy install command' }))
    expect(parts(other.host).button.getAttribute('aria-label')).toBe('Copy install command')
  })

  it('announces nothing before the button is used', () => {
    const { host } = mount(createElement(CommandBox, { command: 'hal0 doctor' }))
    expect(parts(host).status.textContent).toBe('')
    expect(host.textContent).not.toContain('Copied')
  })

  it('announces "Copied" through the status region only after the copy lands', async () => {
    const { host } = mount(createElement(CommandBox, { command: 'hal0 memory embedder switch --dry-run' }))
    const { button, status } = parts(host)
    click(button)
    await settle()
    expect(clipboard.calls).toEqual(['hal0 memory embedder switch --dry-run'])
    expect(status.textContent).toBe('Copied')
    // The same region that was empty at load: it was not swapped for a new node.
    expect(parts(host).status).toBe(status)
  })

  it('says so when the browser refuses the copy', async () => {
    clipboard.ok = false
    const { host } = mount(createElement(CommandBox, { command: 'hal0 doctor' }))
    click(parts(host).button)
    await settle()
    expect(parts(host).status.textContent).toBe('Could not copy')
  })

  it('clears the announcement after a moment, so the next copy is announced again', async () => {
    vi.useFakeTimers()
    const { host } = mount(createElement(CommandBox, { command: 'hal0 doctor' }))
    click(parts(host).button)
    await settle()
    expect(parts(host).status.textContent).toBe('Copied')
    await vi.advanceTimersByTimeAsync(3000)
    await settle()
    expect(parts(host).status.textContent).toBe('')
  })
})
