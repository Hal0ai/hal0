// Memory ▸ Knowledge: what a bank has written down from its facts, and the rules it gives
// agents. For the bank the scope bar names:
//
//   - a summary: knowledge pages, mental models, directives, the last refresh that worked;
//   - Refresh: bring everything stale up to date, and how often the bank may refresh itself;
//   - the knowledge pages as their tree, the mental models as a list, and one reader for both;
//   - the bank's directives.
//
// A knowledge page is a mental model the engine files in a tree. The mental-models list
// carries both, so it is split here (knowledge-pure.ts) and nothing is listed twice.
//
// What is open is the address: `?bank=…&page=…`. `page` is a page's id or a mental model's.
// Choosing either rewrites the address in place, so a link opens the same thing; choosing a
// bank closes what was open, which belonged to the bank that was left. An address with no bank
// gets the first bank written into it, as on Facts.
//
// A refresh is queued when its request returns and finishes later. The tab remembers each one
// it started (`runs`), refetches the bank's knowledge while any is running, and reads the
// outcome off the model's own stamps. That state is the tab's, so it is dropped when the bank
// changes: the part of the page under the scope bar is keyed by the bank.
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import './memory-area.css'
import {
  useDirectives,
  useKnowledgePolling,
  useKnowledgeTree,
  useMentalModelRefresh,
  useMentalModels,
} from '@/api/hooks/useHindsight'
import {
  groupChip,
  groupHelper,
  lastSuccessAt,
  listStatus,
  refreshAll,
  resolveSelection,
  shortError,
  splitKnowledge,
  staleTargets,
  statusCounts,
  watchOutcome,
} from './knowledge-pure'
import { memoryTabHref } from './memory-route-pure'
import { Directives } from './knowledge/Directives.jsx'
import { ModelForm, ModelsCard } from './knowledge/ModelsCard.jsx'
import { PageTree } from './knowledge/PageTree.jsx'
import { Reader } from './knowledge/Reader.jsx'
import { RefreshCard } from './knowledge/RefreshCard.jsx'
import { EmptyState } from './shared/EmptyState.jsx'
import { GoLink } from './shared/GoLink.jsx'
import { NoticeBar } from './shared/NoticeBar.jsx'
import { ScopeBar } from './shared/ScopeBar.jsx'
import { StatStrip } from './shared/StatStrip.jsx'
import { StatusChip } from './shared/StatusChip.jsx'
import { formatDateTime, formatRelativeTime } from './shared/format-pure'

const ENGINE_DOWN = 'The memory engine is not answering.'

const SUMMARY_LABELS = ['Knowledge pages', 'Mental models', 'Directives', 'Last successful refresh']

function Card({ children, testId }) {
  return (
    <section className="card ma-card" aria-label="Knowledge" data-testid={testId}>
      {children}
    </section>
  )
}

function chipFor(counts) {
  const chip = groupChip(counts)
  return chip ? <StatusChip tone={chip.tone}>{chip.word}</StatusChip> : null
}

function PagesCard({ tree, pages, status, openId, onOpen, loading, error, onRetry }) {
  const headingId = useId()
  let body
  if (loading) {
    body = (
      <div className="ma-card-body" aria-busy="true" data-testid="ma-kn-pages-loading">
        <span className="ma-skel ma-skel-line" aria-hidden="true" />
        <span className="ma-skel ma-skel-line" aria-hidden="true" />
        <span className="ma-skel ma-skel-line" aria-hidden="true" />
        <span className="sr-only">Loading</span>
      </div>
    )
  } else if (error) {
    body = (
      <EmptyState
        title="The knowledge pages did not load"
        action={
          <button type="button" className="btn ghost sm" onClick={onRetry}>
            Check again
          </button>
        }
        data-testid="ma-kn-pages-error"
      >
        {shortError(error)}
      </EmptyState>
    )
  } else if (pages.length === 0) {
    body = (
      <p className="ma-body ma-kn-text" data-testid="ma-kn-pages-empty">
        No knowledge pages in this bank. A page is a document the engine writes from the bank&apos;s facts
        and keeps up to date.
      </p>
    )
  } else {
    body = (
      <PageTree
        tree={tree}
        pages={pages}
        openId={openId}
        onOpen={onOpen}
        rowStale={status.rowStale}
        rowFailed={status.rowFailed}
        labelledBy={headingId}
      />
    )
  }
  return (
    <section className="card ma-card" aria-labelledby={headingId} data-testid="ma-kn-pages-card">
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id={headingId}>
          Knowledge pages
        </h2>
        <span className="ma-card-gap" />
        {!loading && !error && pages.length > 0 && (
          <span className="ma-card-meta" data-testid="ma-kn-pages-meta">
            {status.meta}
          </span>
        )}
      </div>
      <div className="ma-kn-scroll">{body}</div>
    </section>
  )
}

/** A bank with no pages and no mental models: what belongs here, and how it comes to exist. */
function EmptyBank({ bank }) {
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState(null)
  return (
    <Card testId="ma-kn-empty">
      <EmptyState
        title={
          <>
            No knowledge pages or mental models in <span className="ma-mono">{bank}</span> yet
          </>
        }
        action={
          !adding && (
            <button
              type="button"
              className="btn ghost"
              onClick={() => {
                setAdded(null)
                setAdding(true)
              }}
              data-testid="ma-kn-model-add"
            >
              Add a mental model
            </button>
          )
        }
      >
        Knowledge pages are documents the engine writes from this bank&apos;s facts and keeps up to date; a
        mental model is one question it keeps an answer ready for. Pages are created from outside the
        dashboard, by whatever sets the bank up:{' '}
        <span className="ma-mono ma-em">hal0 memory spec apply</span> creates the packaged ones. A mental
        model can be added here.
      </EmptyState>
      {adding && (
        <div className="ma-kn-add">
          <ModelForm
            bank={bank}
            onClose={(message) => {
              setAdding(false)
              setAdded(message)
            }}
          />
        </div>
      )}
      <p className="ma-helper ma-kn-added" role="status" data-testid="ma-kn-model-added">
        {added}
      </p>
    </Card>
  )
}

function KnowledgeBank({ bank, area }) {
  const { route, replaceRoute, engine } = area
  const blockedReason = engine === 'reachable' ? null : ENGINE_DOWN

  const treeQuery = useKnowledgeTree(bank)
  const modelsQuery = useMentalModels(bank)
  const directivesQuery = useDirectives(bank)
  const refresh = useMentalModelRefresh()

  // modelId → { phase: 'starting'|'running'|'done'|'failed'|'overdue'|'error', watch?, error? }
  const [runs, setRuns] = useState({})
  const [bulk, setBulk] = useState(null)
  const watching = Object.values(runs).some((run) => run.phase === 'running')
  const round = useKnowledgePolling(bank, watching)
  const readerRef = useRef(null)

  const treeLoading = treeQuery.data === undefined && !treeQuery.isError
  const treeFailed = !!treeQuery.isError && treeQuery.data === undefined
  const modelsLoading = modelsQuery.data === undefined && !modelsQuery.isError
  const modelsFailed = !!modelsQuery.isError && modelsQuery.data === undefined
  const directivesLoading = directivesQuery.data === undefined && !directivesQuery.isError
  const directivesFailed = !!directivesQuery.isError && directivesQuery.data === undefined
  const loading = treeLoading || modelsLoading

  const { pages, models } = useMemo(
    () => splitKnowledge(treeQuery.data, modelsQuery.data?.items),
    [treeQuery.data, modelsQuery.data],
  )
  const pageCounts = statusCounts(pages)
  const modelCounts = statusCounts(models)
  const targets = useMemo(() => staleTargets(pages, models), [pages, models])

  // A started refresh is over when its model's stamps move, or when it has run too long.
  useEffect(() => {
    setRuns((current) => {
      const stamps = new Map()
      for (const page of pages) if (page.modelId) stamps.set(page.modelId, page)
      for (const model of models) stamps.set(model.id, model)
      let next = current
      for (const [id, run] of Object.entries(current)) {
        if (run.phase !== 'running') continue
        const outcome = watchOutcome(run.watch, stamps.get(id), Date.now())
        if (outcome === 'running') continue
        if (next === current) next = { ...current }
        next[id] = { phase: outcome }
      }
      return next
    })
  }, [pages, models, round])

  const selection = loading ? null : resolveSelection(route.page, pages, models)
  const openPageId = selection?.kind === 'page' ? selection.page.id : null
  const openModelId = selection?.kind === 'model' ? selection.model.id : null
  const openRunId = selection?.kind === 'page' ? selection.page.modelId : openModelId

  const open = (id) => {
    replaceRoute({ page: id })
    // On a narrow screen the reader is under the lists, not beside them (the columns wrapped):
    // bring it up, or what was just opened is off the screen.
    const el = readerRef.current
    const lists = el?.previousElementSibling
    if (el && lists && typeof el.scrollIntoView === 'function') {
      const stacked = el.getBoundingClientRect().left < lists.getBoundingClientRect().right - 1
      if (stacked) el.scrollIntoView({ block: 'start' })
    }
  }

  // Ask for one refresh, and start watching for its end. Rejects when the request fails.
  const startOne = async (target) => {
    const stamps =
      pages.find((page) => page.modelId === target.modelId) ??
      models.find((model) => model.id === target.modelId) ??
      null
    setRuns((current) => ({ ...current, [target.modelId]: { phase: 'starting' } }))
    try {
      await refresh.mutateAsync({ bank, id: target.modelId })
    } catch (error) {
      setRuns((current) => ({
        ...current,
        [target.modelId]: { phase: 'error', error: shortError(error) },
      }))
      throw error
    }
    setRuns((current) => ({
      ...current,
      [target.modelId]: {
        phase: 'running',
        watch: {
          refreshedAt: stamps?.lastRefreshedAt ?? null,
          failedAt: stamps?.lastFailedAt ?? null,
          since: Date.now(),
        },
      },
    }))
  }

  const refreshOne = (target) => {
    if (blockedReason || !target.modelId) return
    // The reader shows the reason; nothing else waits on this.
    startOne(target).catch(() => {})
  }

  const refreshEverything = async () => {
    if (blockedReason || targets.length === 0 || bulk?.phase === 'running') return
    setBulk({ phase: 'running', at: 1, total: targets.length, name: targets[0].name })
    const result = await refreshAll(targets, startOne, ({ at, total, target }) =>
      setBulk({ phase: 'running', at, total, name: target.name }),
    )
    setBulk(
      result.failed
        ? {
            phase: 'stopped',
            started: result.started,
            total: result.total,
            name: result.failed.target.name,
            reason: result.failed.reason,
          }
        : { phase: 'done', started: result.started },
    )
  }

  const directives = directivesQuery.data?.items
  const directiveCount = Array.isArray(directives) ? directives.length : null
  const activeCount = Array.isArray(directives) ? directives.filter((d) => d.is_active !== false).length : null
  const last = lastSuccessAt([...pages, ...models])
  const lastRelative = formatRelativeTime(last)
  const lastExact = formatDateTime(last)
  const none = pageCounts.total + modelCounts.total === 0

  const cells = [
    {
      id: 'pages',
      label: SUMMARY_LABELS[0],
      value: treeFailed ? null : pageCounts.total,
      unit: pageCounts.total === 1 ? 'page' : 'pages',
      chip: chipFor(pageCounts),
      helper: treeFailed ? 'did not load' : groupHelper(pageCounts),
    },
    {
      id: 'models',
      label: SUMMARY_LABELS[1],
      value: modelsFailed ? null : modelCounts.total,
      unit: modelCounts.total === 1 ? 'model' : 'models',
      chip: chipFor(modelCounts),
      helper: modelsFailed ? 'did not load' : (groupHelper(modelCounts) ?? 'besides the knowledge pages'),
    },
    {
      id: 'directives',
      label: SUMMARY_LABELS[2],
      value: directivesFailed ? null : directiveCount,
      unit: directiveCount === 1 ? 'directive' : 'directives',
      helper: directivesFailed
        ? 'did not load'
        : directiveCount
          ? `${activeCount} active`
          : 'standing instructions for agents',
    },
    {
      id: 'last-refresh',
      label: SUMMARY_LABELS[3],
      // A relative time is a name-sized value, not a count. A bank with nothing to refresh has
      // no last refresh to report.
      value: none || treeFailed || modelsFailed ? null : (lastRelative ?? 'never'),
      helper: none ? 'nothing to refresh yet' : last ? lastExact : 'no refresh has succeeded yet',
    },
  ]

  const empty = !loading && !treeFailed && !modelsFailed && none

  return (
    <>
      {blockedReason && (
        <NoticeBar
          tone="err"
          status="unreachable"
          action={<GoLink href={memoryTabHref('health', route)}>Open Health</GoLink>}
          data-testid="ma-kn-engine-down"
        >
          The memory engine is not answering. What is shown was read before it stopped, and nothing can be
          refreshed until it answers.
        </NoticeBar>
      )}
      <StatStrip
        label="Knowledge summary"
        loading={loading || directivesLoading}
        cells={cells}
        data-testid="ma-kn-summary"
      />
      <RefreshCard
        bank={bank}
        loading={loading}
        total={pageCounts.total + modelCounts.total}
        stale={targets.length}
        bulk={bulk}
        onRefreshAll={refreshEverything}
        blockedReason={blockedReason}
      />
      {empty ? (
        <EmptyBank bank={bank} />
      ) : (
        <div className="ma-kn-cols">
          <div className="ma-kn-side">
            <PagesCard
              tree={treeQuery.data}
              pages={pages}
              status={listStatus(pageCounts, 'page', 'pages')}
              openId={openPageId}
              onOpen={open}
              loading={treeLoading}
              error={treeFailed ? (treeQuery.error ?? true) : null}
              onRetry={() => treeQuery.refetch()}
            />
            <ModelsCard
              bank={bank}
              models={models}
              status={listStatus(modelCounts, 'model', 'models')}
              openId={openModelId}
              onOpen={open}
              loading={modelsLoading}
              error={modelsFailed ? (modelsQuery.error ?? true) : null}
              onRetry={() => modelsQuery.refetch()}
            />
          </div>
          <div className="ma-kn-main" ref={readerRef}>
            <Reader
              bank={bank}
              selection={selection}
              loading={loading}
              run={openRunId ? runs[openRunId] : undefined}
              onRefresh={refreshOne}
              blockedReason={blockedReason}
              operationsHref={memoryTabHref('operations', route)}
            />
          </div>
        </div>
      )}
      <Directives bank={bank} />
    </>
  )
}

function KnowledgeLoading() {
  return (
    <>
      <StatStrip
        label="Knowledge summary"
        loading
        cells={SUMMARY_LABELS.map((label) => ({ label, value: null }))}
        data-testid="ma-kn-summary"
      />
      <Card testId="ma-knowledge-loading">
        <div className="ma-card-body" aria-busy="true">
          <span className="ma-skel ma-skel-line" aria-hidden="true" />
          <span className="ma-skel ma-skel-line" aria-hidden="true" />
          <span className="ma-skel ma-skel-line" aria-hidden="true" />
          <span className="sr-only">Loading</span>
        </div>
      </Card>
    </>
  )
}

/** @param {{ area: object }} props What MemoryArea holds: the route, the engine check, the banks. */
export function KnowledgeTab({ area }) {
  const { route, replaceRoute, engine, banks, banksLoading, banksFailed, banksQuery } = area
  const ready = engine === 'reachable' && !banksLoading && !banksFailed
  const first = banks[0]?.bank_id ?? null
  const listed = !!route.bank && banks.some((b) => b.bank_id === route.bank)
  // A long bank list arrives one page at a time: a bank that is not on this page may still exist.
  const paged = (banksQuery.data?.total ?? 0) > banks.length

  // An address with no bank is completed in place, so a reload or a copied link names the bank.
  useEffect(() => {
    if (ready && !route.bank && first) replaceRoute({ bank: first })
  }, [ready, route.bank, first, replaceRoute])

  let view
  if (engine === 'loading' || (engine === 'reachable' && banksLoading)) {
    view = <KnowledgeLoading />
  } else if (engine === 'fallback') {
    view = (
      <Card testId="ma-knowledge-fallback">
        <EmptyState title="Memory is not running on Hindsight">
          Knowledge pages, mental models and directives belong to a Hindsight bank. Memory is running on a
          fallback engine, which has none.
        </EmptyState>
      </Card>
    )
  } else if (engine === 'unreachable' && banks.length === 0) {
    // Nothing was read before the engine stopped answering, so there is nothing to show.
    view = (
      <Card testId="ma-knowledge-unreachable">
        <EmptyState
          title="The memory engine is not answering"
          action={<GoLink href={memoryTabHref('health', route)}>Open Health</GoLink>}
        >
          Knowledge is read from the engine, so none can be shown until it answers. Health says what is
          known and where to look.
        </EmptyState>
      </Card>
    )
  } else if (banksFailed) {
    view = (
      <Card testId="ma-knowledge-banks-error">
        <EmptyState
          title="The bank list did not load"
          action={
            <button type="button" className="btn ghost sm" onClick={() => banksQuery.refetch()}>
              Check again
            </button>
          }
        >
          The engine answered its health check but not the request for its banks, so there is no bank to
          open.
        </EmptyState>
      </Card>
    )
  } else if (banks.length === 0) {
    view = (
      <Card testId="ma-knowledge-no-banks">
        <EmptyState title="No banks yet">
          A bank appears here the first time an agent saves a memory to it. Its knowledge pages, mental
          models and directives are shown on this tab.
        </EmptyState>
      </Card>
    )
  } else {
    const scope = (
      <ScopeBar
        banks={banks.map((b) => ({ id: b.bank_id }))}
        bank={route.bank}
        // What is open belongs to the bank being left.
        onBankChange={(id) => replaceRoute({ bank: id, page: null })}
      />
    )
    if (route.bank && !listed && !paged) {
      // The address names a bank the engine does not list. Its knowledge is not shown under
      // another bank's name: say so, and leave the choice to the scope bar.
      view = (
        <>
          {scope}
          <Card testId="ma-knowledge-unknown-bank">
            <EmptyState title="This bank is not in the engine's list">
              The address names <span className="ma-mono ma-em">{route.bank}</span>, which the engine does
              not list. Choose a bank above.
            </EmptyState>
          </Card>
        </>
      )
    } else {
      view = (
        <>
          {scope}
          {route.bank ? <KnowledgeBank key={route.bank} bank={route.bank} area={area} /> : null}
        </>
      )
    }
  }

  return (
    <div className="ma-page ma-kn" data-testid="ma-knowledge">
      {view}
    </div>
  )
}
