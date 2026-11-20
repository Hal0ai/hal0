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
  contextProblem,
  countLabel,
  defaultProviderName,
  filterModels,
  formatContext,
  formatPrice,
  hostOf,
  initialPicks,
  keyEnvFor,
  keyProblem,
  keyState,
  legacyModelsRedirect,
  MODEL_TYPE_CHOICES,
  modelPath,
  modelTypeLabel,
  priceProblem,
  ownerLabel,
  probeErrorMessage,
  providerLabel,
  refreshOutcome,
  refreshSummary,
  providerState,
  rowFlag,
  listingKnown,
  listingNote,
  providerHeading,
  keyMissing,
  sectionRows,
  toggleErrorMessage,
  validateProviderName,
  visibleProviders,
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
  it('still cuts to the page when a search is set; offHidden counts the remaining matches', () => {
    const s = sectionRows(off450, { ...ANY, q: 'model-1' }, OFF_PAGE)
    expect(s.off).toHaveLength(25)
    expect(s.offTotal).toBe(100)
    expect(s.offHidden).toBe(75)
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

describe('listingKnown', () => {
  it('is true only for a fetched list that is not stale', () => {
    expect(listingKnown({ fetched_at: 1_700_000_000, stale: false })).toBe(true)
  })
  it('is false for a list never fetched, a stale list and no listing at all', () => {
    expect(listingKnown({ fetched_at: null, stale: true })).toBe(false)
    expect(listingKnown({ fetched_at: 1_700_000_000, stale: true })).toBe(false)
    expect(listingKnown({ fetched_at: null, stale: false })).toBe(false)
    expect(listingKnown(undefined)).toBe(false)
  })
})

describe('rowFlag', () => {
  it('is gone when the provider answered and no longer lists the model', () => {
    expect(rowFlag(row('x', { discovered: false }), true)).toBe('gone')
    expect(rowFlag(row('x', { discovered: false, enabled: true }), true)).toBe('gone')
  })
  it('is never gone while the model list is unknown', () => {
    expect(rowFlag(row('x', { discovered: false }), false)).toBeNull()
    expect(rowFlag(row('x', { discovered: false, enabled: true }), false)).toBeNull()
    expect(rowFlag(row('x', { discovered: false, recommended: true }), false)).toBe('recommended')
  })
  it('is recommended only for an off row', () => {
    expect(rowFlag(row('x', { recommended: true }), true)).toBe('recommended')
    expect(rowFlag(row('x', { recommended: true, enabled: true }), true)).toBeNull()
  })
  it('is null otherwise', () => {
    expect(rowFlag(row('x'), true)).toBeNull()
  })
})

describe('keyMissing', () => {
  it('is true only when the provider needs a key and none is stored', () => {
    expect(keyMissing(entry({ auth_key_present: false }))).toBe(true)
    expect(keyMissing(entry({ auth_key_present: true }))).toBe(false)
    expect(keyMissing(entry({ auth_key_present: false, auth_style: 'none' }))).toBe(false)
    expect(keyMissing(entry({ auth_key_present: false, managed_by: 'agent:hermes' }))).toBe(false)
  })
})

describe('listingNote', () => {
  const l = (over: Partial<UpstreamListing>): UpstreamListing => ({
    name: 'minimax',
    curated: true,
    protected: false,
    fetched_at: 1_700_000_000,
    stale: false,
    models: [],
    ...over,
  })
  it('says nothing for a fresh list', () => {
    expect(listingNote(entry(), l({}))).toBeNull()
  })
  it('keeps the old note for a previously fetched list that is now stale', () => {
    expect(listingNote(entry(), l({ stale: true }))).toBe('Model list could not be refreshed; showing the last known list.')
  })
  it('says the list is not loaded yet when it was never fetched and a key is there or not needed', () => {
    const text = 'Model list not loaded yet. Refresh model list to load it.'
    expect(listingNote(entry(), l({ fetched_at: null, stale: true }))).toBe(text)
    expect(listingNote(entry({ auth_style: 'none', auth_key_present: false }), l({ fetched_at: null, stale: true }))).toBe(text)
  })
  it('says nothing when the key is missing: the key banner already explains it', () => {
    expect(listingNote(entry({ auth_key_present: false }), l({ fetched_at: null, stale: true }))).toBeNull()
    expect(listingNote(entry({ auth_key_present: false }), l({ stale: true }))).toBeNull()
  })
  it('says nothing without a listing, or for a provider that is not per-model', () => {
    expect(listingNote(entry(), undefined)).toBeNull()
    expect(listingNote(entry({ curated: false }), l({ fetched_at: null, stale: true }))).toBeNull()
    expect(listingNote(entry({ managed_by: 'agent:hermes' }), l({ fetched_at: null }))).toBeNull()
  })
})

describe('providerHeading', () => {
  const preset = { id: 'openrouter', name: 'OpenRouter' } as CatalogEntry
  const catalog = { openrouter: preset }
  it('shows only the display name when the provider carries the preset default name', () => {
    expect(providerHeading(entry({ name: 'openrouter', catalog_id: 'openrouter' }), catalog)).toEqual({
      label: 'OpenRouter',
      name: null,
    })
  })
  it('adds the provider name when it differs from the preset default', () => {
    expect(providerHeading(entry({ name: 'ct150-check', catalog_id: 'openrouter' }), catalog)).toEqual({
      label: 'OpenRouter',
      name: 'ct150-check',
    })
    expect(providerHeading(entry({ name: 'openrouter-2', catalog_id: 'openrouter' }), catalog).name).toBe('openrouter-2')
  })
  it('shows only the name for a provider without a preset, or one the catalog does not know', () => {
    expect(providerHeading(entry({ name: 'corp', catalog_id: null }), catalog)).toEqual({ label: 'corp', name: null })
    expect(providerHeading(entry({ name: 'corp', catalog_id: 'gone' }), catalog)).toEqual({ label: 'corp', name: null })
    expect(providerHeading(entry({ name: 'corp', catalog_id: 'openrouter' }), null).name).toBeNull()
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
  it('says switched off for a provider removed from routing, converted or not', () => {
    expect(countLabel(entry({ enabled: false }))).toBe('switched off')
    expect(countLabel(entry({ enabled: false, curated: false }))).toBe('switched off')
  })
  it('still says read-only for a managed provider that is switched off', () => {
    expect(countLabel(entry({ enabled: false, managed_by: 'agent:hermes' }))).toBe('read-only')
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

describe('keyProblem', () => {
  it('accepts nothing typed, a bare token and one Bearer prefix', () => {
    expect(keyProblem('')).toBeNull()
    expect(keyProblem('   ')).toBeNull()
    expect(keyProblem('sk-abc123')).toBeNull()
    expect(keyProblem('Bearer sk-abc123')).toBeNull()
  })
  it('refuses spaces inside, a bare Bearer and non-ASCII', () => {
    expect(keyProblem('sk abc')).not.toBeNull()
    expect(keyProblem('Bearer')).not.toBeNull()
    expect(keyProblem('Bearer  sk')).not.toBeNull()
    expect(keyProblem('clé')).not.toBeNull()
  })
})

describe('keyEnvFor', () => {
  it('derives the variable from the provider name', () => {
    expect(keyEnvFor('minimax')).toBe('MINIMAX_API_KEY')
    expect(keyEnvFor('minimax-2')).toBe('MINIMAX_2_API_KEY')
  })
  it('prefixes a reserved or digit-leading stem and numbers a variable already in use', () => {
    expect(keyEnvFor('hal0')).toBe('PROVIDER_HAL0_API_KEY')
    expect(keyEnvFor('9lives')).toBe('PROVIDER_9LIVES_API_KEY')
    expect(keyEnvFor('minimax', ['MINIMAX_API_KEY'])).toBe('MINIMAX_2_API_KEY')
  })
})

describe('provider list helpers', () => {
  it('visibleProviders sorts operator providers by name, managed ones last, and hides slots', () => {
    const list = [
      entry({ name: 'zed' }),
      entry({ name: 'a-managed', managed_by: 'agent:hermes' }),
      entry({ name: 'alpha' }),
      entry({ name: 'local', slot_name: 'chat' }),
      entry({ name: 'sl', kind: 'slot' }),
    ]
    expect(visibleProviders(list).map((u) => u.name)).toEqual(['alpha', 'zed', 'a-managed'])
    expect(visibleProviders(undefined)).toEqual([])
  })
  it('providerLabel prefers the preset name', () => {
    expect(providerLabel(entry({ name: 'mm' }), { minimax: { name: 'MiniMax' } as CatalogEntry })).toBe('MiniMax')
    expect(providerLabel(entry({ name: 'mm', catalog_id: null }), {})).toBe('mm')
  })
  it('ownerLabel names the owner in plain words', () => {
    expect(ownerLabel(entry())).toBe('hal0')
    expect(ownerLabel(entry({ managed_by: 'agent:hermes' }))).toBe('the Hermes agent')
    expect(ownerLabel(entry({ managed_by: 'something' }))).toBe('an agent')
  })
  it('hostOf returns the host, or the text when it is not a URL', () => {
    expect(hostOf('https://api.x.io/v1')).toBe('api.x.io')
    expect(hostOf('nope')).toBe('nope')
    expect(hostOf(null)).toBe('')
  })
})

describe('model type labels', () => {
  it('shows speech for tts and transcribe for stt, sending the raw value', () => {
    expect(modelTypeLabel('tts')).toBe('speech')
    expect(modelTypeLabel('stt')).toBe('transcribe')
    expect(modelTypeLabel('chat')).toBe('chat')
    expect(MODEL_TYPE_CHOICES.map((c) => c.id)).toEqual(['chat', 'embed', 'tts', 'stt', 'rerank', 'image'])
  })
})

describe('refreshOutcome', () => {
  const listingOf = (over: Partial<UpstreamListing>) =>
    ({ name: 'p', curated: true, protected: false, fetched_at: 1, stale: false, models: [], ...over }) as UpstreamListing
  it('is clean only when the answer is neither stale nor carries a warning', () => {
    expect(refreshOutcome(listingOf({}))).toBe('clean')
    expect(refreshOutcome(listingOf({ warning: null }))).toBe('clean')
  })
  it('is unreachable when the provider answered stale, with or without a warning', () => {
    expect(refreshOutcome(listingOf({ stale: true }))).toBe('unreachable')
    expect(refreshOutcome(listingOf({ stale: true, warning: 'x' }))).toBe('unreachable')
    expect(refreshOutcome(listingOf({ warning: 'x' }))).toBe('unreachable')
  })
})

describe('refreshSummary', () => {
  it('counts clean answers only and says how many could not be reached', () => {
    expect(refreshSummary(2, 0)).toEqual({ text: 'Refreshed 2 model lists', kind: 'info' })
    expect(refreshSummary(1, 0)).toEqual({ text: 'Refreshed 1 model list', kind: 'info' })
    expect(refreshSummary(1, 2)).toEqual({ text: 'Refreshed 1 model list · 2 could not be reached', kind: 'warn' })
    expect(refreshSummary(0, 1)).toEqual({ text: 'Refreshed 0 model lists · 1 could not be reached', kind: 'warn' })
  })
  it('says there is nothing to refresh when no provider was eligible', () => {
    expect(refreshSummary(0, 0)).toEqual({ text: 'No provider to refresh.', kind: 'info' })
  })
})

describe('toggleErrorMessage for a refused edit', () => {
  it('shows the server line for a 400 or 422 instead of the generic text', () => {
    expect(
      toggleErrorMessage({ status: 400, code: 'upstream.invalid', message: 'invalid model row', details: { errors: ['context: Input should be greater than 0'] } }),
    ).toBe('context: Input should be greater than 0')
    expect(
      toggleErrorMessage({ status: 422, code: 'validation.invalid', message: 'request validation failed', details: { fields: [{ loc: ['body', 'price_in'], msg: 'Input should be greater than or equal to 0', type: 'x' }] } }),
    ).toBe('price_in: Input should be greater than or equal to 0')
  })
  it('falls back to the server message, then to the generic text', () => {
    expect(toggleErrorMessage({ status: 422, message: 'request validation failed', details: {} })).toBe('request validation failed')
    expect(toggleErrorMessage({ status: 500 })).toBe('Could not change the model. Try again.')
  })
})

describe('model field problems', () => {
  it('context must be a whole number above 0, or empty', () => {
    expect(contextProblem('')).toBeNull()
    expect(contextProblem(' 256000 ')).toBeNull()
    expect(contextProblem('0')).not.toBeNull()
    expect(contextProblem('-5')).not.toBeNull()
    expect(contextProblem('1.5')).not.toBeNull()
    expect(contextProblem('12k')).not.toBeNull()
  })
  it('a price must be a number of 0 or more, or empty', () => {
    expect(priceProblem('')).toBeNull()
    expect(priceProblem('0')).toBeNull()
    expect(priceProblem('0.25')).toBeNull()
    expect(priceProblem('-1')).not.toBeNull()
    expect(priceProblem('abc')).not.toBeNull()
    expect(priceProblem('Infinity')).not.toBeNull()
  })
})
