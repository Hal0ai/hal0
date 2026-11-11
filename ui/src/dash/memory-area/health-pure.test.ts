// What the Health tab says, checked as text: the copy of every reason code the route can send,
// the order and state of the path, the verdict, what a cut-short read adds, and the engine strip.
//
// @ts-expect-error The app's tsconfig carries no Node types on purpose; this test runs in Node.
import { existsSync, readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import type { MemoryHealthReport, MemoryHealthStep } from '@/api/hooks/useMemory'
import { memoryHealthFixture, type MockMemoryHealthState } from '@/api/mockFixtures'
import { readHealth } from './area-pure'
import { EMBEDDER_ID, GRAPH, OLD_EMBEDDER_ID, healthReport, modelsAnswer, pathSteps } from './area-test-fixtures'
import {
  KNOWN_REASON_CODES,
  PARTIAL_BANKS_NOTE,
  PARTIAL_EMBEDDER_NOTE,
  TRUNCATED_NOTE,
  banksNote,
  buildPath,
  embedderNotice,
  embedderView,
  engineStrip,
  healthVerdict,
  incompleteNote,
  labelledFigures,
  readableError,
  refreshFailed,
  segmentsText,
  stepCopy,
  stepName,
  switchCommand,
  type PathStep,
} from './health-pure'

/** A step as the route sends it. */
const step = (id: string, reason: string, figures: Record<string, unknown> = {}): MemoryHealthStep => ({
  id,
  state: 'current',
  waits_on: null,
  reason_code: reason,
  figures,
})

/** The title and the sentences of an open step, as plain text. */
function copy(id: string, reason: string, figures: Record<string, unknown> = {}, report = healthReport()) {
  const written = stepCopy(step(id, reason, figures), report)
  return { title: written.title, body: segmentsText(written.body), known: written.known }
}

const WINDOW = { window_s: 21600 }
const byId = (path: PathStep[], id: string) => path.find((s) => s.id === id)!
const bodyOf = (s: PathStep) => segmentsText(s.body)

/** Nothing the route wrote may reach the page raw. */
function expectPlainWords(words: string) {
  expect(words).not.toMatch(/[{}[\]"]/)
  expect(words).not.toMatch(/[a-z]+_[a-z]+/)
  expect(words).not.toMatch(/\d{4}-\d{2}-\d{2}T/)
  expect(words).not.toMatch(/\bnull\b|\bundefined\b|\bNaN\b|\btrue\b|\bfalse\b/)
}

describe('reason codes', () => {
  const source = (name: string) => new URL(`../../../../src/hal0/memory/${name}`, import.meta.url)
  const inRepo = existsSync(source('health_path.py')) && existsSync(source('embedder_verdict.py'))

  it.skipIf(!inRepo)('has its own words for every code the route can send', () => {
    const path = readFileSync(source('health_path.py'), 'utf8') as string
    const verdict = readFileSync(source('embedder_verdict.py'), 'utf8') as string
    const constants = [...path.matchAll(/^REASON_[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1])
    const prefix = constants.find((c) => c.endsWith('_'))!
    const embedder = [...verdict.matchAll(/^[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => `${prefix}${m[1]}`)
    expect(prefix).toBe('embedder_')
    expect(embedder).toHaveLength(5)
    // The fold-in step has no backend yet and no copy of its own: it takes the generic words.
    const expected = [...constants.filter((c) => c !== prefix && c !== 'fold_in_pending'), ...embedder]
    expect([...KNOWN_REASON_CODES].sort()).toEqual(expected.sort())
  })

  it('knows thirteen codes', () => {
    expect([...KNOWN_REASON_CODES].sort()).toEqual([
      'embedder_conflict',
      'embedder_mixed',
      'embedder_needs_switch',
      'embedder_record_unreadable',
      'embedder_store_unavailable',
      'engine_unreachable',
      'extraction_llm_unreachable',
      'extraction_recent_failures',
      'extraction_slot_unresolved',
      'knowledge_refresh_failed',
      'knowledge_stale',
      'queue_operations_failed',
      'queue_waiting_too_long',
    ])
  })
})

describe('stepCopy, one case per reason code', () => {
  it('engine_unreachable', () => {
    expect(copy('engine', 'engine_unreachable', { reachable: false })).toEqual({
      title: 'Check the memory engine service',
      body: 'The memory engine is not answering, so nothing else on this page can be read.',
      known: true,
    })
  })

  it('extraction_slot_unresolved', () => {
    expect(copy('extraction', 'extraction_slot_unresolved', { slot_resolves: false, ...WINDOW })).toEqual({
      title: 'Give the extraction slot a model',
      body: 'Storing a memory runs on the memory slot, and that slot has no model. Until it has one, nothing agents send becomes a fact.',
      known: true,
    })
  })

  it('extraction_llm_unreachable, on a slot and on an upstream model', () => {
    expect(copy('extraction', 'extraction_llm_unreachable', { slot_resolves: true, llm_reachable: false })).toEqual({
      title: 'Reconnect extraction to its model',
      body: 'Storing a memory runs on the memory slot, and its model is not accepting connections. Until it does, nothing agents send becomes a fact.',
      known: true,
    })
    // No slot in play: the slot is not named.
    expect(copy('extraction', 'extraction_llm_unreachable', { slot_resolves: null, llm_reachable: false }).body).toBe(
      'The model that turns what agents send into facts is not accepting connections. Until it does, nothing agents send becomes a fact.',
    )
  })

  it('extraction_recent_failures, plural and singular', () => {
    expect(copy('extraction', 'extraction_recent_failures', { failures: 37, ...WINDOW })).toEqual({
      title: 'Fix what is failing when memories are stored',
      body: '37 attempts to store a memory failed in the last 6 h. Until the cause is fixed, operations wait in the queue and a retry would fail.',
      known: true,
    })
    expect(copy('extraction', 'extraction_recent_failures', { failures: 1, ...WINDOW }).body).toMatch(
      /^1 attempt to store a memory failed in the last 6 h\./,
    )
    // The window is the route's, not a constant of this page.
    expect(copy('extraction', 'extraction_recent_failures', { failures: 2, window_s: 3600 }).body).toMatch(
      /^2 attempts to store a memory failed in the last 1 h\./,
    )
  })

  it('queue_operations_failed, plural and singular, with and without a queue behind it', () => {
    expect(
      copy('queue', 'queue_operations_failed', { failed: 105, waiting: 1093, oldest_waiting_s: 21600 }),
    ).toEqual({
      title: 'Retry or discard the failed operations',
      body: '105 operations have failed and 1,093 are waiting, the oldest for 6 h. Retry or discard them on the Operations tab.',
      known: true,
    })
    expect(copy('queue', 'queue_operations_failed', { failed: 1, waiting: 1, oldest_waiting_s: 720 }).body).toBe(
      '1 operation has failed and 1 is waiting, for 12 min. Retry or discard it on the Operations tab.',
    )
    expect(copy('queue', 'queue_operations_failed', { failed: 3, waiting: 0, oldest_waiting_s: null }).body).toBe(
      '3 operations have failed, and nothing is waiting. Retry or discard them on the Operations tab.',
    )
  })

  it('queue_waiting_too_long, plural and singular, with the threshold the route sent', () => {
    expect(
      copy('queue', 'queue_waiting_too_long', {
        failed: 0,
        waiting: 52,
        oldest_waiting_s: 5400,
        max_waiting_age_s: 1800,
      }),
    ).toEqual({
      title: 'Find out why the queue is not moving',
      body: '52 operations are waiting, the oldest for 1 h. Nothing has failed, but this page asks for you once one has waited longer than 30 min. The Operations tab shows what is running.',
      known: true,
    })
    expect(
      copy('queue', 'queue_waiting_too_long', { waiting: 1, oldest_waiting_s: 7200, max_waiting_age_s: 3600 }).body,
    ).toBe(
      '1 operation has been waiting for 2 h. Nothing has failed, but this page asks for you once one has waited longer than 1 h. The Operations tab shows what is running.',
    )
  })

  it('embedder_record_unreadable', () => {
    expect(copy('embedder', 'embedder_record_unreadable', { recorded: EMBEDDER_ID, configured: EMBEDDER_ID })).toEqual({
      title: 'Repair the embedder record',
      body: 'The record of the embedder this store was built with cannot be read. hal0 has left the file as it is and keeps the engine on hs-embed-1.',
      known: true,
    })
  })

  it('embedder_store_unavailable', () => {
    expect(copy('embedder', 'embedder_store_unavailable', {})).toEqual({
      title: 'Bring the memory store back',
      body: 'The place the memory store is kept is not available: a link that does not resolve, or a disk that is not mounted. Nothing is recorded about its embedder until it is back.',
      known: true,
    })
  })

  it('embedder_conflict: the running engine, its unit files, or both', () => {
    const facts = { recorded: EMBEDDER_ID, configured: EMBEDDER_ID }
    expect(copy('embedder', 'embedder_conflict', { ...facts, conflict: { live: OLD_EMBEDDER_ID } })).toEqual({
      title: 'Make the embedder record and the engine agree',
      body: 'The embedder record says hs-embed-1, but the running engine is on BAAI/bge-small-en-v1.5. hal0 has changed neither. Remove the override or correct the record so the two agree.',
      known: true,
    })
    expect(copy('embedder', 'embedder_conflict', { ...facts, conflict: { on_disk: OLD_EMBEDDER_ID } }).body).toMatch(
      /^The embedder record says hs-embed-1, but its unit files would start it on BAAI\/bge-small-en-v1\.5\./,
    )
    expect(
      copy('embedder', 'embedder_conflict', {
        ...facts,
        conflict: { live: OLD_EMBEDDER_ID, on_disk: { provider: 'openai', model: 'text-embedding-3-small' } },
      }).body,
    ).toMatch(
      /but the running engine is on BAAI\/bge-small-en-v1\.5 and its unit files would start it on the openai provider\./,
    )
  })

  it('embedder_mixed', () => {
    expect(copy('embedder', 'embedder_mixed', { recorded: EMBEDDER_ID, configured: EMBEDDER_ID, mixed: true })).toEqual({
      title: 'Re-embed the store on one embedder',
      body: 'This store may hold vectors from two embedders: it existed before the engine moved to hs-embed-1. Recall over older facts is degraded until the store is re-embedded.',
      known: true,
    })
    // Settings that ask for a third thing are said too.
    expect(
      copy('embedder', 'embedder_mixed', {
        recorded: EMBEDDER_ID,
        configured: { provider: 'local', model: 'nomic-embed' },
        mixed: true,
      }).body,
    ).toMatch(/re-embedded\. Settings ask for nomic-embed\.$/)
  })

  it('embedder_needs_switch', () => {
    expect(
      copy('embedder', 'embedder_needs_switch', {
        recorded: OLD_EMBEDDER_ID,
        configured: { provider: 'local', model: 'nomic-embed' },
      }),
    ).toEqual({
      title: 'Re-embed the store on the embedder settings ask for',
      body: 'This store was built with BAAI/bge-small-en-v1.5 and the engine stays on it. Settings ask for nomic-embed, which has no effect until the store is re-embedded.',
      known: true,
    })
  })

  it('knowledge_refresh_failed, plural and singular, with the date of the last one that worked', () => {
    expect(
      copy('knowledge', 'knowledge_refresh_failed', { pages_stale: 29, models_stale: 5, refresh_failed: 31 }),
    ).toEqual({
      title: 'Refresh the knowledge that failed',
      body: '31 knowledge pages and mental models failed their last refresh, and 34 are stale. The last refresh that worked was on 5 Nov 2026.',
      known: true,
    })
    const one = copy(
      'knowledge',
      'knowledge_refresh_failed',
      { pages_stale: 1, models_stale: 0, refresh_failed: 1 },
      healthReport({ knowledge: { ...healthReport().knowledge!, last_success_at: null } }),
    )
    expect(one.body).toBe('1 knowledge page or mental model failed its last refresh, and 1 is stale.')
  })

  it('knowledge_stale, plural and singular', () => {
    expect(copy('knowledge', 'knowledge_stale', { pages_stale: 5, models_stale: 6, refresh_failed: 0 })).toEqual({
      title: 'Refresh stale knowledge',
      body: '5 knowledge pages and 6 mental models are stale: behind the facts they were built from. No refresh has failed.',
      known: true,
    })
    expect(copy('knowledge', 'knowledge_stale', { pages_stale: 0, models_stale: 1, refresh_failed: 0 }).body).toBe(
      '1 mental model is stale: behind the facts it was built from. No refresh has failed.',
    )
    expect(copy('knowledge', 'knowledge_stale', { pages_stale: 1, models_stale: 0, refresh_failed: 0 }).body).toMatch(
      /^1 knowledge page is stale:/,
    )
  })

  it('writes nothing raw for any known code, even with no figures at all', () => {
    for (const reason of KNOWN_REASON_CODES) {
      const empty = healthReport({ extraction: null, embedder: null, queue: null, knowledge: null })
      for (const written of [copy('x', reason, {}, empty), copy('x', reason, {})]) {
        expect(written.known).toBe(true)
        expectPlainWords(written.title)
        expectPlainWords(written.body)
        expect(written.body).not.toMatch(/\b0 (attempts|operations|knowledge)/)
      }
    }
  })
})

describe('a reason code this page does not know', () => {
  const unknown = step('fold_in', 'fold_in_pending', { banks: 21 })

  it('gets a generic sentence under a plain title, and never the code', () => {
    const written = stepCopy(unknown, healthReport())
    expect(written.known).toBe(false)
    expect(written.title).toBe('Fold in needs attention')
    expect(segmentsText(written.body)).toBe(
      'This step is open, and this page has no description for its cause yet. The figures the check reported are below.',
    )
    expectPlainWords(written.title + segmentsText(written.body))
    expect(written.title + segmentsText(written.body)).not.toContain('pending')
  })

  it('still shows its figures, under labels, as words', () => {
    expect(labelledFigures(unknown.figures)).toEqual([{ label: 'Banks', value: '21' }])
    expect(
      labelledFigures({
        failed: 1093,
        oldest_waiting_s: 21600,
        llm_reachable: false,
        recorded: EMBEDDER_ID,
        some_new_count: 4,
        retry_after_s: 90,
        note: 'held for review',
      }),
    ).toEqual([
      { label: 'Failed operations', value: '1,093' },
      { label: 'Oldest waiting', value: '6 h' },
      { label: 'The extraction model accepts connections', value: 'no' },
      { label: 'Store built with', value: 'hs-embed-1' },
      { label: 'Some new count', value: '4' },
      { label: 'Retry after', value: '1 min' },
      { label: 'Note', value: 'held for review' },
    ])
  })

  it('leaves out lists, nested objects and empty values: nothing is printed as JSON', () => {
    const figures = labelledFigures({
      top_errors: [{ message: 'x', count: 1 }],
      reasons: ['mixed'],
      conflict: { live: EMBEDDER_ID },
      detail: { a: 1 },
      missing: null,
      blank: ' ',
      rows: [1, 2],
    })
    expect(figures).toEqual([])
    expect(labelledFigures(null)).toEqual([])
    expect(labelledFigures('x')).toEqual([])
  })

  it('says so when the check sent no figures', () => {
    const written = stepCopy(step('queue', 'queue_something_new', {}), healthReport())
    expect(written.title).toBe('The queue needs attention')
    expect(segmentsText(written.body)).toMatch(/The check reported no figures with it\.$/)
  })

  it('is a row of the path like any other, in its place, with no action', () => {
    const report = healthReport({
      steps: pathSteps(
        { fold_in: { reason: 'fold_in_pending', figures: { banks: 21 } }, knowledge: { reason: 'knowledge_stale' } },
        ['engine', 'extraction', 'queue', 'fold_in', 'knowledge'],
      ),
    })
    const path = buildPath(report)
    const row = byId(path, 'fold_in')
    expect(row).toMatchObject({ position: 3, of: 4, state: 'current', name: 'fold in', links: [], command: null })
    expect(row.figures).toEqual([{ label: 'Banks', value: '21' }])
    expect(byId(path, 'knowledge').waitsOn).toBe('Waiting on step 3: fold in')
  })
})

describe('stepName', () => {
  it('names each step in plain words', () => {
    expect(['extraction', 'queue', 'embedder', 'knowledge'].map(stepName)).toEqual([
      'storing memories',
      'the queue',
      'the embedder',
      'knowledge',
    ])
    expect(stepName('some-new_step')).toBe('some new step')
  })
})

describe('buildPath', () => {
  const failing = {
    extraction: { reason: 'extraction_recent_failures', figures: { failures: 37, ...WINDOW } },
    queue: { reason: 'queue_operations_failed', figures: { failed: 105, waiting: 1093, oldest_waiting_s: 21600 } },
    knowledge: { reason: 'knowledge_refresh_failed', figures: { pages_stale: 29, models_stale: 5, refresh_failed: 31 } },
  }
  const report = healthReport({
    steps: pathSteps(failing),
    extraction: {
      ...healthReport().extraction!,
      failures_6h: 37,
      top_errors: [
        { message: 'APIConnectionError: Connection error.', count: 36 },
        { message: `Task exceeded the <n>s wall-clock limit ${'x'.repeat(200)}`, count: 1 },
      ],
    },
  })
  const path = buildPath(report)

  it('keeps the route order, numbers the steps from one, and leaves the engine step out', () => {
    expect(path.map((s) => [s.position, s.id, s.state])).toEqual([
      [1, 'extraction', 'current'],
      [2, 'queue', 'waiting'],
      [3, 'embedder', 'clear'],
      [4, 'knowledge', 'waiting'],
    ])
    expect(path.every((s) => s.of === 4)).toBe(true)
  })

  it('does not reorder what the route sent', () => {
    const shuffled = healthReport({ steps: [...pathSteps(failing)].reverse() })
    expect(buildPath(shuffled).map((s) => s.id)).toEqual(['knowledge', 'embedder', 'queue', 'extraction'])
  })

  it('says what a waiting step waits on, by position and in words', () => {
    expect(byId(path, 'queue').waitsOn).toBe('Waiting on step 1: storing memories')
    // Knowledge waits on the nearest open step before it, which is the queue.
    expect(byId(path, 'knowledge').waitsOn).toBe('Waiting on step 2: the queue')
    expect(byId(path, 'extraction').waitsOn).toBeNull()
    expect(byId(path, 'embedder').waitsOn).toBeNull()
  })

  it('names what it waits on even when that step is not in the list', () => {
    const odd = healthReport({
      steps: [{ id: 'queue', state: 'waiting', waits_on: 'engine', reason_code: 'queue_operations_failed', figures: {} }],
    })
    expect(buildPath(odd)[0].waitsOn).toBe('Waiting on the memory engine')
    const none = healthReport({
      steps: [{ id: 'queue', state: 'waiting', waits_on: null, reason_code: 'queue_operations_failed', figures: {} }],
    })
    expect(buildPath(none)[0].waitsOn).toBe('Waiting on an earlier step')
  })

  it('gives the current step its status as a chip, and a waiting step the word waiting', () => {
    expect(byId(path, 'extraction').chip).toEqual({ tone: 'err', word: '37 failures in 6 h' })
    expect(byId(path, 'queue').chip).toEqual({ tone: 'neutral', word: 'waiting' })
    expect(byId(path, 'knowledge').chip).toEqual({ tone: 'neutral', word: 'waiting' })
    expect(byId(path, 'embedder').chip).toBeNull()
  })

  it('collapses a clear step to its name and one sentence', () => {
    const embedder = byId(path, 'embedder')
    expect(embedder.title).toBe('The embedder')
    expect(bodyOf(embedder)).toBe('The store and the engine are on hs-embed-1.')
    expect(embedder).toMatchObject({ errors: [], figures: [], command: null, links: [] })
  })

  it('lists the extraction failures, cut to one line, most frequent first, with their counts', () => {
    const errors = byId(path, 'extraction').errors
    expect(errors[0]).toEqual({ message: 'APIConnectionError: Connection error.', count: 36 })
    expect(errors[1].count).toBe(1)
    expect(errors[1].message.length).toBeLessThanOrEqual(110)
    expect(errors[1].message.endsWith('…')).toBe(true)
  })

  it('links extraction to Services and engine settings, the queue to Operations, knowledge to Knowledge', () => {
    expect(byId(path, 'extraction').links).toEqual([
      { to: 'services', label: 'Open Services' },
      { to: 'settings', label: 'Open engine settings' },
    ])
    expect(byId(path, 'queue').links).toEqual([{ to: 'operations', label: 'Open Operations' }])
    expect(byId(path, 'knowledge').links).toEqual([{ to: 'knowledge', label: 'Open Knowledge' }])
  })

  it('offers no command to a step that is not the current one', () => {
    const waiting = healthReport({
      steps: pathSteps({
        queue: { reason: 'queue_operations_failed', figures: { failed: 2, waiting: 0 } },
        embedder: { reason: 'embedder_mixed', figures: { recorded: EMBEDDER_ID, configured: EMBEDDER_ID, mixed: true } },
      }),
    })
    const embedder = byId(buildPath(waiting), 'embedder')
    expect(embedder.state).toBe('waiting')
    expect(embedder.command).toBeNull()
    expect(embedder.waitsOn).toBe('Waiting on step 2: the queue')
    // Its facts are still said.
    expect(bodyOf(embedder)).toMatch(/may hold vectors from two embedders/)
  })

  it('reads a state it does not know as clear only when the route said nothing is open', () => {
    const odd = healthReport({
      steps: [{ id: 'queue', state: 'paused' as never, waits_on: null, reason_code: null, figures: {} }],
    })
    expect(buildPath(odd)[0].state).toBe('clear')
  })

  describe('clear rows', () => {
    const clear = buildPath(healthReport())

    it('says what each check found', () => {
      expect(clear.map((s) => [s.title, bodyOf(s)])).toEqual([
        ['Storing memories', 'No attempt to store a memory failed in the last 6 h.'],
        ['The queue', 'Nothing is waiting and nothing has failed.'],
        ['The embedder', 'The store and the engine are on hs-embed-1.'],
        ['Knowledge', '34 knowledge pages and mental models are fresh.'],
      ])
      expect(clear.every((s) => s.state === 'clear' && s.chip === null && s.command === null)).toBe(true)
    })

    it('counts what is waiting when the queue is merely busy', () => {
      const busy = healthReport({ queue: { ...healthReport().queue!, waiting: 7, oldest_waiting_s: 720 } })
      expect(bodyOf(byId(buildPath(busy), 'queue'))).toBe('Nothing has failed. 7 operations are waiting, the oldest for 12 min.')
      const one = healthReport({ queue: { ...healthReport().queue!, waiting: 1, oldest_waiting_s: 30 } })
      expect(bodyOf(byId(buildPath(one), 'queue'))).toBe('Nothing has failed. 1 operation is waiting, for 30 s.')
    })

    it('says a box has no knowledge yet, and counts one', () => {
      const none = healthReport({ knowledge: { ...healthReport().knowledge!, pages: 0, models: 0 } })
      expect(bodyOf(byId(buildPath(none), 'knowledge'))).toBe('No knowledge pages or mental models yet.')
      const single = healthReport({ knowledge: { ...healthReport().knowledge!, pages: 1, models: 0 } })
      expect(bodyOf(byId(buildPath(single), 'knowledge'))).toBe('1 knowledge page or mental model is fresh.')
    })

    it('says the ordinary upgraded box needs nothing, and a missing record is not called fine', () => {
      const upgraded = healthReport({
        embedder: { ...healthReport().embedder!, recorded: OLD_EMBEDDER_ID, needs_switch: true, informational: true },
      })
      expect(bodyOf(byId(buildPath(upgraded), 'embedder'))).toBe(
        'The store stays on BAAI/bge-small-en-v1.5, the embedder it was built with. Nothing to do.',
      )
      const unread = healthReport({ embedder: null, partial: ['embedder'] })
      const row = byId(buildPath(unread), 'embedder')
      expect(bodyOf(row)).toBe('The embedder record was not reported, so nothing is known about it.')
      expect(row.note).toBe(PARTIAL_EMBEDDER_NOTE)
    })

    it('keeps the links to Operations and Knowledge, and none for extraction', () => {
      expect(byId(clear, 'queue').links).toEqual([{ to: 'operations', label: 'Open Operations' }])
      expect(byId(clear, 'knowledge').links).toEqual([{ to: 'knowledge', label: 'Open Knowledge' }])
      expect(byId(clear, 'extraction').links).toEqual([])
    })
  })

  describe('the embedder step, when it is the one to do now', () => {
    const current = (reason: string, figures: Record<string, unknown>) =>
      byId(buildPath(healthReport({ steps: pathSteps({ embedder: { reason, figures } }) })), 'embedder')

    it('shows the switch as a command to review for a mixed store, and says it is a dry run', () => {
      const row = current('embedder_mixed', { recorded: EMBEDDER_ID, configured: EMBEDDER_ID, mixed: true })
      expect(row.state).toBe('current')
      expect(row.chip).toEqual({ tone: 'warn', word: 'mixed' })
      expect(row.command!.command).toBe('hal0 memory embedder switch hs-embed-1')
      expect(row.command!.note).toContain('dry run by default')
      expect(row.command!.note).toContain('changes nothing')
      expect(row.links).toEqual([])
    })

    it('switches towards the model settings ask for', () => {
      const row = current('embedder_needs_switch', {
        recorded: OLD_EMBEDDER_ID,
        configured: { provider: 'local', model: 'hindsight-ai/hs-embed-1' },
      })
      expect(row.command!.command).toBe('hal0 memory embedder switch hindsight-ai/hs-embed-1')
      expect(row.chip).toBeNull()
    })

    it('does not offer the switch for a conflict: the doctor names what disagrees', () => {
      const row = current('embedder_conflict', {
        recorded: EMBEDDER_ID,
        configured: EMBEDDER_ID,
        conflict: { live: OLD_EMBEDDER_ID },
      })
      expect(row.chip).toEqual({ tone: 'warn', word: 'conflict' })
      expect(row.command!.command).toBe('hal0 doctor')
      expect(row.command!.note).toContain('changes nothing')
    })

    it('gives the doctor for a record that cannot be read and a store that is gone', () => {
      expect(current('embedder_record_unreadable', {}).command!.command).toBe('hal0 doctor')
      expect(current('embedder_store_unavailable', {}).command!.command).toBe('hal0 doctor')
    })
  })
})

describe('switchCommand', () => {
  it('names the configured model, and the provider when it is not the local one', () => {
    expect(switchCommand({ recorded: OLD_EMBEDDER_ID, configured: EMBEDDER_ID })).toBe(
      'hal0 memory embedder switch hs-embed-1',
    )
    expect(
      switchCommand({ recorded: OLD_EMBEDDER_ID, configured: { provider: 'openai', model: 'text-embedding-3-small' } }),
    ).toBe('hal0 memory embedder switch text-embedding-3-small --provider openai')
  })

  it('falls back to the recorded model, and to nothing when no model is known', () => {
    expect(switchCommand({ recorded: EMBEDDER_ID, configured: { provider: null, model: null } })).toBe(
      'hal0 memory embedder switch hs-embed-1',
    )
    expect(switchCommand({})).toBeNull()
    expect(switchCommand(null)).toBeNull()
  })

  it('does not build a command from a model name that is not one word', () => {
    expect(switchCommand({ configured: { provider: 'local', model: 'x; rm -rf /' } })).toBeNull()
  })
})

describe('healthVerdict', () => {
  const lead = (report: MemoryHealthReport) => healthVerdict(report)

  it('is clear, calm and complete when no step is open', () => {
    expect(lead(healthReport())).toEqual({
      kind: 'clear',
      lead: 'Nothing needs attention',
      detail:
        'The engine answers, and the 4 things this page watches are clear: storing memories, the queue, the embedder and knowledge.',
      qualifier: null,
      chip: { tone: 'ok', word: 'working' },
      open: 0,
    })
  })

  it('counts the open steps, and names the one to start with', () => {
    const three = healthReport({
      steps: pathSteps({
        extraction: { reason: 'extraction_recent_failures', figures: { failures: 7 } },
        queue: { reason: 'queue_operations_failed', figures: { failed: 10 } },
        knowledge: { reason: 'knowledge_refresh_failed', figures: { refresh_failed: 5 } },
      }),
    })
    expect(lead(three)).toMatchObject({
      kind: 'attention',
      lead: '3 things need you',
      detail:
        'Start with step 1, storing memories: fix what is failing when memories are stored. The other 2 wait on it, in the order below.',
      chip: null,
      open: 3,
    })
    const two = healthReport({
      steps: pathSteps({
        queue: { reason: 'queue_operations_failed', figures: { failed: 10 } },
        knowledge: { reason: 'knowledge_stale', figures: { pages_stale: 1 } },
      }),
    })
    expect(lead(two)).toMatchObject({
      lead: '2 things need you',
      detail:
        'Start with step 2, the queue: retry or discard the failed operations. The other one waits on it, in the order below.',
    })
  })

  it('says "1 thing needs you" for one', () => {
    const one = healthReport({
      steps: pathSteps({ knowledge: { reason: 'knowledge_stale', figures: { pages_stale: 5, models_stale: 6 } } }),
    })
    expect(lead(one)).toMatchObject({
      kind: 'attention',
      lead: '1 thing needs you',
      detail: 'It is step 4, knowledge: refresh stale knowledge.',
      open: 1,
    })
  })

  it('is decided from the steps, not from the verdict word', () => {
    const lying = healthReport({
      steps: pathSteps({ queue: { reason: 'queue_operations_failed', figures: { failed: 1 } } }),
      verdict: 'clear',
    })
    expect(lead(lying).kind).toBe('attention')
  })

  it('never calls a partial read clear without saying so, and drops the "working" chip', () => {
    const partial = lead(healthReport({ partial: ['queue', 'banks'] }))
    expect(partial).toMatchObject({
      kind: 'clear',
      lead: 'Nothing needs attention in what was read',
      qualifier: PARTIAL_BANKS_NOTE,
      chip: null,
    })
    expect(PARTIAL_BANKS_NOTE).toBe('Some banks did not answer in time; figures are what was read.')
  })

  it('qualifies a truncated read the same way', () => {
    const truncated = lead(healthReport({ queue: { ...healthReport().queue!, truncated: true } }))
    expect(truncated).toMatchObject({ lead: 'Nothing needs attention in what was read', qualifier: TRUNCATED_NOTE, chip: null })
  })

  it('says the gap beside an attention verdict too', () => {
    const open = healthReport({
      steps: pathSteps({ queue: { reason: 'queue_operations_failed', figures: { failed: 1 } } }),
      partial: ['knowledge'],
    })
    expect(lead(open)).toMatchObject({ kind: 'attention', lead: '1 thing needs you', qualifier: PARTIAL_BANKS_NOTE })
  })
})

describe('what was cut short', () => {
  it('has nothing to say about a whole read', () => {
    const report = healthReport()
    expect(incompleteNote(report)).toBeNull()
    expect(banksNote(report)).toBeNull()
    expect(buildPath(report).every((s) => s.note === null)).toBe(true)
  })

  it('puts the partial wording beside the step whose section was cut short, and only there', () => {
    const path = buildPath(healthReport({ partial: ['queue'] }))
    expect(byId(path, 'queue').note).toBe(PARTIAL_BANKS_NOTE)
    expect(byId(path, 'extraction').note).toBeNull()
    expect(byId(path, 'knowledge').note).toBeNull()
    expect(byId(buildPath(healthReport({ partial: ['knowledge'] })), 'knowledge').note).toBe(PARTIAL_BANKS_NOTE)
    expect(byId(buildPath(healthReport({ partial: ['extraction'] })), 'extraction').note).toBe(PARTIAL_BANKS_NOTE)
  })

  it('puts the truncated wording beside the queue and the failure reasons', () => {
    const path = buildPath(healthReport({ queue: { ...healthReport().queue!, truncated: true } }))
    expect(byId(path, 'queue').note).toBe(TRUNCATED_NOTE)
    expect(byId(path, 'extraction').note).toBe(TRUNCATED_NOTE)
    expect(byId(path, 'knowledge').note).toBeNull()
    expect(TRUNCATED_NOTE).toMatch(/more operations than were read/)
  })

  it('says both when both hold', () => {
    const report = healthReport({ partial: ['queue', 'embedder'], queue: { ...healthReport().queue!, truncated: true } })
    expect(byId(buildPath(report), 'queue').note).toBe(`${PARTIAL_BANKS_NOTE} ${TRUNCATED_NOTE}`)
    expect(incompleteNote(report)).toBe(`${PARTIAL_BANKS_NOTE} ${PARTIAL_EMBEDDER_NOTE} ${TRUNCATED_NOTE}`)
  })

  it('qualifies the banks table when its facts or its operation counts were cut short', () => {
    expect(banksNote(healthReport({ partial: ['banks'] }))).toBe(PARTIAL_BANKS_NOTE)
    expect(banksNote(healthReport({ partial: ['queue'] }))).toBe(PARTIAL_BANKS_NOTE)
    expect(banksNote(healthReport({ partial: ['knowledge'] }))).toBeNull()
  })
})

describe('embedderView', () => {
  const base = healthReport().embedder!

  it('is clean when the store and the settings agree', () => {
    expect(embedderView(base)).toEqual({ state: 'clean', recorded: 'hs-embed-1', configured: 'hs-embed-1', other: null })
    expect(embedderNotice(embedderView(base))).toBeNull()
  })

  it("takes the ordinary upgraded box from the route's own mark, and gives it one quiet line", () => {
    const view = embedderView({ ...base, recorded: OLD_EMBEDDER_ID, needs_switch: true, informational: true })
    expect(view.state).toBe('informational')
    expect(segmentsText(embedderNotice(view)!)).toBe(
      'This store was built with BAAI/bge-small-en-v1.5 and the engine stays on it. ' +
        'Settings ask for hs-embed-1, which has no effect until the store is re-embedded.',
    )
  })

  it('does not call a difference the operator made informational', () => {
    const view = embedderView({ ...base, recorded: OLD_EMBEDDER_ID, needs_switch: true, informational: false })
    expect(view.state).toBe('differs')
    // It is a step of the path, so it gets no line of its own under the strip.
    expect(embedderNotice(view)).toBeNull()
  })

  it('reads mixed and conflict, a conflict first', () => {
    expect(embedderView({ ...base, mixed: true, needs_switch: true }).state).toBe('mixed')
    const conflict = embedderView({ ...base, mixed: true, conflict: { on_disk: OLD_EMBEDDER_ID } })
    expect(conflict).toMatchObject({ state: 'conflict', other: 'BAAI/bge-small-en-v1.5' })
    expect(embedderNotice(conflict)).toBeNull()
  })

  it('names a provider that is not the local one', () => {
    const view = embedderView({ ...base, recorded: { provider: 'openai', model: 'text-embedding-3-small' } })
    expect(view.recorded).toBe('the openai provider')
  })

  it('is unknown without a record', () => {
    expect(embedderView(null).state).toBe('unknown')
    expect(embedderView({ ...base, recorded: { provider: null, model: null } }).state).toBe('unknown')
  })
})

describe('engineStrip', () => {
  const cells = (report: MemoryHealthReport, graph: unknown = GRAPH, models: unknown = modelsAnswer()) =>
    engineStrip({ report, graph: graph as never, models: models as never })
  const cell = (list: ReturnType<typeof cells>, id: string) => list.find((c) => c.id === id)!

  it('is five cells, always in this order', () => {
    expect(cells(healthReport()).map((c) => c.label)).toEqual([
      'Engine',
      'Extraction',
      'Embedder',
      'Reranker',
      'Decision model',
    ])
  })

  it('reads the engine, extraction and embedder from the health answer', () => {
    const list = cells(healthReport())
    expect(cell(list, 'engine')).toMatchObject({ value: 'Hindsight 1.2.0', chip: { tone: 'ok', word: 'reachable' } })
    expect(cell(list, 'extraction')).toEqual({
      id: 'extraction',
      label: 'Extraction',
      value: 'memory slot',
      helper: 'dedicated slot · 2 at a time · 0 failures in 6 h',
      chip: { tone: 'ok', word: 'working' },
    })
    expect(cell(list, 'embedder')).toMatchObject({ value: 'hs-embed-1', helper: 'the model this store was built with' })
  })

  it('does not need the older reads for those three', () => {
    const list = cells(healthReport(), null, null)
    expect(cell(list, 'engine').value).toBe('Hindsight 1.2.0')
    expect(cell(list, 'extraction').value).toBe('memory slot')
    expect(cell(list, 'embedder').value).toBe('hs-embed-1')
    // The reranker and the decision model are the models read's, and are not reported without it.
    expect(cell(list, 'reranker').value).toBeNull()
    expect(cell(list, 'decision').value).toBeNull()
  })

  it('marks extraction failing while its step is open, with the count as words', () => {
    const report = healthReport({
      steps: pathSteps({ extraction: { reason: 'extraction_recent_failures', figures: { failures: 37, ...WINDOW } } }),
      extraction: { ...healthReport().extraction!, failures_6h: 37 },
    })
    expect(cell(cells(report), 'extraction')).toMatchObject({
      helper: 'dedicated slot · 2 at a time · 37 failures in 6 h',
      chip: { tone: 'err', word: 'failing' },
    })
    const unresolved = healthReport({
      steps: pathSteps({ extraction: { reason: 'extraction_slot_unresolved' } }),
      extraction: { ...healthReport().extraction!, slot_resolves: false, failures_6h: 1 },
    })
    expect(cell(cells(unresolved), 'extraction').helper).toBe(
      'dedicated slot · no enabled slot with this name · 2 at a time · 1 failure in 6 h',
    )
  })

  it('takes the upstream model and the routing switch from graph status, the two things health does not carry', () => {
    const upstream = cells(healthReport({ extraction: { ...healthReport().extraction!, slot_resolves: null, mode: 'custom' } }), {
      ...GRAPH,
      target: { kind: 'upstream', model: 'gpt-oss-120b' },
    })
    expect(cell(upstream, 'extraction')).toMatchObject({
      value: 'gpt-oss-120b',
      helper: 'upstream model, through hal0 · 2 at a time · 0 failures in 6 h',
    })
    const off = cells(healthReport(), { ...GRAPH, enabled: false })
    expect(cell(off, 'extraction')).toMatchObject({
      value: 'memory slot',
      helper: 'routing to this target is off in Engine settings',
      chip: null,
    })
  })

  it('shows a mixed store and a conflict as a chip beside the recorded model, never a tinted name', () => {
    const mixed = cells(healthReport({ embedder: { ...healthReport().embedder!, mixed: true, needs_switch: true } }))
    expect(cell(mixed, 'embedder')).toMatchObject({ value: 'hs-embed-1', chip: { tone: 'warn', word: 'mixed' } })
    const conflict = cells(
      healthReport({ embedder: { ...healthReport().embedder!, conflict: { live: OLD_EMBEDDER_ID } } }),
    )
    expect(cell(conflict, 'embedder')).toMatchObject({
      value: 'hs-embed-1',
      helper: 'the engine is set to run on BAAI/bge-small-en-v1.5',
      chip: { tone: 'warn', word: 'conflict' },
    })
  })

  it('reads the reranker and the decision model from the models read', () => {
    const list = cells(healthReport())
    expect(cell(list, 'reranker')).toMatchObject({ value: 'hs-rerank-1', helper: 'local provider' })
    expect(cell(list, 'decision')).toMatchObject({ value: 'Off', chip: null })
    const on = cells(healthReport(), GRAPH, {
      ...modelsAnswer(),
      decision: { enabled: true, model: 'decider', reachable: true },
    })
    expect(cell(on, 'decision')).toMatchObject({ value: 'decider', chip: { tone: 'ok', word: 'reachable' } })
  })

  it('reports nothing it was not told', () => {
    const list = cells(healthReport({ extraction: null, embedder: null }), undefined, {})
    for (const id of ['extraction', 'embedder', 'reranker', 'decision']) {
      expect(cell(list, id)).toMatchObject({ value: null, chip: null })
    }
  })
})

describe('the forced mock, one fixture per step that can be first', () => {
  const read = (state: MockMemoryHealthState) => {
    const view = readHealth({ data: memoryHealthFixture(state) as never })
    if (view.kind !== 'ready') throw new Error(`${state} is not a report`)
    return { report: view.report, path: buildPath(view.report) }
  }
  const states = (path: PathStep[]) => path.map((s) => `${s.id}:${s.state}`)

  it('attention: extraction is current, the queue and knowledge wait on it in turn', () => {
    const { report, path } = read('attention')
    expect(states(path)).toEqual(['extraction:current', 'queue:waiting', 'embedder:clear', 'knowledge:waiting'])
    expect(healthVerdict(report, path).lead).toBe('3 things need you')
    expect(byId(path, 'queue').waitsOn).toBe('Waiting on step 1: storing memories')
    expect(byId(path, 'knowledge').waitsOn).toBe('Waiting on step 2: the queue')
    expect(byId(path, 'extraction').errors.length).toBeGreaterThan(0)
  })

  it('queue: the queue is current, alone', () => {
    const { report, path } = read('queue')
    expect(states(path)).toEqual(['extraction:clear', 'queue:current', 'embedder:clear', 'knowledge:clear'])
    expect(healthVerdict(report, path)).toMatchObject({ lead: '1 thing needs you', open: 1 })
    expect(bodyOf(byId(path, 'queue'))).toMatch(/^\d+ operations have failed and \d+ are waiting, the oldest for \d+ h\./)
  })

  it('embedder-mixed and embedder-conflict: the embedder is current, alone', () => {
    const mixed = read('embedder-mixed').path
    expect(states(mixed)).toEqual(['extraction:clear', 'queue:clear', 'embedder:current', 'knowledge:clear'])
    expect(byId(mixed, 'embedder').command!.command).toBe('hal0 memory embedder switch hindsight-ai/hs-embed-1')
    const conflict = read('embedder-conflict').path
    expect(states(conflict)).toEqual(['extraction:clear', 'queue:clear', 'embedder:current', 'knowledge:clear'])
    expect(bodyOf(byId(conflict, 'embedder'))).toContain('the running engine is on BAAI/bge-small-en-v1.5')
    expect(byId(conflict, 'embedder').command!.command).toBe('hal0 doctor')
  })

  it('knowledge: knowledge is current, alone, and only stale', () => {
    const { path } = read('knowledge')
    expect(states(path)).toEqual(['extraction:clear', 'queue:clear', 'embedder:clear', 'knowledge:current'])
    expect(bodyOf(byId(path, 'knowledge'))).toBe(
      '5 knowledge pages and 6 mental models are stale: behind the facts they were built from. No refresh has failed.',
    )
  })

  it('clear: nothing is open and the verdict says working', () => {
    const { report, path } = read('clear')
    expect(path.every((s) => s.state === 'clear')).toBe(true)
    expect(healthVerdict(report, path)).toMatchObject({ lead: 'Nothing needs attention', chip: { tone: 'ok', word: 'working' } })
  })

  it('partial: nothing is open, and the verdict says what was not read', () => {
    const { report, path } = read('partial')
    expect(report.partial).toEqual(['queue', 'banks'])
    expect(path.every((s) => s.state === 'clear')).toBe(true)
    expect(healthVerdict(report, path)).toMatchObject({
      lead: 'Nothing needs attention in what was read',
      qualifier: PARTIAL_BANKS_NOTE,
      chip: null,
    })
    expect(byId(path, 'queue').note).toBe(PARTIAL_BANKS_NOTE)
    expect(banksNote(report)).toBe(PARTIAL_BANKS_NOTE)
  })
})

describe('readableError', () => {
  it('turns the route placeholders into words a person reads', () => {
    expect(readableError('Task exceeded the <n>s wall-clock limit')).toBe('Task exceeded the Ns wall-clock limit')
    expect(readableError('HTTP 503 from <host> for <id>')).toBe('HTTP 503 from a host for ID')
    expect(readableError('Retried <n> times, <n> left')).toBe('Retried N times, N left')
  })

  it('leaves other angle-bracket text alone', () => {
    expect(readableError('expected <list> got <none>')).toBe('expected <list> got <none>')
  })

  it('is applied to the extraction step errors in the path', () => {
    const report = healthReport({
      steps: pathSteps({
        extraction: { reason: 'extraction_recent_failures', figures: { failures: 3, window_s: 21600 } },
      }),
      extraction: {
        ...healthReport().extraction!,
        failures_6h: 3,
        top_errors: [{ message: 'Task exceeded the <n>s wall-clock limit', count: 3 }],
      },
    })
    const extraction = buildPath(report).find((step) => step.id === 'extraction')!
    expect(extraction.errors.map((group) => group.message)).toEqual(['Task exceeded the Ns wall-clock limit'])
  })
})

describe('refreshFailed', () => {
  const model = (over: Record<string, string | null> = {}) =>
    ({ last_refreshed_at: '2026-11-05T08:00:00Z', last_refresh_failed_at: null, ...over }) as never

  it('is a fault only when the failure is later than the last refresh that worked', () => {
    expect(refreshFailed(model())).toBe(false)
    expect(refreshFailed(model({ last_refresh_failed_at: '2026-11-04T08:00:00Z' }))).toBe(false)
    expect(refreshFailed(model({ last_refresh_failed_at: '2026-11-06T08:00:00Z' }))).toBe(true)
    expect(refreshFailed(model({ last_refreshed_at: null, last_refresh_failed_at: '2026-11-06T08:00:00Z' }))).toBe(true)
  })
})
