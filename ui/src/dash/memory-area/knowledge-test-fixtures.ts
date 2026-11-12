// Stand-ins for the hooks the Knowledge tab reads, for the component tests. Not imported by the
// app. A test holds one `KnowledgeHookState`, mocks `@/api/hooks/useHindsight` with
// `knowledgeHooks(() => state)`, and changes the state between renders:
//
//   - each read (`tree`, `models`, `directives`, `config`, a page, a history) is a query result
//     as the components read it; `{}` is "still loading";
//   - each write records what it was called with in `calls`, rejects when `fail[name]` is set,
//     and waits on `gate[name]` when a test wants to see the in-between state.
import type { Directive, KnowledgeNode, KnowledgeTree } from '@/api/hooks/useHindsight'
import type { FakeQuery } from './area-test-fixtures'
import type { KnowledgeModel } from './knowledge-pure'

export type WriteName =
  | 'refresh'
  | 'modelUpdate'
  | 'modelCreate'
  | 'directiveCreate'
  | 'directiveUpdate'
  | 'directiveDelete'
  | 'configUpdate'

export interface KnowledgeHookState {
  tree: FakeQuery<KnowledgeTree>
  models: FakeQuery<{ items: KnowledgeModel[]; total?: number }>
  directives: FakeQuery<{ items: Directive[]; total?: number }>
  config: FakeQuery<{ bank_id: string; config: Record<string, unknown> }>
  /** By page id. A page that is not here is still loading. */
  pages: Record<string, FakeQuery<unknown>>
  /** By mental-model id. A model that is not here has an empty history. */
  history: Record<string, FakeQuery<{ items: unknown[] }>>
  calls: { name: WriteName; vars: unknown }[]
  fail: Partial<Record<WriteName, Error>>
  gate: Partial<Record<WriteName, Promise<unknown>>>
  /** What each read was asked for, so a test can check a request was or was not made. */
  asked: { name: string; args: unknown[] }[]
  /** The `active` flag the tab last passed to the polling hook. */
  polling: boolean
}

export function knowledgeState(over: Partial<KnowledgeHookState> = {}): KnowledgeHookState {
  return {
    tree: {},
    models: {},
    directives: {},
    config: {},
    pages: {},
    history: {},
    calls: [],
    fail: {},
    gate: {},
    asked: [],
    polling: false,
    ...over,
  }
}

/** The hooks, reading whatever state `get` answers at the time of each render or call. */
export function knowledgeHooks(get: () => KnowledgeHookState) {
  const write = (name: WriteName) => () => {
    const run = async (vars: unknown) => {
      const state = get()
      state.calls.push({ name, vars })
      if (state.gate[name]) await state.gate[name]
      if (state.fail[name]) throw state.fail[name]
      return {}
    }
    return {
      isPending: false,
      mutateAsync: run,
      mutate: (vars: unknown) => {
        void run(vars).catch(() => {})
      },
    }
  }
  const read =
    <T>(name: string, pick: (state: KnowledgeHookState, ...args: never[]) => T) =>
    (...args: unknown[]) => {
      const state = get()
      state.asked.push({ name, args })
      return pick(state, ...(args as never[]))
    }
  return {
    useKnowledgeTree: read('tree', (s) => s.tree),
    useMentalModels: read('models', (s) => s.models),
    useDirectives: read('directives', (s) => s.directives),
    useBankConfig: read('config', (s) => s.config),
    useKnowledgePage: read('page', (s, _bank: string, id: string) => s.pages[id] ?? {}),
    useMentalModelHistory: read(
      'history',
      (s, _bank: string, id: string | null, opts?: { enabled?: boolean }) =>
        opts?.enabled === false || !id ? {} : (s.history[id] ?? { data: { items: [] } }),
    ),
    useKnowledgePolling: (_bank: string | null, active: boolean) => {
      get().polling = active
      return 0
    },
    useMentalModelRefresh: write('refresh'),
    useMentalModelUpdate: write('modelUpdate'),
    useMentalModelCreate: write('modelCreate'),
    useDirectiveCreate: write('directiveCreate'),
    useDirectiveUpdate: write('directiveUpdate'),
    useDirectiveDelete: write('directiveDelete'),
    useBankConfigUpdate: write('configUpdate'),
  }
}

// ── A bank's knowledge, as the engine sends it ────────────────────────────────

export function pageNode(over: Partial<KnowledgeNode> & { id: string }): KnowledgeNode {
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

export function folderNode(id: string, name: string, children: KnowledgeNode[]): KnowledgeNode {
  return pageNode({ id, kind: 'folder', name, children: children.map((c) => ({ ...c, parent_id: id })) })
}

export function knowledgeModel(over: Partial<KnowledgeModel> & { id: string }): KnowledgeModel {
  return {
    name: over.id,
    source_query: `what about ${over.id}?`,
    content: `Text of ${over.id}.`,
    tags: [],
    is_stale: false,
    last_refreshed_at: '2026-11-05T08:00:00+00:00',
    last_refresh_failed_at: null,
    is_knowledge_page: false,
    ...over,
  }
}

/**
 * One bank: three pages in two folders (one fresh, one stale, one stale whose last refresh
 * failed) and two mental models that are not pages (one fresh, one stale).
 */
export const TREE: KnowledgeTree = {
  roots: [
    folderNode('f-box', 'The box', [
      pageNode({
        id: 'kb-thermal',
        name: 'Thermal envelope',
        mental_model_id: 'mm-thermal',
        description: 'what are the thermal limits?',
        tags: ['performance'],
      }),
      pageNode({
        id: 'kb-power',
        name: 'Power posture',
        mental_model_id: 'mm-power',
        description: 'how does the box handle power loss?',
        is_stale: true,
      }),
    ]),
    folderNode('f-operator', 'The operator', [
      pageNode({
        id: 'kb-style',
        name: 'Operator style',
        mental_model_id: 'mm-style',
        description: 'how should agents talk to the operator?',
        is_stale: true,
        last_refresh_failed_at: '2026-11-08T08:00:00+00:00',
      }),
    ]),
  ],
}

export const MODELS: KnowledgeModel[] = [
  knowledgeModel({ id: 'mm-thermal', name: 'Thermal envelope', is_knowledge_page: true, last_refreshed_at: '2026-11-09T06:00:00+00:00' }),
  knowledgeModel({ id: 'mm-power', name: 'Power posture', is_knowledge_page: true, is_stale: true }),
  knowledgeModel({
    id: 'mm-style',
    name: 'Operator style',
    is_knowledge_page: true,
    is_stale: true,
    last_refreshed_at: '2026-11-01T08:00:00+00:00',
    last_refresh_failed_at: '2026-11-08T08:00:00+00:00',
  }),
  knowledgeModel({
    id: 'mm-decisions',
    name: 'Project decisions',
    source_query: 'what has been decided, and why?',
    content: '## Decided\n\nUpdates go in before noon.',
    tags: ['decisions'],
    trigger: { mode: 'delta', refresh_after_consolidation: true, min_refresh_interval_seconds: 3600 },
  }),
  knowledgeModel({ id: 'mm-preferences', name: 'Operator preferences', is_stale: true }),
]

export const DIRECTIVES: Directive[] = [
  { id: 'dir-cite', name: 'Always cite sources', content: 'Include citations in answers.', priority: 1, is_active: true, tags: ['operator'] },
  { id: 'dir-terse', name: 'Keep it terse', content: 'Default to terse responses.', priority: 3, is_active: true, tags: [] },
  { id: 'dir-disk', name: 'Guard disk headroom', content: 'Keep the disk below 85%.', priority: 2, is_active: false, tags: [] },
]

export const HISTORY = [
  { previous_content: 'Second text.', previous_reflect_response: null, changed_at: '2026-11-05T08:00:00+00:00' },
  {
    previous_content: null,
    previous_reflect_response: null,
    changed_at: '2026-11-03T08:00:00+00:00',
    kind: 'refresh_failed',
    outcome: 'refresh_failed_error',
    failure_reason: 'unexpected_error',
    error_message: 'APIConnectionError: Connection error. The model did not answer.',
  },
  { previous_content: 'First text.', previous_reflect_response: null, changed_at: '2026-11-02T08:00:00+00:00' },
  { previous_content: null, previous_reflect_response: null, changed_at: '2026-11-01T08:00:00+00:00' },
]

/** Everything loaded, for the bank above. */
export function loadedState(over: Partial<KnowledgeHookState> = {}): KnowledgeHookState {
  return knowledgeState({
    tree: { data: TREE },
    models: { data: { items: MODELS, total: MODELS.length } },
    directives: { data: { items: DIRECTIVES, total: DIRECTIVES.length } },
    config: { data: { bank_id: 'shared', config: { mental_model_min_refresh_interval_seconds: 14400 } } },
    pages: {
      'kb-thermal': {
        data: { id: 'kb-thermal', name: 'Thermal envelope', body: '## Limits\n\nPeak is **84 C**.', markdown: '---\nid: "kb-thermal"\n---\n\n## Limits\n\nPeak is **84 C**.' },
      },
      'kb-power': { data: { id: 'kb-power', name: 'Power posture', body: 'A UPS holds the box up.' } },
      'kb-style': { data: { id: 'kb-style', name: 'Operator style', body: 'Terse and technical.' } },
    },
    history: {
      'mm-decisions': { data: { items: HISTORY } },
      'mm-style': { data: { items: [HISTORY[1]] } },
    },
    ...over,
  })
}
