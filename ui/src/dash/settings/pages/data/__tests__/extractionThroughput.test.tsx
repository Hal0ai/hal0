// @vitest-environment happy-dom
// #2798 — the Memory page shows what PR #2793 (#2353) added to the API: the
// `warnings` on the PUT /api/memory/graph response, and the cached
// `throughput` estimate on GET /api/memory/graph/status.
//
// Field names and shapes mirror the server: routes/memory.py (status carries
// `throughput`; the PUT response carries `throughput` and, on a too_slow
// verdict only, `warnings: [throughput.message]`) and
// ExtractionThroughput.as_dict() in memory/extraction_env.py.
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { graphSaveOutcome, saveWarnings, throughputNote } from '../extractionThroughput.js'

// primitives.jsx (Banner) is a window-globals dash module: `React` and `Icons`
// resolve off globalThis at import time, so install them before the dynamic
// import — same pattern as persona-modal.test.tsx.
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

const { SaveWarnings, ThroughputNote } = await import('../ExtractionThroughputViews.jsx')

const TOO_SLOW_MESSAGE =
  "extraction slot 'cpu' measured 12.0 prompt tok/s and 3.0 decode tok/s (from probe); one " +
  'extraction (~3500 prompt + up to 4096 completion tokens) projects to ~1658 s, over ' +
  '[memory.graph].llm_timeout_s = 300 s. Retains will be accepted, time out in extraction and ' +
  'land under Retry failed once the retries run out.'

const throughput = (over: Record<string, unknown> = {}) => ({
  slot: 'cpu',
  timeout_s: 300,
  max_tokens: 4096,
  prompt_tps: 12,
  decode_tps: 3,
  source: 'probe',
  probe_timed_out: false,
  measured_at: 1_700_000_000,
  detail: '',
  projected_s: 1658.3,
  prompt_tokens: 3500,
  verdict: 'too_slow',
  message: TOO_SLOW_MESSAGE,
  ...over,
})

const status = (over: Record<string, unknown> = {}) => ({
  enabled: true,
  extraction_slot: 'cpu',
  extraction_model: null,
  target: { kind: 'slot', model: 'hal0/cpu', alias: 'hal0/memory' },
  throughput: throughput(),
  ...over,
})

describe('saveWarnings (PUT /api/memory/graph response)', () => {
  it('returns the warning strings the server sent', () => {
    expect(saveWarnings({ warnings: [TOO_SLOW_MESSAGE] })).toEqual([TOO_SLOW_MESSAGE])
  })

  it('is empty when warnings is absent, empty, or not a list', () => {
    expect(saveWarnings({})).toEqual([])
    expect(saveWarnings({ warnings: [] })).toEqual([])
    expect(saveWarnings({ warnings: null })).toEqual([])
    expect(saveWarnings({ warnings: 'oops' })).toEqual([])
    expect(saveWarnings(undefined)).toEqual([])
  })

  it('drops blank and non-string entries', () => {
    expect(saveWarnings({ warnings: ['', '  ', 7, null, 'real one'] })).toEqual(['real one'])
  })
})

describe('graphSaveOutcome (what the page does with the PUT response)', () => {
  it('keeps the warnings and softens the toast to warn when the slot is too slow', () => {
    const out = graphSaveOutcome({ throughput: throughput(), warnings: [TOO_SLOW_MESSAGE] })
    expect(out.warnings).toEqual([TOO_SLOW_MESSAGE])
    expect(out.toast.kind).toBe('warn')
    expect(out.toast.text).toMatch(/^Memory graph settings saved/)
  })

  it('is the plain ok toast with no warnings when the save is clean', () => {
    expect(graphSaveOutcome({ enabled: true })).toEqual({
      warnings: [],
      toast: { text: 'Memory graph settings saved', kind: 'ok' },
    })
    expect(graphSaveOutcome({ warnings: [] }).toast.kind).toBe('ok')
  })

  it('still reports a failed hindsight-api restart as an error, and keeps the warnings', () => {
    const out = graphSaveOutcome({ propagation: { error: 'unit not found' }, warnings: ['slow'] })
    expect(out.toast).toEqual({ text: 'Saved, but hindsight-api restart failed — unit not found', kind: 'err' })
    expect(out.warnings).toEqual(['slow'])
  })
})

describe('throughputNote (GET /api/memory/graph/status)', () => {
  it('carries the server message and a tone per verdict', () => {
    expect(throughputNote(status())).toEqual({ tone: 'warn', verdict: 'too_slow', text: TOO_SLOW_MESSAGE })
    const ok = throughputNote(status({ throughput: throughput({ verdict: 'ok', message: 'fits' }) }))
    expect(ok).toEqual({ tone: 'ok', verdict: 'ok', text: 'fits' })
    const unknown = throughputNote(status({ throughput: throughput({ verdict: 'unknown', message: 'could not measure' }) }))
    expect(unknown).toEqual({ tone: 'muted', verdict: 'unknown', text: 'could not measure' })
  })

  it('is null before anything has measured the slot', () => {
    expect(throughputNote(status({ throughput: null }))).toBeNull()
    expect(throughputNote(status({ throughput: undefined }))).toBeNull()
    expect(throughputNote(undefined)).toBeNull()
  })

  it('is null for an API that predates the field, or a blank message', () => {
    const { throughput: _omit, ...legacy } = status()
    expect(throughputNote(legacy)).toBeNull()
    expect(throughputNote(status({ throughput: throughput({ message: '' }) }))).toBeNull()
  })

  it('is null while the engine runs on an upstream model, not the slot', () => {
    expect(throughputNote(status({ target: { kind: 'upstream', model: 'MiniMax-M2.5', alias: 'hal0/memory' } }))).toBeNull()
    expect(throughputNote(status({ extraction_model: 'MiniMax-M2.5', target: undefined }))).toBeNull()
  })
})

describe('<SaveWarnings> after a save', () => {
  it('renders each warning as a warn banner', () => {
    const html = renderToStaticMarkup(<SaveWarnings warnings={[TOO_SLOW_MESSAGE]} />)
    expect(html).toContain('data-testid="mem-graph-warnings"')
    expect(html).toContain('banner banner-warn')
    expect(html).toContain("extraction slot &#x27;cpu&#x27; measured 12.0 prompt tok/s")
    expect(html).toContain('Retry failed')
  })

  it('renders every warning when there are several', () => {
    const html = renderToStaticMarkup(<SaveWarnings warnings={['first warning', 'second warning']} />)
    expect(html).toContain('first warning')
    expect(html).toContain('second warning')
    expect(html.match(/banner banner-warn/g)).toHaveLength(2)
  })

  it('renders nothing when warnings is empty', () => {
    expect(renderToStaticMarkup(<SaveWarnings warnings={[]} />)).toBe('')
    expect(renderToStaticMarkup(<SaveWarnings warnings={undefined} />)).toBe('')
  })

  it('offers a dismiss control only when given a handler', () => {
    const without = renderToStaticMarkup(<SaveWarnings warnings={['w']} />)
    expect(without).not.toContain('banner-dismiss')
    const withIt = renderToStaticMarkup(<SaveWarnings warnings={['w']} onDismiss={() => {}} />)
    expect(withIt).toContain('banner-dismiss')
  })
})

describe('<ThroughputNote> beside the extraction slot picker', () => {
  it('shows the cached throughput message, tagged with its verdict', () => {
    const html = renderToStaticMarkup(<ThroughputNote status={status()} />)
    expect(html).toContain('data-testid="mem-graph-throughput"')
    expect(html).toContain('data-verdict="too_slow"')
    expect(html).toContain('projects to ~1658 s')
  })

  it('shows an ok verdict without the warn colour', () => {
    const html = renderToStaticMarkup(
      <ThroughputNote status={status({ throughput: throughput({ verdict: 'ok', message: 'inside the timeout' }) })} />,
    )
    expect(html).toContain('data-verdict="ok"')
    expect(html).toContain('inside the timeout')
    expect(html).not.toContain('var(--warn)')
  })

  it('renders nothing when there is no cached throughput', () => {
    expect(renderToStaticMarkup(<ThroughputNote status={status({ throughput: null })} />)).toBe('')
    expect(renderToStaticMarkup(<ThroughputNote status={undefined} />)).toBe('')
  })
})
