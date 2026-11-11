// The Refresh card: bring everything stale up to date, and say how often the bank may refresh
// on its own.
//
// "Refresh all stale" has no route of its own. It asks for one refresh at a time (the tab runs
// the loop and reports it here as `bulk`), shows how far it has got, and stops at the first
// that does not start, naming it. While the engine is not answering it is disabled, and says so.
//
// The interval is the bank's minimum time between automatic refreshes. Choosing a value saves
// it at once.
import { useId, useState } from 'react'
import '../memory-area.css'
import { useBankConfig, useBankConfigUpdate } from '@/api/hooks/useHindsight'
import { RichSelect } from '../../rich-select'
import { intervalOptions, intervalSeconds, shortError } from '../knowledge-pure'
import { ReasonedButton } from '../shared/ReasonedButton.jsx'
import { formatCount } from '../shared/format-pure'

function RefreshInterval({ bank, blockedReason }) {
  const labelId = useId()
  const helpId = useId()
  const configQuery = useBankConfig(bank)
  const update = useBankConfigUpdate()
  const [status, setStatus] = useState(null)

  const loading = configQuery.data === undefined && !configQuery.isError
  const current = intervalSeconds(configQuery.data?.config)
  const unknownReason =
    current === null && !loading ? "The bank's settings did not load, so this cannot be shown or changed." : null
  const reason = unknownReason ?? blockedReason

  const choose = async (id) => {
    const seconds = Number(id)
    if (seconds === current) return
    setStatus(null)
    try {
      await update.mutateAsync({
        bank,
        updates: { mental_model_min_refresh_interval_seconds: seconds },
      })
      setStatus({ ok: true, text: 'Saved.' })
    } catch (error) {
      setStatus({ ok: false, text: `Not saved. ${shortError(error)}` })
    }
  }

  return (
    <div className="ma-kn-interval" data-testid="ma-kn-interval">
      <div className="ma-kn-interval-row">
        <span className="ma-eyebrow" id={labelId}>
          Refresh at most every
        </span>
        {loading ? (
          <span className="ma-skel" aria-hidden="true" />
        ) : (
          <div className="ma-kn-interval-select">
            <RichSelect
              aria-label="Refresh at most every"
              value={current === null ? null : String(current)}
              disabled={!!reason || update.isPending}
              options={intervalOptions(current).map((option) => ({
                id: String(option.seconds),
                row: option.label,
              }))}
              onChange={choose}
              data-testid="ma-kn-interval-select"
            />
          </div>
        )}
        {loading && <span className="sr-only">Loading</span>}
        {/* Present from the start, so "Saved." is announced when it appears. */}
        <span className="ma-helper" role="status" data-testid="ma-kn-interval-status">
          {status?.ok ? status.text : null}
        </span>
      </div>
      {status && !status.ok && (
        <p className="ma-helper ma-kn-error" role="alert" data-testid="ma-kn-interval-error">
          {status.text}
        </p>
      )}
      {reason && (
        <p className="ma-reason" data-testid="ma-kn-interval-reason">
          {reason}
        </p>
      )}
      <p className="ma-helper" id={helpId}>
        How long this bank&apos;s pages and models wait after a refresh before the next automatic one. A page
        or model with its own setting keeps it. A refresh started on this tab always runs.
      </p>
    </div>
  )
}

function bulkReason({ blockedReason, loading, total, stale, bulk }) {
  if (blockedReason) return blockedReason
  if (loading) return 'Still loading.'
  if (bulk?.phase === 'running') return 'Refreshes are being started.'
  if (total === 0) return 'There is nothing to refresh in this bank.'
  if (stale === 0) return 'Nothing is stale.'
  return null
}

function BulkStatus({ bulk }) {
  const count = (n) => formatCount(n) ?? String(n)
  let status = null
  if (bulk?.phase === 'running') {
    status = `Starting refresh ${count(bulk.at)} of ${count(bulk.total)}: “${bulk.name}”.`
  } else if (bulk?.phase === 'done') {
    status = `${count(bulk.started)} ${bulk.started === 1 ? 'refresh' : 'refreshes'} started. Each finishes on its own, and this tab updates as they do.`
  }
  return (
    <>
      <p className="ma-helper ma-kn-status" role="status" data-testid="ma-kn-bulk-status">
        {status}
      </p>
      {bulk?.phase === 'stopped' && (
        <p className="ma-helper ma-kn-status ma-kn-error" role="alert" data-testid="ma-kn-bulk-stopped">
          Stopped after {count(bulk.started)} of {count(bulk.total)}. “{bulk.name}” did not start: {bulk.reason}
        </p>
      )}
    </>
  )
}

/**
 * @param {{
 *   bank: string,
 *   loading: boolean,
 *   total: number,
 *   stale: number,
 *   bulk: null|{phase: 'running'|'done'|'stopped', at?: number, total?: number, started?: number, name?: string, reason?: string},
 *   onRefreshAll: () => void,
 *   blockedReason: string|null,
 * }} props `total` counts pages and models together; `stale` counts those "Refresh all stale"
 *   would refresh (stale, or failed their last refresh).
 */
export function RefreshCard({ bank, loading, total, stale, bulk, onRefreshAll, blockedReason }) {
  const headingId = useId()
  const count = (n) => formatCount(n) ?? String(n)
  let sentence
  if (loading) {
    sentence = (
      <span aria-busy="true">
        <span className="ma-skel ma-skel-line" aria-hidden="true" />
        <span className="sr-only">Loading</span>
      </span>
    )
  } else if (total === 0) {
    sentence = 'This bank has no knowledge pages or mental models to refresh yet.'
  } else if (stale === 0) {
    sentence = (
      <>
        Every knowledge page and mental model in this bank is <span className="ma-em">fresh</span>.
      </>
    )
  } else {
    sentence = (
      <>
        <span className="ma-em">
          {count(stale)} of {count(total)} knowledge pages and mental models
        </span>{' '}
        {stale === 1 ? 'is' : 'are'} stale or failed {stale === 1 ? 'its' : 'their'} last refresh. Until a
        refresh succeeds, agents read the text from the last one that did.
      </>
    )
  }
  return (
    <section className="card ma-card" aria-labelledby={headingId} data-testid="ma-kn-refresh-card">
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id={headingId}>
          Refresh
        </h2>
      </div>
      <div className="ma-kn-refresh">
        <div className="ma-kn-refresh-main">
          <p className="ma-body" data-testid="ma-kn-refresh-sentence">
            {sentence}
          </p>
          <div>
            <ReasonedButton
              className="btn ghost"
              reason={bulkReason({ blockedReason, loading, total, stale, bulk })}
              onClick={onRefreshAll}
              data-testid="ma-kn-refresh-all"
            >
              Refresh all stale
            </ReasonedButton>
            <BulkStatus bulk={bulk} />
          </div>
        </div>
        <RefreshInterval bank={bank} blockedReason={blockedReason} />
      </div>
    </section>
  )
}
