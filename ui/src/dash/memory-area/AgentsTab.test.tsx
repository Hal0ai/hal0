// @vitest-environment happy-dom
//
// Memory ▸ Agents: the tab's states. What is read (the agents route, each bank's tags, the
// installed agents, the key holders) is replaced; the cards are the real ones.
import { createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BankTagsEntry, MemoryClient } from './agents-pure'
import { makeArea, type FakeQuery } from './area-test-fixtures'
import { cleanup, click, mount, settle } from './shared/test-harness'

const COMMAND = 'hal0 memory agents install <harness> --bridge-key-file hal0-memory.key'
const MINT = 'hal0 bridge add <harness> --scope memory'
const HARNESSES = ['claude-code', 'codex', 'opencode', 'pi', 'cursor-cli', 'copilot-cli', 'qwen-code']

const m = vi.hoisted(() => ({
  agents: {} as unknown,
  installed: {} as unknown,
  clients: {} as unknown,
  tags: {} as Record<string, unknown>,
  tagsFor: [] as string[][],
  refreshed: 0,
  refetched: 0,
  copied: [] as string[],
}))

vi.mock('./agents-data', () => ({
  useAgentsRoute: () => m.agents,
  useInstalledAgents: () => m.installed,
  useMemoryKeyClients: () => m.clients,
  useAllBankTags: (ids: string[]) => {
    m.tagsFor.push(ids)
    return ids.map((bankId) => (m.tags[bankId] as BankTagsEntry | undefined) ?? { bankId, items: [] })
  },
  useTagsRefresh: () => () => {
    m.refreshed += 1
  },
}))
vi.mock('@/lib/clipboard', () => ({
  copyTextToClipboard: async (text: string) => {
    m.copied.push(text)
    return true
  },
}))

const { AgentsTab } = await import('./AgentsTab.jsx')

const tags = (bankId: string, counts: Record<string, number>): BankTagsEntry => ({
  bankId,
  items: Object.entries(counts).map(([tag, count]) => ({ tag, count })),
})
const agentsAnswer = (over: Record<string, unknown> = {}) => ({
  data: { banks: [], install: { harnesses: HARNESSES, command: COMMAND, mint_command: MINT, key_file: 'hal0-memory.key' }, console_url: null, ...over },
  refetch: () => {
    m.refetched += 1
  },
})
const client = (name: string, over: Partial<MemoryClient> = {}): MemoryClient => ({
  name,
  scopes: ['agents', 'memory'],
  created_by: 'admin',
  revoked_at: null,
  last_seen_at: null,
  ...over,
})

beforeEach(() => {
  m.agents = agentsAnswer()
  m.installed = { data: { agents: [{ name: 'hermes' }, { name: 'pi' }], count: 2 } }
  m.clients = { data: { items: [] } }
  m.tags = {
    shared: tags('shared', { 'agent:hermes': 880, 'agent:unknown': 321 }),
    'Hal0::Agents': tags('Hal0::Agents', { 'harness:claude-code': 8975, 'agent:pi': 8105 }),
  }
  m.tagsFor = []
  m.refreshed = 0
  m.refetched = 0
  m.copied = []
})
afterEach(cleanup)

const render = (over: Parameters<typeof makeArea>[0] = {}) =>
  mount(createElement(AgentsTab, { area: makeArea({ hash: '#memory/agents', ...over }) })).host
const byId = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const textOf = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
const state = (host: HTMLElement) => byId(host, 'ma-agents')!.dataset.state
const headers = (table: Element) => [...table.querySelectorAll('thead th')].map((th) => th.textContent)

describe('agents from evidence', () => {
  it('lists one row per agent, with its bank, its count and the tag the count is of', () => {
    const host = render()
    expect(state(host)).toBe('ready')
    const table = byId(host, 'ma-agents-table')!
    expect(headers(table)).toEqual(['Agent', 'Reads', 'Writes', 'Where its facts are', 'Status'])
    expect([...table.querySelectorAll('tbody tr')].map((tr) => tr.getAttribute('data-testid'))).toEqual([
      'ma-agents-row-hermes',
      'ma-agents-row-pi',
      'ma-agents-row-claude-code',
    ])
    expect(textOf(byId(host, 'ma-agents-row-hermes'))).toContain('shared: 880 facts tagged agent:hermes')
    expect(textOf(byId(host, 'ma-agents-row-claude-code'))).toContain(
      'Hal0::Agents: 8,975 facts tagged harness:claude-code',
    )
    expect(textOf(byId(host, 'ma-agents-summary'))).toBe('3 agents · 3 seen writing')
  })

  it('is a real table with header cells, in a named region a keyboard can scroll', () => {
    const host = render()
    const region = byId(host, 'ma-agents-table')!.parentElement!
    expect(region.getAttribute('role')).toBe('region')
    expect(region.getAttribute('aria-label')).toBe('Agents table')
    expect(region.getAttribute('tabindex')).toBe('0')
    for (const th of host.querySelectorAll('thead th')) expect(th.getAttribute('scope')).toBe('col')
    for (const th of host.querySelectorAll('tbody th')) expect(th.getAttribute('scope')).toBe('row')
  })

  it('says what Hermes reads and writes in words, in separate columns', () => {
    const cells = [...byId(render(), 'ma-agents-row-hermes')!.querySelectorAll('td')].map(textOf)
    expect(cells[0]).toMatch(/^Recalls from shared memory and its own private facts/)
    expect(cells[1]).toContain('Conversation turns and checkpoints, private to Hermes.')
    expect(cells[1]).toContain('Facts it chooses to keep, shared unless it marks them private or its profile is set to keep memory private.')
  })

  it('gives each status as a word: seen writing, connected, not seen', () => {
    m.tags = { shared: tags('shared', { 'agent:hermes': 3 }) }
    m.clients = { data: { items: [client('laptop', { last_seen_at: null })] } }
    const host = render()
    expect(textOf(byId(host, 'ma-agents-status-hermes'))).toBe('seen writing')
    expect(textOf(byId(host, 'ma-agents-status-pi'))).toBe('not seen')
    expect(textOf(byId(host, 'ma-agents-status-laptop'))).toBe('connected')
    // "connected" says what it rests on, in the row and in the footer.
    expect(textOf(byId(host, 'ma-agents-row-laptop'))).toContain('Holds a key with the memory scope. Key not used yet.')
    expect(textOf(byId(host, 'ma-agents-card'))).toContain(
      'connected: a key with the memory scope exists for that name; it does not say the key has been used for memory.',
    )
    expect(textOf(byId(host, 'ma-agents-row-pi'))).toContain('No facts tagged agent:pi on any bank.')
  })

  it('shows no agent that is neither installed, nor keyed, nor seen', () => {
    m.installed = { data: { agents: [], count: 0 } }
    m.tags = { shared: tags('shared', { 'agent:hermes': 3 }) }
    const host = render()
    expect(host.querySelectorAll('[data-testid="ma-agents-table"] tbody tr')).toHaveLength(1)
    expect(byId(host, 'ma-agents-row-pi')).toBeNull()
    expect(byId(host, 'ma-agents-row-codex')).toBeNull()
  })

  it("reads the tags of the engine's banks and of the coding-agent banks", () => {
    m.agents = agentsAnswer({ banks: ['coding-agent::elsewhere'] })
    render()
    expect(m.tagsFor.at(-1)).toEqual(['shared', 'Hal0::Agents', 'coding-agent::ultrareview-1.4', 'coding-agent::elsewhere'])
  })

  it('lists the coding-agent banks, and says when none of their facts names its writer', () => {
    m.agents = agentsAnswer({ banks: ['coding-agent::ultrareview-1.4'] })
    m.tags['coding-agent::ultrareview-1.4'] = tags('coding-agent::ultrareview-1.4', { 'project:ultrareview': 12 })
    const host = render()
    const banks = byId(host, 'ma-agents-coding-banks')!
    expect(textOf(banks)).toContain('The engine holds 1 bank named the way the Hindsight plugin names a repository')
    const link = banks.querySelector('a')!
    expect(link.textContent).toBe('coding-agent::ultrareview-1.4')
    expect(link.getAttribute('href')).toBe('#memory/facts?bank=coding-agent::ultrareview-1.4')
    expect(textOf(byId(host, 'ma-agents-unattributed'))).toMatch(/^That bank holds no fact tagged with an agent's name/)
  })

  it('names the banks whose tags did not load, and counts nothing for them', () => {
    m.tags['Hal0::Agents'] = { bankId: 'Hal0::Agents', items: null, isError: true }
    const host = render()
    expect(textOf(byId(host, 'ma-agents-partial'))).toContain('The tags of 1 bank did not load')
    expect(textOf(byId(host, 'ma-agents-partial'))).toContain('Hal0::Agents')
    click(byId(host, 'ma-agents-partial')!.querySelector('button')!)
    expect(m.refreshed).toBe(1)
  })
})

describe('built into hal0', () => {
  it('has the dashboard, brain chat and Open WebUI, with Reads and Writes and no status chip', () => {
    const card = byId(render(), 'ma-agents-builtin')!
    expect(headers(card)).toEqual(['Part', 'Reads', 'Writes', 'Facts carrying its tag'])
    expect([...card.querySelectorAll('tbody th')].map((th) => th.firstChild?.textContent)).toEqual([
      'Dashboard',
      'Brain chat',
      'Open WebUI',
    ])
    expect(card.querySelector('.ma-chip')).toBeNull()
    expect(textOf(byId(card as HTMLElement, 'ma-agents-builtin-openwebui'))).toContain('The context source does not write.')
    expect(textOf(byId(card as HTMLElement, 'ma-agents-builtin-brain'))).toContain('It does not keep its conversations.')
  })

  it("counts the facts tagged with brain chat's name", () => {
    m.tags.shared = tags('shared', { 'agent:hermes__hal0-brain': 4 })
    const host = render()
    expect(textOf(byId(host, 'ma-agents-builtin-brain-count'))).toBe('shared: 4 facts tagged agent:hermes__hal0-brain')
    expect(textOf(byId(host, 'ma-agents-builtin-openwebui-count'))).toBe('No facts tagged agent:openwebui on any bank.')
    expect(byId(host, 'ma-agents-row-hermes__hal0-brain')).toBeNull()
  })
})

describe('facts with no known writer', () => {
  it('says how many, on which bank, and what the tag means', () => {
    const card = byId(render(), 'ma-agents-unknown')!
    expect(textOf(card.querySelector('dt'))).toBe('Tagged agent:unknown')
    expect(textOf(card.querySelector('.ma-stat-value'))).toBe('321facts')
    expect(textOf(card.querySelector('.ma-stat-helper'))).toBe('in the bank shared')
    expect(textOf(card)).toContain('by a caller that sent no agent name')
    expect(byId(card, 'ma-agents-unknown-facts')!.getAttribute('href')).toBe('#memory/facts?bank=shared')
  })

  it('lists the banks when there is more than one', () => {
    m.tags['Hal0::Agents'] = tags('Hal0::Agents', { 'agent:unknown': 9 })
    const card = byId(render(), 'ma-agents-unknown')!
    expect(textOf(card.querySelector('.ma-stat-value'))).toBe('330facts')
    expect(textOf(card.querySelector('.ma-stat-helper'))).toBe('on 2 banks')
    expect([...card.querySelectorAll('[data-testid="ma-agents-unknown-bank"]')].map(textOf)).toEqual([
      'shared: 321 facts',
      'Hal0::Agents: 9 facts',
    ])
  })

  it('is absent when no fact carries the tag', () => {
    m.tags.shared = tags('shared', { 'agent:hermes': 880 })
    const host = render()
    expect(byId(host, 'ma-agents-unknown')).toBeNull()
    expect(textOf(host)).not.toContain('no known writer')
  })
})

describe('connect a coding agent', () => {
  const combobox = (host: HTMLElement) => host.querySelector<HTMLButtonElement>('[role="combobox"][aria-label="Coding agent"]')!
  const command = (host: HTMLElement) => byId(host, 'ma-agents-command')!.querySelector('code')!.textContent

  it('shows the command the route built, for the first agent', () => {
    const host = render()
    expect(command(host)).toBe(COMMAND.replace('<harness>', 'claude-code'))
    expect(textOf(combobox(host))).toContain('Claude Code')
  })

  it('changes the command when another agent is picked', () => {
    const host = render()
    click(combobox(host))
    const options = [...host.querySelectorAll<HTMLElement>('[role="option"]')]
    expect(options).toHaveLength(HARNESSES.length)
    click(options[1])
    expect(command(host)).toBe(COMMAND.replace('<harness>', 'codex'))
    expect(textOf(byId(host, 'ma-agents-mint-command'))).toContain('hal0 bridge add codex --scope memory')
    expect(textOf(byId(host, 'ma-agents-connect-cli'))).toContain('agent:codex')
    expect(textOf(byId(host, 'ma-agents-connect-what'))).toContain('installs the Hindsight memory plugin for Codex')
  })

  it('copies the command, and says so only afterwards', async () => {
    const host = render()
    const box = byId(host, 'ma-agents-command')!
    const status = box.querySelector('[role="status"]')!
    expect(status.textContent).toBe('')
    const button = box.querySelector('button')!
    expect(button.getAttribute('aria-label')).toBe('Copy the command for Claude Code')
    click(button)
    await settle()
    expect(m.copied).toEqual([COMMAND.replace('<harness>', 'claude-code')])
    expect(status.textContent).toBe('Copied')
  })

  it('says what the command does, where it points, and which bank it writes', () => {
    const what = textOf(byId(render(), 'ma-agents-connect-what'))
    expect(what).toContain('installs the Hindsight memory plugin for Claude Code')
    expect(what).toContain("hal0's /api/memory/compat")
    expect(what).toContain('one bank per repository, named coding-agent::<repository>')
    expect(what).not.toContain('directly')
  })

  it('shows the key command, the key file and the install command, never the engine address', () => {
    const host = render()
    expect(textOf(byId(host, 'ma-agents-mint-command'))).toContain('hal0 bridge add claude-code --scope memory')
    expect(textOf(byId(host, 'ma-agents-connect-save'))).toContain('hal0-memory.key')
    expect(textOf(byId(host, 'ma-agents-command'))).toContain(
      'hal0 memory agents install claude-code --bridge-key-file hal0-memory.key',
    )
    expect(textOf(host)).not.toContain('--api-url')
  })

  it('has no primary button anywhere on the tab', () => {
    const host = render()
    for (const button of host.querySelectorAll('button.btn')) expect(button.className).toContain('ghost')
  })
})

describe('states', () => {
  const noFigure = (host: HTMLElement) => expect(textOf(host)).not.toMatch(/\b0 (facts|agents|banks)\b/)

  it('shows skeletons, and no zero, while the engine check is out', () => {
    const host = render({ engineQuery: {} })
    expect(state(host)).toBe('loading')
    expect(byId(host, 'ma-agents-loading')!.getAttribute('aria-busy')).toBe('true')
    expect(byId(host, 'ma-agents-table')).toBeNull()
    expect(byId(host, 'ma-agents-builtin-brain-count')!.querySelector('.ma-skel')).not.toBeNull()
    expect(textOf(host)).not.toContain('not seen')
    noFigure(host)
  })

  it.each([
    ['a bank’s tags', () => (m.tags.shared = { bankId: 'shared', items: null, isLoading: true })],
    ['the installed agents', () => (m.installed = {})],
    ['the key holders', () => (m.clients = {})],
    ['the agents route', () => (m.agents = {})],
  ])('stays loading until %s have answered', (_what, arrange) => {
    arrange()
    const host = render()
    expect(state(host)).toBe('loading')
    expect(byId(host, 'ma-agents-table')).toBeNull()
    expect(textOf(host)).not.toContain('not seen')
    noFigure(host)
  })

  it('says what will appear when no agent has been seen, and points at the connect card', () => {
    m.installed = { data: { agents: [], count: 0 } }
    m.tags = {}
    const host = render()
    expect(state(host)).toBe('empty')
    const empty = byId(host, 'ma-agents-empty')!
    expect(empty.querySelector('h3')!.textContent).toBe('No agents seen yet')
    expect(textOf(empty)).toContain('once a bank holds a fact tagged with its name')
    expect(empty.getAttribute('role')).toBeNull()
    const heading = byId(host, 'ma-agents-connect')!.querySelector('h2')!
    click(byId(host, 'ma-agents-to-connect')!)
    expect(document.activeElement).toBe(heading)
    // The connect card and the built-in card are still there.
    expect(byId(host, 'ma-agents-command')).not.toBeNull()
    expect(byId(host, 'ma-agents-builtin')).not.toBeNull()
    expect(byId(host, 'ma-agents-summary')).toBeNull()
  })

  it('lists no agent, and none as not seen, while the engine does not answer', () => {
    const host = render({ engineQuery: { isError: true } as FakeQuery<never> })
    expect(state(host)).toBe('unreachable')
    expect(byId(host, 'ma-agents-unreachable')!.querySelector('h3')!.textContent).toBe('The memory engine is not answering')
    expect(textOf(byId(host, 'ma-agents-card'))).toContain('unreachable')
    expect(byId(host, 'ma-agents-table')).toBeNull()
    expect(m.tagsFor.at(-1)).toEqual([])
    expect(byId(host, 'ma-agents-builtin-brain-count')!.querySelector('[aria-label="not reported"]')).not.toBeNull()
    // The command comes from hal0, not the engine, so it is still offered.
    expect(byId(host, 'ma-agents-command')).not.toBeNull()
    noFigure(host)
  })

  it('offers "Check again" when the bank list failed', () => {
    let asked = 0
    const host = render({ banksQuery: { isError: true, refetch: () => (asked += 1) } })
    expect(state(host)).toBe('banks-error')
    click(byId(host, 'ma-agents-recheck')!)
    expect(asked).toBe(1)
    expect(byId(host, 'ma-agents-table')).toBeNull()
  })

  it('offers "Check again" when no bank’s tags could be read', () => {
    for (const id of ['shared', 'Hal0::Agents', 'coding-agent::ultrareview-1.4']) {
      m.tags[id] = { bankId: id, items: null, isError: true }
    }
    const host = render()
    expect(state(host)).toBe('tags-error')
    expect(byId(host, 'ma-agents-tags-error')).not.toBeNull()
    // No row, so no agent carries a status at all.
    expect(host.querySelector('.ma-chip')).toBeNull()
    click(byId(host, 'ma-agents-recheck')!)
    expect(m.refreshed).toBe(1)
  })

  it('says the command did not load when the agents route fails, with "Check again" and no raw error', () => {
    m.agents = {
      isError: true,
      error: { message: '{"detail":"boom"}' },
      refetch: () => {
        m.refetched += 1
      },
    }
    const host = render()
    // The agents are still listed: they are read from the tags, not from this route.
    expect(state(host)).toBe('ready')
    expect(byId(host, 'ma-agents-row-hermes')).not.toBeNull()
    const error = byId(host, 'ma-agents-connect-error')!
    expect(error.querySelector('h3')!.textContent).toBe('The install command did not load')
    expect(textOf(host)).not.toMatch(/[{}]|boom|detail/)
    expect(byId(host, 'ma-agents-command')).toBeNull()
    expect(byId(host, 'ma-agents-coding-banks-error')).not.toBeNull()
    click(byId(host, 'ma-agents-connect-recheck')!)
    expect(m.refetched).toBe(1)
  })

  it('treats a body of another shape from the agents route as a failure, not a crash', () => {
    m.agents = { data: {} }
    const host = render()
    expect(byId(host, 'ma-agents-connect-error')).not.toBeNull()
  })

  it('says memory is not on Hindsight when it is not', () => {
    const host = render({
      engineQuery: { data: { enabled: true, engine: null, reachable: false, version: null, features: null, banks_total: 0 } as never },
    })
    expect(state(host)).toBe('unsupported')
    expect(byId(host, 'ma-agents-fallback')).not.toBeNull()
  })

  it('no longer links to the legacy tools page', () => {
    const host = render()
    for (const a of host.querySelectorAll('a')) expect(a.getAttribute('href')).not.toContain('memworkspace')
  })
})
