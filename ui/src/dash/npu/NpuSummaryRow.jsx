// One-line NPU summary on the Inference tab: who holds the NPU, summed tok/s, a time-sliced
// chip, and a link to the NPU tab (#slots/npu). It reads the shared occupancy query (same key as
// the NPU tab, no extra poll). Every readout is a real field or renders "—".
import { useNpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import { npuHolders, npuTabVisible } from './npu-pure'
import './npu-tab.css'

export function NpuSummaryRow({ slots }) {
  const occ = useNpuOccupancy().data
  const list = slots || []
  if (!npuTabVisible(occ, list)) return null

  const holders = npuHolders(occ?.slots)
  const rates = list
    .filter((s) => s?.device_class === 'npu')
    .map((s) => s.metrics?.toks)
    .filter((t) => typeof t === 'number' && t > 0)
  const toks = rates.length ? Math.round(rates.reduce((a, b) => a + b, 0) * 10) / 10 : null

  return (
    <div className="npt-summary" data-testid="npu-summary-row">
      <span className="npt-summary-title">NPU</span>
      <span className="npt-status-part">
        <span className="npt-status-k">holders</span>
        <span className="npt-status-v" data-testid="npu-summary-holders">{occ ? holders.length : '—'}</span>
      </span>
      <span className="npt-status-part">
        <span className="npt-status-k">tok/s</span>
        <span className="npt-status-v" data-testid="npu-summary-toks">{toks ?? '—'}</span>
      </span>
      {holders.length > 1 && (
        <span className="npt-summary-chip" data-testid="npu-summary-sliced">time-sliced</span>
      )}
      <a className="npt-summary-link" href="#slots/npu" data-testid="npu-summary-link">Open NPU tab</a>
    </div>
  )
}
