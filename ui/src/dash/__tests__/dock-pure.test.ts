import { describe, expect, it } from 'vitest'
import { entryKey } from '@/api/hooks/logRing.js'
import type { ActivityRecord } from '@/api/hooks/useActivity'
import type { JournalEntry } from '@/api/hooks/useLogs'
import {
  DOCK_FILTERS_DEFAULT,
  TICKER_ROWS,
  activityToEvents,
  exportFilters,
  filterEvents,
  journalToEvents,
  mergeFeeds,
  newRows,
  actorLabel,
  segmentActions,
  sevOf,
  shortTime,
  slotOfEvent,
  sourceGroup,
  sourceGroups,
} from '@/dash/dock/dock-pure'

function entry(over: Partial<JournalEntry> = {}): JournalEntry {
  return { id: 1, ts: '2026-10-26T15:00:00Z', source: 'hal0', level: 'info', msg: 'm', ...over }
}

function rec(over: Partial<ActivityRecord> = {}): ActivityRecord {
  return {
    id: 1,
    ts: '2026-10-26T15:00:00Z',
    kind: 'action',
    category: 'system',
    action: 'system.noop',
    target: null,
    actor: 'dashboard',
    severity: 'info',
    outcome: null,
    message: 'm',
    before: null,
    after: null,
    error: null,
    duration_ms: null,
    request_id: null,
    ...over,
  }
}

// Six events spanning both streams, oldest first.
const journal: JournalEntry[] = [
  entry({ id: 10, ts: '2026-10-26T15:00:00Z', source: 'slot:brain', level: 'info', msg: 'brain loaded', slot: 'brain' }),
  entry({ id: 11, ts: '2026-10-26T15:00:01Z', source: 'comfyui', level: 'warn', msg: 'queue is deep' }),
  entry({ id: 12, ts: '2026-10-26T15:00:02Z', source: 'hal0', level: 'error', msg: 'Boom failure' }),
  entry({ id: 13, ts: '2026-10-26T15:00:03Z', source: 'slot:img', level: 'info', msg: 'img idle', slot: null }),
]
const activity: ActivityRecord[] = [
  rec({ id: 20, ts: '2026-10-26T15:00:04Z', category: 'slot', target: 'brain/main', severity: 'ok', message: 'slot.start brain' }),
  rec({ id: 21, ts: '2026-10-26T15:00:05Z', category: 'system', target: null, severity: 'warn', message: 'system.restart' }),
]
const events = mergeFeeds(journal, activity)
// A journal fixture's event id: keyed by content, not by the server's row id.
const jid = (i: number) => `j:${entryKey(journal[i])}`

describe('dock-pure', () => {
  it('exposes the ticker row count', () => {
    expect(TICKER_ROWS).toBe(2)
  })

  it('journal events keep source fallback and level, keyed by content', () => {
    const out = journalToEvents([entry({ id: 5, source: '', level: 'warn', msg: 'x' })])
    expect(out[0]).toMatchObject({ source: 'hal0', level: 'warn', stream: 'j', record: null })
    expect(out[0].id).toBe('j:2026-10-26T15:00:00Z||x')
  })

  it('journal ids restart after an API restart: same id with different content gets a different event id', () => {
    const [a, b, a2] = journalToEvents([
      entry({ id: 1, ts: '2026-10-26T15:00:00Z', msg: 'before restart' }),
      entry({ id: 1, ts: '2026-10-26T15:09:00Z', msg: 'after restart' }),
      entry({ id: 7, ts: '2026-10-26T15:00:00Z', msg: 'before restart' }),
    ])
    expect(a.id).not.toBe(b.id)
    // Identical content is the same event, whatever id the server assigned.
    expect(a.id).toBe(a2.id)
  })

  it('activity events are only kind=action', () => {
    const out = activityToEvents([rec({ id: 1, kind: 'event' }), rec({ id: 2, kind: 'action', message: 'slot.start brain' })])
    expect(out.map((e) => e.id)).toEqual(['a:2'])
    expect(out[0]).toMatchObject({ stream: 'a', source: 'system', msg: 'slot.start brain' })
    expect(out[0].record?.id).toBe(2)
  })

  it('t is the parsed ts and 0 when unparseable', () => {
    const out = journalToEvents([entry({ ts: '2026-10-26T15:00:00Z' }), entry({ id: 2, ts: 'nope' })])
    expect(out[0].t).toBe(Date.parse('2026-10-26T15:00:00Z'))
    expect(out[1].t).toBe(0)
  })

  it('merges ascending by time and ties keep input order', () => {
    const j = [entry({ id: 99, ts: '2026-10-26T15:00:01Z', msg: 'j1' })]
    const a = [
      rec({ id: 7, kind: 'action', ts: '2026-10-26T15:00:01Z', message: 'a1' }),
      rec({ id: 8, kind: 'action', ts: '2026-10-26T15:00:00Z', message: 'a0' }),
    ]
    expect(mergeFeeds(j, a).map((e) => e.msg)).toEqual(['a0', 'j1', 'a1'])
    expect(mergeFeeds(j, a).map((e) => e.msg)).toEqual(mergeFeeds(j, a).map((e) => e.msg))
  })

  it('sourceGroup collapses at the first colon and defaults to hal0', () => {
    expect(sourceGroup('slot:brain')).toBe('slot')
    expect(sourceGroup('system')).toBe('system')
    expect(sourceGroup('')).toBe('hal0')
  })

  it('source groups collapse slot:* and start with all', () => {
    expect(sourceGroups(events)).toEqual(['all', 'comfyui', 'hal0', 'slot', 'system'])
  })

  it('default filters keep every event', () => {
    expect(filterEvents(events, DOCK_FILTERS_DEFAULT)).toHaveLength(6)
  })

  it('filters by stream', () => {
    expect(filterEvents(events, { ...DOCK_FILTERS_DEFAULT, stream: 'a' }).map((e) => e.id)).toEqual(['a:20', 'a:21'])
    expect(filterEvents(events, { ...DOCK_FILTERS_DEFAULT, stream: 'j' })).toHaveLength(4)
  })

  it('filters by exact severity', () => {
    expect(filterEvents(events, { ...DOCK_FILTERS_DEFAULT, severity: 'warn' }).map((e) => e.id)).toEqual([jid(1), 'a:21'])
    expect(filterEvents(events, { ...DOCK_FILTERS_DEFAULT, severity: 'ok' }).map((e) => e.id)).toEqual(['a:20'])
  })

  it('filters by source group', () => {
    expect(filterEvents(events, { ...DOCK_FILTERS_DEFAULT, source: 'slot' }).map((e) => e.id)).toEqual([jid(0), jid(3), 'a:20'])
    expect(filterEvents(events, { ...DOCK_FILTERS_DEFAULT, source: 'comfyui' }).map((e) => e.id)).toEqual([jid(1)])
  })

  it('filters by slot across both streams', () => {
    expect(filterEvents(events, { ...DOCK_FILTERS_DEFAULT, slot: 'brain' }).map((e) => e.id)).toEqual([jid(0), 'a:20'])
    expect(filterEvents(events, { ...DOCK_FILTERS_DEFAULT, slot: 'img' }).map((e) => e.id)).toEqual([jid(3)])
  })

  it('slot filter matches a slot field without a slot: source', () => {
    const e = journalToEvents([entry({ id: 1, source: 'hal0', slot: 'brain' })])
    expect(filterEvents(e, { ...DOCK_FILTERS_DEFAULT, slot: 'brain' })).toHaveLength(1)
  })

  it('search is case-insensitive over msg and source; empty matches all', () => {
    expect(filterEvents(events, { ...DOCK_FILTERS_DEFAULT, q: 'BOOM' }).map((e) => e.id)).toEqual([jid(2)])
    expect(filterEvents(events, { ...DOCK_FILTERS_DEFAULT, q: 'comfy' }).map((e) => e.id)).toEqual([jid(1)])
    expect(filterEvents(events, { ...DOCK_FILTERS_DEFAULT, q: '' })).toHaveLength(6)
  })

  it('segment actions apply the pane filter before the slice', () => {
    expect(segmentActions(['open_editor', 'dismiss'], { editorUrl: null })).toEqual(['dismiss'])
    expect(segmentActions(['cancel', 'keep', 'stop'], { onCancel: null })).toEqual(['keep', 'stop'])
    expect(segmentActions(['hold'], { editorUrl: 'http://x' })).toEqual(['hold'])
    expect(segmentActions(['hold', 'keep', 'stop'], {})).toEqual(['hold', 'keep'])
  })

  it('export filters carry severity, a single activity category and the slot term', () => {
    expect(exportFilters({ ...DOCK_FILTERS_DEFAULT, severity: 'warn', source: 'slot', slot: 'brain' })).toEqual({
      severity: 'warn',
      category: 'slot',
      search: 'brain',
    })
    expect(exportFilters({ ...DOCK_FILTERS_DEFAULT, source: 'comfyui' })).toEqual({})
    expect(exportFilters(DOCK_FILTERS_DEFAULT)).toEqual({})
  })
})

describe('slotOfEvent and the moved activity helpers', () => {
  it('reads a journal line slot from the slot field, then from a slot: source', () => {
    const [a, b, c] = journalToEvents([
      entry({ id: 1, source: 'hal0', slot: 'brain' }),
      entry({ id: 2, source: 'slot:coder' }),
      entry({ id: 3, source: 'model:x' }),
    ])
    expect(slotOfEvent(a)).toBe('brain')
    expect(slotOfEvent(b)).toBe('coder')
    expect(slotOfEvent(c)).toBeNull()
  })

  it('reads an activity action slot from the target up to the first slash', () => {
    const [a, b, c] = activityToEvents([
      rec({ id: 1, target: 'brain' }),
      rec({ id: 2, target: 'brain/config' }),
      rec({ id: 3, target: null }),
    ])
    expect(slotOfEvent(a)).toBe('brain')
    expect(slotOfEvent(b)).toBe('brain')
    expect(slotOfEvent(c)).toBeNull()
  })

  it('sevOf normalises, shortTime falls back, actorLabel names mcp agents', () => {
    expect(sevOf(rec({ severity: 'warn' }))).toBe('warn')
    expect(sevOf({ severity: 'weird' } as unknown as ActivityRecord)).toBe('info')
    expect(shortTime('')).toBe('')
    expect(shortTime('not a time at all, long')).toBe('not a time at all, long'.slice(11, 19))
    expect(actorLabel('mcp:hermes')).toBe('hermes (mcp)')
    expect(actorLabel('dashboard')).toBe('dashboard')
    expect(actorLabel('')).toBe('system')
  })
})


describe('newRows', () => {
  const evs = mergeFeeds([entry({ id: 1, msg: 'one' }), entry({ id: 2, msg: 'two' })], [rec({ id: 1 })])
  it('skips a stream that has not delivered its first batch', () => {
    expect(newRows(evs, new Set(), { j: true, a: false }).map((e) => e.id)).toEqual([evs[0].id, evs[1].id])
    expect(newRows(evs, new Set(), { j: false, a: false })).toEqual([])
  })
  it('skips seen ids', () => {
    expect(newRows(evs, new Set([evs[0].id, 'a:1']), { j: true, a: true }).map((e) => e.id)).toEqual([evs[1].id])
  })
})
