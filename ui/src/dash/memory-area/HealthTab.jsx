// Memory ▸ Health: is the engine up, what does it run on, and is anything failing.
//
// This is the interim view. It is built from the per-subject routes (see health-pure.ts), so it
// is a status view with no order to it: it lists what it can see and never says which fault to
// fix first. The ordered repair path, read from the composed `GET /api/memory/health`, replaces
// the summary card in a later change.
//
// Four states, chosen by the engine check MemoryArea already holds:
//   loading       skeletons, and no figure at all
//   unreachable   what is known, what is not, and where to look
//   fallback      memory is not running on Hindsight
//   reachable     the summary, the engine facts, the queue and knowledge lines, the banks
//
// A figure is shown only when it was reported. A sum says how many banks it covers.
import './memory-area.css'
import { useMemoryGraphStatus, useMemoryModels } from '@/api/hooks/useMemory'
import { useAllMentalModels } from './area-data'
import { plural } from './area-pure'
import {
  embedderNotice,
  embedderView,
  engineStrip,
  healthVerdict,
  knowledgeLine,
  knowledgeTotals,
  queueLine,
} from './health-pure'
import { memoryTabHref, serializeMemoryRoute } from './memory-route-pure'
import { CommandBox } from './shared/CommandBox.jsx'
import { EmptyState } from './shared/EmptyState.jsx'
import { GoLink } from './shared/GoLink.jsx'
import { NoticeBar } from './shared/NoticeBar.jsx'
import { StatStrip } from './shared/StatStrip.jsx'
import { StatusChip } from './shared/StatusChip.jsx'
import { formatCount } from './shared/format-pure'

const STRIP_LABELS = [
  { id: 'engine', label: 'Engine' },
  { id: 'extraction', label: 'Extraction' },
  { id: 'embedder', label: 'Embedder' },
  { id: 'reranker', label: 'Reranker' },
  { id: 'decision', label: 'Decision model' },
]

// A sentence with its names (a model, a slot) set in mono.
function Segments({ segments }) {
  return segments.map((segment, at) =>
    typeof segment === 'string' ? (
      segment
    ) : (
      <span className="ma-mono ma-em" key={at}>
        {segment.code}
      </span>
    ),
  )
}

function SkeletonLines({ count = 2 }) {
  return (
    <div className="ma-card-body" aria-busy="true">
      {Array.from({ length: count }, (_, at) => (
        <span className="ma-skel ma-skel-line" aria-hidden="true" key={at} />
      ))}
      <span className="sr-only">Loading</span>
    </div>
  )
}

// A count in a table cell: the number, or the "not reported" mark. Never a zero that was not
// reported.
function Figure({ value }) {
  const text = formatCount(value)
  if (text === null) {
    return (
      <span className="ma-missing" role="img" aria-label="not reported">
        –
      </span>
    )
  }
  return text
}

function HealthLoading() {
  return (
    <>
      <section className="card ma-card" aria-label="Summary" data-testid="ma-health-loading">
        <SkeletonLines count={2} />
      </section>
      <StatStrip label="Engine" cells={STRIP_LABELS.map((c) => ({ ...c, value: null }))} loading />
      <section className="card ma-card" aria-label="Banks">
        <SkeletonLines count={4} />
      </section>
    </>
  )
}

function HealthDown({ area }) {
  const { engineQuery } = area
  const error = engineQuery.error?.message || null
  return (
    <>
      <section className="card ma-card" aria-labelledby="ma-health-lead" data-testid="ma-health-unreachable">
        <div className="ma-lead-row">
          <div className="ma-lead-main">
            <h2 className="ma-lead" id="ma-health-lead">
              The memory engine is not answering
            </h2>
            <p className="ma-body">
              Every figure on this page comes from the engine, so none is shown. Nothing here is counted as
              zero, and nothing from an earlier visit is shown as current.
            </p>
          </div>
          <div className="ma-lead-side">
            <StatusChip tone="err">unreachable</StatusChip>
            <button
              type="button"
              className="btn ghost sm"
              disabled={engineQuery.isFetching}
              onClick={() => engineQuery.refetch()}
              data-testid="ma-health-recheck"
            >
              {engineQuery.isFetching ? 'Checking' : 'Check again'}
            </button>
          </div>
        </div>
        <div className="ma-card-raised">
          <div className="ma-lead-main">
            <span className="ma-eyebrow ma-eyebrow-block">What to do</span>
            <h3 className="ma-item-title">Check the memory engine service</h3>
            <p className="ma-body">
              Open Services and look at the memory engine. From a shell on the box, the command below runs
              hal0&apos;s own checks and prints what failed.
            </p>
            <div className="ma-cmd-wrap">
              <CommandBox command="hal0 doctor" data-testid="ma-health-doctor" />
            </div>
          </div>
          <a className="btn" href="#services" data-testid="ma-health-services">
            Open Services
          </a>
        </div>
      </section>

      <div className="ma-cards">
        <section className="card ma-card" aria-labelledby="ma-health-known">
          <div className="ma-card-head">
            <h2 className="ma-eyebrow" id="ma-health-known">
              What is known
            </h2>
          </div>
          <dl className="ma-kv">
            <div className="ma-kv-row">
              <dt>Engine</dt>
              <dd>Hindsight did not answer this dashboard&apos;s check.</dd>
            </div>
            {error && (
              <div className="ma-kv-row">
                <dt>Error</dt>
                <dd className="ma-mono">{error}</dd>
              </div>
            )}
          </dl>
        </section>
        <section className="card ma-card" aria-labelledby="ma-health-unknown">
          <div className="ma-card-head">
            <h2 className="ma-eyebrow" id="ma-health-unknown">
              What is not known until it answers
            </h2>
          </div>
          <dl className="ma-kv">
            <div className="ma-kv-row">
              <dt>Banks</dt>
              <dd>How many facts each bank holds.</dd>
            </div>
            <div className="ma-kv-row">
              <dt>Queue</dt>
              <dd>How many operations are waiting or failed.</dd>
            </div>
            <div className="ma-kv-row">
              <dt>Knowledge</dt>
              <dd>Which mental models are stale.</dd>
            </div>
            <div className="ma-kv-row">
              <dt>Engine facts</dt>
              <dd>The engine version, and whether the embedder record is consistent.</dd>
            </div>
          </dl>
        </section>
      </div>
    </>
  )
}

// The wording is the notice the old Memory page showed for the same state.
function HealthFallback({ area }) {
  const name = area.engineQuery.data?.engine
  return (
    <section className="card ma-card" aria-label="Engine" data-testid="ma-health-fallback">
      <EmptyState
        title="Memory is not running on Hindsight"
        action={<GoLink href="#settings/memory">Open engine settings</GoLink>}
      >
        Memory is running on a non-Hindsight fallback engine{name ? ` (${name})` : ''} — volatile, writes
        don&apos;t survive restart. Graph extraction, reflect, and directives aren&apos;t available either.
      </EmptyState>
    </section>
  )
}

function Summary({ verdict, loading }) {
  if (loading) {
    return (
      <section className="card ma-card" aria-label="Summary" data-testid="ma-health-summary" data-state="loading">
        <SkeletonLines count={2} />
      </section>
    )
  }
  return (
    <section
      className="card ma-card"
      aria-labelledby="ma-health-lead"
      data-testid="ma-health-summary"
      data-state={verdict.kind}
    >
      <div className="ma-lead-row">
        <div className="ma-lead-main">
          <h2 className="ma-lead" id="ma-health-lead">
            {verdict.lead}
          </h2>
          <p className="ma-body">{verdict.detail}</p>
        </div>
        {verdict.kind === 'clear' && (
          <div className="ma-lead-side">
            <StatusChip tone="ok">working</StatusChip>
          </div>
        )}
      </div>
      {verdict.findings.length > 0 && (
        <ul className="ma-list" aria-label="What needs attention" data-testid="ma-health-findings">
          {verdict.findings.map((finding) => (
            <li className="ma-list-row" key={finding.id} data-finding={finding.id}>
              {finding.text}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function EmbedderNote({ notice }) {
  if (!notice) return null
  if (notice.kind === 'warning') {
    return (
      <NoticeBar tone="warn" status={notice.status} data-testid="ma-health-embedder">
        <Segments segments={notice.segments} />
      </NoticeBar>
    )
  }
  // The ordinary upgraded box: one quiet line, no chip, nothing to do.
  return (
    <div className="ma-notice" data-testid="ma-health-embedder" data-kind="quiet">
      <p className="ma-notice-text ma-notice-quiet">
        <Segments segments={notice.segments} />
      </p>
    </div>
  )
}

function WorkCard({ area, queue, knowledge, loading }) {
  return (
    <section className="card ma-card" aria-labelledby="ma-health-work" data-testid="ma-health-work">
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id="ma-health-work">
          Queue and knowledge
        </h2>
        <span className="ma-card-gap" />
        <span className="ma-card-meta">each line is a sum of per-bank figures</span>
      </div>
      {loading ? (
        <SkeletonLines count={2} />
      ) : (
        <dl className="ma-kv">
          <div className="ma-kv-row" data-testid="ma-health-queue">
            <dt>Queue</dt>
            <dd className={queue.known ? undefined : 'ma-kv-unknown'}>{queue.text}</dd>
            <GoLink href={memoryTabHref('operations', area.route)}>Open Operations</GoLink>
          </div>
          <div className="ma-kv-row" data-testid="ma-health-knowledge">
            <dt>Knowledge</dt>
            <dd className={knowledge.known ? undefined : 'ma-kv-unknown'}>{knowledge.text}</dd>
            <GoLink href={memoryTabHref('knowledge', area.route)}>Open Knowledge</GoLink>
          </div>
        </dl>
      )}
    </section>
  )
}

function BanksCard({ area }) {
  const { rows, totals, banksLoading, banksFailed, statsLoading, banksQuery } = area
  const anyMissing = rows.some(
    (row) => row.facts === null || row.documents === null || row.waiting === null || row.failed === null,
  )
  let body
  if (banksLoading) {
    body = <SkeletonLines count={4} />
  } else if (banksFailed) {
    body = (
      <EmptyState
        title="The bank list did not load"
        action={
          <button type="button" className="btn ghost sm" onClick={() => banksQuery.refetch()}>
            Check again
          </button>
        }
        data-testid="ma-health-banks-error"
      >
        The engine answered its health check but not the request for its banks, so no bank is listed.
      </EmptyState>
    )
  } else if (rows.length === 0) {
    body = (
      <EmptyState title="No banks yet" data-testid="ma-health-banks-empty">
        A bank appears here the first time an agent saves a memory to it.
      </EmptyState>
    )
  } else {
    body = (
      <>
        <div className="ma-table-region" role="region" aria-label="Banks table" tabIndex={0}>
          <table className="ma-table" data-testid="ma-health-banks" aria-busy={statsLoading || undefined}>
            <thead>
              <tr>
                <th scope="col">Bank</th>
                <th scope="col" className="ma-num">
                  Facts
                </th>
                <th scope="col" className="ma-num">
                  Documents
                </th>
                <th scope="col" className="ma-num">
                  Waiting
                </th>
                <th scope="col" className="ma-num">
                  Failed
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} data-bank={row.id}>
                  <th scope="row">
                    <a className="ma-link" href={serializeMemoryRoute({ tab: 'facts', bank: row.id })}>
                      {row.id}
                    </a>
                  </th>
                  {['facts', 'documents', 'waiting', 'failed'].map((key) => (
                    <td className="ma-num" key={key} data-col={key}>
                      {statsLoading && row[key] === null ? (
                        <span className="ma-skel ma-skel-cell" aria-hidden="true" />
                      ) : (
                        <Figure value={row[key]} />
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!statsLoading && anyMissing && (
          <div className="ma-card-foot" data-testid="ma-health-banks-foot">
            – means the engine did not report a figure
          </div>
        )}
        {totals.banks > totals.listed && (
          <div className="ma-card-foot" data-testid="ma-health-banks-paged">
            Showing the first {formatCount(totals.listed)} of {plural(totals.banks, 'bank', 'banks')}.
          </div>
        )}
      </>
    )
  }
  return (
    <section className="card ma-card" aria-labelledby="ma-health-banks-h">
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id="ma-health-banks-h">
          Banks
        </h2>
        <span className="ma-card-gap" />
        {!banksLoading && !banksFailed && rows.length > 0 && (
          <span className="ma-card-meta">{plural(totals.banks, 'bank', 'banks')} · a row opens its facts</span>
        )}
      </div>
      {body}
    </section>
  )
}

function HealthReachable({ area }) {
  const graphQuery = useMemoryGraphStatus()
  const modelsQuery = useMemoryModels()
  const mentalModels = useAllMentalModels(area.rows.map((row) => row.id))

  const stripLoading =
    (graphQuery.data === undefined && !graphQuery.isError) ||
    (modelsQuery.data === undefined && !modelsQuery.isError)
  const sumsLoading = area.banksLoading || area.statsLoading || mentalModels.some((entry) => entry.isLoading)

  const cells = engineStrip({
    engine: area.engineQuery.data,
    graph: graphQuery.data,
    models: modelsQuery.data,
  })
  const embedder = embedderView(modelsQuery.data?.embeddings)
  const knowledge = knowledgeTotals(mentalModels, area.totals.listed)
  const verdict = healthVerdict({
    strip: cells,
    embedder,
    totals: area.totals,
    knowledge,
    banksFailed: area.banksFailed,
  })
  // With no bank list there is nothing to add up; say so instead of "no banks".
  const unlisted = area.banksFailed ? { known: false, text: 'Not reported: the bank list did not load.' } : null

  return (
    <>
      <Summary verdict={verdict} loading={stripLoading || sumsLoading} />
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
      {!stripLoading && <EmbedderNote notice={embedderNotice(embedder)} />}
      <WorkCard
        area={area}
        queue={unlisted ?? queueLine(area.totals)}
        knowledge={unlisted ?? knowledgeLine(knowledge)}
        loading={sumsLoading}
      />
      <BanksCard area={area} />
    </>
  )
}

/** @param {{ area: object }} props What MemoryArea holds: the route, the engine check, the banks. */
export function HealthTab({ area }) {
  if (area.engine === 'loading') return <HealthLoading />
  if (area.engine === 'unreachable') return <HealthDown area={area} />
  if (area.engine === 'fallback') return <HealthFallback area={area} />
  return <HealthReachable area={area} />
}
