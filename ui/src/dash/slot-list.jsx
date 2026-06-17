// hal0 dashboard — SlotList anchor card (W2).
//
// Dense table of all real slots, living in a DCard (noPad). Data is 100% live
// from useSlots() — NO stub data anywhere in this file.
//
// Window-global module: exports SlotList onto window so the W3 grid can
// reference it without a bundler import (same pattern as all other dash modules).
//
// Props:
//   pinnedSet     — Set<string> of pinned slot names (W3 owns the layout store)
//   onTogglePin   — (name: string) => void
// Both default to empty/no-op so SlotList renders standalone during development.
//
// Import order: must come after cards-shell.jsx (DCard + StatusDot on window)
// and after useSlots is available (via the TS hook, imported below via the
// Vite alias path — same as inference-pane.jsx).

import { useSlots } from '@/api/hooks/useSlots'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { slotIndicatorFromPhase } from './slot-status.js'

const { useState: useStateL, useCallback: useCallbackL } = React

// ── devKind — copied verbatim from inference-pane.jsx (window-global fn there;
//    safest to carry a local copy than depend on load order).
function devKind(device) {
  const d = String(device || '').toLowerCase()
  if (d === 'npu') return 'npu'
  if (d === 'cpu') return 'cpu'
  if (d.includes('vulkan')) return 'vulkan'
  if (d.includes('rocm') || d.startsWith('gpu')) return 'rocm'
  return 'cpu'
}

// ── metric helpers ────────────────────────────────────────────────────────────

// Format tok/s: one decimal place, never fabricate.
function fmtToks(n) {
  if (typeof n !== 'number' || n <= 0) return null
  return n < 10 ? n.toFixed(1) : String(Math.round(n))
}

// Format ttft in ms.
function fmtTtft(n) {
  if (typeof n !== 'number' || n <= 0) return null
  return Math.round(n) + 'ms'
}

// Format memory in GB from MiB. Em-dash when absent.
function fmtMem(mem_mb) {
  if (typeof mem_mb !== 'number' || mem_mb <= 0) return null
  const gb = mem_mb / 1024
  return gb < 10 ? gb.toFixed(1) + 'GB' : Math.round(gb) + 'GB'
}

// ctx "<used>/<max>k" — never fabricate. Returns null when no ctx_max.
function fmtCtx(s) {
  const max = typeof s.ctx_max === 'number' && s.ctx_max > 0 ? s.ctx_max : null
  if (!max) return null
  const usedRaw = s.metrics?.ctx
  const used = typeof usedRaw === 'number' && usedRaw > 0
    ? Math.round(usedRaw / 1024) + 'k'
    : '—'
  const maxK = Math.round(max / 1024) + 'k'
  return used + '/' + maxK
}

// ── DevChip ───────────────────────────────────────────────────────────────────
// FLM/npu slots → "FLM · npu" chip.
// Others → colored dot + device label chip.
function DevChip({ s }) {
  const kind = devKind(s.device)
  if (kind === 'npu') {
    return <span className="sl-dchip sl-flm">FLM · npu</span>
  }
  return (
    <span className={'sl-dchip sl-dev-' + kind}>
      <span className="sl-dd" />
      {kind}
    </span>
  )
}

// ── PinBtn ────────────────────────────────────────────────────────────────────
function PinBtn({ name, pinned, onToggle }) {
  return (
    <button
      className={'pinbtn' + (pinned ? ' pinned' : '')}
      title={pinned ? 'Unpin slot' : 'Pin to home'}
      aria-pressed={pinned}
      onClick={(e) => {
        e.stopPropagation()
        onToggle(name)
      }}
    >
      {/* Pin icon — 10×10 inline SVG */}
      <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor"
           strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M9 2l5 5-2 2-3-1-4 4-1-1 4-4-1-3 2-2z" />
        <path d="M2 14l4-4" />
      </svg>
    </button>
  )
}

// ── SlotRow ───────────────────────────────────────────────────────────────────
function SlotRow({ s, pinned, onTogglePin }) {
  const ind = slotIndicatorFromPhase(s)
  const isServing = ind.cls === 'serving'
  const isDimmed = ind.cls === 'offline' || s.state === 'idle' || s.state === 'stopped'

  const toks = fmtToks(s.metrics?.toks)
  const ttft = fmtTtft(s.metrics?.ttft)
  const mem  = fmtMem(s.mem_mb)
  const ctx  = fmtCtx(s)

  const handleRowClick = () => {
    window.location.hash = '#slots/' + s.name
  }

  return (
    <div
      className={'sr' + (isDimmed ? ' dim' : '')}
      onClick={handleRowClick}
      role="row"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleRowClick() }}
      data-testid={'sl-row-' + s.name}
    >
      {/* col 1: status dot (10px col, 8px dot) */}
      <span className="sl-dot-cell">
        <StatusDot slot={s} />
      </span>

      {/* col 2: slot name (88px) */}
      <span className="snm">
        {s.name}
        {s.isDefault && <span className="sl-star" title="Default chat route">★</span>}
      </span>

      {/* col 3: model (minmax 120px, 1fr) */}
      <span className="smodel" title={s.model || ''}>
        {s.model || '—'}
      </span>

      <span className="sl-detail-row">
        {/* col 4: device chip (auto) */}
        <DevChip s={s} />

        {/* col 5: mem (58px) */}
        <span className="sl-metric">
          <span className="sl-ml">mem</span>
          <span className="sl-mv">{mem || '—'}</span>
        </span>

        {/* col 6: tok/s (52px) */}
        <span className="sl-metric">
          <span className="sl-ml">tok/s</span>
          <span className={'sl-mv' + (isServing && toks ? ' sl-acc' : ' sl-muted')}>
            {toks || '—'}
          </span>
        </span>

        {/* col 7: ttft (56px) */}
        <span className="sl-metric">
          <span className="sl-ml">ttft</span>
          <span className={'sl-mv' + (ttft ? '' : ' sl-muted')}>{ttft || '—'}</span>
        </span>

        {/* col 8: ctx (74px) */}
        <span className="sl-metric">
          <span className="sl-ml">ctx</span>
          <span className={'sl-mv sl-ctx' + (ctx ? '' : ' sl-muted')}>{ctx || '—'}</span>
        </span>
      </span>

      {/* col 9: pin (26px) */}
      <PinBtn name={s.name} pinned={pinned} onToggle={onTogglePin} />
    </div>
  )
}

// ── SlotList ──────────────────────────────────────────────────────────────────
export function SlotList({ pinnedSet = new Set(), onTogglePin = () => {} }) {
  const slotsQ  = useSlots()
  const hwQ     = useStatsHardware()

  const slots   = slotsQ.data || []
  const loading = slotsQ.isLoading

  // Serving count — derived from real indicator, not slot.state directly,
  // so it matches the dot classification exactly (recency / health-gate).
  const servingN = slots.filter((s) => {
    const ind = slotIndicatorFromPhase(s)
    return ind.cls === 'serving'
  }).length

  // GB free from hardware stats — only include when the data is present.
  const hw = hwQ.data
  const gbFree = hw && typeof hw.ram_total_mb === 'number' && typeof hw.ram_used_mb === 'number'
    ? Math.round((hw.ram_total_mb - hw.ram_used_mb) / 1024)
    : null

  // Footer text — omit GB-free clause when we have no real source.
  const footerText = gbFree != null
    ? `${servingN} serving · scheduler steady · ${gbFree} GB free`
    : `${servingN} serving · scheduler steady`

  // Card note: brief slot count summary.
  const note = loading ? 'loading…' : `${slots.length} slot${slots.length !== 1 ? 's' : ''}`

  return (
    <DCard title="SLOTS" noPad note={note} className="sl-card">
      <div className="sl-table" role="table" aria-label="Slot list">
        {/* Header row */}
        <div className="sh" role="row">
          <span />
          <span>Name</span>
          <span>Model</span>
          <span>Device</span>
          <span>Mem</span>
          <span>Tok/s</span>
          <span>TTFT</span>
          <span>Ctx</span>
          <span />
        </div>

        {/* Body */}
        {loading ? (
          <div className="sl-empty">
            <div className="sr sr-skeleton" aria-hidden="true">
              <span className="sl-dot-cell"><span className="sdot offline" /></span>
              <span className="snm sl-skel-bar" />
              <span className="smodel sl-skel-bar sl-skel-wide" />
              <span /><span /><span /><span /><span /><span />
            </div>
            <div className="sl-loading-hint">loading…</div>
          </div>
        ) : slots.length === 0 ? (
          <div className="sl-empty">
            <span className="sl-empty-hint">no slots configured</span>
          </div>
        ) : (
          slots.map((s) => (
            <SlotRow
              key={s.name}
              s={s}
              pinned={pinnedSet.has(s.name)}
              onTogglePin={onTogglePin}
            />
          ))
        )}

        {/* Footer */}
        <div className="slist-foot">{footerText}</div>
      </div>
    </DCard>
  )
}

Object.assign(window, { SlotList })
