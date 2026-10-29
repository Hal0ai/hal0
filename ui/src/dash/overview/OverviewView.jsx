// hal0 Overview v2 — the landing page: OverviewView({ onGo }).
//
// A window-global module. Each section lives in its own file and is imported directly, so
// the section modules always evaluate before this one; this view owns the band layout and the
// column classes (cN + ov-col) around them.

import { useSlots } from '@/api/hooks/useSlots'
import { useHardware } from '@/api/hooks/useHardware'
import { OvNowBand } from './now-band.jsx'
import { OvNeedsYou } from './needs-you.jsx'
import { OvServingNow } from './serving-now.jsx'
import { OvAgents } from './agents-panel.jsx'
import { OvUsage } from './usage.jsx'
import { OvHardware } from './hardware.jsx'
import { OvRecent } from './recent.jsx'
import { OvImageEngine } from './image-engine.jsx'
import { OvServices } from './services.jsx'
import { OvMemory } from './memory.jsx'
import { OvVersionStrip } from './version-strip.jsx'

// Confirmed-empty install → point at setup instead of an empty board.
function OverviewEmpty({ onGo }) {
  const hw = useHardware()
  const hostName = hw.data?.name || (typeof HAL0_DATA !== 'undefined' ? HAL0_DATA.host?.name : '') || '—'
  const Wm = window.Wordmark
  return (
    <div className="view">
      <div className="dash-empty">
        <div className="dash-empty-glyph">{Wm ? <Wm size={56} /> : null}</div>
        <h1 className="mono">No models configured yet</h1>
        <p>hal0 is ready, but no slot has a model loaded. Run <span className="mono">hal0 setup</span> in your terminal to install a bundle, or configure slots one at a time.</p>
        <div className="dash-empty-meta mono">
          <span><span style={{ color: 'var(--fg-3)' }}>host</span> {hostName}</span>
          {hw.data?.ram?.total ? <><span style={{ color: 'var(--fg-5)' }}>·</span><span><span style={{ color: 'var(--fg-3)' }}>ram</span> {hw.data.ram.total} GB</span></> : null}
          {hw.data?.npu?.present && <><span style={{ color: 'var(--fg-5)' }}>·</span><span><span style={{ color: 'var(--fg-3)' }}>npu</span> ready</span></>}
        </div>
        <div className="dash-empty-cta">
          <button className="btn ghost lg" onClick={() => onGo('slots')}>Configure slots</button>
        </div>
      </div>
    </div>
  )
}

function OverviewView({ onGo }) {
  const go = onGo || ((id) => { window.location.hash = '#' + id })
  const slotsQuery = useSlots()

  if (Array.isArray(slotsQuery.data) && slotsQuery.data.length === 0) {
    return <OverviewEmpty onGo={go} />
  }

  return (
    <div className="view ov-view">
      <OvNowBand onGo={go} />
      <OvNeedsYou onGo={go} />
      <div className="ov-band">
        <div className="c8 ov-col"><OvServingNow onGo={go} /></div>
        <div className="c4 ov-col"><OvAgents onGo={go} /></div>
      </div>
      <div className="ov-band">
        <div className="c7 ov-col"><OvUsage onGo={go} /></div>
        <div className="c5 ov-stack">
          <OvHardware onGo={go} />
          <OvRecent onGo={go} />
        </div>
      </div>
      <div className="ov-band">
        <div className="c4 ov-col"><OvImageEngine onGo={go} /></div>
        <div className="c4 ov-col"><OvServices onGo={go} /></div>
        <div className="c4 ov-col"><OvMemory onGo={go} /></div>
      </div>
      <OvVersionStrip onGo={go} />
    </div>
  )
}

Object.assign(window, { OverviewView })
