// The NPU tab (Slots → NPU): status line on top, then Lanes | Models | Engines & profiles.
//
// Which tab opens: the route's sub-tab (`#slots/npu/<sub>`, `sub`) when it names one, else Lanes.
// A tab change rewrites the URL with replaceState (Back does not fill with tabs). All three panels
// stay mounted and the inactive ones are hidden, like Image Gen.
//
// NpuTabs owns the FLM draft (`flmDraft`): the Lanes panel edits it, and the Models panel's
// "Use for chat" stages into it, so both see one staged set. `onSwitchTab` lets Models jump to
// Lanes.
//
// No NPU (probe says absent) and no NPU slot → the "No NPU on this box" panel. NPU slots with a
// failed probe keep the shell (degraded): lanes stay operable.
import { useCallback, useEffect, useRef, useState } from 'react'
import './npu-tab.css'
import { useNpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { ArrayBand } from './ArrayBand.jsx'
import { FlmLane } from './FlmLane.jsx'
import { GgmlLane } from './GgmlLane.jsx'
import { NpuEnginesPanel } from './NpuEnginesPanel.jsx'
import { NpuModelsPanel } from './NpuModelsPanel.jsx'
import { draftCount } from './flm-draft-pure'
import { NPU_TABS, npuStatusParts, npuTabVisible, validNpuTab } from './npu-pure'

const IDS = NPU_TABS.map((t) => t.id)

// `#slots/npu/<sub>`: the sub-tab the URL names now (replaceState moves the URL without telling
// the router, so the route's own `sub` can be behind it).
function subFromHash() {
  const m = /^#slots\/npu\/([^/?]+)/.exec(window.location.hash || '')
  return m ? validNpuTab(m[1]) : null
}

// Lanes: the array band on top, then the FLM lane and the GGML lane.
function LanesPanel({ draft, setDraft, occ, hw, slots }) {
  return (
    <div className="npt-lanes" data-testid="npu-lanes">
      <ArrayBand occ={occ} hw={hw} slots={slots} />
      <FlmLane slots={slots} draft={draft} setDraft={setDraft} />
      <GgmlLane slots={slots} occ={occ} hw={hw} />
    </div>
  )
}

function AbsentPanel({ host }) {
  const rows = [
    ['device node', host?.device_node ?? '—'],
    ['driver loaded', host?.driver_loaded == null ? '—' : host.driver_loaded ? 'yes' : 'no'],
    ['xdna generation', host?.xdna_gen == null ? '—' : String(host.xdna_gen)],
  ]
  return (
    <div className="npt-absent" data-testid="npu-absent">
      <h2>No NPU on this box</h2>
      <p>hal0 found no AMD XDNA NPU and no NPU slot is configured.</p>
      <dl>
        {rows.map(([k, v]) => (
          <div key={k} style={{ display: 'contents' }}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <a href="/docs/getting-started/drivers/" target="_blank" rel="noreferrer">
        NPU driver setup
      </a>
    </div>
  )
}

/**
 * @param {object} props
 * @param {string | null} [props.sub]  the route's sub-tab (`#slots/npu/<sub>`)
 * @param {Array<{device_class?: string}>} [props.slots]  the page's local slots
 */
export function NpuTabs({ sub, slots = [] }) {
  const occQ = useNpuOccupancy()
  const hwQ = useStatsHardware()
  const occ = occQ.data

  const [tab, setTab] = useState(() => validNpuTab(sub) ?? 'lanes')
  const [flmDraft, setFlmDraft] = useState({})

  useEffect(() => {
    const next = validNpuTab(sub)
    if (next) setTab(next)
  }, [sub])
  useEffect(() => {
    const onHash = () => {
      const next = subFromHash()
      if (next) setTab(next)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const select = useCallback((next) => {
    setTab(next)
    try {
      window.history.replaceState(window.history.state, '', `#slots/npu/${next}`)
    } catch {
      /* a sandboxed frame refuses; the tab still changes */
    }
  }, [])

  const tabRefs = useRef({})
  const onKeyDown = (e) => {
    const at = IDS.indexOf(tab)
    let next = null
    if (e.key === 'ArrowRight') next = IDS[(at + 1) % IDS.length]
    else if (e.key === 'ArrowLeft') next = IDS[(at - 1 + IDS.length) % IDS.length]
    else if (e.key === 'Home') next = IDS[0]
    else if (e.key === 'End') next = IDS[IDS.length - 1]
    if (!next) return
    e.preventDefault()
    select(next)
    tabRefs.current[next]?.focus()
  }

  // Wait for the first occupancy answer before deciding "absent": a loading probe is not an
  // absent NPU.
  if (occ === undefined && !npuTabVisible(undefined, slots)) {
    if (occQ.isLoading) return <div className="npt npu-card" data-testid="npu-loading">Loading…</div>
  }
  // A failed occupancy read with no NPU slot to fall back on is "can't tell", not "no NPU".
  if (occQ.isError && !npuTabVisible(occ, slots)) {
    return (
      <div className="npt npu-card" data-testid="npu-pane">
        <div className="npt-absent" data-testid="npu-status-error" role="alert">
          <h2>Couldn't read the NPU status</h2>
          <p>hal0 could not reach the NPU status endpoint, so it cannot tell whether this box has an NPU.</p>
          <button type="button" className="btn sm" data-testid="npu-status-retry" disabled={occQ.isFetching} onClick={() => occQ.refetch()}>
            Retry
          </button>
        </div>
      </div>
    )
  }
  if (!npuTabVisible(occ, slots)) {
    return (
      <div className="npt npu-card" data-testid="npu-pane">
        <AbsentPanel host={occ?.npu_host} />
      </div>
    )
  }

  const parts = npuStatusParts(occ, hwQ.data, slots)

  return (
    <div className="npt npu-card" data-testid="npu-pane">
      <div className="npt-status" data-testid="npu-status" role="status">
        {parts.map((p) => (
          <span key={p.k} className={`npt-status-part${p.tone ? ` ${p.tone}` : ''}`}>
            <span className="npt-status-k">{p.k}</span>
            <span className="npt-status-v">{p.v}</span>
          </span>
        ))}
      </div>

      <div className="npt-tabs" role="tablist" aria-label="NPU">
        {NPU_TABS.map((t) => {
          const on = tab === t.id
          return (
            <button
              key={t.id}
              ref={(el) => {
                tabRefs.current[t.id] = el
              }}
              type="button"
              role="tab"
              id={`npu-tab-${t.id}`}
              className={`npt-tab${on ? ' on' : ''}`}
              data-testid={`npu-tab-${t.id}`}
              aria-selected={on}
              aria-controls={`npu-panel-${t.id}`}
              tabIndex={on ? 0 : -1}
              onClick={() => select(t.id)}
              onKeyDown={onKeyDown}
            >
              {t.label}
            </button>
          )
        })}
      </div>

      <div role="tabpanel" id="npu-panel-lanes" className="npt-panel" aria-labelledby="npu-tab-lanes" hidden={tab !== 'lanes'}>
        <LanesPanel draft={flmDraft} setDraft={setFlmDraft} occ={occ} hw={hwQ.data} slots={slots} />
      </div>
      <div role="tabpanel" id="npu-panel-models" className="npt-panel" aria-labelledby="npu-tab-models" hidden={tab !== 'models'}>
        <NpuModelsPanel slots={slots} occ={occ} setFlmDraft={setFlmDraft} onSwitchTab={select} />
      </div>
      <div role="tabpanel" id="npu-panel-engines" className="npt-panel" aria-labelledby="npu-tab-engines" hidden={tab !== 'engines'}>
        <NpuEnginesPanel occ={occ} hw={hwQ.data} slots={slots} flmDirty={draftCount(flmDraft) > 0} />
      </div>
    </div>
  )
}
