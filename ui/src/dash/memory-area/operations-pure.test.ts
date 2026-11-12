// The Operations tab's words and sums: the engine's statuses and types as plain words, error
// messages cut to a line, durations, page maths, the merge of several banks' lists, and what
// the health answer is read as.
import { describe, expect, it } from 'vitest'
import type { BankOperation } from '@/api/hooks/useHindsight'
import type { MemoryHealth, MemoryHealthQueue } from '@/api/hooks/useMemory'
import { memoryHealthFixture } from '@/api/mockFixtures'
import {
  BULK_CAP,
  ERROR_FULL_MAX,
  ERROR_LINE_MAX,
  MERGE_CAP,
  OPERATION_FILTERS,
  PAGE_SIZE,
  bankBreakdown,
  banksForFilter,
  bulkText,
  clampOffset,
  durationParts,
  filterStatus,
  groupErrors,
  lastCompletedAt,
  mergeOperationLists,
  nextTryText,
  operationStatus,
  operationType,
  pageWindow,
  plainError,
  queueFigures,
  queueIsClear,
  readQueueHealth,
  retriedText,
  shortError,
  spreadText,
  type BankList,
} from './operations-pure'

const health = (state: 'attention' | 'clear' | 'unreachable') =>
  memoryHealthFixture(state) as unknown as MemoryHealth

const op = (id: string, createdAt: string, over: Partial<BankOperation> = {}): BankOperation => ({
  id,
  task_type: 'batch_retain',
  status: 'pending',
  created_at: createdAt,
  error_message: null,
  retry_count: 0,
  ...over,
})

/** `n` rows, one a minute, the newest at `newest`. */
const run = (prefix: string, n: number, newest: string, total: number | null = n): BankList => ({
  bankId: prefix,
  total,
  operations: Array.from({ length: n }, (_, at) =>
    op(`${prefix}-${at}`, new Date(Date.parse(newest) - at * 60_000).toISOString()),
  ),
})

describe('status', () => {
  it('offers All, Waiting, Running, Failed and Done, in that order', () => {
    expect(OPERATION_FILTERS.map((f) => f.label)).toEqual(['All', 'Waiting', 'Running', 'Failed', 'Done'])
  })

  it("maps each filter to the engine's status, and All to none", () => {
    expect(filterStatus('all')).toBeUndefined()
    expect(filterStatus('waiting')).toBe('pending')
    expect(filterStatus('running')).toBe('processing')
    expect(filterStatus('failed')).toBe('failed')
    expect(filterStatus('done')).toBe('completed')
  })

  it.each([
    ['pending', 'waiting', 'neutral'],
    ['processing', 'running', 'info'],
    ['failed', 'failed', 'err'],
    ['completed', 'done', 'ok'],
    ['cancelled', 'stopped', 'neutral'],
    ['PENDING', 'waiting', 'neutral'],
  ])('%s reads as "%s"', (raw, word, tone) => {
    expect(operationStatus(raw)).toMatchObject({ word, tone })
  })

  it('keeps the words of a status it does not know, and never shows an empty chip', () => {
    expect(operationStatus('awaiting_children')).toEqual({ kind: 'other', word: 'awaiting children', tone: 'neutral' })
    expect(operationStatus(undefined).word).toBe('not reported')
    expect(operationStatus('').word).toBe('not reported')
  })
})

describe('type', () => {
  it.each([
    ['retain', 'Store a memory'],
    ['batch_retain', 'Store a batch'],
    ['file_convert_retain', 'Store a file'],
    ['consolidation', 'Consolidate'],
    ['refresh_mental_model', 'Refresh a mental model'],
    ['document_ingest', 'Store a document'],
  ])('%s reads as "%s", with the raw name kept for the title', (raw, label) => {
    expect(operationType({ task_type: raw })).toEqual({ label, raw })
  })

  it('prefers operation_type, and falls back to task_type', () => {
    expect(operationType({ operation_type: 'consolidation', task_type: 'retain' }).label).toBe('Consolidate')
    expect(operationType({ operation_type: '', task_type: 'retain' }).label).toBe('Store a memory')
  })

  it('turns a type it does not know into words, never an underscore', () => {
    expect(operationType({ task_type: 'webhook_delivery' })).toEqual({
      label: 'Webhook delivery',
      raw: 'webhook_delivery',
    })
    expect(operationType({ task_type: '' })).toEqual({ label: 'Operation', raw: '' })
  })

  it('says how often the engine retried, and nothing when it has not', () => {
    expect(retriedText(0)).toBeNull()
    expect(retriedText(undefined)).toBeNull()
    expect(retriedText(1)).toBe('retried once')
    expect(retriedText(3)).toBe('retried 3 times')
  })

  it('says when the next try is, relative to now', () => {
    const now = Date.parse('2026-11-05T12:00:00Z')
    expect(nextTryText(null, now)).toBeNull()
    expect(nextTryText('not a time', now)).toBeNull()
    expect(nextTryText('2026-11-05T12:12:00Z', now)).toBe('next try in 12 min')
    expect(nextTryText('2026-11-05T12:00:30Z', now)).toBe('next try in under 1 min')
    expect(nextTryText('2026-11-05T11:00:00Z', now)).toBe('next try due')
  })
})

describe('error messages', () => {
  it('answers null for no message', () => {
    expect(shortError(null)).toBeNull()
    expect(shortError('   ')).toBeNull()
    expect(shortError(42)).toBeNull()
  })

  it('leaves a short message whole, with nothing more to show', () => {
    expect(shortError('APIConnectionError: Connection error.')).toEqual({
      line: 'APIConnectionError: Connection error.',
      full: 'APIConnectionError: Connection error.',
      more: false,
    })
  })

  it('cuts a long message to one line that ends in an ellipsis, and keeps the whole of it', () => {
    const long = `Task exceeded the 14400s wall-clock limit for 'batch_retain' ${'x'.repeat(200)}`
    const short = shortError(long)!
    expect(short.line.length).toBe(ERROR_LINE_MAX)
    expect(short.line.endsWith('…')).toBe(true)
    expect(short.full).toBe(long)
    expect(short.more).toBe(true)
  })

  it('shows only the first line of a traceback, on one line', () => {
    const short = shortError('\n  Connection   refused\nTraceback (most recent call last):\n  File "x.py"')!
    expect(short.line).toBe('Connection refused')
    expect(short.more).toBe(true)
    expect(short.full).toContain('Traceback')
  })

  it('caps the whole message too', () => {
    const short = shortError('y'.repeat(ERROR_FULL_MAX * 2))!
    expect(short.full.length).toBe(ERROR_FULL_MAX)
  })

  it('groups messages that read the same once cut, most frequent first', () => {
    const base = 'z'.repeat(ERROR_LINE_MAX + 20)
    expect(
      groupErrors([
        { message: 'Connection error.', count: 3 },
        { message: `${base} tail one`, count: 4 },
        { message: `${base} tail two`, count: 5 },
        { message: '', count: 9 },
        { message: 'ignored', count: 0 },
        null,
      ]),
    ).toEqual([
      { message: `${base.slice(0, ERROR_LINE_MAX - 1)}…`, count: 9 },
      { message: 'Connection error.', count: 3 },
    ])
    expect(groupErrors(undefined)).toEqual([])
    expect(groupErrors({})).toEqual([])
  })

  it('turns a failed request into one sentence, and never shows a raw body', () => {
    expect(plainError(new Error('hindsight-api is not responding'))).toBe('hindsight-api is not responding.')
    expect(plainError('Already a sentence.')).toBe('Already a sentence.')
    expect(plainError(new Error('{"error":{"code":"x"}}'))).toBe('The engine did not accept the request.')
    expect(plainError(new Error('<html><body>502</body></html>'))).toBe('The engine did not accept the request.')
    expect(plainError(new Error(''), 'Could not do it.')).toBe('Could not do it.')
    expect(plainError(undefined)).toBe('The engine did not accept the request.')
    expect(plainError(new Error('a\nb'))).toBe('a.')
    expect(plainError(new Error('w'.repeat(500))).length).toBe(200)
  })
})

describe('durations', () => {
  it('splits a duration into its figure and its unit', () => {
    expect(durationParts(45)).toEqual({ value: 45, unit: 's' })
    expect(durationParts(12 * 60)).toEqual({ value: 12, unit: 'min' })
    expect(durationParts(6 * 3600 + 59 * 60)).toEqual({ value: 6, unit: 'h' })
    expect(durationParts(1500 * 86400)).toEqual({ value: 1500, unit: 'd' })
  })

  it('answers null for a length that was not reported', () => {
    expect(durationParts(null)).toBeNull()
    expect(durationParts(undefined)).toBeNull()
    expect(durationParts(-1)).toBeNull()
    expect(durationParts('6')).toBeNull()
  })
})

describe('paging', () => {
  it('describes the first, a middle and the last page', () => {
    expect(pageWindow(137, 25, 0)).toMatchObject({
      page: 1,
      pages: 6,
      from: 1,
      to: 25,
      hasPrev: false,
      hasNext: true,
      nextOffset: 25,
    })
    expect(pageWindow(137, 25, 50)).toMatchObject({
      page: 3,
      from: 51,
      to: 75,
      hasPrev: true,
      hasNext: true,
      prevOffset: 25,
      nextOffset: 75,
    })
    expect(pageWindow(137, 25, 125)).toMatchObject({ page: 6, from: 126, to: 137, hasPrev: true, hasNext: false })
  })

  it('is one empty page when there is nothing', () => {
    expect(pageWindow(0, 25, 0)).toMatchObject({ page: 1, pages: 1, from: 0, to: 0, hasPrev: false, hasNext: false })
  })

  it('has no next page when the list ends exactly on a page', () => {
    expect(pageWindow(50, 25, 25)).toMatchObject({ page: 2, pages: 2, from: 26, to: 50, hasNext: false })
  })

  it('falls back to the last page when the offset is past the end', () => {
    expect(clampOffset(26, 25, 50)).toBe(25)
    expect(clampOffset(25, 25, 25)).toBe(0)
    expect(clampOffset(0, 25, 75)).toBe(0)
    expect(clampOffset(137, 25, 60)).toBe(50)
    expect(pageWindow(26, 25, 500)).toMatchObject({ page: 2, from: 26, to: 26 })
  })

  it('uses 25 rows a page', () => {
    expect(PAGE_SIZE).toBe(25)
  })
})

describe('merging several banks', () => {
  it('orders rows from every bank by time, newest first, and names each row’s bank', () => {
    const merged = mergeOperationLists([
      { bankId: 'a', total: 2, operations: [op('a1', '2026-06-12T10:00:00Z'), op('a2', '2026-06-12T08:00:00Z')] },
      { bankId: 'b', total: 1, operations: [op('b1', '2026-06-12T09:00:00Z')] },
    ])
    expect(merged.rows.map((r) => `${r.bank}/${r.id}`)).toEqual(['a/a1', 'b/b1', 'a/a2'])
    expect(merged.total).toBe(3)
    expect(merged.capped).toBe(false)
  })

  it('keeps the order steady for rows queued at the same moment', () => {
    const at = '2026-06-12T10:00:00Z'
    const merged = mergeOperationLists([
      { bankId: 'b', total: 2, operations: [op('2', at), op('1', at)] },
      { bankId: 'a', total: 1, operations: [op('9', at)] },
    ])
    expect(merged.rows.map((r) => `${r.bank}/${r.id}`)).toEqual(['b/1', 'b/2', 'a/9'])
  })

  it('stops at the oldest row a bank with more was read back to, so nothing is missing in between', () => {
    // `busy` has 500 rows and sent its newest 3 (10:00, 09:59, 09:58). `quiet` sent all of its
    // rows, two of which are older than anything read from `busy`.
    const busy = run('busy', 3, '2026-06-12T10:00:00Z', 500)
    const quiet: BankList = {
      bankId: 'quiet',
      total: 3,
      operations: [
        op('q1', '2026-06-12T09:59:30Z'),
        op('q2', '2026-06-12T09:00:00Z'),
        op('q3', '2026-06-12T08:00:00Z'),
      ],
    }
    const merged = mergeOperationLists([busy, quiet], { perBank: 3 })
    expect(merged.rows.map((r) => r.id)).toEqual(['busy-0', 'q1', 'busy-1', 'busy-2'])
    expect(merged.total).toBe(503)
    expect(merged.capped).toBe(true)
  })

  it('holds no more than the cap, and says the list is capped', () => {
    const merged = mergeOperationLists([run('a', 50, '2026-06-12T10:00:00Z'), run('b', 50, '2026-06-12T10:00:30Z')], {
      cap: 30,
    })
    expect(merged.rows).toHaveLength(30)
    expect(merged.total).toBe(100)
    expect(merged.capped).toBe(true)
    expect(MERGE_CAP).toBe(200)
  })

  it('is not capped when every bank sent all it has', () => {
    const merged = mergeOperationLists([run('a', 4, '2026-06-12T10:00:00Z'), run('b', 0, '2026-06-12T10:00:00Z')])
    expect(merged).toMatchObject({ total: 4, capped: false })
    expect(merged.rows).toHaveLength(4)
  })

  it('does not claim a total when a bank sent a full page and no count', () => {
    const merged = mergeOperationLists([run('a', 3, '2026-06-12T10:00:00Z', null)], { perBank: 3 })
    expect(merged.total).toBeNull()
    expect(merged.capped).toBe(true)
    // A short answer with no count is the whole list.
    expect(mergeOperationLists([run('a', 2, '2026-06-12T10:00:00Z', null)], { perBank: 3 })).toMatchObject({
      total: 2,
      capped: false,
    })
  })

  it('puts a row with no readable time last instead of dropping it', () => {
    const merged = mergeOperationLists([
      { bankId: 'a', total: 2, operations: [op('ok', '2026-06-12T10:00:00Z'), op('odd', 'never')] },
    ])
    expect(merged.rows.map((r) => r.id)).toEqual(['ok', 'odd'])
  })

  it('asks only the banks a waiting or failed filter has rows in', () => {
    const byBank = [
      { bank_id: 'a', waiting: 4, failed: 0, oldest_waiting_s: 10 },
      { bank_id: 'b', waiting: 0, failed: 2, oldest_waiting_s: null },
      { bank_id: 'c', waiting: null, failed: null, oldest_waiting_s: null },
    ]
    const ids = ['a', 'b', 'c', 'unlisted']
    expect(banksForFilter('waiting', ids, byBank)).toEqual(['a', 'c', 'unlisted'])
    expect(banksForFilter('failed', ids, byBank)).toEqual(['b', 'c', 'unlisted'])
    // No figure says where running or finished rows are, so every bank is asked.
    expect(banksForFilter('running', ids, byBank)).toEqual(ids)
    expect(banksForFilter('all', ids, byBank)).toEqual(ids)
    expect(banksForFilter('failed', ids, null)).toEqual(ids)
  })

  it('finds when the newest finished operation finished', () => {
    expect(
      lastCompletedAt([
        {
          bankId: 'a',
          total: 2,
          operations: [
            op('1', '2026-06-12T08:00:00Z', { status: 'completed', updated_at: '2026-06-12T08:05:00Z' }),
            op('2', '2026-06-12T11:00:00Z', { status: 'failed', updated_at: '2026-06-12T11:05:00Z' }),
          ],
        },
        { bankId: 'b', total: 1, operations: [op('3', '2026-06-12T09:00:00Z', { status: 'completed' })] },
      ]),
    ).toBe('2026-06-12T09:00:00Z')
    expect(lastCompletedAt([{ bankId: 'a', total: 0, operations: [] }])).toBeNull()
  })
})

describe('the health answer', () => {
  it('is loading with no answer, and failed when the request failed', () => {
    expect(readQueueHealth({})).toEqual({ kind: 'loading' })
    expect(readQueueHealth({ isError: true })).toEqual({ kind: 'failed' })
  })

  it('reads a body that is not the route’s shape as failed, and does not throw', () => {
    expect(readQueueHealth({ data: {} as MemoryHealth })).toEqual({ kind: 'failed' })
    expect(readQueueHealth({ data: [] as unknown as MemoryHealth })).toEqual({ kind: 'failed' })
    expect(readQueueHealth({ data: 'nope' as unknown as MemoryHealth })).toEqual({ kind: 'failed' })
  })

  it('reads memory off and an engine with no queue', () => {
    const none = { checked_at: '2026-11-11T03:12:17+00:00', verdict: null, partial: [] as [], steps: [] as [] }
    const off: MemoryHealth = { ...none, enabled: false, reason: 'memory_disabled' }
    const unsupported: MemoryHealth = { ...none, enabled: true, reason: 'engine_unsupported' }
    expect(readQueueHealth({ data: off })).toEqual({ kind: 'off' })
    expect(readQueueHealth({ data: unsupported })).toEqual({ kind: 'unsupported' })
  })

  it('reads an engine that does not answer as unreachable', () => {
    expect(readQueueHealth({ data: health('unreachable') })).toEqual({ kind: 'unreachable' })
  })

  it('names the cause while extraction is the current step', () => {
    const read = readQueueHealth({ data: health('attention') })
    expect(read.kind).toBe('ready')
    if (read.kind !== 'ready') return
    expect(read.cause).toEqual({
      reason: 'recent_failures',
      slot: 'memory',
      failures: 7,
      window: '6 h',
      top: { message: 'APIConnectionError: Connection error.', count: 6 },
      otherMessages: 1,
    })
    expect(read.partial).toBe(false)
    expect(read.truncated).toBe(false)
    expect(read.queue?.waiting).toBe(100)
    expect(read.queue?.failed).toBe(10)
  })

  it('has no cause when extraction is clear, or is only waiting on another step', () => {
    const clear = readQueueHealth({ data: health('clear') })
    expect(clear.kind === 'ready' && clear.cause).toBeNull()

    const body = health('attention') as unknown as { steps: { id: string; state: string }[] }
    const waiting = {
      ...body,
      steps: body.steps.map((s) => (s.id === 'extraction' ? { ...s, state: 'waiting' } : s)),
    } as unknown as MemoryHealth
    const read = readQueueHealth({ data: waiting })
    expect(read.kind === 'ready' && read.cause).toBeNull()
  })

  it.each([
    ['extraction_slot_unresolved', 'slot_unresolved'],
    ['extraction_llm_unreachable', 'llm_unreachable'],
    ['something_new', null],
  ])('reads the reason code %s', (code, reason) => {
    const body = health('attention') as unknown as { steps: { id: string; reason_code: string }[] }
    const data = {
      ...body,
      steps: body.steps.map((s) => (s.id === 'extraction' ? { ...s, reason_code: code } : s)),
    } as unknown as MemoryHealth
    const read = readQueueHealth({ data })
    expect(read.kind === 'ready' && read.cause?.reason).toBe(reason)
  })

  it('says when the queue read was cut short', () => {
    const body = health('attention') as unknown as { queue: MemoryHealthQueue }
    const data = {
      ...body,
      partial: ['extraction', 'queue'],
      queue: { ...body.queue, truncated: true },
    } as unknown as MemoryHealth
    expect(readQueueHealth({ data })).toMatchObject({ kind: 'ready', partial: true, truncated: true })
    // Another section being partial says nothing about the queue.
    const other = { ...body, partial: ['knowledge'] } as unknown as MemoryHealth
    expect(readQueueHealth({ data: other })).toMatchObject({ partial: false })
  })
})

describe('figures', () => {
  const queue = (health('attention') as unknown as { queue: MemoryHealthQueue }).queue

  it('takes every bank’s figures from the health answer as they are', () => {
    expect(queueFigures(queue, null)).toEqual({
      waiting: 100,
      failed: 10,
      oldestWaitingS: 24480,
      consolidationPending: 2,
      consolidationFailed: 0,
    })
  })

  it('takes one bank’s figures from its row, and its consolidation figures from its stats', () => {
    expect(queueFigures(queue, 'big', { pending_consolidation: 5, failed_consolidation: 1 })).toEqual({
      waiting: 96,
      failed: 9,
      oldestWaitingS: 24480,
      consolidationPending: 5,
      consolidationFailed: 1,
    })
  })

  it('reports nothing, not zero, for a bank the answer does not list or a queue that is missing', () => {
    expect(queueFigures(queue, 'nope')).toEqual({
      waiting: null,
      failed: null,
      oldestWaitingS: null,
      consolidationPending: null,
      consolidationFailed: null,
    })
    expect(queueFigures(null, null).waiting).toBeNull()
  })

  it('is clear only when both figures were reported as zero', () => {
    expect(queueIsClear(queueFigures(queue, null))).toBe(false)
    expect(queueIsClear(queueFigures(queue, 'empty'))).toBe(true)
    expect(queueIsClear(queueFigures(null, null))).toBe(false)
  })

  it('lists the banks that hold something, most failed first, and counts the rest', () => {
    const { rows, quiet } = bankBreakdown(queue)
    expect(rows.map((r) => r.bank_id)).toEqual(['big', 'ingest', 'hermes', 'primary', 'scratch'])
    expect(rows[0]).toEqual({ bank_id: 'big', waiting: 96, failed: 9, oldest_waiting_s: 24480 })
    expect(quiet).toBe(1)
  })

  it('lists a bank whose figures were not reported, and does not count it as quiet', () => {
    const { rows, quiet } = bankBreakdown({
      ...queue,
      by_bank: [
        { bank_id: 'a', waiting: 0, failed: 0, oldest_waiting_s: null },
        { bank_id: 'b', waiting: null, failed: null, oldest_waiting_s: null },
      ],
    })
    expect(rows.map((r) => r.bank_id)).toEqual(['b'])
    expect(quiet).toBe(1)
    expect(bankBreakdown(null)).toEqual({ rows: [], quiet: 0 })
  })

  it('says where a figure sits', () => {
    expect(spreadText(queue, 'waiting')).toBe('in 5 banks')
    expect(spreadText(queue, 'failed')).toBe('in 2 banks')
    const one = { ...queue, by_bank: [{ bank_id: 'Hal0::Agents', waiting: 3, failed: 0, oldest_waiting_s: 1 }] }
    expect(spreadText(one, 'waiting')).toBe('all on Hal0::Agents')
    expect(spreadText(one, 'failed')).toBeNull()
    expect(spreadText(null, 'waiting')).toBeNull()
  })
})

describe('many at once', () => {
  it('says what a run is doing', () => {
    expect(bulkText({ phase: 'collecting', action: 'retry' })).toBe('Reading the failed operations.')
    expect(bulkText({ phase: 'running', action: 'retry', done: 11, total: 105 })).toBe('Retrying 12 of 105.')
    expect(bulkText({ phase: 'running', action: 'discard', done: 0, total: 1500 })).toBe('Discarding 1 of 1,500.')
  })

  it('says what a finished run did', () => {
    const base = { phase: 'finished', error: null, more: false } as const
    expect(bulkText({ ...base, action: 'retry', done: 105, total: 105 })).toBe('Queued 105 failed operations again.')
    expect(bulkText({ ...base, action: 'discard', done: 1, total: 1 })).toBe('Discarded 1 failed operation.')
    expect(bulkText({ ...base, action: 'discard', done: 0, total: 0 })).toBe(
      'Nothing to discard: no failed operation was found.',
    )
    expect(bulkText({ ...base, action: 'discard', done: BULK_CAP, total: BULK_CAP, more: true })).toBe(
      'Discarded 1,000 failed operations. More are left: run it again for the rest.',
    )
  })

  it('reports where it stopped, how many were left, and why', () => {
    expect(
      bulkText({
        phase: 'finished',
        action: 'retry',
        done: 12,
        total: 105,
        error: 'hindsight-api is not responding',
        more: false,
      }),
    ).toBe('Stopped after 12 of 105. 93 operations were not retried. hindsight-api is not responding')
    expect(
      bulkText({ phase: 'finished', action: 'discard', done: 0, total: 1, error: 'No.', more: false }),
    ).toBe('Stopped after 0 of 1. 1 operation was not discarded. No.')
    // The failed operations could not even be read.
    expect(
      bulkText({ phase: 'finished', action: 'retry', done: 0, total: 0, error: 'Could not read them.', more: false }),
    ).toBe('Could not read them.')
  })
})
