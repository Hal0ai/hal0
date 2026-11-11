// Memory ▸ Health: does memory need you, and what first.
//
// The page is read from ONE answer, `GET /api/memory/health`, which MemoryArea holds for its
// summary line and hands down as `area.health`. Nothing here asks a bank for anything.
//
// Top to bottom, when the engine answers:
//   the verdict      "Nothing needs attention", or how many things need you and which is first
//   the repair path  the steps in the route's order: the one to do now, the ones that wait on
//                    it and say so, and the clear ones as one line each (health/RepairPath.jsx)
//   engine facts     engine, extraction, embedder, reranker, decision model
//   embedder line    only for the ordinary upgraded box: one quiet sentence, nothing to do
//   banks            every bank with its facts, documents, and waiting and failed operations
//
// Two older reads remain, for the strip only (see `engineStrip` in health-pure.ts): the models
// route for the reranker and the decision model, and graph status for the name of an upstream
// extraction model and whether hal0's routing of extraction is switched off. The verdict and
// the path never wait on them.
//
// The other states (loading, hal0 did not answer, memory off, another engine, engine
// unreachable) are in health/HealthStates.jsx. None of them shows a figure.
import './memory-area.css'
import { useMemoryGraphStatus, useMemoryModels } from '@/api/hooks/useMemory'
import { bankListMissing } from './area-pure'
import { banksNote, buildPath, embedderNotice, embedderView, engineStrip, healthVerdict } from './health-pure'
import { BanksCard } from './health/BanksCard.jsx'
import {
  HealthDown,
  HealthFailed,
  HealthFallback,
  HealthLoading,
  HealthOff,
} from './health/HealthStates.jsx'
import { Segments, VerdictCard } from './health/RepairPath.jsx'
import { StatStrip } from './shared/StatStrip.jsx'
import { StatusChip } from './shared/StatusChip.jsx'

const pending = (query) => query.data === undefined && !query.isError

function HealthReady({ area, report }) {
  const graphQuery = useMemoryGraphStatus()
  const modelsQuery = useMemoryModels()
  const path = buildPath(report)
  const verdict = healthVerdict(report, path)
  const stripLoading = pending(graphQuery) || pending(modelsQuery)
  const cells = engineStrip({ report, graph: graphQuery.data, models: modelsQuery.data })
  const note = embedderNotice(embedderView(report.embedder))

  return (
    <>
      <VerdictCard verdict={verdict} path={path} route={area.route} checkedAt={report.checked_at || null} />
      <StatStrip
        label="Engine"
        loading={stripLoading}
        data-testid="ma-health-strip"
        cells={cells.map((cell) => ({
          id: cell.id,
          label: cell.label,
          value: cell.value,
          helper: cell.helper,
          chip: cell.chip ? <StatusChip tone={cell.chip.tone}>{cell.chip.word}</StatusChip> : null,
        }))}
      />
      {note && (
        // The ordinary upgraded box: one quiet line, no chip, nothing to do.
        <div className="ma-notice" data-testid="ma-health-embedder" data-kind="quiet">
          <p className="ma-notice-text ma-notice-quiet">
            <Segments segments={note} />
          </p>
        </div>
      )}
      <BanksCard
        rows={area.rows}
        totals={area.totals}
        listMissing={bankListMissing(area.health)}
        note={banksNote(report)}
        onRecheck={() => area.healthQuery?.refetch?.()}
        rechecking={!!area.healthQuery?.isFetching}
      />
    </>
  )
}

/**
 * @param {{ area: object }} props What MemoryArea holds: the route, the health answer
 *   (`health`, `healthQuery`) and the bank rows worked out from it (`rows`, `totals`).
 */
export function HealthTab({ area }) {
  const { health, healthQuery } = area
  if (health.kind === 'loading') return <HealthLoading />
  if (health.kind === 'failed') return <HealthFailed query={healthQuery} />
  if (health.kind === 'off') return <HealthOff />
  if (health.kind === 'unsupported') return <HealthFallback />
  if (health.kind === 'unreachable') return <HealthDown query={healthQuery} />
  return <HealthReady area={area} report={health.report} />
}
