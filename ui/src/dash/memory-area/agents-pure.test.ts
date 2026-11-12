// The Agents tab's rules: which rows the evidence supports, what status each gets, the
// `agent:unknown` summary, and how the install command is read.
import { describe, expect, it } from 'vitest'
import {
  BRAIN_ID,
  OPENWEBUI_ID,
  agentRows,
  builtInSightings,
  commandApiUrl,
  harnessName,
  installCommandFor,
  isLoopbackUrl,
  memoryClients,
  readAgentsRoute,
  rowsSummary,
  sightingsByName,
  tagsState,
  unattributedCodingBanks,
  unknownSummary,
  type BankTagsEntry,
  type MemoryClient,
} from './agents-pure'

const bank = (bankId: string, tags: Record<string, number>): BankTagsEntry => ({
  bankId,
  items: Object.entries(tags).map(([tag, count]) => ({ tag, count })),
})

const client = (name: string, over: Partial<MemoryClient> = {}): MemoryClient => ({
  name,
  scopes: ['agents', 'memory'],
  created_by: 'admin',
  revoked_at: null,
  last_seen_at: null,
  ...over,
})

const HARNESSES = ['claude-code', 'codex', 'pi']

const TAGS: BankTagsEntry[] = [
  bank('shared', { 'agent:hermes': 880, 'agent:unknown': 321, 'kind:durable': 500, [`agent:${BRAIN_ID}`]: 4 }),
  bank('Hal0::Agents', { 'harness:claude-code': 8975, 'agent:pi': 8105, 'agent:hermes': 12 }),
  bank('coding-agent::hal0', { 'project:hal0': 40 }),
]

describe('sightingsByName', () => {
  it('reads agent: and harness: tags, per bank, and nothing else', () => {
    const seen = sightingsByName(TAGS)
    expect([...seen.keys()].sort()).toEqual(['claude-code', 'hermes', BRAIN_ID, 'pi', 'unknown'].sort())
    expect(seen.get('hermes')).toEqual([
      { tag: 'agent:hermes', bank: 'shared', count: 880 },
      { tag: 'agent:hermes', bank: 'Hal0::Agents', count: 12 },
    ])
    expect(seen.get('claude-code')).toEqual([{ tag: 'harness:claude-code', bank: 'Hal0::Agents', count: 8975 }])
  })

  it('ignores a tag with no facts, a bare prefix and a bank that did not answer', () => {
    const seen = sightingsByName([
      bank('a', { 'agent:ghost': 0, 'agent:': 9, 'harness:': 3 }),
      { bankId: 'b', items: null, isError: true },
    ])
    expect(seen.size).toBe(0)
  })
})

describe('agentRows', () => {
  it('lists an agent for each name with tagged facts, and no row for agent:unknown or hal0 itself', () => {
    const rows = agentRows({ tags: TAGS, harnesses: HARNESSES })
    expect(rows.map((r) => r.id)).toEqual(['hermes', 'pi', 'claude-code'])
    expect(rows.every((r) => r.status === 'seen writing')).toBe(true)
  })

  it('names the kinds it knows, and keeps the id when the name differs', () => {
    const rows = agentRows({
      tags: [bank('shared', { 'agent:hermes__research': 3, 'agent:mystery': 1, 'agent:claude-code': 2 })],
      harnesses: HARNESSES,
    })
    expect(rows.map((r) => [r.id, r.kind, r.name, r.idNote])).toEqual([
      ['hermes__research', 'hermes-profile', 'Hermes profile research', 'hermes__research'],
      ['claude-code', 'coding', 'Claude Code', 'claude-code'],
      ['mystery', 'tag', 'mystery', null],
    ])
  })

  it('treats a name seen on a coding-agent bank as a coding agent', () => {
    const rows = agentRows({ tags: [bank('coding-agent::site', { 'agent:aider': 5 })], harnesses: [] })
    expect(rows[0]).toMatchObject({ id: 'aider', kind: 'coding', name: 'aider' })
  })

  it('lists Hermes and Pi when installed, as not seen until they have tagged facts', () => {
    const rows = agentRows({ tags: [bank('shared', {})], installed: [{ name: 'hermes' }, { name: 'pi' }] })
    expect(rows.map((r) => [r.id, r.status])).toEqual([
      ['hermes', 'not seen'],
      ['pi', 'not seen'],
    ])
  })

  it('lists nothing hal0 does not ship just because it is installed', () => {
    expect(agentRows({ tags: [], installed: [{ name: 'other' }] })).toEqual([])
  })

  it('invents no agent: with no evidence there are no rows', () => {
    expect(agentRows({ tags: [], installed: [], clients: [], harnesses: HARNESSES })).toEqual([])
    expect(agentRows({ tags: [bank('shared', { 'kind:durable': 9 })] })).toEqual([])
  })

  it('says connected only for a live key with the memory scope', () => {
    const rows = agentRows({
      tags: [],
      clients: [
        client('laptop'),
        client('no-memory', { scopes: ['agents'] }),
        client('revoked', { revoked_at: 1700000000 }),
      ],
    })
    expect(rows.map((r) => [r.id, r.kind, r.status])).toEqual([['laptop', 'client', 'connected']])
    expect(rows[0].client?.name).toBe('laptop')
  })

  it('says seen writing, not connected, once the client has tagged facts', () => {
    const rows = agentRows({ tags: [bank('shared', { 'agent:laptop': 7 })], clients: [client('laptop')] })
    expect(rows[0]).toMatchObject({ id: 'laptop', kind: 'client', status: 'seen writing' })
    expect(rows[0].client).not.toBeNull()
  })

  it('keeps the key hal0 holds for Open WebUI, and brain chat, out of the agents', () => {
    const rows = agentRows({
      tags: [bank('shared', { [`agent:${OPENWEBUI_ID}`]: 2, [`agent:${BRAIN_ID}`]: 4 })],
      clients: [client(OPENWEBUI_ID, { created_by: 'system:openwebui' }), client('sys', { created_by: 'system:x' })],
    })
    expect(rows).toEqual([])
  })

  it('orders Hermes, its profiles, Pi, coding agents, clients, then other tags', () => {
    const rows = agentRows({
      tags: [bank('shared', { 'agent:zed': 1, 'agent:codex': 1, 'agent:pi': 1, 'agent:hermes__b': 1, 'agent:hermes': 1 })],
      clients: [client('laptop')],
      harnesses: HARNESSES,
    })
    expect(rows.map((r) => r.id)).toEqual(['hermes', 'hermes__b', 'pi', 'codex', 'laptop', 'zed'])
  })

  it('tolerates answers of another shape', () => {
    const rows = agentRows({
      tags: [{ bankId: 'x', items: null }],
      installed: null,
      clients: {} as unknown as MemoryClient[],
      harnesses: null,
    })
    expect(rows).toEqual([])
  })
})

describe('rowsSummary', () => {
  it('counts the rows, and each status that occurs', () => {
    const rows = agentRows({
      tags: [bank('shared', { 'agent:hermes': 880 })],
      installed: [{ name: 'hermes' }, { name: 'pi' }],
      clients: [client('laptop')],
    })
    expect(rowsSummary(rows)).toBe('3 agents · 1 seen writing · 1 connected · 1 not seen')
    expect(rowsSummary(rows.slice(0, 1))).toBe('1 agent · 1 seen writing')
  })
})

describe('unknownSummary', () => {
  it('is null when no bank holds a fact tagged agent:unknown', () => {
    expect(unknownSummary([bank('shared', { 'agent:hermes': 3, 'agent:unknown': 0 })])).toBeNull()
    expect(unknownSummary([])).toBeNull()
  })

  it('adds the banks up, largest first', () => {
    const summary = unknownSummary([
      bank('a', { 'agent:unknown': 4 }),
      bank('shared', { 'agent:unknown': 321 }),
      bank('c', { 'agent:hermes': 9 }),
    ])
    expect(summary).toEqual({
      total: 325,
      banks: [
        { bank: 'shared', count: 321 },
        { bank: 'a', count: 4 },
      ],
    })
  })
})

describe('builtInSightings', () => {
  it("is the tagged facts of one of hal0's own parts", () => {
    expect(builtInSightings(TAGS, BRAIN_ID)).toEqual([{ tag: `agent:${BRAIN_ID}`, bank: 'shared', count: 4 }])
    expect(builtInSightings(TAGS, OPENWEBUI_ID)).toEqual([])
  })
})

describe('tagsState', () => {
  it('is loading while any bank is, and names the banks that failed', () => {
    const state = tagsState([
      bank('a', {}),
      { bankId: 'b', items: null, isLoading: true },
      { bankId: 'c', items: null, isError: true },
    ])
    expect(state).toEqual({ loading: true, failed: ['c'], allFailed: false })
  })

  it('says so when every bank failed, and not when there is no bank', () => {
    expect(tagsState([{ bankId: 'a', items: null, isError: true }]).allFailed).toBe(true)
    expect(tagsState([]).allFailed).toBe(false)
  })
})

describe('unattributedCodingBanks', () => {
  it('is the coding-agent banks with no fact naming its writer', () => {
    const tags = [
      bank('coding-agent::hal0', { 'project:hal0': 40, 'agent:unknown': 2 }),
      bank('coding-agent::site', { 'agent:claude-code': 3 }),
      { bankId: 'coding-agent::down', items: null, isError: true },
    ]
    expect(
      unattributedCodingBanks(['coding-agent::hal0', 'coding-agent::site', 'coding-agent::down', 'coding-agent::gone'], tags),
    ).toEqual(['coding-agent::hal0'])
    expect(unattributedCodingBanks(null, tags)).toEqual([])
  })
})

describe('memoryClients', () => {
  it('drops a body that is not a list', () => {
    expect(memoryClients(undefined)).toEqual([])
    expect(memoryClients({} as unknown as MemoryClient[])).toEqual([])
  })
})

describe('the install command', () => {
  const template =
    'npx @vectorize-io/hindsight-coding-agents@0.8.5 install <harness> --server self-hosted --api-url http://127.0.0.1:9177'

  it('puts the agent in place of the slot, and changes nothing else', () => {
    expect(installCommandFor(template, 'codex')).toBe(
      'npx @vectorize-io/hindsight-coding-agents@0.8.5 install codex --server self-hosted --api-url http://127.0.0.1:9177',
    )
    expect(installCommandFor('', 'codex')).toBe('')
    expect(installCommandFor(template, '')).toBe('')
  })

  it('reads the address the command points at', () => {
    expect(commandApiUrl(template)).toBe('http://127.0.0.1:9177')
    expect(commandApiUrl('npx thing install codex')).toBeNull()
    expect(commandApiUrl(null)).toBeNull()
  })

  it.each([
    ['http://127.0.0.1:9177', true],
    ['http://localhost:9177', true],
    ['http://[::1]:9177', true],
    ['http://127.8.9.1:9177', true],
    ['http://192.0.2.10:9177', false],
    ['https://memory.example.test', false],
    ['http://127.example.test:9177', false],
    ['not a url', false],
    [null, false],
  ])('knows whether %s reaches only the box itself', (url, expected) => {
    expect(isLoopbackUrl(url)).toBe(expected)
  })

  it('names the coding agents it knows, and shows the id of one it does not', () => {
    expect(harnessName('claude-code')).toBe('Claude Code')
    expect(harnessName('new-thing')).toBe('new-thing')
  })
})

describe('readAgentsRoute', () => {
  const body = { banks: ['coding-agent::hal0', 7], install: { harnesses: ['codex', '', 3], command: 'npx x <harness>' } }

  it('is loading with no answer, and failed after an error', () => {
    expect(readAgentsRoute({})).toEqual({ kind: 'loading' })
    expect(readAgentsRoute(undefined)).toEqual({ kind: 'loading' })
    expect(readAgentsRoute({ isError: true })).toEqual({ kind: 'failed' })
  })

  it('reads the banks, the agents and the command', () => {
    expect(readAgentsRoute({ data: body })).toEqual({
      kind: 'ready',
      banks: ['coding-agent::hal0'],
      harnesses: ['codex'],
      command: 'npx x <harness>',
    })
  })

  it('treats a body of another shape as failed, never as an empty answer', () => {
    expect(readAgentsRoute({ data: {} })).toEqual({ kind: 'failed' })
    expect(readAgentsRoute({ data: { banks: [], install: { harnesses: 'x', command: 'y' } } })).toEqual({ kind: 'failed' })
  })

  it('keeps the last good answer while a refetch fails', () => {
    expect(readAgentsRoute({ data: body, isError: true }).kind).toBe('ready')
  })
})
