// What Memory ▸ Health shows when there is no path to show: the answer is still on its way,
// hal0 did not give one, memory is off or on another engine, or the engine does not answer.
//
// None of these shows a figure. An outage is not an empty engine, so nothing is counted as zero.
import '../memory-area.css'
import { CommandBox } from '../shared/CommandBox.jsx'
import { EmptyState } from '../shared/EmptyState.jsx'
import { GoLink } from '../shared/GoLink.jsx'
import { StatStrip } from '../shared/StatStrip.jsx'
import { StatusChip } from '../shared/StatusChip.jsx'

/** The five cells of the engine facts strip, by name. Their values load after their labels. */
export const STRIP_LABELS = [
  { id: 'engine', label: 'Engine' },
  { id: 'extraction', label: 'Extraction' },
  { id: 'embedder', label: 'Embedder' },
  { id: 'reranker', label: 'Reranker' },
  { id: 'decision', label: 'Decision model' },
]

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

function RecheckButton({ query, testId }) {
  return (
    <button
      type="button"
      className="btn ghost sm"
      disabled={!!query?.isFetching}
      onClick={() => query?.refetch?.()}
      data-testid={testId}
    >
      {query?.isFetching ? 'Checking' : 'Check again'}
    </button>
  )
}

export function HealthLoading() {
  return (
    <>
      <section className="card ma-card" aria-label="Summary" data-testid="ma-health-loading">
        <SkeletonLines count={3} />
      </section>
      <StatStrip label="Engine" cells={STRIP_LABELS.map((c) => ({ ...c, value: null }))} loading />
      <section className="card ma-card" aria-label="Banks">
        <SkeletonLines count={4} />
      </section>
    </>
  )
}

/** hal0's own API did not answer the health request: nothing is known, the engine included. */
export function HealthFailed({ query }) {
  return (
    <section className="card ma-card" aria-label="Summary" data-testid="ma-health-failed">
      <EmptyState
        title="Memory's health did not load"
        action={<RecheckButton query={query} testId="ma-health-recheck" />}
      >
        hal0 did not answer the request for it, so this page shows no verdict and no figure. That says
        nothing about the memory engine itself.
      </EmptyState>
    </section>
  )
}

export function HealthOff() {
  return (
    <section className="card ma-card" aria-label="Summary" data-testid="ma-health-off">
      <EmptyState title="Memory is turned off" action={<GoLink href="#settings/memory">Open engine settings</GoLink>}>
        Nothing is stored or recalled while memory is off, so there is nothing to check. Turn it on in
        engine settings.
      </EmptyState>
    </section>
  )
}

// The wording is the notice the old Memory page showed for the same state.
export function HealthFallback() {
  return (
    <section className="card ma-card" aria-label="Engine" data-testid="ma-health-fallback">
      <EmptyState
        title="Memory is not running on Hindsight"
        action={<GoLink href="#settings/memory">Open engine settings</GoLink>}
      >
        Memory is running on a non-Hindsight fallback engine — volatile, writes don&apos;t survive restart.
        Graph extraction, reflect, and directives aren&apos;t available either.
      </EmptyState>
    </section>
  )
}

/** The engine does not answer. What is known, what is not, and where to look. */
export function HealthDown({ query }) {
  return (
    <>
      <section className="card ma-card" aria-labelledby="ma-health-lead" data-testid="ma-health-unreachable">
        <div className="ma-lead-row">
          <div className="ma-lead-main">
            <span className="ma-eyebrow ma-eyebrow-block">Does anything need you?</span>
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
            <RecheckButton query={query} testId="ma-health-recheck" />
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
          <GoLink href="#services" data-testid="ma-health-services">
            Open Services
          </GoLink>
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
            <div className="ma-kv-row">
              <dt>hal0</dt>
              <dd>hal0 answered this page&apos;s request. It is the memory engine behind it that did not.</dd>
            </div>
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
              <dd>Which knowledge pages and mental models are stale.</dd>
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
