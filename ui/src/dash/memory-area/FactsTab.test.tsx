// @vitest-environment happy-dom
//
// Memory ▸ Facts: the scope bar over the existing fact workspace. The workspace itself is a
// window global here (as in the app); a stand-in records the props it is mounted with.
import { createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MemoryBank } from '@/api/hooks/useHindsight'
import { BANKS, ENGINE, makeArea } from './area-test-fixtures'
import { cleanup, click, mount } from './shared/test-harness'

const { FactsTab, LAST_BANK_KEY } = await import('./FactsTab.jsx')

interface WorkspaceProps {
  bank: string
  setBank: (id: string | null) => void
  sel: string | null
  setSel: (id: string | null) => void
  embedded?: boolean
}

let mounted: WorkspaceProps[] = []
const lastProps = () => mounted[mounted.length - 1]

beforeEach(() => {
  mounted = []
  ;(window as unknown as { MemV2Workspace?: unknown }).MemV2Workspace = (props: WorkspaceProps) => {
    mounted.push(props)
    return createElement('div', { 'data-testid': 'workspace-stub' }, props.bank)
  }
  localStorage.clear()
})
afterEach(() => {
  cleanup()
  delete (window as unknown as { MemV2Workspace?: unknown }).MemV2Workspace
})

const render = (area: ReturnType<typeof makeArea>) => mount(createElement(FactsTab, { area }))
const byId = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const radios = (host: HTMLElement) => [...host.querySelectorAll<HTMLButtonElement>('[role="radio"]')]
const textOf = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()

describe('with a bank in the address', () => {
  it('mounts the workspace on that bank, inside the area', () => {
    const { host } = render(makeArea({ hash: '#memory/facts?bank=Hal0::Agents' }))
    expect(byId(host, 'workspace-stub')!.textContent).toBe('Hal0::Agents')
    expect(lastProps()).toMatchObject({ bank: 'Hal0::Agents', sel: null, embedded: true })
  })

  it('opens the fact a deep link names', () => {
    render(makeArea({ hash: '#memory/facts?bank=coding-agent::ultrareview-1.4&fact=f26' }))
    expect(lastProps()).toMatchObject({ bank: 'coding-agent::ultrareview-1.4', sel: 'f26' })
  })

  it('shows the scope bar with the real banks, ids verbatim, and no project control', () => {
    const { host } = render(makeArea({ hash: '#memory/facts?bank=shared' }))
    const scope = byId(host, 'ma-scope')!
    expect(scope.getAttribute('aria-label')).toBe('Scope')
    expect(radios(host).map((r) => r.textContent)).toEqual(BANKS.map((b) => b.bank_id))
    expect(radios(host).map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false'])
    expect(host.querySelector('[data-testid="ma-scope-project"]')).toBeNull()
    expect(textOf(scope)).not.toContain('Project')
  })

  it('puts the scope bar above the workspace', () => {
    const { host } = render(makeArea({ hash: '#memory/facts?bank=shared' }))
    const scope = byId(host, 'ma-scope')!
    const workspace = byId(host, 'workspace-stub')!
    expect(scope.compareDocumentPosition(workspace) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('remembers the bank for the Overview card', () => {
    render(makeArea({ hash: '#memory/facts?bank=Hal0::Agents' }))
    expect(localStorage.getItem(LAST_BANK_KEY)).toBe('Hal0::Agents')
  })
})

describe('switching bank', () => {
  it('rewrites the bank and clears the open fact, from the scope bar', () => {
    const replaceRoute = vi.fn()
    const { host } = render(makeArea({ hash: '#memory/facts?bank=shared&fact=f26', replaceRoute }))
    click(radios(host)[1])
    expect(replaceRoute).toHaveBeenCalledTimes(1)
    expect(replaceRoute).toHaveBeenCalledWith({ bank: 'Hal0::Agents', fact: null })
  })

  it('does the same when the workspace changes bank (after deleting one)', () => {
    const replaceRoute = vi.fn()
    render(makeArea({ hash: '#memory/facts?bank=shared&fact=f26', replaceRoute }))
    lastProps().setBank('coding-agent::ultrareview-1.4')
    expect(replaceRoute).toHaveBeenCalledWith({ bank: 'coding-agent::ultrareview-1.4', fact: null })
  })

  it('writes the open fact into the address, and clears it when the inspector closes', () => {
    const replaceRoute = vi.fn()
    render(makeArea({ hash: '#memory/facts?bank=shared', replaceRoute }))
    lastProps().setSel('f3')
    expect(replaceRoute).toHaveBeenLastCalledWith({ fact: 'f3' })
    lastProps().setSel(null)
    expect(replaceRoute).toHaveBeenLastCalledWith({ fact: null })
  })

  it('does not rewrite the address when the bank already chosen is picked', () => {
    const replaceRoute = vi.fn()
    const { host } = render(makeArea({ hash: '#memory/facts?bank=shared', replaceRoute }))
    click(radios(host)[0])
    expect(replaceRoute).not.toHaveBeenCalled()
  })
})

describe('an address with no bank', () => {
  it('falls back to the first bank, written into the address in place', () => {
    const replaceRoute = vi.fn()
    const { host } = render(makeArea({ hash: '#memory/facts', replaceRoute }))
    expect(replaceRoute).toHaveBeenCalledTimes(1)
    expect(replaceRoute).toHaveBeenCalledWith({ bank: 'shared' })
    // Nothing is mounted on a bank the address does not name yet.
    expect(byId(host, 'workspace-stub')).toBeNull()
    expect(mounted).toEqual([])
    // The scope bar is already there, with nothing checked.
    expect(radios(host).map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'false', 'false'])
  })

  it('keeps a fact out of the fallback: it only fills in the bank', () => {
    const replaceRoute = vi.fn()
    render(makeArea({ hash: '#memory/facts?fact=f26', replaceRoute }))
    expect(replaceRoute).toHaveBeenCalledWith({ bank: 'shared' })
  })

  it('waits for the bank list before it falls back', () => {
    const replaceRoute = vi.fn()
    const { host } = render(makeArea({ hash: '#memory/facts', banksQuery: {}, replaceRoute }))
    expect(replaceRoute).not.toHaveBeenCalled()
    expect(byId(host, 'ma-facts-loading')).not.toBeNull()
    expect(host.textContent).not.toMatch(/\d/)
  })
})

describe('a bank the engine does not list', () => {
  it('says so, keeps the scope bar, and mounts nothing under another name', () => {
    const replaceRoute = vi.fn()
    const { host } = render(makeArea({ hash: '#memory/facts?bank=gone::bank', replaceRoute }))
    expect(textOf(byId(host, 'ma-facts-unknown-bank'))).toContain('gone::bank')
    expect(textOf(byId(host, 'ma-facts-unknown-bank'))).toContain('Choose a bank above.')
    expect(byId(host, 'ma-scope')).not.toBeNull()
    expect(byId(host, 'workspace-stub')).toBeNull()
    expect(replaceRoute).not.toHaveBeenCalled()
    expect(localStorage.getItem(LAST_BANK_KEY)).toBeNull()
  })

  it('opens it all the same when the bank list is only the first page', () => {
    const { host } = render(
      makeArea({ hash: '#memory/facts?bank=later::bank', banksQuery: { data: { banks: BANKS, total: 140 } } }),
    )
    expect(byId(host, 'ma-facts-unknown-bank')).toBeNull()
    expect(lastProps()).toMatchObject({ bank: 'later::bank' })
  })
})

describe('nothing to show', () => {
  it('says the engine is not answering, and links to Health', () => {
    const { host } = render(
      makeArea({ hash: '#memory/facts?bank=shared', engineQuery: { isError: true }, banksQuery: { isError: true } }),
    )
    const card = byId(host, 'ma-facts-unreachable')!
    expect(textOf(card)).toContain('The memory engine is not answering')
    expect(card.querySelector('a')!.getAttribute('href')).toBe('#memory/health?bank=shared')
    expect(byId(host, 'ma-scope')).toBeNull()
    expect(mounted).toEqual([])
  })

  it('shows a skeleton while the engine check is out', () => {
    const { host } = render(makeArea({ hash: '#memory/facts?bank=shared', engineQuery: {} }))
    expect(byId(host, 'ma-facts-loading')).not.toBeNull()
    expect(mounted).toEqual([])
  })

  it('says there are no banks yet', () => {
    const replaceRoute = vi.fn()
    const { host } = render(
      makeArea({ hash: '#memory/facts', banksQuery: { data: { banks: [] as MemoryBank[] } }, replaceRoute }),
    )
    expect(textOf(byId(host, 'ma-facts-no-banks'))).toContain('No banks yet')
    expect(replaceRoute).not.toHaveBeenCalled()
  })

  it('says the bank list did not load, and can ask again', () => {
    const refetch = vi.fn()
    const { host } = render(makeArea({ hash: '#memory/facts', banksQuery: { isError: true, refetch } }))
    click(byId(host, 'ma-facts-banks-error')!.querySelector('button')!)
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('says so when memory is not on Hindsight', () => {
    const { host } = render(
      makeArea({ hash: '#memory/facts', engineQuery: { data: { ...ENGINE, engine: null, reachable: false } } }),
    )
    expect(textOf(byId(host, 'ma-facts-fallback'))).toContain('Memory is not running on Hindsight')
  })
})
