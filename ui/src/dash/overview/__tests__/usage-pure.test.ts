import { describe, expect, it } from 'vitest'
import { usageModel } from '../usage-pure'
import type { StatsSummary } from '@/api/hooks/useStatsSummary'

const MODEL = 'tiel-coder-35b-a3b-mtp-ud-q6-k-xl'

const STATS_1H: StatsSummary = {
  window: { from: '2026-10-29T10:00:00Z', to: '2026-10-29T11:00:00Z', bucket: '2.5m' },
  totals: { requests: 6, ok: 6, errors: 0, tokens_completed: 1854 },
  by_model: [
    {
      model_id: MODEL,
      ttft_ms: { p50: 3777, p95: 7434 },
      tps_decode: { avg: 60.34, p50: 59.8 },
      tps_prefill: { avg: 1046.2 },
      spec_accept_rate: 0.5337,
      ok: 6,
      errors: 0,
    },
  ],
  buckets: [
    { ts: 'a', requests: 0, tokens_completed: 0 },
    { ts: 'b', requests: 6, tokens_completed: 1854 },
  ],
}

const STATS_EMPTY: StatsSummary = {
  window: { from: 'a', to: 'b', bucket: '1h' },
  totals: { requests: 0, ok: 0, errors: 0, tokens_completed: 0 },
  by_model: [],
}

const STATS_NO_DRAFT: StatsSummary = {
  ...STATS_1H,
  by_model: [{ ...STATS_1H.by_model[0], spec_accept_rate: null }],
}

describe('usageModel', () => {
  it('formats the CT105 1h snapshot', () => {
    const m = usageModel(STATS_1H, '1h')
    expect(m).toMatchObject({ empty: false, tokens: '1.9k', requests: 6, errors: 0, decode: '60.3', prefill: '1046', ttft: '3.8s', p95: '7.4s', bucketNote: 'tokens per 2.5 min' })
    expect(m.rows[0]).toMatchObject({ model: MODEL, req: 6, share: 100, ttft: '3.8s', tps: '59.8', accept: '53%', errors: 0 })
    expect(m.buckets).toEqual([0, 1854])
  })
  it('tolerates a response without buckets', () => {
    expect(usageModel({ ...STATS_1H, buckets: undefined }, '1h').buckets).toEqual([])
  })
  it('marks an empty window', () => {
    expect(usageModel(STATS_EMPTY, '24h')).toMatchObject({ empty: true, tokens: '—', decode: '—' })
  })
  it('dashes accept rate for models without a drafter', () => {
    expect(usageModel(STATS_NO_DRAFT, '24h').rows[0].accept).toBe('—')
  })
  it('is all dashes while the response is still loading', () => {
    expect(usageModel(undefined, '24h')).toMatchObject({ empty: false, tokens: '—', requests: null, errors: null, rows: [], buckets: [] })
  })
  it('names the bucket width per window', () => {
    expect(usageModel(STATS_1H, '24h').bucketNote).toBe('tokens per hour')
    expect(usageModel(STATS_1H, '7d').bucketNote).toBe('tokens per 8 h')
  })
})

describe('usageModel rows of one model on several devices', () => {
  const two: StatsSummary = {
    ...STATS_1H,
    totals: { ...STATS_1H.totals, requests: 6 },
    by_model: [
      { ...STATS_1H.by_model[0], runner: 'llama', device: 'gpu-vulkan', modality: 'text', ok: 4, errors: 0 },
      { ...STATS_1H.by_model[0], runner: 'llama', device: 'gpu-rocm', modality: 'text', ok: 2, errors: 0 },
    ],
  }

  it('gives every group its own key and names the device when the model repeats', () => {
    const rows = usageModel(two, '1h').rows
    expect(new Set(rows.map((r) => r.key)).size).toBe(2)
    expect(rows.map((r) => r.label).sort()).toEqual([`${MODEL} · gpu-rocm`, `${MODEL} · gpu-vulkan`])
  })

  it('leaves the plain model id when it appears once', () => {
    expect(usageModel({ ...two, by_model: [two.by_model[0]] }, '1h').rows[0].label).toBe(MODEL)
  })
})
