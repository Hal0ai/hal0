import { describe, expect, it } from 'vitest'
import type {
  CatalogEntry,
  ProbeModelRow,
  ProbeResult,
  UpstreamEntry,
  UpstreamListing,
  UpstreamModelRow,
} from '@/api/hooks/useUpstreams'
import {
  OFF_PAGE,
  buildCreateBody,
  countLabel,
  defaultProviderName,
  filterModels,
  formatContext,
  formatPrice,
  initialPicks,
  keyState,
  legacyModelsRedirect,
  modelPath,
  probeErrorMessage,
  providerState,
  rowFlag,
  sectionRows,
  toggleErrorMessage,
  validateProviderName,
  type ModelFilter,
} from './providers-pure'

const entry = (over: Partial<UpstreamEntry> = {}): UpstreamEntry =>
  ({
    name: 'minimax',
    kind: 'remote',
    url: 'https://api.minimax.io/v1',
    auth_style: 'bearer',
    auth_header: '',
    auth_value_env: 'MINIMAX_API_KEY',
    auth_configured: true,
    auth_key_present: true,
    slot_name: null,
    managed_by: null,
    catalog_id: 'minimax',
    curated: true,
    enabled_count: 3,
    models_total: 7,
    ...over,
  }) as UpstreamEntry

const row = (id: string, over: Partial<UpstreamModelRow> = {}): UpstreamModelRow => ({
  id,
  canonical_id: `p/${id}`,
  enabled: false,
  type: 'chat',
  context: null,
  price_in: null,
  price_out: null,
  price_unit: 'Mtok',
  discovered: true,
  recommended: false,
  in_use_by: [],
  ...over,
})

const probeRow = (id: string, over: Partial<ProbeModelRow> = {}): ProbeModelRow => ({
  id,
  canonical_id: null,
  type: 'chat',
  type_source: 'guess',
  context: null,
  price_in: null,
  price_out: null,
  price_unit: 'Mtok',
  recommended: false,
  ...over,
})

const ANY: ModelFilter = { q: '', show: 'all', type: 'any' }

describe('providerState', () => {
  it('is per-model for a curated operator remote', () => {
    expect(providerState(entry())).toBe('per-model')
  })
  it('is not-converted for an uncurated operator remote', () => {
    expect(providerState(entry({ curated: false }))).toBe('not-converted')
  })
  it('is managed for an agent-managed entry', () => {
    expect(providerState(entry({ managed_by: 'agent:hermes' }))).toBe('managed')
  })
  it('is managed for a slot-kind or slot-backed entry', () => {
    expect(providerState(entry({ kind: 'slot' }))).toBe('managed')
    expect(providerState(entry({ slot_name: 'agent' }))).toBe('managed')
  })
  it('is managed for the reserved hal0 name', () => {
    expect(providerState(entry({ name: 'hal0' }))).toBe('managed')
  })
  it('is managed when the listing says protected', () => {
    const listing = { protected: true } as UpstreamListing
    expect(providerState(entry(), listing)).toBe('managed')
  })
})

describe('keyState', () => {
  it('is set when the key is present', () => {
    expect(keyState(entry())).toBe('set')
  })
  it('is missing when a keyed provider has no key', () => {
    expect(keyState(entry({ auth_key_present: false }))).toBe('missing')
  })
  it('is not-needed for auth_style none', () => {
    expect(keyState(entry({ auth_style: 'none', auth_key_present: false }))).toBe('not-needed')
  })
  it('is managed for a managed provider', () => {
    expect(keyState(entry({ managed_by: 'agent:hermes' }))).toBe('managed')
  })
})

describe('modelPath', () => {
  it('keeps slashes as separators', () => {
    expect(modelPath('qwen/qwen3-coder')).toBe('qwen/qwen3-coder')
  })
  it('percent-encodes everything else per segment', () => {
    expect(modelPath('a b/c?d#e%')).toBe('a%20b/c%3Fd%23e%25')
  })
})

describe('filterModels', () => {
  const rows = [
    row('GPT-5', { enabled: true }),
    row('gpt-5-mini', { enabled: true }),
    row('whisper-1', { type: 'stt' }),
    row('tts-1', { type: 'tts' }),
  ]
  it('matches id as a case-insensitive substring', () => {
    expect(filterModels(rows, { ...ANY, q: 'gPt' }).map((r) => r.id)).toEqual(['GPT-5', 'gpt-5-mini'])
  })
  it('filters on or off', () => {
    expect(filterModels(rows, { ...ANY, show: 'on' })).toHaveLength(2)
    expect(filterModels(rows, { ...ANY, show: 'off' })).toHaveLength(2)
  })
  it('filters by type', () => {
    expect(filterModels(rows, { ...ANY, type: 'stt' }).map((r) => r.id)).toEqual(['whisper-1'])
  })
  it('returns everything for the empty filter', () => {
    expect(filterModels(rows, ANY)).toHaveLength(4)
  })
})

describe('sectionRows', () => {
  const off450 = Array.from({ length: 450 }, (_, i) => row(`vendor/model-${String(i).padStart(3, '0')}`))
  it('exports the page size', () => {
    expect(OFF_PAGE).toBe(25)
  })
  it('cuts 450 off rows to the page and counts what is hidden', () => {
    const s = sectionRows(off450, ANY, OFF_PAGE)
    expect(s.off).toHaveLength(25)
    expect(s.offTotal).toBe(450)
    expect(s.offHidden).toBe(425)
  })
  it('applies no cut when a search is set', () => {
    const s = sectionRows(off450, { ...ANY, q: 'model-1' }, OFF_PAGE)
    expect(s.off).toHaveLength(100)
    expect(s.offHidden).toBe(0)
    expect(s.offTotal).toBe(100)
  })
  it('shows every on row, sorted by id', () => {
    const on = [row('b', { enabled: true }), row('a', { enabled: true }), ...off450]
    const s = sectionRows(on, ANY, 1)
    expect(s.on.map((r) => r.id)).toEqual(['a', 'b'])
  })
  it('puts recommended off rows first, then by id', () => {
    const rows = [row('c'), row('b', { recommended: true }), row('a')]
    expect(sectionRows(rows, ANY, 10).off.map((r) => r.id)).toEqual(['b', 'a', 'c'])
  })
  it('grows the page with offShown', () => {
    expect(sectionRows(off450, ANY, 50).off).toHaveLength(50)
  })
})

describe('rowFlag on a stored row only', () => {
  it('a probe row has no discovered flag, so it is not passed to rowFlag', () => {
    expect('discovered' in probeRow('x')).toBe(false)
  })
})

describe('rowFlag', () => {
  it('is gone when the provider no longer lists the model', () => {
    expect(rowFlag(row('x', { discovered: false }))).toBe('gone')
    expect(rowFlag(row('x', { discovered: false, enabled: true }))).toBe('gone')
  })
  it('is recommended only for an off row', () => {
    expect(rowFlag(row('x', { recommended: true }))).toBe('recommended')
    expect(rowFlag(row('x', { recommended: true, enabled: true }))).toBeNull()
  })
  it('is null otherwise', () => {
    expect(rowFlag(row('x'))).toBeNull()
  })
})

describe('countLabel', () => {
  it('counts on of total for a per-model provider', () => {
    expect(countLabel(entry())).toBe('3 of 7 on')
  })
  it('says everything is routed for a not-converted provider', () => {
    expect(countLabel(entry({ curated: false }))).toBe('routes everything')
  })
  it('says read-only for a managed provider', () => {
    expect(countLabel(entry({ managed_by: 'agent:hermes' }))).toBe('read-only')
  })
})

describe('formatPrice', () => {
  it('shows in and out per Mtok with two decimals', () => {
    expect(formatPrice(row('x', { price_in: 0.3, price_out: 1.2 }))).toBe('0.30 / 1.20')
  })
  it('shows a dash for a missing side', () => {
    expect(formatPrice(row('x', { price_in: 0.07 }))).toBe('0.07 / —')
  })
  it('shows one value and the unit for a non-token unit', () => {
    expect(formatPrice(row('x', { price_in: 100, price_unit: 'Mchar' }))).toBe('100 / Mchar')
    expect(formatPrice(row('x', { price_in: 0.0035, price_unit: 'img' }))).toBe('0.0035 / img')
  })
  it('names the side when a non-token unit has only an output price', () => {
    expect(formatPrice(row('x', { price_out: 5, price_unit: 'Mchar' }))).toBe('out 5 / Mchar')
    expect(formatPrice(row('x', { price_in: 1, price_out: 2, price_unit: 'Mchar' }))).toBe('1 / 2 / Mchar')
  })
  it('formats a probe row too', () => {
    expect(formatPrice(probeRow('x', { price_in: 0.3, price_out: 1.2 }))).toBe('0.30 / 1.20')
    expect(formatContext(probeRow('x', { context: 204000 }).context)).toBe('204k')
  })
  it('is a dash when both are null', () => {
    expect(formatPrice(row('x'))).toBe('—')
  })
})

describe('formatContext', () => {
  it('abbreviates', () => {
    expect(formatContext(204000)).toBe('204k')
    expect(formatContext(1000000)).toBe('1M')
    expect(formatContext(262144)).toBe('262k')
    expect(formatContext(8191)).toBe('8k')
  })
  it('never prints 1000k', () => {
    expect(formatContext(999_499)).toBe('999k')
    expect(formatContext(999_500)).toBe('1M')
    expect(formatContext(999_999)).toBe('1M')
  })
  it('is a dash for null', () => {
    expect(formatContext(null)).toBe('—')
  })
})

describe('defaultProviderName', () => {
  const preset = (id: string) => ({ id }) as CatalogEntry
  it('turns underscores into dashes', () => {
    expect(defaultProviderName(preset('google_ai_studio'), [])).toBe('google-ai-studio')
  })
  it('dedupes with a numeric suffix', () => {
    expect(defaultProviderName(preset('minimax'), ['minimax'])).toBe('minimax-2')
    expect(defaultProviderName(preset('minimax'), ['minimax', 'minimax-2'])).toBe('minimax-3')
  })
  it('is empty for custom', () => {
    expect(defaultProviderName(preset('custom'), [])).toBe('')
  })
})

describe('validateProviderName', () => {
  it('accepts a good name', () => {
    expect(validateProviderName('minimax-2', [])).toBeNull()
  })
  it('refuses empty', () => {
    expect(validateProviderName('', [])).toMatch(/name/i)
  })
  it('refuses a slash and uppercase', () => {
    expect(validateProviderName('a/b', [])).not.toBeNull()
    expect(validateProviderName('Foo', [])).not.toBeNull()
  })
  it('refuses a leading dash and 65 characters', () => {
    expect(validateProviderName('-a', [])).not.toBeNull()
    expect(validateProviderName('a'.repeat(65), [])).not.toBeNull()
    expect(validateProviderName('a'.repeat(64), [])).toBeNull()
  })
  it('reserves hal0', () => {
    expect(validateProviderName('hal0', [])).toMatch(/reserved/i)
  })
  it('refuses a taken name', () => {
    expect(validateProviderName('minimax', ['minimax'])).toMatch(/already/i)
  })
})

describe('buildCreateBody', () => {
  const probed = [
    probeRow('a', { recommended: true, context: 1000, price_in: 1, price_out: 2 }),
    probeRow('b', { type: 'chat' }),
  ]
  const base = {
    name: 'p',
    catalogId: 'minimax',
    url: 'https://x/v1',
    authStyle: 'bearer',
    authHeader: '',
    probed,
    types: { b: 'embed' as const },
  }
  it('carries every probed row with enabled from the picks and the type override', () => {
    const body = buildCreateBody({ ...base, picked: new Set(['a']) })
    expect(body.name).toBe('p')
    expect(body.catalog_id).toBe('minimax')
    expect(body.url).toBe('https://x/v1')
    expect(body.auth_style).toBe('bearer')
    expect(body.models).toEqual([
      { id: 'a', enabled: true, type: 'chat', context: 1000, price_in: 1, price_out: 2, price_unit: 'Mtok' },
      { id: 'b', enabled: false, type: 'embed', context: null, price_in: null, price_out: null, price_unit: 'Mtok' },
    ])
  })
  it('sends an explicit empty models list when nothing was probed', () => {
    const body = buildCreateBody({ ...base, probed: [], picked: new Set() })
    expect(body.models).toEqual([])
  })
  it('omits catalog_id for a custom provider', () => {
    const body = buildCreateBody({ ...base, catalogId: '', picked: new Set() })
    expect('catalog_id' in body).toBe(false)
  })
})

describe('initialPicks', () => {
  it('picks the recommended ids', () => {
    const s = initialPicks([probeRow('a', { recommended: true }), probeRow('b'), probeRow('c', { recommended: true })])
    expect([...s].sort()).toEqual(['a', 'c'])
  })
  it('is empty when none are recommended', () => {
    expect(initialPicks([probeRow('a')]).size).toBe(0)
  })
})

describe('toggleErrorMessage', () => {
  it('explains not curated', () => {
    expect(toggleErrorMessage({ status: 409, code: 'upstream.not_curated' })).toBe(
      'Choose models for this provider first.',
    )
  })
  it('explains protected', () => {
    expect(toggleErrorMessage({ status: 409, code: 'upstream.protected' })).toBe(
      'This provider is managed by an agent and cannot be changed here.',
    )
  })
  it('explains a missing provider', () => {
    expect(toggleErrorMessage({ status: 404 })).toBe('This provider no longer exists. Reload the list.')
  })
  it('falls back for anything else', () => {
    expect(toggleErrorMessage({})).toBe('Could not change the model. Try again.')
    expect(toggleErrorMessage({ status: 500 })).toBe('Could not change the model. Try again.')
  })
})

describe('probeErrorMessage', () => {
  const r = (over: Partial<ProbeResult>): ProbeResult => ({
    ok: false,
    status: null,
    latency_ms: 0,
    models: [],
    rate_limit: {},
    error: null,
    ...over,
  })
  it('names a rejected key', () => {
    const msg = 'The provider rejected the key. Check that it was copied whole and is active.'
    expect(probeErrorMessage(r({ status: 401 }))).toBe(msg)
    expect(probeErrorMessage(r({ status: 403 }))).toBe(msg)
  })
  it('names an unreachable provider', () => {
    expect(probeErrorMessage(r({ status: null }))).toBe('Could not reach the provider. Check the address.')
  })
  it('passes the server error through', () => {
    expect(probeErrorMessage(r({ status: 500, error: 'boom' }))).toBe('boom')
  })
  it('has a last-resort message', () => {
    expect(probeErrorMessage(r({ status: 500 }))).toBe('The provider answered with an error.')
  })
})

describe('legacyModelsRedirect', () => {
  it('forwards the old Models tab', () => {
    expect(legacyModelsRedirect('models', 'upstream', new URLSearchParams())).toBe('models/providers')
  })
  it('forwards the Endpoints upstream focus', () => {
    expect(legacyModelsRedirect('slots', 'endpoints', new URLSearchParams('focus=upstream'))).toBe(
      'models/providers',
    )
  })
  it('leaves everything else alone', () => {
    expect(legacyModelsRedirect('models', 'inference', new URLSearchParams())).toBeNull()
    expect(legacyModelsRedirect('slots', 'endpoints', new URLSearchParams())).toBeNull()
    expect(legacyModelsRedirect('slots', undefined, new URLSearchParams('focus=upstream'))).toBeNull()
  })
})
