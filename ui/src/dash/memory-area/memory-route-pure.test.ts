import { describe, expect, it } from 'vitest'
import {
  DEFAULT_MEMORY_TAB,
  MEMORY_TABS,
  legacyMemoryRedirect,
  memoryTabHref,
  parseMemoryRoute,
  serializeMemoryRoute,
  type MemoryRoute,
} from './memory-route-pure'

const NOTHING = { flow: null, bank: null, project: null, fact: null, page: null }

// Real bank ids: a double colon, a single colon, a dot, mixed case.
const BANK_IDS = [
  'coding-agent::hal0-mono',
  'coding-agent::ultrareview-1.4',
  'Hal0::Agents',
  'hal0:shared',
  'claude::hal0.web',
  'shared',
]

describe('parseMemoryRoute', () => {
  it.each(MEMORY_TABS)('reads the %s tab', (tab) => {
    expect(parseMemoryRoute(`#memory/${tab}`)).toEqual({ tab, ...NOTHING })
  })

  it('opens Health for an address with no tab', () => {
    expect(DEFAULT_MEMORY_TAB).toBe('health')
    expect(parseMemoryRoute('#memory').tab).toBe('health')
    expect(parseMemoryRoute('#memory/').tab).toBe('health')
    expect(parseMemoryRoute('').tab).toBe('health')
  })

  it.each(['#memory/nope', '#memory/bank', '#memory/Health', '#memory/facts2'])(
    'falls back to Health for the unknown tab in %s',
    (hash) => {
      expect(parseMemoryRoute(hash)).toEqual({ tab: 'health', ...NOTHING })
    },
  )

  it('keeps the query of an address whose tab is unknown', () => {
    expect(parseMemoryRoute('#memory/nope?bank=shared')).toEqual({ tab: 'health', ...NOTHING, bank: 'shared' })
  })

  it('reads the bank, project, fact and page', () => {
    expect(parseMemoryRoute('#memory/facts?bank=shared&project=hal0&fact=f26&page=kb-1')).toEqual({
      tab: 'facts',
      flow: null,
      bank: 'shared',
      project: 'hal0',
      fact: 'f26',
      page: 'kb-1',
    })
  })

  it('reads a flow under a tab', () => {
    expect(parseMemoryRoute('#memory/health/embedder-switch?bank=shared')).toEqual({
      tab: 'health',
      ...NOTHING,
      flow: 'embedder-switch',
      bank: 'shared',
    })
    // A flow belongs to a tab that exists.
    expect(parseMemoryRoute('#memory/nope/embedder-switch').flow).toBeNull()
  })

  it.each(BANK_IDS)('reads the bank id %s exactly, escaped or not', (id) => {
    expect(parseMemoryRoute(`#memory/facts?bank=${id}`).bank).toBe(id)
    expect(parseMemoryRoute(`#memory/facts?bank=${encodeURIComponent(id)}`).bank).toBe(id)
  })

  it('ignores keys it does not own, and empty values', () => {
    expect(parseMemoryRoute('#memory/facts?bank=&x=1&fact=f1&&=2')).toEqual({
      tab: 'facts',
      ...NOTHING,
      fact: 'f1',
    })
  })

  it('keeps a value with a stray percent sign instead of throwing', () => {
    expect(parseMemoryRoute('#memory/facts?bank=50%').bank).toBe('50%')
  })

  it('reads the hash with or without its leading #', () => {
    expect(parseMemoryRoute('memory/operations?bank=shared')).toEqual({
      tab: 'operations',
      ...NOTHING,
      bank: 'shared',
    })
  })

  it('reads nothing from an address outside the Memory area', () => {
    expect(parseMemoryRoute('#logs?bank=shared')).toEqual({ tab: 'health', ...NOTHING })
    expect(parseMemoryRoute('#memworkspace/kb?bank=shared')).toEqual({ tab: 'health', ...NOTHING })
  })
})

describe('serializeMemoryRoute', () => {
  it('writes the tab alone when nothing is chosen', () => {
    expect(serializeMemoryRoute({ tab: 'health' })).toBe('#memory/health')
    expect(serializeMemoryRoute({ tab: 'facts', bank: null, fact: '' })).toBe('#memory/facts')
  })

  it('writes the query in one fixed order', () => {
    expect(
      serializeMemoryRoute({ tab: 'knowledge', page: 'kb-1', fact: 'f1', project: 'hal0', bank: 'shared' }),
    ).toBe('#memory/knowledge?bank=shared&project=hal0&fact=f1&page=kb-1')
  })

  it('leaves the colons and dots of a bank id as they are', () => {
    expect(serializeMemoryRoute({ tab: 'facts', bank: 'coding-agent::ultrareview-1.4' })).toBe(
      '#memory/facts?bank=coding-agent::ultrareview-1.4',
    )
  })

  it('escapes what would break the query', () => {
    expect(serializeMemoryRoute({ tab: 'facts', bank: 'a b&c=d#e?f' })).toBe(
      '#memory/facts?bank=a%20b%26c%3Dd%23e%3Ff',
    )
  })

  it('writes a flow after the tab', () => {
    expect(serializeMemoryRoute({ tab: 'health', flow: 'embedder-switch' })).toBe('#memory/health/embedder-switch')
  })
})

describe('round trip', () => {
  const routes: MemoryRoute[] = [
    { tab: 'health', ...NOTHING },
    { tab: 'facts', ...NOTHING, bank: 'shared', fact: 'f26' },
    { tab: 'knowledge', ...NOTHING, bank: 'Hal0::Agents', page: 'kb-operator-style' },
    { tab: 'operations', ...NOTHING, bank: 'coding-agent::ultrareview-1.4', project: 'hal0-web' },
    { tab: 'health', ...NOTHING, flow: 'fold-in', bank: 'hal0:shared' },
    { tab: 'facts', ...NOTHING, bank: 'a b&c=d#e?f%g', fact: '3f2c::x.y' },
  ]

  it.each(routes)('parse(serialize($tab $bank)) is the same route', (route) => {
    expect(parseMemoryRoute(serializeMemoryRoute(route))).toEqual(route)
  })

  it.each(BANK_IDS)('keeps the bank id %s through write and read, on every tab', (id) => {
    for (const tab of MEMORY_TABS) {
      const hash = serializeMemoryRoute({ tab, bank: id })
      expect(hash).toBe(`#memory/${tab}?bank=${id}`)
      expect(parseMemoryRoute(hash).bank).toBe(id)
    }
  })
})

describe('memoryTabHref', () => {
  it('is the bare tab when nothing is in scope', () => {
    expect(memoryTabHref('operations')).toBe('#memory/operations')
    expect(memoryTabHref('operations', null)).toBe('#memory/operations')
  })

  it('carries the scope to the next tab, and leaves what was open behind', () => {
    const from = parseMemoryRoute('#memory/facts?bank=Hal0::Agents&project=hal0&fact=f26')
    expect(memoryTabHref('knowledge', from)).toBe('#memory/knowledge?bank=Hal0::Agents&project=hal0')
    expect(memoryTabHref('health', from)).toBe('#memory/health?bank=Hal0::Agents&project=hal0')
  })
})

describe('legacyMemoryRedirect', () => {
  it('sends the bare address to Health', () => {
    expect(legacyMemoryRedirect('#memory')).toBe('#memory/health')
    expect(legacyMemoryRedirect('#memory/')).toBe('#memory/health')
    expect(legacyMemoryRedirect('memory')).toBe('#memory/health')
  })

  it('keeps the query of the bare address', () => {
    expect(legacyMemoryRedirect('#memory?bank=shared')).toBe('#memory/health?bank=shared')
  })

  it('sends the bank workspace to Facts, with its query as written', () => {
    expect(legacyMemoryRedirect('#memory/bank')).toBe('#memory/facts')
    expect(legacyMemoryRedirect('#memory/bank?bank=big&fact=f26')).toBe('#memory/facts?bank=big&fact=f26')
    expect(legacyMemoryRedirect('#memory/bank?bank=coding-agent::ultrareview-1.4')).toBe(
      '#memory/facts?bank=coding-agent::ultrareview-1.4',
    )
    // An escaped id is not rewritten on the way through.
    expect(legacyMemoryRedirect('#memory/bank?bank=coding-agent%3A%3Ahal0-mono')).toBe(
      '#memory/facts?bank=coding-agent%3A%3Ahal0-mono',
    )
  })

  it.each([
    ['#agent/memory', '#memory/facts'],
    ['#agent/memory/', '#memory/facts'],
    ['#agent/memory/bank', '#memory/facts'],
    ['#agent/memory/overview', '#memory/facts'],
    ['#agent/memory/bank?bank=big&fact=f26', '#memory/facts?bank=big&fact=f26'],
    ['#agent/memory?bank=Hal0::Agents', '#memory/facts?bank=Hal0::Agents'],
  ])('sends the Agents ▸ Memory address %s to %s', (from, to) => {
    expect(legacyMemoryRedirect(from)).toBe(to)
  })

  it('lands every redirect on an address the area reads back', () => {
    const to = legacyMemoryRedirect('#agent/memory/bank?bank=coding-agent::hal0-mono&fact=f1')!
    expect(parseMemoryRoute(to)).toEqual({
      tab: 'facts',
      ...NOTHING,
      bank: 'coding-agent::hal0-mono',
      fact: 'f1',
    })
  })

  it.each(MEMORY_TABS)('leaves #memory/%s alone', (tab) => {
    expect(legacyMemoryRedirect(`#memory/${tab}`)).toBeNull()
    expect(legacyMemoryRedirect(`#memory/${tab}?bank=shared`)).toBeNull()
  })

  it('leaves an unknown tab alone (the page falls back to Health by itself)', () => {
    expect(legacyMemoryRedirect('#memory/nope')).toBeNull()
  })

  it.each([
    '#memworkspace',
    '#memworkspace/overview',
    '#memworkspace/kb',
    '#memworkspace/mental-models',
    '#memworkspace/directives',
    '#memworkspace/operations',
    '#memworkspace/agents',
    '#memworkspace/models',
  ])('does not redirect %s: that page still exists', (hash) => {
    expect(legacyMemoryRedirect(hash)).toBeNull()
  })

  it.each(['', '#', '#dashboard', '#agent', '#agent/mcp', '#agent/memoryx', '#settings/memory', '#memoryx'])(
    'leaves %j alone',
    (hash) => {
      expect(legacyMemoryRedirect(hash)).toBeNull()
    },
  )
})
