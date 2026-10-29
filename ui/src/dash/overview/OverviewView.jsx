// hal0 Overview v2 — the landing page: OverviewView({ onGo }).
//
// A window-global module. The sections (OvNowBand, OvNeedsYou, …) live in their own
// files and register themselves on window; this view resolves each by name at render
// time and renders nothing for a missing one, so the section files can land in any order.

import { useSlots } from '@/api/hooks/useSlots'
import { useHardware } from '@/api/hooks/useHardware'

// One overview section, looked up on window at render time.
function Section({ name, onGo }) {
  const C = window[name]
  return C ? <C onGo={onGo} /> : null
}

// A column wrapper (c8, c4, …) around one section; empty when the section is missing.
function Col({ cls, name, onGo }) {
  return window[name] ? <div className={cls + ' ov-col'}><Section name={name} onGo={onGo} /></div> : null
}

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
      <Section name="OvNowBand" onGo={go} />
      <Section name="OvNeedsYou" onGo={go} />
      <div className="ov-band">
        <Col cls="c8" name="OvServingNow" onGo={go} />
        <Col cls="c4" name="OvAgents" onGo={go} />
      </div>
      <div className="ov-band">
        <Col cls="c7" name="OvUsage" onGo={go} />
        <div className="c5 ov-stack">
          <Section name="OvHardware" onGo={go} />
          <Section name="OvRecent" onGo={go} />
        </div>
      </div>
      <div className="ov-band">
        <Col cls="c4" name="OvImageEngine" onGo={go} />
        <Col cls="c4" name="OvServices" onGo={go} />
        <Col cls="c4" name="OvMemory" onGo={go} />
      </div>
      <Section name="OvVersionStrip" onGo={go} />
    </div>
  )
}

Object.assign(window, { OverviewView })
