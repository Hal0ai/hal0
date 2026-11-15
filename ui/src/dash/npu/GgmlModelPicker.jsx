// The GGML slot card's model dropdown: the same RichSelect the inference slot cards use (name line,
// then a chip line), listing only models an xdna slot can run. Fit data is GET /api/runners/xdna/fit;
// rows the backend marks `npu_ok: false` (too big / wrong kind for the NPU) stay hidden behind a
// "show all" toggle, where they carry their `npu_note`.
import { useMemo, useState } from 'react'
import { RichSelect } from '../rich-select.jsx'
import { ggmlVisibleRows } from './ggml-slot-pure'

const DownloadIcon = () => (
  <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d="M8 2v8M4.5 7L8 10.5 11.5 7M3 13.5h10" />
  </svg>
)

/** RichSelect options for the visible rows. `cur` not in the list is prepended so the closed
 *  trigger still names it. Exported for tests. */
export function ggmlPickerOptions({ rows, cur, curLabel }) {
  const list = []
  if (cur && !rows.some((r) => r.id === cur)) list.push({ id: cur, row: curLabel || cur })
  if (!cur) list.push({ id: '', row: '—' })
  for (const r of rows) {
    const chips = (
      <>
        <span className={`chip mono ${r.path === 'fused' ? 'ok' : 'amber'}`} title={r.reason || undefined}>{r.path}</span>
        {r.quant && <span className="chip amber">{r.quant}</span>}
        {r.size && <span className="chip mono">{r.size}</span>}
        {!r.installed && (
          <span className="chip info" title="Not downloaded yet" aria-label="Not downloaded yet" data-testid={`ggml-pick-dl-${r.id}`}>
            <DownloadIcon />
          </span>
        )}
        {!r.npuOk && (
          <span className="chip amber" title={r.npuNote || 'Not practical on the NPU'} data-testid={`ggml-pick-note-${r.id}`}>
            {r.npuNote || 'not practical on the NPU'}
          </span>
        )}
      </>
    )
    list.push({
      id: r.id,
      row: <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.id}</span>,
      trigger: (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.id}</span>
          {chips}
        </span>
      ),
      desc: <span className="rsel-desc-line">{chips}</span>,
    })
  }
  return list
}

/**
 * @param {object} props
 * @param {string} props.slotName
 * @param {string} props.cur  the slot's bound model id ('' when none)
 * @param {string} [props.curLabel]
 * @param {Array<import('./ggml-slot-pure').GgmlPickerRow>} props.rows  every xdna-compatible row
 * @param {boolean} [props.disabled]
 * @param {(id: string) => void} props.onSwap
 */
export function GgmlModelPicker({ slotName, cur, curLabel, rows, disabled, onSwap }) {
  const [showAll, setShowAll] = useState(false)
  const vis = useMemo(() => ggmlVisibleRows(rows, showAll, cur), [rows, showAll, cur])
  const options = useMemo(() => ggmlPickerOptions({ rows: vis.rows, cur, curLabel }), [vis.rows, cur, curLabel])
  return (
    <span className="npt-gpick" data-testid={`ggml-pick-${slotName}`}>
      <RichSelect
        className="model-picker"
        value={cur}
        options={options}
        disabled={disabled}
        aria-label={`Model for ${slotName}`}
        data-testid={`ggml-model-pick-${slotName}`}
        onChange={(id) => {
          if (id && id !== cur) onSwap(id)
        }}
      />
      {(vis.hidden > 0 || showAll) && (
        <button type="button" className="npt-gpick-all" aria-pressed={showAll} data-testid={`ggml-pick-all-${slotName}`} onClick={() => setShowAll((v) => !v)}>
          {showAll ? 'hide impractical' : `show all (${vis.hidden} hidden)`}
        </button>
      )}
    </span>
  )
}
