// hal0 v3 dashboard — ActivityLog sidebar pane (Slots page).
//
// A durable, colorized, severity-filterable, BOUNDED audit-trail pane that
// replaces the old SnapshotStrip / MemoryMap / ThroughputCard sidebar stack
// on the Slots page. Structurally cloned from the Footer "Live journal"
// pane (chrome.jsx) but wrapped in the `.side-card` shell so it sits in the
// 320px sidebar column.
//
// Priority: readability + confirmation. A failed action renders a RED row
// with a ✗ glyph so the user can answer "did it take?" at a glance. The
// pane is bounded (fixed max-height contained scroll, newest-first, capped
// ring) so a burst can't turn it into an unusable firehose; the Export
// button is the escape hatch for full history.
//
// Data: useActivityStream (durable backfill + live tail, epoch-aware) with
// the active severity/category filters forwarded server-side. Auto-scroll
// pauses while the cursor hovers the list so a live tail can't yank the
// row you're reading.
//
// NOTE: this is a `.jsx` file (no TS type annotations) — it sits in the
// same component layer as slots.jsx / chrome.jsx. The typed contract lives
// in the useActivity.ts hook.

import { useMemo, useRef, useState } from 'react'
import { useActivityStream, activityExportUrl } from '@/api/hooks/useActivity'

// Normalize severity into one of the four CSS row classes.
function sevOf(rec) {
  const s = String(rec.severity || '').toLowerCase()
  if (s === 'ok' || s === 'warn' || s === 'error' || s === 'info') return s
  return 'info'
}

// Severity → outcome glyph + class. `ok` = ✓ green confirmation, `error`
// = ✗ red, everything else = · neutral dot.
function outcomeGlyph(rec) {
  const sev = sevOf(rec)
  if (sev === 'ok' || rec.outcome === 'ok') return { glyph: '✓', cls: 'ok' }
  if (sev === 'error' || rec.outcome === 'error') return { glyph: '✗', cls: 'error' }
  if (rec.outcome === 'pending') return { glyph: '…', cls: 'warn' }
  if (sev === 'warn') return { glyph: '!', cls: 'warn' }
  return { glyph: '·', cls: 'info' }
}

// Short HH:MM:SS from an ISO/epoch ts; falls back to the raw string.
function shortTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  if (!Number.isNaN(d.getTime())) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }
  // Already-short or non-parseable — show the tail.
  return ts.length > 8 ? ts.slice(11, 19) || ts : ts
}

// Compact actor label: "mcp:hermes" → "hermes (mcp)", else as-is.
function actorLabel(actor) {
  if (!actor) return 'system'
  if (actor.startsWith('mcp:')) return `${actor.slice(4)} (mcp)`
  return actor
}

const SEVERITY_CHIPS = [
  { key: 'all', label: 'all' },
  { key: 'ok', label: 'ok' },
  { key: 'info', label: 'info' },
  { key: 'warn', label: 'warn' },
  { key: 'error', label: 'error' },
]

const CATEGORY_OPTIONS = [
  { key: 'all', label: 'All' },
  { key: 'slot', label: 'slot' },
  { key: 'model', label: 'model' },
  { key: 'profile', label: 'profile' },
  { key: 'capability', label: 'capability' },
  { key: 'system', label: 'system' },
]

export function ActivityLog() {
  const [severity, setSeverity] = useState('all')
  const [category, setCategory] = useState('all')
  const [search, setSearch] = useState('')
  // Auto-scroll-to-top pauses while hovering so a live frame can't yank
  // the row you're reading. (Newest-first → "scroll" is really the top.)
  const [paused, setPaused] = useState(false)
  const listRef = useRef(null)

  const stream = useActivityStream({
    follow: true,
    severity: severity === 'all' ? null : severity,
    category: category === 'all' ? null : category,
    // search is forwarded server-side; the client filter below gives
    // instant feedback for the residual ring while the SSE reconnects.
    search: search || null,
  })

  // Residual client filter so records already in the ring honour a tightened
  // filter immediately (server-side filtering only gates FUTURE frames).
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return stream.records.filter((r) => {
      if (severity !== 'all' && sevOf(r) !== severity) return false
      if (category !== 'all' && r.category !== category) return false
      if (q) {
        const hay =
          `${r.message || ''} ${r.action || ''} ${r.target || ''} ${r.actor || ''}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [stream.records, severity, category, search])

  const exportFilters = {
    severity: severity === 'all' ? null : severity,
    category: category === 'all' ? null : category,
    search: search || null,
  }
  const csvHref = activityExportUrl('csv', exportFilters)
  const jsonHref = activityExportUrl('json', exportFilters)

  return (
    <div className="side-card act-card" data-testid="activity-log">
      <div className="act-h">
        <span className="act-title">Activity</span>
        <span className="act-ct mono">
          {rows.length}
          {stream.records.length !== rows.length ? ` / ${stream.records.length}` : ''}
        </span>
        <span
          className={'act-conn' + (stream.disconnected ? ' off' : '')}
          title={stream.disconnected ? 'stream reconnecting' : 'live tail'}
        >
          <span className={'dot' + (stream.disconnected ? '' : ' ready')} />
          {stream.disconnected ? 'reconnecting…' : 'live'}
        </span>
      </div>

      {/* Sticky filter bar */}
      <div className="act-filters">
        <div className="act-chips mono" role="group" aria-label="Filter by severity">
          {SEVERITY_CHIPS.map((c) => (
            <button
              key={c.key}
              type="button"
              data-testid={`act-sev-${c.key}`}
              className={'act-chip act-chip-' + c.key + (severity === c.key ? ' on' : '')}
              aria-pressed={severity === c.key}
              onClick={() => setSeverity(c.key)}
            >
              {c.label}
            </button>
          ))}
        </div>
        <div className="act-filters-row">
          <select
            className="act-cat mono"
            data-testid="act-category"
            aria-label="Filter by category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            {CATEGORY_OPTIONS.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>
          <input
            className="act-search mono"
            data-testid="act-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="search…"
            aria-label="Search activity"
          />
        </div>
      </div>

      {/* Bounded, scroll-contained, newest-first body */}
      <div
        ref={listRef}
        className={'act-body' + (paused ? ' paused' : '')}
        data-testid="activity-log-body"
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
      >
        {rows.length === 0 ? (
          <div className="act-empty mono">
            {stream.records.length === 0 ? (
              'No activity yet'
            ) : (
              <>
                No activity matches.{' '}
                <button
                  type="button"
                  className="act-clear"
                  onClick={() => {
                    setSeverity('all')
                    setCategory('all')
                    setSearch('')
                  }}
                >
                  Clear filters
                </button>
              </>
            )}
          </div>
        ) : (
          rows.map((r, i) => {
            const g = outcomeGlyph(r)
            const sev = sevOf(r)
            return (
              <div
                key={r.id ?? `${r.ts}-${i}`}
                className={'act-line ' + sev}
                data-testid="act-row"
                data-severity={sev}
                title={r.error || r.message || r.action}
              >
                <span className="act-ts">{shortTime(r.ts)}</span>
                <span className={'act-glyph ' + g.cls} aria-hidden="true">
                  {g.glyph}
                </span>
                <span className="act-actor">{actorLabel(r.actor)}</span>
                <span className="act-action mono">{r.action}</span>
                {r.target ? <span className="act-target">{r.target}</span> : null}
                <span className="act-msg">{r.message}</span>
              </div>
            )
          })
        )}
      </div>

      {/* Export footer — full history without keeping the pane open */}
      <div className="act-foot">
        <span className="act-foot-label mono">export</span>
        <a className="act-export mono" data-testid="act-export-csv" href={csvHref} download>
          CSV
        </a>
        <a className="act-export mono" data-testid="act-export-json" href={jsonHref} download>
          JSON
        </a>
      </div>
    </div>
  )
}

export default ActivityLog
