// Mount helpers for the parts' unit tests. The same harness the other dash tests hand-roll: a
// real React root in happy-dom, driven with `act`, and no testing library. A test file that uses
// it starts with `// @vitest-environment happy-dom`.
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

export interface Mounted {
  host: HTMLDivElement
  render: (node: ReactNode) => void
  unmount: () => void
}

const live: { root: Root; host: HTMLDivElement }[] = []

/** Render `node` into a fresh host attached to the document (focus needs a connected tree). */
export function mount(node: ReactNode): Mounted {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  live.push({ root, host })
  act(() => root.render(node))
  return {
    host,
    render: (next) => act(() => root.render(next)),
    unmount: () => act(() => root.unmount()),
  }
}

/** Unmount everything `mount` created. Call from `afterEach`. */
export function cleanup() {
  for (const { root, host } of live.splice(0)) {
    act(() => root.unmount())
    host.remove()
  }
}

export function click(el: Element) {
  act(() => {
    ;(el as HTMLElement).click()
  })
}

/** Send one keydown to `el`. Answers whether the handler left the default action alone. */
export function press(el: Element, key: string): boolean {
  let allowed = true
  act(() => {
    allowed = el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  })
  return allowed
}

/** Let pending promise callbacks (an awaited clipboard write) run and their renders commit. */
export async function settle() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

/**
 * Advance vitest's fake clock by `ms` and commit what the fired timers rendered.
 *
 * A timer callback that sets state runs outside React's `act`, so React only schedules the
 * render; whether it has committed after a couple of microtasks depends on the scheduler and
 * on how busy the machine is (#4098). Advancing inside `act` makes the commit part of the call.
 */
export async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}
