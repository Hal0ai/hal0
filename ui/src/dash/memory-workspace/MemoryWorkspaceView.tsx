// hal0 v3 dashboard — Memory workspace view (Hindsight 1.2 showcase).
//
// A real ES module (pattern: Benchmarks.tsx), not the window-globals .jsx
// shim #memory uses. Lives at its own top-level route (#memworkspace) so it
// can evolve independently of the AgentView/Memory tab gate. Data comes from
// useMemoryWorkspace.ts (lanes/agents/models + the allowlisted engine
// passthrough `/api/memory/engine/<upstream path>`, mirroring
// hindsight-1.2.0-openapi.json). Pure formatting/derivation lives in
// memory-workspace-pure.ts so vitest can pin it without a DOM.

import React, { useMemo, useState } from 'react'
import {
  useInvalidateMemoryEngine,
  useMemoryEngineMutation,
  useMemoryEngineQuery,
  useMemoryLanes,
  useMemoryModels,
  useMemoryWorkspaceAgents,
} from '@/api/hooks/useMemoryWorkspace'
import {
  fmtAge,
  installCommandFor,
  laneHealthTone,
  normalizeLlmOpStats,
  oldestPendingAge,
  operationsByStatus,
  operationStatusCounts,
  pageIsStale,
  sortBanksByPressure,
  totalQueueDepth,
  type LaneBankRow,
  type OperationRow,
} from './memory-workspace-pure'

const TABS = ['overview', 'kb', 'mental-models', 'directives', 'operations', 'agents', 'models'] as const
type Tab = (typeof TABS)[number]

const TAB_LABEL: Record<Tab, string> = {
  overview: 'Overview',
  kb: 'Knowledge base',
  'mental-models': 'Mental models',
  directives: 'Directives',
  operations: 'Operations',
  agents: 'Agents',
  models: 'Models',
}

export const tabFromParam = (param?: string | null): Tab =>
  (TABS as readonly string[]).includes(param || '') ? (param as Tab) : 'overview'

export const HINDSIGHT_CONSOLE_DEFAULT_PORT = 9999

/** Default Hindsight Control Plane URL: same host as the dashboard, fixed port. */
export function hindsightConsoleUrl(hostname: string): string {
  return `http://${hostname}:${HINDSIGHT_CONSOLE_DEFAULT_PORT}`
}

interface BankSummary {
  bank_id: string
  name?: string
  fact_count?: number
  [key: string]: unknown
}

function useBankList() {
  return useMemoryEngineQuery<{ banks: BankSummary[] }>('v1/default/banks', { refetchInterval: 15_000 })
}

const Card: React.FC<{ title?: string; children: React.ReactNode; testId?: string }> = ({ title, children, testId }) => (
  <div className="card" data-testid={testId}>
    {title ? <div className="card-title">{title}</div> : null}
    {children}
  </div>
)

const EngineError: React.FC<{ error: unknown; what: string; onRetry?: () => void }> = ({ error, what, onRetry }) => (
  <div className="empty mono mem-error" data-testid="memws-error">
    <div>Memory engine unreachable — {(error as Error)?.message || `could not load ${what}`}</div>
    {onRetry ? (
      <button className="btn ghost sm" style={{ marginTop: 8 }} onClick={onRetry}>
        Retry
      </button>
    ) : null}
  </div>
)

// ── Tab 1: Overview ─────────────────────────────────────────────────────

const OverviewTab: React.FC<{ bank: string }> = ({ bank }) => {
  const lanes = useMemoryLanes()
  const stats = useMemoryEngineQuery<Record<string, unknown>>(`v1/default/banks/${encodeURIComponent(bank)}/stats`)
  const llmStats = useMemoryEngineQuery<unknown>('v1/default/llm-requests/stats')

  const banks: LaneBankRow[] = useMemo(() => sortBanksByPressure(lanes.data?.banks || []), [lanes.data])
  const llmOps = useMemo(() => normalizeLlmOpStats(llmStats.data), [llmStats.data])

  if (lanes.isError) return <EngineError error={lanes.error} what="lanes" onRetry={() => lanes.refetch()} />

  const decision = lanes.data?.decision
  const llm = lanes.data?.llm
  const tone = laneHealthTone(decision?.health)

  return (
    <div className="memws-grid" data-testid="memws-overview">
      <Card title="Decision server" testId="memws-decision-card">
        <div className={`badge badge-${tone}`} data-testid="memws-decision-health">
          {decision?.health || 'unknown'}
        </div>
        <div className="mono small">device: {decision?.device ?? '—'}</div>
        <div className="mono small">requests: {decision?.requests ?? 0}</div>
        <div className="mono small">busy: {typeof decision?.busy_s === 'number' ? decision.busy_s.toFixed(1) : (decision?.busy_s ?? 0)}s</div>
      </Card>
      <Card title="LLM lane">
        <div className="mono small">model: {llm?.model ?? '—'}</div>
        <div className="mono small">max concurrent: {llm?.max_concurrent ?? '—'}</div>
      </Card>
      <Card title="Queue by bank" testId="memws-queue-card">
        <div className="mono small">total in flight: {totalQueueDepth(banks)}</div>
        <div className="mono small">oldest pending: {fmtAge(oldestPendingAge(banks))}</div>
        <table className="tbl mono small">
          <thead>
            <tr><th>bank</th><th>pending</th><th>processing</th><th>oldest</th></tr>
          </thead>
          <tbody>
            {banks.map((b) => (
              <tr key={b.bank_id} data-testid={`memws-queue-row-${b.bank_id}`}>
                <td>{b.bank_id}</td>
                <td>{b.pending}</td>
                <td>{b.processing}</td>
                <td>{fmtAge(b.oldest_pending_age_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title={`Bank stats — ${bank}`}>
        {stats.isError ? (
          <EngineError error={stats.error} what="bank stats" onRetry={() => stats.refetch()} />
        ) : (
          <pre className="mono small memws-pre">{JSON.stringify(stats.data ?? {}, null, 2)}</pre>
        )}
      </Card>
      <Card title="LLM requests by operation">
        {llmStats.isError ? (
          <EngineError error={llmStats.error} what="llm stats" onRetry={() => llmStats.refetch()} />
        ) : (
          <table className="tbl mono small">
            <thead><tr><th>operation</th><th>count</th></tr></thead>
            <tbody>
              {llmOps.map((o) => (
                <tr key={o.operation}><td>{o.operation}</td><td>{o.count}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}

// ── Tab 2: Knowledge base ───────────────────────────────────────────────

interface KbNode {
  id: string
  kind: 'folder' | 'page'
  name: string
  children?: KbNode[]
  mental_model_id?: string | null
}

function flattenPages(nodes: KbNode[] | undefined, depth = 0, out: { node: KbNode; depth: number }[] = []) {
  for (const n of nodes || []) {
    out.push({ node: n, depth })
    if (n.children) flattenPages(n.children, depth + 1, out)
  }
  return out
}

const KnowledgeBaseTab: React.FC<{ bank: string }> = ({ bank }) => {
  const tree = useMemoryEngineQuery<{ roots: KbNode[] }>(`v1/default/banks/${encodeURIComponent(bank)}/knowledge-base/tree`)
  const [pageId, setPageId] = useState<string | null>(null)
  const page = useMemoryEngineQuery<{
    id: string
    name: string
    content?: string | null
    last_refreshed_at?: string | null
    is_stale?: boolean
    source_query?: string | null
    tags?: string[]
    trigger?: string | null
  }>(`v1/default/banks/${encodeURIComponent(bank)}/knowledge-base/pages/${pageId}`, { enabled: !!pageId })

  const invalidate = useInvalidateMemoryEngine()
  const refreshMutation = useMemoryEngineMutation('post', 'knowledge-base')
  const saveMutation = useMemoryEngineMutation('patch', 'knowledge-base')

  const rows = useMemo(() => flattenPages(tree.data?.roots), [tree.data])
  const [draft, setDraft] = useState<{ source_query: string; tags: string; trigger: string } | null>(null)

  if (tree.isError) return <EngineError error={tree.error} what="knowledge-base tree" onRetry={() => tree.refetch()} />

  const openPage = (id: string) => {
    setPageId(id)
    setDraft(null)
  }

  const startEdit = () => {
    if (!page.data) return
    setDraft({
      source_query: page.data.source_query || '',
      tags: (page.data.tags || []).join(', '),
      trigger: page.data.trigger || '',
    })
  }

  const save = () => {
    if (!pageId || !draft) return
    saveMutation.mutate(
      {
        sub: `v1/default/banks/${bank}/knowledge-base/pages/${pageId}`,
        body: {
          source_query: draft.source_query || null,
          tags: draft.tags.split(',').map((t) => t.trim()).filter(Boolean),
          trigger: draft.trigger || null,
        },
      },
      { onSuccess: () => setDraft(null) },
    )
  }

  return (
    <div className="memws-kb" data-testid="memws-kb">
      <Card title="Pages">
        <ul className="memws-tree mono small" data-testid="memws-kb-tree">
          {rows.map(({ node, depth }) => (
            <li
              key={node.id}
              style={{ paddingLeft: depth * 14 }}
              className={node.kind === 'page' ? 'memws-tree-page' : 'memws-tree-folder'}
            >
              {node.kind === 'page' ? (
                <button
                  className="btn ghost sm"
                  data-testid={`memws-kb-page-${node.id}`}
                  onClick={() => openPage(node.id)}
                >
                  {node.name}
                </button>
              ) : (
                <span>{node.name}/</span>
              )}
            </li>
          ))}
        </ul>
      </Card>
      <Card title={page.data?.name || 'Select a page'}>
        {!pageId ? (
          <div className="empty mono">Pick a page from the tree.</div>
        ) : page.isError ? (
          <EngineError error={page.error} what="page" onRetry={() => page.refetch()} />
        ) : (
          <>
            <div className="mono small" data-testid="memws-kb-meta">
              last refreshed: {page.data?.last_refreshed_at || '—'}
              {pageIsStale(page.data) ? (
                <span className="badge badge-warn" style={{ marginLeft: 8 }}>stale</span>
              ) : null}
            </div>
            <div className="memws-pre" data-testid="memws-kb-content">
              {page.data?.content || <span className="mono small">(no content yet)</span>}
            </div>
            <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
              <button
                className="btn sm"
                data-testid="memws-kb-refresh"
                disabled={refreshMutation.isPending}
                onClick={() =>
                  refreshMutation.mutate({ sub: `v1/default/banks/${bank}/knowledge-base/pages/${pageId}/refresh` })
                }
              >
                Refresh
              </button>
              {!draft ? (
                <button className="btn ghost sm" onClick={startEdit} data-testid="memws-kb-edit">
                  Edit definition
                </button>
              ) : null}
            </div>
            {draft ? (
              <div className="memws-form" data-testid="memws-kb-form">
                <label>
                  Source query
                  <textarea
                    value={draft.source_query}
                    onChange={(e) => setDraft({ ...draft, source_query: e.target.value })}
                  />
                </label>
                <label>
                  Tags (comma-separated)
                  <input value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
                </label>
                <label>
                  Trigger
                  <input value={draft.trigger} onChange={(e) => setDraft({ ...draft, trigger: e.target.value })} />
                </label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn sm" onClick={save} disabled={saveMutation.isPending} data-testid="memws-kb-save">
                    Save
                  </button>
                  <button className="btn ghost sm" onClick={() => setDraft(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : null}
          </>
        )}
      </Card>
    </div>
  )
}

// ── Tab 3: Mental models ────────────────────────────────────────────────

interface MentalModel {
  id: string
  name: string
  content?: string | null
  source_query?: string | null
  trigger?: string | null
  is_stale?: boolean
  last_refreshed_at?: string | null
}

const MentalModelsTab: React.FC<{ bank: string }> = ({ bank }) => {
  const list = useMemoryEngineQuery<{ items: MentalModel[] }>(`v1/default/banks/${encodeURIComponent(bank)}/mental-models`)
  const [selId, setSelId] = useState<string | null>(null)
  const detail = useMemoryEngineQuery<MentalModel>(
    `v1/default/banks/${encodeURIComponent(bank)}/mental-models/${selId}`,
    { enabled: !!selId },
  )
  const history = useMemoryEngineQuery<{ items: unknown[] }>(
    `v1/default/banks/${encodeURIComponent(bank)}/mental-models/${selId}/history`,
    { enabled: !!selId },
  )
  const refresh = useMemoryEngineMutation('post', 'mental-models')

  if (list.isError) return <EngineError error={list.error} what="mental models" onRetry={() => list.refetch()} />
  const items = list.data?.items || []

  return (
    <div className="memws-kb" data-testid="memws-mental-models">
      <Card title="Mental models">
        <ul className="mono small">
          {items.map((mm) => (
            <li key={mm.id}>
              <button className="btn ghost sm" data-testid={`memws-mm-${mm.id}`} onClick={() => setSelId(mm.id)}>
                {mm.name} {mm.is_stale ? <span className="badge badge-warn">stale</span> : null}
              </button>
            </li>
          ))}
        </ul>
      </Card>
      <Card title={detail.data?.name || 'Select a mental model'}>
        {!selId ? (
          <div className="empty mono">Pick a mental model.</div>
        ) : detail.isError ? (
          <EngineError error={detail.error} what="mental model" onRetry={() => detail.refetch()} />
        ) : (
          <>
            <div className="mono small">source query: {detail.data?.source_query || '—'}</div>
            <div className="mono small">trigger: {detail.data?.trigger || '—'}</div>
            <div className="mono small">last refreshed: {detail.data?.last_refreshed_at || '—'}</div>
            <div className="memws-pre">{detail.data?.content || '(no content yet)'}</div>
            <button
              className="btn sm"
              data-testid="memws-mm-refresh"
              disabled={refresh.isPending}
              onClick={() => refresh.mutate({ sub: `v1/default/banks/${bank}/mental-models/${selId}/refresh` })}
            >
              Refresh
            </button>
            <div className="mono small" style={{ marginTop: 10 }}>
              History ({(history.data?.items || []).length})
            </div>
          </>
        )}
      </Card>
    </div>
  )
}

// ── Tab 4: Directives ───────────────────────────────────────────────────

interface Directive {
  id: string
  name: string
  content: string
  priority: number
  is_active: boolean
  tags: string[]
}

const emptyDirectiveDraft = { name: '', content: '', priority: 0, is_active: true, tags: '' }

const DirectivesTab: React.FC<{ bank: string }> = ({ bank }) => {
  const list = useMemoryEngineQuery<{ items: Directive[] }>(`v1/default/banks/${encodeURIComponent(bank)}/directives`)
  const create = useMemoryEngineMutation('post', 'directives')
  const update = useMemoryEngineMutation('patch', 'directives')
  const del = useMemoryEngineMutation('delete', 'directives')
  const [editingId, setEditingId] = useState<string | 'new' | null>(null)
  const [draft, setDraft] = useState(emptyDirectiveDraft)

  if (list.isError) return <EngineError error={list.error} what="directives" onRetry={() => list.refetch()} />
  const items = list.data?.items || []

  const beginEdit = (d?: Directive) => {
    if (d) {
      setEditingId(d.id)
      setDraft({ name: d.name, content: d.content, priority: d.priority, is_active: d.is_active, tags: d.tags.join(', ') })
    } else {
      setEditingId('new')
      setDraft(emptyDirectiveDraft)
    }
  }

  const submit = () => {
    const body = {
      name: draft.name,
      content: draft.content,
      priority: Number(draft.priority) || 0,
      is_active: draft.is_active,
      tags: draft.tags.split(',').map((t) => t.trim()).filter(Boolean),
    }
    if (editingId === 'new') {
      create.mutate({ sub: `v1/default/banks/${bank}/directives`, body }, { onSuccess: () => setEditingId(null) })
    } else if (editingId) {
      update.mutate(
        { sub: `v1/default/banks/${bank}/directives/${editingId}`, body },
        { onSuccess: () => setEditingId(null) },
      )
    }
  }

  return (
    <div data-testid="memws-directives">
      <button className="btn sm" data-testid="memws-directive-new" onClick={() => beginEdit()}>
        + New directive
      </button>
      <table className="tbl mono small">
        <thead><tr><th>name</th><th>priority</th><th>active</th><th>tags</th><th></th></tr></thead>
        <tbody>
          {items.map((d) => (
            <tr key={d.id} data-testid={`memws-directive-row-${d.id}`}>
              <td>{d.name}</td>
              <td>{d.priority}</td>
              <td>{d.is_active ? 'yes' : 'no'}</td>
              <td>{d.tags.join(', ')}</td>
              <td>
                <button className="btn ghost sm" onClick={() => beginEdit(d)}>Edit</button>
                <button
                  className="btn ghost sm"
                  onClick={() => del.mutate({ sub: `v1/default/banks/${bank}/directives/${d.id}` })}
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editingId ? (
        <div className="memws-form" data-testid="memws-directive-form">
          <label>Name<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
          <label>Content<textarea value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} /></label>
          <label>Priority<input type="number" value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })} /></label>
          <label>
            <input type="checkbox" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} /> Active
          </label>
          <label>Tags<input value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} /></label>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn sm" data-testid="memws-directive-save" onClick={submit}>Save</button>
            <button className="btn ghost sm" onClick={() => setEditingId(null)}>Cancel</button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

// ── Tab 5: Operations ───────────────────────────────────────────────────

const OperationsTab: React.FC<{ bank: string }> = ({ bank }) => {
  const list = useMemoryEngineQuery<{ operations: OperationRow[] }>(
    `v1/default/banks/${encodeURIComponent(bank)}/operations?limit=100`,
    { refetchInterval: 10_000 },
  )
  const retry = useMemoryEngineMutation('post', 'operations')
  const [status, setStatus] = useState<string>('all')

  if (list.isError) return <EngineError error={list.error} what="operations" onRetry={() => list.refetch()} />
  const ops = list.data?.operations || []
  const counts = operationStatusCounts(ops)
  const filtered = operationsByStatus(ops, status)

  return (
    <div data-testid="memws-operations">
      <div className="memws-chips">
        {['all', 'pending', 'processing', 'completed', 'failed'].map((s) => (
          <button
            key={s}
            className={`btn ghost sm${status === s ? ' active' : ''}`}
            data-testid={`memws-op-filter-${s}`}
            onClick={() => setStatus(s)}
          >
            {s} {s !== 'all' ? `(${counts[s] || 0})` : `(${ops.length})`}
          </button>
        ))}
      </div>
      <table className="tbl mono small">
        <thead><tr><th>id</th><th>type</th><th>status</th><th>created</th><th></th></tr></thead>
        <tbody>
          {filtered.map((o) => (
            <tr key={o.id} data-testid={`memws-op-row-${o.id}`}>
              <td>{o.id.slice(0, 8)}</td>
              <td>{(o as Record<string, unknown>).operation_type as string}</td>
              <td>{o.status}</td>
              <td>{(o as Record<string, unknown>).created_at as string}</td>
              <td>
                {o.status === 'failed' ? (
                  <button
                    className="btn ghost sm"
                    data-testid={`memws-op-retry-${o.id}`}
                    disabled={retry.isPending}
                    onClick={() => retry.mutate({ sub: `v1/default/banks/${bank}/operations/${o.id}/retry` })}
                  >
                    Retry
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Tab 6: Agents ───────────────────────────────────────────────────────

const AgentsTab: React.FC = () => {
  const agents = useMemoryWorkspaceAgents()
  const [harness, setHarness] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  if (agents.isError) return <EngineError error={agents.error} what="agents" onRetry={() => agents.refetch()} />
  const harnesses = agents.data?.install.harnesses || []
  const active = harness || harnesses[0] || ''
  const command = active ? installCommandFor(agents.data?.install.command || '', active) : ''

  return (
    <div data-testid="memws-agents">
      <Card title="Coding-agent banks">
        <ul className="mono small">
          {(agents.data?.banks || []).map((b) => <li key={b}>{b}</li>)}
        </ul>
      </Card>
      <Card title="Install command">
        <div className="memws-chips">
          {harnesses.map((h) => (
            <button key={h} className={`btn ghost sm${active === h ? ' active' : ''}`} onClick={() => setHarness(h)}>
              {h}
            </button>
          ))}
        </div>
        <pre className="mono small memws-pre" data-testid="memws-install-command">{command}</pre>
        <button
          className="btn sm"
          onClick={() => {
            navigator.clipboard?.writeText(command)
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          }}
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </Card>
    </div>
  )
}

// ── Tab 7: Models ────────────────────────────────────────────────────────

const DECISION_TOGGLE_KEYS = [
  'recall_decision_model_prune',
  'recall_decision_model_dedup',
  'consolidation_use_decision_model',
  'reflect_use_decision_model',
  'mental_model_delta_use_decision_model',
] as const

const ModelsTab: React.FC<{ bank: string }> = ({ bank }) => {
  const models = useMemoryModels()
  const config = useMemoryEngineQuery<{ updates?: Record<string, boolean>; [k: string]: unknown }>(
    `v1/default/banks/${encodeURIComponent(bank)}/config`,
  )
  const patchConfig = useMemoryEngineMutation<{ updates: Record<string, boolean> }>('patch', 'config')

  if (models.isError) return <EngineError error={models.error} what="models" onRetry={() => models.refetch()} />

  const cfg = (config.data || {}) as Record<string, unknown>

  return (
    <div data-testid="memws-models">
      <Card title="Identity">
        <div className="mono small">embeddings: {models.data?.embeddings?.model}</div>
        <div className="mono small">reranker: {models.data?.reranker?.model}</div>
        <div className="mono small">decision: {models.data?.decision?.model}</div>
      </Card>
      <Card title={`Decision-model toggles — ${bank}`}>
        {config.isError ? (
          <EngineError error={config.error} what="bank config" onRetry={() => config.refetch()} />
        ) : (
          <ul className="memws-toggles">
            {DECISION_TOGGLE_KEYS.map((key) => (
              <li key={key}>
                <label>
                  <input
                    type="checkbox"
                    data-testid={`memws-toggle-${key}`}
                    checked={!!cfg[key]}
                    disabled={patchConfig.isPending}
                    onChange={(e) =>
                      patchConfig.mutate({
                        sub: `v1/default/banks/${bank}/config`,
                        body: { updates: { [key]: e.target.checked } },
                      })
                    }
                  />
                  {key}
                </label>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

// ── Shell ────────────────────────────────────────────────────────────────

export const MemoryWorkspaceView: React.FC<{ param?: string | null }> = ({ param = null }) => {
  const tab = tabFromParam(param)
  const bankList = useBankList()
  const banks = bankList.data?.banks || []
  const [bank, setBank] = useState<string | null>(null)
  const effectiveBank = bank || banks[0]?.bank_id || 'shared'
  const consoleUrl = hindsightConsoleUrl(typeof window !== 'undefined' ? window.location.hostname : 'localhost')

  const goTab = (t: Tab) => {
    window.location.hash = `#memworkspace/${t}`
  }

  return (
    <div className="view memws" data-testid="memws-view">
      <div className="memws-header">
        <h1>Memory</h1>
        <select
          className="memws-bank-select"
          data-testid="memws-bank-select"
          value={effectiveBank}
          onChange={(e) => setBank(e.target.value)}
        >
          {banks.map((b) => (
            <option key={b.bank_id} value={b.bank_id}>{b.bank_id}</option>
          ))}
        </select>
        <a className="btn ghost sm" href={consoleUrl} target="_blank" rel="noreferrer" data-testid="memws-console-link">
          Open console ↗
        </a>
      </div>
      <div className="memws-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            className={`memws-tab${tab === t ? ' active' : ''}`}
            data-testid={`memws-tab-${t}`}
            onClick={() => goTab(t)}
          >
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>
      <div className="memws-body">
        {tab === 'overview' && <OverviewTab bank={effectiveBank} />}
        {tab === 'kb' && <KnowledgeBaseTab bank={effectiveBank} />}
        {tab === 'mental-models' && <MentalModelsTab bank={effectiveBank} />}
        {tab === 'directives' && <DirectivesTab bank={effectiveBank} />}
        {tab === 'operations' && <OperationsTab bank={effectiveBank} />}
        {tab === 'agents' && <AgentsTab />}
        {tab === 'models' && <ModelsTab bank={effectiveBank} />}
      </div>
    </div>
  )
}

export default MemoryWorkspaceView
