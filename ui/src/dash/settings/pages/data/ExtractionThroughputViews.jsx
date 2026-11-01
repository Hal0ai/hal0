// Settings ▸ Data ▸ Memory — extraction throughput views (#2798).
//
// Presentational only (props in, markup out) so vitest can render them with
// react-dom/server; MemoryGraphPanel owns the state and the data. The reading
// of the API payloads lives in extractionThroughput.js.
import { Banner } from '../../../primitives.jsx'
import { throughputNote } from './extractionThroughput.js'

const NOTE_COLOR = { warn: 'var(--warn)', ok: 'var(--ok)', muted: 'var(--fg-4)' }

/**
 * Warnings from the last save (PUT /api/memory/graph), one warn Banner each —
 * the same shell the Agents brain page uses for a setting that will not work
 * as saved. Renders nothing for an empty list.
 * @param {{ warnings?: string[], onDismiss?: () => void }} props
 */
export function SaveWarnings({ warnings, onDismiss }) {
  if (!warnings || warnings.length === 0) return null
  return (
    <div data-testid="mem-graph-warnings" style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '4px 12px 8px' }}>
      {warnings.map((w, i) => (
        <Banner key={i} kind="warn" body={w} onDismiss={onDismiss} />
      ))}
    </div>
  )
}

/**
 * The cached throughput message under the extraction slot picker. Same
 * small mono note the panel uses for other per-field status lines.
 * @param {{ status?: any }} props
 */
export function ThroughputNote({ status }) {
  const note = throughputNote(status)
  if (!note) return null
  return (
    <div
      className="mono"
      data-testid="mem-graph-throughput"
      data-verdict={note.verdict}
      style={{ fontSize: 10.5, lineHeight: 1.55, color: NOTE_COLOR[note.tone], padding: '2px 12px 6px' }}
    >
      {note.text}
    </div>
  )
}
