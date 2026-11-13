// @vitest-environment happy-dom
//
// The Memory area's page: the header and its summary line, the tab row, the settings link, and
// how the address is read and rewritten. The hooks are replaced; the tabs are the real ones.
import { act, createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MemoryBank, MemoryEngine } from '@/api/hooks/useHindsight'
import type { MemoryHealth } from '@/api/hooks/useMemory'
import {
  BANKS,
  ENGINE,
  GRAPH,
  HEALTH_BANKS,
  healthReport,
  healthUnreachable,
  modelsAnswer,
  type FakeQuery,
} from './area-test-fixtures'
import { MEMORY_TABS } from './memory-route-pure'
import { cleanup, click, mount } from './shared/test-harness'

const m = vi.hoisted(() => ({
  engine: {} as unknown,
  banks: {} as unknown,
  health: {} as unknown,
  /** Whether MemoryArea asked for the engine check and the bank list, render by render. */
  engineAsked: [] as boolean[],
  banksAsked: [] as boolean[],
}))

vi.mock('@/api/hooks/useHindsight', async () => {
  // The Knowledge tab's own reads, all still loading: this file is about the page around the tabs.
  const { knowledgeHooks, knowledgeState } = await import('./knowledge-test-fixtures')
  const idle = knowledgeState()
  return {
    ...knowledgeHooks(() => idle),
    useMemoryEngine: () => m.engine,
    useMemoryBanks: () => m.banks,
  }
})
vi.mock('@/api/hooks/useMemory', () => ({
  useMemoryGraphStatus: () => ({ data: GRAPH }),
  useMemoryModels: () => ({ data: modelsAnswer() }),
}))
vi.mock('./area-data', () => ({
  useMemoryHealth: () => m.health,
  useAreaEngine: (enabled: boolean) => {
    m.engineAsked.push(enabled)
    return enabled ? m.engine : {}
  },
  useAreaBanks: (enabled: boolean) => {
    m.banksAsked.push(enabled)
    return enabled ? m.banks : {}
  },
}))
// The Operations tab's requests: no answer yet. The tab's own states are in OperationsTab.test.
vi.mock('./operations-data', () => ({
  useQueueHealth: () => ({}),
  useBankConsolidation: () => ({}),
  useOperationLists: () => ({ entries: [], refetch: () => {} }),
  useRetryOne: () => ({ mutateAsync: async () => ({}) }),
  useConsolidateNow: () => ({ mutate: () => {}, isPending: false }),
  collectFailed: async () => ({ targets: [], more: false }),
  retryOperation: async () => ({}),
  discardOperation: async () => ({}),
  useQueueRefresh: () => () => {},
}))

// The Agents tab's requests: no answer yet. The tab's own states are in AgentsTab.test.
vi.mock('./agents-data', () => ({
  useAgentsRoute: () => ({}),
  useInstalledAgents: () => ({}),
  useMemoryKeyClients: () => ({}),
  useAllBankTags: (ids: string[]) => ids.map((bankId) => ({ bankId, items: null, isLoading: true })),
  useTagsRefresh: () => () => {},
}))

const { MemoryArea } = await import('./MemoryArea.jsx')

const setHash = (hash: string) =>
  (window as unknown as { happyDOM: { setURL: (url: string) => void } }).happyDOM.setURL(`http://localhost:9120/${hash}`)

/** Move to `hash` the way a followed link does: the address changes, then `hashchange` fires. */
function navigate(hash: string) {
  act(() => {
    setHash(hash)
    window.dispatchEvent(new Event('hashchange'))
  })
}

let workspaceProps: { bank: string; sel: string | null; embedded?: boolean }[] = []

beforeEach(() => {
  m.engine = { data: ENGINE } satisfies FakeQuery<MemoryEngine>
  m.banks = { data: { banks: BANKS } } satisfies FakeQuery<{ banks: MemoryBank[] }>
  m.health = { data: healthReport() } satisfies FakeQuery<MemoryHealth>
  m.engineAsked = []
  m.banksAsked = []
  workspaceProps = []
  ;(window as unknown as { MemV2Workspace?: unknown }).MemV2Workspace = (props: (typeof workspaceProps)[number]) => {
    workspaceProps.push(props)
    return createElement('div', { 'data-testid': 'workspace-stub' })
  }
  localStorage.clear()
  setHash('#memory/health')
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  delete (window as unknown as { MemV2Workspace?: unknown }).MemV2Workspace
})

const render = (hash?: string) => {
  if (hash) setHash(hash)
  return mount(createElement(MemoryArea)).host
}
const byId = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const tabs = (host: HTMLElement) =>
  [...byId(host, 'ma-tabs')!.querySelectorAll<HTMLAnchorElement>('a.ma-tab')]
const current = (host: HTMLElement) => tabs(host).filter((a) => a.getAttribute('aria-current') === 'page')
const meta = (host: HTMLElement) => byId(host, 'ma-meta')!

describe('header', () => {
  it('is the dashboard header pattern with one h1', () => {
    const host = render()
    const head = host.querySelector('.view.ma .vh')!
    expect(head.querySelector('h1')!.textContent).toBe('Memory')
    expect(host.querySelectorAll('h1')).toHaveLength(1)
    expect(head.contains(meta(host))).toBe(true)
  })

  it('has no bank control: the bank is chosen in a tab, and lives in the address', () => {
    const host = render('#memory/health?bank=shared')
    for (const selector of ['select', '[role="combobox"]', '[role="radiogroup"]', '[role="listbox"]']) {
      expect(host.querySelector(selector), selector).toBeNull()
    }
  })
})

describe('tab row', () => {
  it('is five real links, in order', () => {
    const host = render()
    expect(byId(host, 'ma-tabs')!.tagName).toBe('NAV')
    expect(byId(host, 'ma-tabs')!.getAttribute('aria-label')).toBe('Memory sections')
    expect(tabs(host).map((a) => a.textContent)).toEqual(['Health', 'Facts', 'Knowledge', 'Operations', 'Agents'])
    expect(tabs(host).map((a) => a.getAttribute('href'))).toEqual(MEMORY_TABS.map((t) => `#memory/${t}`))
    for (const tab of tabs(host)) expect(tab.tagName).toBe('A')
  })

  it.each(MEMORY_TABS)('marks exactly one tab as the current page on #memory/%s', (tab) => {
    const host = render(`#memory/${tab}`)
    expect(current(host)).toHaveLength(1)
    expect(current(host)[0].dataset.testid).toBe(`ma-tab-${tab}`)
    // Every other tab carries no aria-current at all, not "false".
    for (const other of tabs(host).filter((a) => a !== current(host)[0])) {
      expect(other.hasAttribute('aria-current')).toBe(false)
    }
  })

  it('opens Health, and marks only Health, for an unknown tab', () => {
    const host = render('#memory/nope')
    expect(current(host).map((a) => a.textContent)).toEqual(['Health'])
    expect(byId(host, 'ma-health-summary')).not.toBeNull()
  })

  it('shows the view of the tab that is open, and only that one', () => {
    expect(byId(render('#memory/knowledge'), 'ma-knowledge')).not.toBeNull()
    cleanup()
    expect(byId(render('#memory/operations'), 'ma-operations')).not.toBeNull()
    cleanup()
    const agents = render('#memory/agents')
    expect(byId(agents, 'ma-agents')).not.toBeNull()
    expect(byId(agents, 'ma-health-summary')).toBeNull()
    expect(byId(agents, 'ma-knowledge')).toBeNull()
  })

  it('carries the chosen bank to the other tabs, and leaves the open fact behind', () => {
    const host = render('#memory/facts?bank=Hal0::Agents&fact=f26')
    expect(tabs(host).map((a) => a.getAttribute('href'))).toEqual(
      MEMORY_TABS.map((t) => `#memory/${t}?bank=Hal0::Agents`),
    )
  })

  it('follows the address when it changes (a tab link, Back, Forward)', () => {
    const host = render('#memory/health')
    navigate('#memory/agents')
    expect(current(host).map((a) => a.textContent)).toEqual(['Agents'])
    expect(byId(host, 'ma-agents')).not.toBeNull()
    navigate('#memory/health')
    expect(current(host).map((a) => a.textContent)).toEqual(['Health'])
    expect(byId(host, 'ma-agents')).toBeNull()
  })

  it('ignores an address outside the area (the page is about to unmount)', () => {
    const host = render('#memory/agents')
    navigate('#slots')
    expect(current(host).map((a) => a.textContent)).toEqual(['Agents'])
  })
})

describe('engine settings link', () => {
  it('is a quiet link to Settings ▸ Memory, after the tabs, and not a tab', () => {
    const host = render()
    const link = byId(host, 'ma-engine-settings')!
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe('#settings/memory')
    expect(link.textContent).toBe('Engine settings')
    expect(link.hasAttribute('aria-current')).toBe(false)
    expect(link.classList.contains('ma-tab')).toBe(false)
    const row = [...byId(host, 'ma-tabs')!.children]
    expect(row.indexOf(link)).toBe(row.length - 1)
  })

  it.each(MEMORY_TABS)('is there on #memory/%s', (tab) => {
    expect(byId(render(`#memory/${tab}`), 'ma-engine-settings')).not.toBeNull()
  })
})

describe('summary line', () => {
  it('counts the facts and the banks from the health answer', () => {
    const host = render()
    expect(meta(host).dataset.state).toBe('ready')
    expect(meta(host).textContent).toBe('27,542 facts in 3 banks')
  })

  it('is a skeleton, never a zero, while the health answer is out', () => {
    m.health = {}
    const host = render()
    expect(meta(host).dataset.state).toBe('loading')
    expect(meta(host).querySelector('.ma-skel')).not.toBeNull()
    expect(meta(host).textContent).toBe('Loading')
  })

  it('says the engine is unreachable, with no number', () => {
    m.health = { data: healthUnreachable() }
    const host = render()
    expect(meta(host).textContent).toBe('Engine unreachable · no counts available')
  })

  it('says when hal0 did not answer, and when memory is off', () => {
    m.health = { isError: true }
    expect(meta(render()).textContent).toBe('Memory status did not load · no counts available')
    cleanup()
    m.health = {
      data: { enabled: false, reason: 'memory_disabled', checked_at: '', verdict: null, partial: [], steps: [] },
    }
    expect(meta(render()).textContent).toBe('Memory is turned off · no counts available')
  })

  it('says how many banks a partial count covers', () => {
    const list = HEALTH_BANKS!.list.map((b) => (b.bank_id === 'Hal0::Agents' ? { ...b, facts: null } : b))
    m.health = { data: healthReport({ partial: ['banks'], banks: { ...HEALTH_BANKS!, list } }) }
    expect(meta(render()).textContent).toBe('3 banks · 3,012 facts in the 2 that reported')
  })

  it('gives no fact count when no bank reported one', () => {
    const list = HEALTH_BANKS!.list.map((b) => ({ ...b, facts: null }))
    m.health = { data: healthReport({ banks: { ...HEALTH_BANKS!, facts: null, list } }) }
    expect(meta(render()).textContent).toBe('3 banks · fact counts not reported')
  })

  it('says there are no banks, and that memory is on a fallback engine', () => {
    const base = healthReport()
    m.health = {
      data: healthReport({
        banks: { layout: 'per_bank', total: 0, facts: 0, list: [] },
        queue: { ...base.queue!, by_bank: [] },
      }),
    }
    expect(meta(render()).textContent).toBe('Engine reachable · no banks yet')
    cleanup()
    m.health = {
      data: { enabled: true, reason: 'engine_unsupported', checked_at: '', verdict: null, partial: [], steps: [] },
    }
    expect(meta(render()).textContent).toBe('Fallback engine · no counts available')
  })

  it('is the same line on every tab', () => {
    for (const tab of MEMORY_TABS) {
      expect(meta(render(`#memory/${tab}`)).textContent).toBe('27,542 facts in 3 banks')
      cleanup()
    }
  })
})

describe('requests', () => {
  it('Health asks for neither the engine check nor the bank list: the health answer has both', () => {
    render('#memory/health')
    expect(m.engineAsked.length).toBeGreaterThan(0)
    expect(m.engineAsked.every((asked) => asked === false)).toBe(true)
    expect(m.banksAsked.every((asked) => asked === false)).toBe(true)
  })

  it('a tab with a bank control asks for them', () => {
    render('#memory/facts?bank=shared')
    expect(m.engineAsked.at(-1)).toBe(true)
    expect(m.banksAsked.at(-1)).toBe(true)
  })
})

describe('the address holds the bank and the fact', () => {
  it('fills in the first bank with replaceState, not a new history entry', () => {
    const replace = vi.spyOn(window.history, 'replaceState')
    const push = vi.spyOn(window.history, 'pushState')
    const host = render('#memory/facts')
    expect(replace).toHaveBeenCalledTimes(1)
    expect(replace.mock.calls[0][2]).toBe('#memory/facts?bank=shared')
    expect(push).not.toHaveBeenCalled()
    expect(byId(host, 'workspace-stub')).not.toBeNull()
    expect(workspaceProps.at(-1)).toMatchObject({ bank: 'shared', sel: null, embedded: true })
  })

  it('opens the bank and the fact a deep link names, and rewrites nothing', () => {
    const replace = vi.spyOn(window.history, 'replaceState')
    render('#memory/facts?bank=coding-agent::ultrareview-1.4&fact=f26')
    expect(workspaceProps.at(-1)).toMatchObject({ bank: 'coding-agent::ultrareview-1.4', sel: 'f26' })
    expect(replace).not.toHaveBeenCalled()
  })

  it('switching bank rewrites the bank and clears the open fact, with replaceState', () => {
    const host = render('#memory/facts?bank=shared&fact=f26')
    const replace = vi.spyOn(window.history, 'replaceState')
    const push = vi.spyOn(window.history, 'pushState')
    const radios = [...host.querySelectorAll<HTMLButtonElement>('[data-testid="ma-scope-bank"] [role="radio"]')]
    click(radios.find((r) => r.textContent === 'Hal0::Agents')!)
    expect(replace).toHaveBeenCalledTimes(1)
    expect(replace.mock.calls[0][2]).toBe('#memory/facts?bank=Hal0::Agents')
    expect(push).not.toHaveBeenCalled()
    expect(workspaceProps.at(-1)).toMatchObject({ bank: 'Hal0::Agents', sel: null })
    // The tab links follow the bank that is now chosen.
    expect(tabs(host)[0].getAttribute('href')).toBe('#memory/health?bank=Hal0::Agents')
  })

  it('a bank change and a fact change made in one handler do not undo each other', () => {
    render('#memory/facts?bank=shared&fact=f26')
    const replace = vi.spyOn(window.history, 'replaceState')
    const props = workspaceProps.at(-1) as unknown as {
      setBank: (id: string) => void
      setSel: (id: string | null) => void
    }
    act(() => {
      props.setBank('Hal0::Agents')
      props.setSel('f3')
    })
    expect(replace.mock.calls.at(-1)![2]).toBe('#memory/facts?bank=Hal0::Agents&fact=f3')
    expect(workspaceProps.at(-1)).toMatchObject({ bank: 'Hal0::Agents', sel: 'f3' })
  })
})

describe('notice slot', () => {
  it('holds a notice between the tab row and the tab, on every tab', () => {
    const notice = createElement('div', { 'data-testid': 'area-notice' }, 'The fold-in is running.')
    const { host } = mount(createElement(MemoryArea, { notice }))
    const slot = byId(host, 'area-notice')!
    const position = (el: Element) => byId(host, 'ma-tabs')!.compareDocumentPosition(el)
    expect(position(slot) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(slot.compareDocumentPosition(byId(host, 'ma-health-summary')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('is empty by default', () => {
    expect(byId(render(), 'area-notice')).toBeNull()
  })
})
