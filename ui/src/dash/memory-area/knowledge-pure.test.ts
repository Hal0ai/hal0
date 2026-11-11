// Memory ▸ Knowledge, the logic: telling pages from mental models, counting what is stale and
// what failed, joining a tree node to its model, the tree's rows and keys, reading a page's
// text, a model's history, a refresh setting, and the loop behind "Refresh all stale".
import { describe, expect, it, vi } from 'vitest'
import type { Directive, KnowledgeNode, KnowledgeTree } from '@/api/hooks/useHindsight'
import {
  EMPTY_DIRECTIVE,
  WATCH_LIMIT_MS,
  cronWords,
  directiveBody,
  directiveDraft,
  groupChip,
  groupHelper,
  historyEntries,
  intervalLabel,
  intervalOptions,
  intervalSeconds,
  lastSuccessAt,
  latestFailureReason,
  listStatus,
  orderDirectives,
  pageBody,
  parseTags,
  refreshAll,
  resolveSelection,
  shortError,
  sortSiblings,
  splitKnowledge,
  staleTargets,
  statusCounts,
  stripFrontMatter,
  treeKeyAction,
  treeRows,
  triggerWords,
  watchOutcome,
  type KnowledgeModel,
  type RefreshTarget,
} from './knowledge-pure'

function node(over: Partial<KnowledgeNode> & { id: string }): KnowledgeNode {
  return {
    kind: 'page',
    name: over.id,
    parent_id: null,
    children: [],
    description: null,
    tags: [],
    managed: false,
    mental_model_id: null,
    timestamp: null,
    trigger: null,
    is_stale: false,
    last_refresh_failed_at: null,
    ...over,
  }
}

const page = (id: string, modelId: string | null, over: Partial<KnowledgeNode> = {}) =>
  node({ id, mental_model_id: modelId, ...over })
const folder = (id: string, children: KnowledgeNode[], over: Partial<KnowledgeNode> = {}) =>
  node({ id, kind: 'folder', children: children.map((c) => ({ ...c, parent_id: id })), ...over })

function model(over: Partial<KnowledgeModel> & { id: string }): KnowledgeModel {
  return {
    name: over.id,
    source_query: `about ${over.id}?`,
    content: `text of ${over.id}`,
    tags: [],
    is_stale: false,
    last_refreshed_at: '2026-11-05T08:00:00+00:00',
    last_refresh_failed_at: null,
    ...over,
  }
}

const T1 = '2026-11-01T08:00:00+00:00'
const T2 = '2026-11-05T08:00:00+00:00'
const T3 = '2026-11-09T08:00:00+00:00'

describe('splitKnowledge', () => {
  const tree: KnowledgeTree = {
    roots: [
      page('kb-root', 'mm-root', { name: 'Zed at the root' }),
      folder('f-box', [page('kb-b', 'mm-b', { name: 'Beta' }), page('kb-a', 'mm-a', { name: 'alpha' })], {
        name: 'The box',
      }),
    ],
  }
  const models = [
    model({ id: 'mm-root', is_knowledge_page: true }),
    model({ id: 'mm-a', is_knowledge_page: true }),
    model({ id: 'mm-b', is_knowledge_page: true }),
    model({ id: 'mm-plain-2', name: 'Zebra', is_knowledge_page: false }),
    model({ id: 'mm-plain-1', name: 'apple', is_knowledge_page: false }),
  ]

  it('lists every model once: as a page or as a mental model, never both', () => {
    const split = splitKnowledge(tree, models)
    expect(split.pages.map((p) => p.modelId).sort()).toEqual(['mm-a', 'mm-b', 'mm-root'])
    expect(split.models.map((m) => m.id).sort()).toEqual(['mm-plain-1', 'mm-plain-2'])
    expect(split.pages.length + split.models.length).toBe(models.length)
  })

  it('takes a model for a page when the tree names it, even if the list does not mark it', () => {
    const unmarked = models.map((m) => ({ ...m, is_knowledge_page: undefined }))
    expect(splitKnowledge(tree, unmarked).models.map((m) => m.id).sort()).toEqual(['mm-plain-1', 'mm-plain-2'])
  })

  it('takes a model for a page when the list marks it, even if the tree did not load', () => {
    expect(splitKnowledge(undefined, models).models.map((m) => m.id).sort()).toEqual(['mm-plain-1', 'mm-plain-2'])
    expect(splitKnowledge(undefined, models).pages).toEqual([])
  })

  it('orders pages as the tree shows them: folders first, then by name whatever the case', () => {
    expect(splitKnowledge(tree, models).pages.map((p) => p.name)).toEqual(['alpha', 'Beta', 'Zed at the root'])
  })

  it('orders mental models by name whatever the case', () => {
    expect(splitKnowledge(tree, models).models.map((m) => m.name)).toEqual(['apple', 'Zebra'])
  })

  it('reads when a page was last refreshed from its model, since the node does not say', () => {
    const split = splitKnowledge({ roots: [page('kb-1', 'mm-1')] }, [model({ id: 'mm-1', last_refreshed_at: T2 })])
    expect(split.pages[0].lastRefreshedAt).toBe(T2)
    // A page whose model is not in the list has no known refresh time: null, not a guess.
    expect(splitKnowledge({ roots: [page('kb-1', 'mm-gone')] }, []).pages[0].lastRefreshedAt).toBeNull()
  })

  it('reads the question from the node, and from the model when the node has none', () => {
    const models1 = [model({ id: 'mm-1', source_query: 'from the model?' })]
    expect(splitKnowledge({ roots: [page('kb-1', 'mm-1', { description: 'from the node?' })] }, models1).pages[0].question).toBe(
      'from the node?',
    )
    expect(splitKnowledge({ roots: [page('kb-1', 'mm-1')] }, models1).pages[0].question).toBe('from the model?')
  })

  it('carries the trigger from the model, where the engine keeps it', () => {
    const trigger = { refresh_cron: '0 */4 * * *' }
    expect(splitKnowledge({ roots: [page('kb-1', 'mm-1')] }, [model({ id: 'mm-1', trigger })]).pages[0].trigger).toBe(
      trigger,
    )
  })

  it('is empty for a bank with nothing', () => {
    expect(splitKnowledge({ roots: [] }, [])).toEqual({ pages: [], models: [] })
    expect(splitKnowledge(null, null)).toEqual({ pages: [], models: [] })
  })
})

describe('stale and failed', () => {
  it('marks a page stale from its node or its model', () => {
    const split = splitKnowledge(
      { roots: [page('kb-1', 'mm-1', { is_stale: true }), page('kb-2', 'mm-2'), page('kb-3', 'mm-3')] },
      [model({ id: 'mm-1' }), model({ id: 'mm-2', is_stale: true }), model({ id: 'mm-3' })],
    )
    expect(split.pages.map((p) => p.stale)).toEqual([true, true, false])
  })

  it('counts a failed refresh only while it is later than the last one that worked', () => {
    const split = splitKnowledge({ roots: [] }, [
      model({ id: 'a', last_refreshed_at: T1, last_refresh_failed_at: T2 }),
      model({ id: 'b', last_refreshed_at: T2, last_refresh_failed_at: T1 }),
      model({ id: 'c', last_refreshed_at: null, last_refresh_failed_at: T1 }),
      model({ id: 'd', last_refreshed_at: T1, last_refresh_failed_at: null }),
    ])
    expect(split.models.map((m) => [m.id, m.failed])).toEqual([
      ['a', true],
      ['b', false],
      ['c', true],
      ['d', false],
    ])
  })

  it("joins a page's failure, which is on its node, to its last success, which is on its model", () => {
    const failedAfter = splitKnowledge({ roots: [page('kb-1', 'mm-1', { last_refresh_failed_at: T3 })] }, [
      model({ id: 'mm-1', last_refreshed_at: T2 }),
    ]).pages[0]
    expect(failedAfter).toMatchObject({ failed: true, lastFailedAt: T3, lastRefreshedAt: T2 })
    const failedBefore = splitKnowledge({ roots: [page('kb-1', 'mm-1', { last_refresh_failed_at: T1 })] }, [
      model({ id: 'mm-1', last_refreshed_at: T2 }),
    ]).pages[0]
    expect(failedBefore.failed).toBe(false)
  })

  it('counts stale and failed separately: a row can be both', () => {
    const split = splitKnowledge({ roots: [] }, [
      model({ id: 'a', is_stale: true, last_refreshed_at: T1, last_refresh_failed_at: T2 }),
      model({ id: 'b', is_stale: true }),
      model({ id: 'c' }),
    ])
    expect(statusCounts(split.models)).toEqual({ total: 3, stale: 2, failed: 1 })
    expect(statusCounts([])).toEqual({ total: 0, stale: 0, failed: 0 })
  })

  it('finds the most recent refresh that worked, and none when none has', () => {
    const rows = splitKnowledge({ roots: [] }, [
      model({ id: 'a', last_refreshed_at: T1 }),
      model({ id: 'b', last_refreshed_at: T3 }),
      model({ id: 'c', last_refreshed_at: null }),
    ]).models
    expect(lastSuccessAt(rows)).toBe(T3)
    expect(lastSuccessAt(rows.filter((r) => r.id === 'c'))).toBeNull()
    expect(lastSuccessAt([])).toBeNull()
  })
})

describe('summary words', () => {
  it('gives a group one chip: failing over stale over fresh, and none when it is empty', () => {
    expect(groupChip({ total: 0, stale: 0, failed: 0 })).toBeNull()
    expect(groupChip({ total: 3, stale: 0, failed: 0 })).toEqual({ tone: 'ok', word: 'fresh' })
    expect(groupChip({ total: 3, stale: 2, failed: 0 })).toEqual({ tone: 'warn', word: 'stale' })
    expect(groupChip({ total: 3, stale: 2, failed: 1 })).toEqual({ tone: 'err', word: 'failing' })
  })

  it('says how many are stale and how many failed, with thousands separated', () => {
    expect(groupHelper({ total: 0, stale: 0, failed: 0 })).toBeNull()
    expect(groupHelper({ total: 3, stale: 0, failed: 0 })).toBe('all fresh')
    expect(groupHelper({ total: 1, stale: 0, failed: 0 })).toBe('fresh')
    expect(groupHelper({ total: 29, stale: 29, failed: 0 })).toBe('29 of 29 stale')
    expect(groupHelper({ total: 29, stale: 3, failed: 1 })).toBe('3 of 29 stale · 1 failed its last refresh')
    expect(groupHelper({ total: 1200, stale: 1100, failed: 31 })).toBe(
      '1,100 of 1,200 stale · 31 failed their last refresh',
    )
  })

  it('says a status every row shares once, in the header, and leaves it off the rows', () => {
    expect(listStatus({ total: 29, stale: 29, failed: 0 }, 'page', 'pages')).toEqual({
      meta: '29 pages, all stale',
      rowStale: false,
      rowFailed: false,
    })
    expect(listStatus({ total: 3, stale: 0, failed: 0 }, 'page', 'pages').meta).toBe('3 pages, all fresh')
    expect(listStatus({ total: 1, stale: 1, failed: 1 }, 'model', 'models')).toEqual({
      meta: '1 model, stale, last refresh failed',
      rowStale: false,
      rowFailed: false,
    })
  })

  it('puts a chip on the rows only where they differ', () => {
    expect(listStatus({ total: 3, stale: 1, failed: 1 }, 'page', 'pages')).toEqual({
      meta: '3 pages, 1 stale, 1 failed',
      rowStale: true,
      rowFailed: true,
    })
    expect(listStatus({ total: 5, stale: 5, failed: 2 }, 'page', 'pages')).toEqual({
      meta: '5 pages, all stale, 2 failed',
      rowStale: false,
      rowFailed: true,
    })
    expect(listStatus({ total: 0, stale: 0, failed: 0 }, 'page', 'pages').meta).toBe('0 pages')
  })
})

describe('the tree', () => {
  const tree: KnowledgeTree = {
    roots: [
      page('kb-root', 'm0', { name: 'Root page' }),
      folder('f2', [page('kb-3', 'm3', { name: 'Three' })], { name: 'Second folder' }),
      folder(
        'f1',
        [page('kb-2', 'm2', { name: 'Two' }), folder('f1a', [page('kb-1', 'm1', { name: 'One' })], { name: 'Inner' })],
        { name: 'First folder' },
      ),
    ],
  }
  const none = new Set<string>()
  const ids = (collapsed: Set<string> = none) => treeRows(tree, collapsed).map((r) => r.id)

  it('sorts folders before pages, each by name', () => {
    expect(sortSiblings(tree.roots).map((n) => n.id)).toEqual(['f1', 'f2', 'kb-root'])
  })

  it('shows every node of an open tree, depth first', () => {
    expect(ids()).toEqual(['f1', 'f1a', 'kb-1', 'kb-2', 'f2', 'kb-3', 'kb-root'])
  })

  it('hides what is under a closed folder', () => {
    expect(ids(new Set(['f1']))).toEqual(['f1', 'f2', 'kb-3', 'kb-root'])
    expect(ids(new Set(['f1a']))).toEqual(['f1', 'f1a', 'kb-2', 'f2', 'kb-3', 'kb-root'])
  })

  it('places each row for assistive tech: level, position, set size, parent', () => {
    const rows = treeRows(tree, none)
    const row = (id: string) => rows.find((r) => r.id === id)!
    expect(row('f1')).toMatchObject({ kind: 'folder', level: 1, posInSet: 1, setSize: 3, parentId: null, expanded: true })
    expect(row('kb-root')).toMatchObject({ kind: 'page', level: 1, posInSet: 3, setSize: 3, expanded: false })
    expect(row('kb-1')).toMatchObject({ level: 3, posInSet: 1, setSize: 1, parentId: 'f1a' })
    expect(row('kb-2')).toMatchObject({ level: 2, posInSet: 2, setSize: 2, parentId: 'f1' })
  })

  it('counts the pages anywhere under a folder', () => {
    const rows = treeRows(tree, new Set(['f1']))
    expect(rows.find((r) => r.id === 'f1')).toMatchObject({ pageCount: 2, expanded: false })
    expect(rows.find((r) => r.id === 'f2')!.pageCount).toBe(1)
  })

  describe('keys', () => {
    const rows = treeRows(tree, none)
    const key = (id: string | null, k: string, list = rows) => treeKeyAction(list, id, k)

    it('moves through the visible rows with Up and Down, and stops at the ends', () => {
      expect(key('f1', 'ArrowDown')).toEqual({ type: 'focus', id: 'f1a' })
      expect(key('kb-2', 'ArrowUp')).toEqual({ type: 'focus', id: 'kb-1' })
      expect(key('kb-root', 'ArrowDown')).toBeNull()
      expect(key('f1', 'ArrowUp')).toBeNull()
    })

    it('jumps to the ends with Home and End', () => {
      expect(key('kb-2', 'Home')).toEqual({ type: 'focus', id: 'f1' })
      expect(key('kb-2', 'End')).toEqual({ type: 'focus', id: 'kb-root' })
      expect(key('f1', 'Home')).toBeNull()
    })

    it('opens a closed folder with Right, then steps into it', () => {
      const closed = treeRows(tree, new Set(['f1']))
      expect(key('f1', 'ArrowRight', closed)).toEqual({ type: 'toggle', id: 'f1' })
      expect(key('f1', 'ArrowRight')).toEqual({ type: 'focus', id: 'f1a' })
      expect(key('kb-1', 'ArrowRight')).toBeNull()
    })

    it('closes an open folder with Left, and steps out of a page to its folder', () => {
      expect(key('f1', 'ArrowLeft')).toEqual({ type: 'toggle', id: 'f1' })
      expect(key('kb-1', 'ArrowLeft')).toEqual({ type: 'focus', id: 'f1a' })
      expect(key('kb-root', 'ArrowLeft')).toBeNull()
      const closed = treeRows(tree, new Set(['f1a']))
      expect(key('f1a', 'ArrowLeft', closed)).toEqual({ type: 'focus', id: 'f1' })
    })

    it('opens a page and toggles a folder with Enter or Space', () => {
      for (const k of ['Enter', ' ']) {
        expect(key('kb-1', k)).toEqual({ type: 'open', id: 'kb-1' })
        expect(key('f2', k)).toEqual({ type: 'toggle', id: 'f2' })
      }
    })

    it('leaves every other key alone', () => {
      expect(key('kb-1', 'a')).toBeNull()
      expect(key('kb-1', 'Tab')).toBeNull()
      expect(treeKeyAction([], null, 'ArrowDown')).toBeNull()
    })
  })
})

describe('resolveSelection', () => {
  const { pages, models } = splitKnowledge({ roots: [page('kb-1', 'mm-1'), page('kb-2', 'mm-2')] }, [
    model({ id: 'mm-1', is_knowledge_page: true }),
    model({ id: 'mm-2', is_knowledge_page: true }),
    model({ id: 'mm-plain' }),
  ])

  it('opens the page the address names', () => {
    expect(resolveSelection('kb-2', pages, models)).toMatchObject({ kind: 'page', page: { id: 'kb-2' } })
  })

  it('opens a mental model the address names by its id', () => {
    expect(resolveSelection('mm-plain', pages, models)).toMatchObject({ kind: 'model', model: { id: 'mm-plain' } })
  })

  it('shows the first page when the address names nothing, and the first model in a bank with no pages', () => {
    expect(resolveSelection(null, pages, models)).toMatchObject({ kind: 'page', page: { id: 'kb-1' } })
    expect(resolveSelection(null, [], models)).toMatchObject({ kind: 'model', model: { id: 'mm-plain' } })
    expect(resolveSelection(null, [], [])).toBeNull()
  })

  it('never swaps an id that matches nothing for another page', () => {
    expect(resolveSelection('kb-gone', pages, models)).toEqual({ kind: 'missing', id: 'kb-gone' })
    // A page's own model is not a mental model of the list, so its id opens nothing.
    expect(resolveSelection('mm-1', pages, models)).toEqual({ kind: 'missing', id: 'mm-1' })
  })
})

describe("a page's text", () => {
  it('reads `body`, as a 1.x engine sends it', () => {
    expect(pageBody({ id: 'kb-1', body: '## Body', markdown: '---\nid: "kb-1"\n---\n\n## Body' })).toBe('## Body')
  })

  it('reads `content` when that is what was sent', () => {
    expect(pageBody({ id: 'kb-1', content: 'plain' })).toBe('plain')
  })

  it('reads `markdown` without its front matter when there is nothing else', () => {
    expect(pageBody({ markdown: '---\nid: "kb-1"\ntitle: "T"\n---\n\n## Body\n\ntext' })).toBe('## Body\n\ntext')
  })

  it('is null for a page no refresh has written, whatever `markdown` says in words', () => {
    expect(pageBody({ body: '', markdown: '---\nid: "x"\n---\n\nNo content yet.' })).toBeNull()
    expect(pageBody({ body: null, markdown: '---\nid: "x"\n---\n\nNo content yet.' })).toBeNull()
    expect(pageBody({})).toBeNull()
    expect(pageBody(null)).toBeNull()
    expect(pageBody(undefined)).toBeNull()
  })

  it('strips front matter only when the text starts with it', () => {
    expect(stripFrontMatter('---\na: 1\n---\nbody')).toBe('body')
    expect(stripFrontMatter('body\n\n---\n\nmore')).toBe('body\n\n---\n\nmore')
    expect(stripFrontMatter('---\r\na: 1\r\n---\r\n\r\nbody')).toBe('body')
  })
})

describe('shortError', () => {
  it('gives the message of an Error', () => {
    expect(shortError(new Error('hindsight-api is not responding'))).toBe('hindsight-api is not responding')
  })

  it('keeps the first sentence of a long message', () => {
    expect(
      shortError(
        "Task exceeded the 300s wall-clock limit for 'refresh_mental_model' (stage=llm.openai.reflect) and was cancelled. Raise HINDSIGHT_API_TASK_TIMEOUT to allow more.",
      ),
    ).toBe("Task exceeded the 300s wall-clock limit for 'refresh_mental_model' (stage=llm.openai.reflect) and was cancelled.")
  })

  it('never shows JSON: it takes the message inside, or says the engine did not explain', () => {
    expect(shortError('{"error":{"code":"memory.engine_unreachable","message":"The engine is down."}}')).toBe(
      'The engine is down.',
    )
    expect(shortError('{"detail":"Mental model not found"}')).toBe('Mental model not found')
    expect(shortError('{"status":500}')).toBe('The engine did not say why.')
    expect(shortError('{not json')).toBe('The engine did not say why.')
    for (const out of [shortError('{"a":{"b":1}}'), shortError('[1,2]'), shortError({ error: { message: 'Inside.' } })]) {
      expect(out).not.toMatch(/[{}[\]"]/)
    }
  })

  it('cuts a sentence that is still too long, and marks the cut', () => {
    const out = shortError('x'.repeat(400))
    expect(out).toHaveLength(160)
    expect(out.endsWith('…')).toBe(true)
  })

  it('says so when there is nothing to say', () => {
    for (const nothing of [null, undefined, '', '   ', {}, 42]) {
      expect(shortError(nothing)).toBe('The engine did not say why.')
    }
  })

  it('folds line breaks into one line', () => {
    expect(shortError('first line\n  second line')).toBe('first line second line')
  })
})

describe('history', () => {
  const engine = [
    { previous_content: 'second text', previous_reflect_response: null, changed_at: T3 },
    {
      previous_content: null,
      previous_reflect_response: null,
      changed_at: T2,
      kind: 'refresh_failed',
      outcome: 'refresh_failed_error',
      failure_reason: 'unexpected_error',
      error_message: 'APIConnectionError: Connection error. Retrying will not help.',
    },
    { previous_content: null, previous_reflect_response: null, changed_at: T1 },
  ]

  it('reads the text a refresh replaced, a failed refresh and the first write', () => {
    const entries = historyEntries(engine)
    expect(entries.map((e) => [e.kind, e.at])).toEqual([
      ['version', T3],
      ['failure', T2],
      ['first', T1],
    ])
    expect(entries[0].content).toBe('second text')
    expect(entries[1]).toMatchObject({ content: null, reason: 'APIConnectionError: Connection error.' })
    expect(entries[2]).toMatchObject({ content: null, reason: null })
  })

  it('gives every entry its own key, even two with the same time', () => {
    const entries = historyEntries([
      { previous_content: 'a', changed_at: T1 },
      { previous_content: 'b', changed_at: T1 },
    ])
    expect(new Set(entries.map((e) => e.key)).size).toBe(2)
  })

  it('puts the newest first, whatever order it arrives in', () => {
    const shuffled = [engine[2], engine[0], engine[1]]
    expect(historyEntries(shuffled).map((e) => e.at)).toEqual([T3, T2, T1])
  })

  it('reads the older row shape too', () => {
    const entries = historyEntries([{ updated_at: T2, content: 'v2' }, { updated_at: T1, content: 'v1' }])
    expect(entries.map((e) => [e.kind, e.at, e.content])).toEqual([
      ['version', T2, 'v2'],
      ['version', T1, 'v1'],
    ])
  })

  it('falls back to the failure reason when the engine sent no message', () => {
    const [entry] = historyEntries([{ kind: 'refresh_failed', failure_reason: 'unexpected_error', changed_at: T1 }])
    expect(entry.reason).toBe('unexpected_error')
  })

  it('is empty for no history, and skips what is not an entry', () => {
    expect(historyEntries([])).toEqual([])
    expect(historyEntries(null)).toEqual([])
    expect(historyEntries(['x', null, 3] as unknown[])).toEqual([])
  })

  it('gives the reason of the latest refresh only when that refresh is the one that failed', () => {
    expect(latestFailureReason(historyEntries([engine[1], engine[2]]))).toBe('APIConnectionError: Connection error.')
    // A later refresh worked: the old failure is no longer why anything is wrong.
    expect(latestFailureReason(historyEntries(engine))).toBeNull()
    expect(latestFailureReason([])).toBeNull()
  })
})

describe('refresh settings in words', () => {
  it('reads the schedules the engine uses', () => {
    expect(cronWords('0 */4 * * *')).toBe('every 4 h')
    expect(cronWords('30 */6 * * *')).toBe('every 6 h, at 30 min past the hour')
    expect(cronWords('0 * * * *')).toBe('every hour')
    expect(cronWords('0 3 * * *')).toBe('every day at 03:00 UTC')
    expect(cronWords('15 14 * * 0')).toBe('every Sunday at 14:15 UTC')
    expect(cronWords('0 3 * * 7')).toBe('every Sunday at 03:00 UTC')
  })

  it('does not guess at a schedule it cannot read', () => {
    for (const odd of ['0 3 1 * *', '*/5 * * * *', '0 3 * * 1-5', 'nonsense', '0 3 * *', '0 */30 * * *']) {
      expect(cronWords(odd), odd).toBeNull()
    }
  })

  it('says when it refreshes: on a schedule, after consolidation, or only by hand', () => {
    expect(triggerWords({ refresh_cron: '0 */4 * * *' })[0]).toBe(
      'Refreshes every 4 h, when facts in its scope have changed.',
    )
    expect(triggerWords({ refresh_after_consolidation: true })[0]).toBe(
      'Refreshes each time new facts are consolidated.',
    )
    expect(triggerWords({ refresh_after_consolidation: false, refresh_cron: null })[0]).toBe(
      'Refreshes only when one is started by hand.',
    )
    // A schedule it cannot put in words is shown as it is, and says it is UTC.
    expect(triggerWords({ refresh_cron: '0 3 1 * *' })[0]).toBe(
      'Refreshes on the schedule 0 3 1 * * (UTC), when facts in its scope have changed.',
    )
  })

  it('adds the mode, a full-rewrite schedule, the minimum interval and a narrowed scope', () => {
    expect(
      triggerWords({
        mode: 'delta',
        refresh_after_consolidation: true,
        full_refresh_cron: '0 3 * * 0',
        min_refresh_interval_seconds: 14400,
        fact_types: ['world', 'experience'],
      }),
    ).toEqual([
      'Refreshes each time new facts are consolidated.',
      'Rewritten from scratch every Sunday at 03:00 UTC.',
      'A refresh edits the existing text and leaves unchanged sections as they are.',
      'At most one automatic refresh every 4 h.',
      'Written from world and experience facts only.',
    ])
    expect(triggerWords({ mode: 'full', min_refresh_interval_seconds: 0 })).toEqual([
      'Refreshes only when one is started by hand.',
      'A refresh rewrites the whole text.',
      'No minimum time between automatic refreshes.',
    ])
  })

  it('leaves out what was not set, and says nothing with no trigger', () => {
    expect(triggerWords({ refresh_after_consolidation: true, min_refresh_interval_seconds: null, fact_types: null })).toEqual([
      'Refreshes each time new facts are consolidated.',
    ])
    expect(triggerWords({ refresh_cron: '0 3 * * *', fact_types: ['world', 'experience', 'observation'] })).toHaveLength(1)
    expect(triggerWords(null)).toEqual([])
    expect(triggerWords('daily')).toEqual([])
  })
})

describe("the bank's refresh interval", () => {
  it('reads the interval from the config, 0 when it sets none, null when the config is not known', () => {
    expect(intervalSeconds({ mental_model_min_refresh_interval_seconds: 14400 })).toBe(14400)
    expect(intervalSeconds({ mental_model_min_refresh_interval_seconds: '3600' })).toBe(3600)
    expect(intervalSeconds({ mental_model_min_refresh_interval_seconds: null })).toBe(0)
    expect(intervalSeconds({})).toBe(0)
    expect(intervalSeconds(undefined)).toBeNull()
    expect(intervalSeconds(null)).toBeNull()
  })

  it('labels each choice so it completes "Refresh at most every …"', () => {
    expect([0, 3600, 14400, 43200, 86400].map(intervalLabel)).toEqual(['time facts are consolidated', '1 h', '4 h', '12 h', '24 h'])
  })

  it('offers the five standard choices, plus a value the bank holds that is not one of them', () => {
    expect(intervalOptions(14400).map((o) => o.seconds)).toEqual([0, 3600, 14400, 43200, 86400])
    expect(intervalOptions(7200)).toEqual([
      { seconds: 0, label: 'time facts are consolidated' },
      { seconds: 3600, label: '1 h' },
      { seconds: 7200, label: '2 h' },
      { seconds: 14400, label: '4 h' },
      { seconds: 43200, label: '12 h' },
      { seconds: 86400, label: '24 h' },
    ])
    expect(intervalOptions(null)).toHaveLength(5)
  })
})

describe('directives', () => {
  const d = (id: string, over: Partial<Directive> = {}): Directive => ({ id, name: id, content: `do ${id}`, ...over })

  it('orders by priority, highest first, then by name', () => {
    expect(
      orderDirectives([d('b', { priority: 1 }), d('z', { priority: 3 }), d('A', { priority: 1 }), d('none')]).map((x) => x.id),
    ).toEqual(['z', 'A', 'b', 'none'])
    expect(orderDirectives(null)).toEqual([])
  })

  it('starts an edit from the directive, and an add from nothing', () => {
    expect(directiveDraft(d('x', { name: 'Cite', content: 'Always cite.', priority: 2 }))).toEqual({
      name: 'Cite',
      content: 'Always cite.',
      priority: '2',
    })
    expect(directiveDraft(d('x')).priority).toBe('0')
    expect(EMPTY_DIRECTIVE).toEqual({ name: '', content: '', priority: '0' })
  })

  it('checks a draft into the three fields the engine takes', () => {
    expect(directiveBody({ name: '  Cite  ', content: ' Always cite. ', priority: ' 3 ' })).toEqual({
      ok: true,
      body: { name: 'Cite', content: 'Always cite.', priority: 3 },
    })
    expect(directiveBody({ name: 'n', content: 'c', priority: '' })).toMatchObject({ ok: true, body: { priority: 0 } })
    expect(directiveBody({ name: 'n', content: 'c', priority: '-2' })).toMatchObject({ ok: true, body: { priority: -2 } })
  })

  it('says why a draft cannot be saved', () => {
    expect(directiveBody({ name: ' ', content: 'c', priority: '0' })).toEqual({ ok: false, reason: 'Give the directive a name.' })
    expect(directiveBody({ name: 'n', content: '', priority: '0' })).toEqual({
      ok: false,
      reason: 'Write the text agents are given.',
    })
    for (const bad of ['1.5', 'high', '1e3', '--1']) {
      expect(directiveBody({ name: 'n', content: 'c', priority: bad }), bad).toEqual({
        ok: false,
        reason: 'Priority is a whole number.',
      })
    }
    expect(directiveBody({ name: 'n', content: 'c', priority: '99999999999' })).toEqual({
      ok: false,
      reason: 'Priority is out of range.',
    })
  })

  it('reads tags from a comma-separated line', () => {
    expect(parseTags(' a, b ,, c ,a')).toEqual(['a', 'b', 'c'])
    expect(parseTags('')).toEqual([])
    expect(parseTags('project:hal0')).toEqual(['project:hal0'])
  })
})

describe('refresh all stale', () => {
  const { pages, models } = splitKnowledge(
    {
      roots: [
        page('kb-fresh', 'mm-1'),
        page('kb-stale', 'mm-2', { is_stale: true }),
        page('kb-failed', 'mm-3', { last_refresh_failed_at: T3 }),
        page('kb-orphan', null, { is_stale: true }),
      ],
    },
    [
      model({ id: 'mm-1', is_knowledge_page: true }),
      model({ id: 'mm-2', is_knowledge_page: true }),
      model({ id: 'mm-3', is_knowledge_page: true, last_refreshed_at: T2 }),
      model({ id: 'mm-plain-stale', name: 'Plain stale', is_stale: true }),
      model({ id: 'mm-plain-fresh', name: 'Plain fresh' }),
    ],
  )

  it('targets what is stale or failed, pages first, each through its model', () => {
    expect(staleTargets(pages, models)).toEqual<RefreshTarget[]>([
      { modelId: 'mm-3', name: 'kb-failed', kind: 'page' },
      { modelId: 'mm-2', name: 'kb-stale', kind: 'page' },
      { modelId: 'mm-plain-stale', name: 'Plain stale', kind: 'model' },
    ])
  })

  it('leaves out a page with no model: there is nothing to refresh it from', () => {
    expect(staleTargets(pages, models).some((t) => t.name === 'kb-orphan')).toBe(false)
  })

  it('starts each refresh in order, one at a time, and reports the count as it goes', async () => {
    const targets = staleTargets(pages, models)
    const calls: string[] = []
    const progress: string[] = []
    let running = 0
    const result = await refreshAll(
      targets,
      async (target) => {
        running += 1
        expect(running).toBe(1)
        calls.push(target.modelId)
        await Promise.resolve()
        running -= 1
      },
      ({ at, total, target }) => progress.push(`${at}/${total} ${target.name}`),
    )
    expect(calls).toEqual(['mm-3', 'mm-2', 'mm-plain-stale'])
    expect(progress).toEqual(['1/3 kb-failed', '2/3 kb-stale', '3/3 Plain stale'])
    expect(result).toEqual({ started: 3, total: 3, failed: null })
  })

  it('stops at the first that does not start, and says which and why', async () => {
    const targets = staleTargets(pages, models)
    const start = vi.fn(async (target: RefreshTarget) => {
      if (target.modelId === 'mm-2') throw new Error('hindsight-api is not responding. Try later.')
    })
    const result = await refreshAll(targets, start)
    expect(start).toHaveBeenCalledTimes(2)
    expect(result).toEqual({
      started: 1,
      total: 3,
      failed: { target: targets[1], reason: 'hindsight-api is not responding.' },
    })
  })

  it('does nothing with nothing to refresh', async () => {
    const start = vi.fn()
    expect(await refreshAll([], start)).toEqual({ started: 0, total: 0, failed: null })
    expect(start).not.toHaveBeenCalled()
  })
})

describe('a refresh that was started', () => {
  const watch = { refreshedAt: T1, failedAt: null, since: 1_000 }

  it('is running until the model says otherwise', () => {
    expect(watchOutcome(watch, { lastRefreshedAt: T1, lastFailedAt: null }, 2_000)).toBe('running')
    expect(watchOutcome(watch, undefined, 2_000)).toBe('running')
  })

  it('is done when the model was refreshed since', () => {
    expect(watchOutcome(watch, { lastRefreshedAt: T2, lastFailedAt: null }, 2_000)).toBe('done')
  })

  it('has failed when a refresh failed since, even one that failed before too', () => {
    expect(watchOutcome(watch, { lastRefreshedAt: T1, lastFailedAt: T2 }, 2_000)).toBe('failed')
    const failedBefore = { refreshedAt: T1, failedAt: T2, since: 1_000 }
    expect(watchOutcome(failedBefore, { lastRefreshedAt: T1, lastFailedAt: T2 }, 2_000)).toBe('running')
    expect(watchOutcome(failedBefore, { lastRefreshedAt: T1, lastFailedAt: T3 }, 2_000)).toBe('failed')
  })

  it('is overdue once it has run past the limit with nothing changed', () => {
    expect(watchOutcome(watch, { lastRefreshedAt: T1, lastFailedAt: null }, 1_000 + WATCH_LIMIT_MS)).toBe('running')
    expect(watchOutcome(watch, { lastRefreshedAt: T1, lastFailedAt: null }, 1_001 + WATCH_LIMIT_MS)).toBe('overdue')
  })
})
