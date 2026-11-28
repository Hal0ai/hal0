import { describe, expect, it } from 'vitest'
import {
  emptyFactsLabel,
  facetOptions,
  formatContext,
  galleryCards,
  matchesFilters,
  profileToml,
  toggleFilter,
  tuneFacts,
  tuneOf,
} from '../profile-gallery.js'

// GET /api/profiles returns every field; these are the unset values.
const FULL = {
  flags: '', ctx_size: null, enable_thinking: null, reasoning_effort: 'default',
  reasoning_budget: -1, vision: 'auto', speculation: 'auto', runner: null,
  cloned_from: null, intent: '',
}

describe('formatContext', () => {
  it('shortens exact multiples of 1024 from 16K up, and nothing else', () => {
    expect(formatContext(65536)).toBe('64K')
    expect(formatContext(262144)).toBe('256K')
    expect(formatContext(16384)).toBe('16K')
    expect(formatContext(4096)).toBe('4096')
    expect(formatContext(8192)).toBe('8192')
    expect(formatContext(250000)).toBe('250000')
    expect(formatContext(131000)).toBe('131000')
  })
})

describe('tuneOf / tuneFacts', () => {
  it('a profile at its defaults sets nothing', () => {
    expect(tuneOf({ name: 'plain', ...FULL })).toEqual({})
    expect(tuneFacts({ name: 'plain', ...FULL })).toEqual([])
    expect(emptyFactsLabel({ ...FULL })).toBe('engine defaults')
  })

  it('a profile that only carries flags says so', () => {
    expect(tuneFacts({ ...FULL, flags: '-fa on' })).toEqual([])
    expect(emptyFactsLabel({ ...FULL, flags: '-fa on' })).toBe('flags only')
  })

  it('lists the typed fields a profile sets, in a fixed order', () => {
    const facts = tuneFacts(
      { ...FULL, speculation: 'off', runner: 'standard', vision: 'off', enable_thinking: false,
        ctx_size: 65536, reasoning_effort: 'high', reasoning_budget: 2048, cloned_from: 'agent' },
      (key: string) => (key === 'standard' ? 'Standard' : key),
    )
    expect(facts.map((f: { label: string; value: string }) => `${f.label} ${f.value}`)).toEqual([
      'context 64K', 'thinking off', 'effort high', 'reasoning budget 2048',
      'vision off', 'speculation off', 'runner Standard', 'cloned from agent',
    ])
  })

  it('reads a sparse catalog body the same way as a full local profile', () => {
    const sparse = { flags: '-fa auto', ctx_size: 32768, enable_thinking: true, vision: 'off' }
    expect(tuneFacts(sparse)).toEqual(tuneFacts({ ...FULL, ...sparse }))
  })

  it('a zero reasoning budget is a setting, not an absence', () => {
    expect(tuneFacts({ ...FULL, reasoning_budget: 0 })).toEqual([
      { key: 'reasoning_budget', label: 'reasoning budget', value: '0' },
    ])
  })
})

describe('profileToml', () => {
  it('writes the profile as the table hal0 reads', () => {
    expect(
      profileToml('thinking', {
        ...FULL, flags: '-fa auto --temp 1.0', ctx_size: 32768, enable_thinking: true,
        vision: 'off', intent: 'Reasoning "on"',
      }),
    ).toBe(
      [
        '[profile.thinking]',
        'flags = "-fa auto --temp 1.0"',
        'ctx_size = 32768',
        'enable_thinking = true',
        'vision = "off"',
        'intent = "Reasoning \\"on\\""',
      ].join('\n'),
    )
  })
})

describe('galleryCards and filters', () => {
  const profiles = [
    { name: 'my-tune', seed: false, runtime_family: 'llama-server' },
    { name: 'kokoro', seed: true, runtime_family: 'kokoro' },
    { name: 'agent', seed: true, runtime_family: 'llama-server' },
    { name: 'broken', quarantined: true, error: 'x' },
  ]
  const addons = [
    { name: 'dense', runtime_family: 'llama-server', profile: { ctx_size: 131072 } },
    { name: 'gone', runtime_family: null, profile: null, blocked: { code: 'x', message: 'y' } },
  ]
  const cards = galleryCards(profiles, addons)

  it('orders built in, then custom, then import, keeps the server order within a kind, and leaves quarantined entries out', () => {
    expect(cards.map((c: { kind: string; name: string }) => `${c.kind}:${c.name}`)).toEqual([
      'built in:kokoro', 'built in:agent', 'custom:my-tune', 'import:dense', 'import:gone',
    ])
  })

  it('a local profile and a catalog row of the same name are two cards', () => {
    const both = galleryCards([{ name: 'dense', seed: false }], [{ name: 'dense' }])
    expect(new Set(both.map((c: { id: string }) => c.id)).size).toBe(2)
  })

  it('counts every option over the whole set', () => {
    const [kind, runtime] = facetOptions(cards)
    expect(kind.options).toEqual(['built in', 'custom', 'import'])
    expect(kind.counts.get('built in')).toBe(2)
    expect(kind.counts.get('import')).toBe(2)
    expect(runtime.options).toEqual(['kokoro', 'llama-server'])
    expect(runtime.counts.get('llama-server')).toBe(3)
  })

  it('filters AND across facets and OR within one', () => {
    const names = (sel: Record<string, string[]>) =>
      cards.filter((c: unknown) => matchesFilters(c, sel)).map((c: { name: string }) => c.name)
    expect(names({})).toHaveLength(5)
    expect(names({ kind: ['built in'] })).toEqual(['kokoro', 'agent'])
    expect(names({ kind: ['built in', 'custom'] })).toEqual(['kokoro', 'agent', 'my-tune'])
    expect(names({ kind: ['built in'], runtime: ['kokoro'] })).toEqual(['kokoro'])
    expect(names({ kind: ['import'], runtime: ['kokoro'] })).toEqual([])
  })

  it('toggleFilter adds, then removes, one value', () => {
    const one = toggleFilter({}, 'kind', 'custom')
    expect(one).toEqual({ kind: ['custom'] })
    expect(toggleFilter(one, 'kind', 'custom')).toEqual({ kind: [] })
  })
})
