// Memory ▸ Operations: the engine's queue. What is waiting, running and failed, why, and what to
// do about the ones that failed.
//
// Top to bottom:
//   scope bar       every bank, or one. The choice is the address (`?bank=`), nothing else.
//   the cause       only while storing memories is itself failing: the reason, and a link to
//                   Health, where the fix is. No retry is offered there.
//   the all-clear   only when nothing waits and nothing failed.
//   the figures     waiting, failed, oldest waiting, and the two consolidation figures.
//   queue by bank   only for every bank: the banks that hold the figures above.
//   failed          retry all, and discard (behind a confirm).
//   operations      the rows, with a status filter, page controls and "Consolidate now".
//
// The figures are the health route's (`GET /api/memory/health`), read through operations-data.
// That route decides whether extraction is the step to fix first; while it is, every retry on
// this tab is disabled and says why, because a retry would fail. Discard stays available.
//
// The engine check MemoryArea holds and the health answer both say whether the engine answers.
// Either one saying it does not puts the whole tab in its unreachable state.
import { useEffect, useRef, useState } from 'react'
import './memory-area.css'
import { memoryTabHref } from './memory-route-pure'
import {
  collectFailed,
  discardOperation,
  retryOperation,
  useBankConsolidation,
  useOperationLists,
  useQueueHealth,
  useQueueRefresh,
} from './operations-data'
import {
  BULK_CAP,
  banksForFilter,
  lastCompletedAt,
  plainError,
  queueFigures,
  queueIsClear,
  readQueueHealth,
} from './operations-pure'
import { FailedCard } from './operations/FailedCard.jsx'
import { OperationsList } from './operations/OperationsList.jsx'
import { BreakdownCard, CauseCard, ClearCard, QueueStrip } from './operations/QueueSummary.jsx'
import { EmptyState } from './shared/EmptyState.jsx'
import { GoLink } from './shared/GoLink.jsx'
import { ScopeBar } from './shared/ScopeBar.jsx'
import { StatusChip } from './shared/StatusChip.jsx'

/** The scope bar's id for "every bank". No bank has an empty id. */
const ALL_BANKS = ''

function Frame({ state, children }) {
  return (
    <div className="ma-ops" data-testid="ma-operations" data-state={state}>
      {children}
    </div>
  )
}

function Card({ testId, chip, children }) {
  return (
    <section className="card ma-card" aria-label="Operations" data-testid={testId}>
      {chip && (
        <div className="ma-card-head">
          <h2 className="ma-eyebrow">Operations</h2>
          <span className="ma-card-gap" />
          {chip}
        </div>
      )}
      {children}
    </section>
  )
}

function Skeleton() {
  return (
    <section className="card ma-card" aria-label="Operations">
      <div className="ma-card-body" aria-busy="true">
        {Array.from({ length: 4 }, (_, at) => (
          <span className="ma-skel ma-skel-line" aria-hidden="true" key={at} />
        ))}
        <span className="sr-only">Loading</span>
      </div>
    </section>
  )
}

// The figures are placeholders until the health answer arrives: no number is shown, not a zero.
const NO_FIGURES = queueFigures(null, null)

// Runs "retry all" or "discard" over the failed operations of `bankIds`: read them, then one
// request each, stopping at the first that fails. It stops asking when the tab is left.
function useBulkRun(refresh) {
  const [run, setRun] = useState(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const start = async (action, bankIds) => {
    if (run && run.phase !== 'finished') return
    const finish = (result) => {
      if (!alive.current) return
      setRun({ phase: 'finished', action, more: false, error: null, ...result })
      refresh(bankIds)
    }
    setRun({ phase: 'collecting', action })
    let found
    try {
      found = await collectFailed(bankIds, BULK_CAP)
    } catch (error) {
      finish({ done: 0, total: 0, error: plainError(error, 'The failed operations could not be read.') })
      return
    }
    const total = found.targets.length
    const act = action === 'retry' ? retryOperation : discardOperation
    let done = 0
    for (const target of found.targets) {
      if (!alive.current) return
      setRun({ phase: 'running', action, done, total })
      try {
        await act(target.bank, target.id)
      } catch (error) {
        finish({ done, total, error: plainError(error) })
        return
      }
      done += 1
    }
    finish({ done, total, more: found.more })
  }

  return [run, start]
}

function Ready({ area, healthQuery, health }) {
  const { route, replaceRoute, banks, banksQuery } = area
  const bank = route.bank
  const bankIds = banks.map((b) => b.bank_id)
  const listed = bank !== null && bankIds.includes(bank)
  // A long bank list arrives one page at a time: a bank that is not on this page may still exist.
  const paged = (banksQuery.data?.total ?? 0) > banks.length
  const unknownBank = bank !== null && !listed && !paged

  const report = health.kind === 'ready' ? health : null
  const queue = report?.queue ?? null
  const statsQuery = useBankConsolidation(bank !== null && !unknownBank ? bank : null)
  const figures = report ? queueFigures(queue, bank, statsQuery.data) : NO_FIGURES
  const clear = !!report && !report.cause && queueIsClear(figures)

  const refresh = useQueueRefresh()
  const [run, startBulk] = useBulkRun(refresh)

  // When the last operation finished: asked for only once the queue is known to be clear.
  const scopeBanks = bank !== null ? [bank] : bankIds
  const finished = useOperationLists(scopeBanks, { status: 'completed', limit: 1, enabled: clear })
  const lastDone = clear ? lastCompletedAt(finished.entries) : null

  const healthHref = memoryTabHref('health', route)
  const scopeBar = (
    <ScopeBar
      banks={[{ id: ALL_BANKS, label: 'All banks' }, ...bankIds.map((id) => ({ id }))]}
      bank={bank ?? ALL_BANKS}
      onBankChange={(id) => replaceRoute({ bank: id || null })}
    />
  )

  // The address names a bank the engine does not list. Another bank's operations are not shown
  // under its name: say so, and leave the choice to the scope bar.
  if (unknownBank) {
    return (
      <Frame state="unknown-bank">
        {scopeBar}
        <Card testId="ma-ops-unknown-bank">
          <EmptyState title="This bank is not in the engine's list">
            The address names <span className="ma-mono ma-em">{bank}</span>, which the engine does not list.
            Choose a bank above, or all banks.
          </EmptyState>
        </Card>
      </Frame>
    )
  }

  const stripLoading =
    health.kind === 'loading' || (bank !== null && statsQuery.data === undefined && !statsQuery.isError)
  // The banks a many-at-once action touches: the one in scope, or every bank with a failure.
  const failedBanks = bank !== null ? [bank] : banksForFilter('failed', bankIds, queue?.by_bank ?? null)

  return (
    <Frame state={health.kind === 'ready' ? (clear ? 'clear' : 'ready') : health.kind}>
      {scopeBar}
      {report?.cause && <CauseCard cause={report.cause} route={route} />}
      {clear && <ClearCard bank={bank} lastCompletedAt={lastDone} />}
      {health.kind === 'failed' ? (
        <Card testId="ma-ops-summary-error">
          <EmptyState
            title="The queue summary did not load"
            action={
              <button
                type="button"
                className="btn ghost sm"
                disabled={healthQuery.isFetching}
                onClick={() => healthQuery.refetch()}
                data-testid="ma-ops-summary-recheck"
              >
                {healthQuery.isFetching ? 'Checking' : 'Check again'}
              </button>
            }
          >
            hal0 did not answer the request for the queue figures, so none is shown here. The list below is
            read separately.
          </EmptyState>
        </Card>
      ) : (
        <QueueStrip figures={figures} report={report} bank={bank} loading={stripLoading} />
      )}
      {bank === null && <BreakdownCard queue={queue} />}
      <FailedCard
        count={figures.failed}
        bank={bank}
        blocked={!!report?.cause}
        healthHref={healthHref}
        run={run}
        onRetryAll={() => startBulk('retry', failedBanks)}
        onDiscardAll={() => startBulk('discard', failedBanks)}
      />
      <OperationsList
        bank={bank}
        bankIds={bankIds}
        byBank={queue?.by_bank ?? null}
        blocked={!!report?.cause}
        healthHref={healthHref}
        onChanged={refresh}
      />
    </Frame>
  )
}

/** @param {{ area: object }} props What MemoryArea holds: the route, the engine check, the banks. */
export function OperationsTab({ area }) {
  const { route, engine, banks, banksLoading, banksFailed, banksQuery } = area
  const healthQuery = useQueueHealth()
  const health = readQueueHealth(healthQuery)
  const healthLink = <GoLink href={memoryTabHref('health', route)}>Open Health</GoLink>

  if (engine === 'fallback' || health.kind === 'unsupported') {
    return (
      <Frame state="unsupported">
        <Card testId="ma-ops-fallback">
          <EmptyState title="Memory is not running on Hindsight">
            The queue belongs to the Hindsight engine. Memory is running on another engine, which has none.
          </EmptyState>
        </Card>
      </Frame>
    )
  }

  if (engine === 'unreachable' || health.kind === 'unreachable') {
    return (
      <Frame state="unreachable">
        <Card testId="ma-ops-unreachable" chip={<StatusChip tone="err">unreachable</StatusChip>}>
          <EmptyState title="The memory engine is not answering" action={healthLink}>
            Operations are read from the engine, so none can be shown until it answers, and none is counted
            as zero. Health says what is known and where to look.
          </EmptyState>
        </Card>
      </Frame>
    )
  }

  if (health.kind === 'off') {
    return (
      <Frame state="off">
        <Card testId="ma-ops-off">
          <EmptyState title="Memory is turned off">
            There is no queue while memory is off. Turn it on in engine settings.
          </EmptyState>
        </Card>
      </Frame>
    )
  }

  if (engine === 'loading' || banksLoading) {
    return (
      <Frame state="loading">
        <QueueStrip figures={NO_FIGURES} report={null} bank={null} loading />
        <Skeleton />
      </Frame>
    )
  }

  if (banksFailed) {
    return (
      <Frame state="banks-error">
        <Card testId="ma-ops-banks-error">
          <EmptyState
            title="The bank list did not load"
            action={
              <button type="button" className="btn ghost sm" onClick={() => banksQuery.refetch()}>
                Check again
              </button>
            }
          >
            The engine answered its health check but not the request for its banks, so there is no bank to
            list operations for.
          </EmptyState>
        </Card>
      </Frame>
    )
  }

  if (banks.length === 0) {
    return (
      <Frame state="no-banks">
        <Card testId="ma-ops-no-banks">
          <EmptyState title="No banks yet">
            A bank appears the first time an agent saves a memory to it, and its operations appear here.
          </EmptyState>
        </Card>
      </Frame>
    )
  }

  return <Ready area={area} healthQuery={healthQuery} health={health} />
}
