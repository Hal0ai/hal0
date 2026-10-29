// hal0 Overview v2 — Agents panel: OvAgents({ onGo }).
//
// Hermes (state, version, the profiles it serves, ctx fill of the slot it orchestrates, persona,
// approvals, channels), Pi (installed word and version), then the last delegated bridge runs and
// the board's in-progress count. Every figure comes from agents-pure.ts; a figure a probe cannot
// source renders a dash.

import { useAgents, useAgentOverview } from '@/api/hooks/useAgents'
import { useSlots } from '@/api/hooks/useSlots'
import { useBridgeRuns, useBridgeClients } from '@/api/hooks/useBridge'
import { useBoardStats } from '@/api/hooks/useBoard'
import { fmtK } from './overview-pure'
import { hermesRow, piRow, runRow, inProgress } from './agents-pure'

const { useMemo } = React

const NO_DATA = { data: null }
const STATE_TONE = { serving: 'ok', ready: 'warn', down: 'err', unknown: 'dim' }

// The same event the topbar and the board toolbar dispatch; main.jsx listens for it.
const openAgentChat = () => window.dispatchEvent(new CustomEvent('hal0:toggle-agent-chat'))

function emptyRunsLine(clients) {
  const base = 'No delegated runs in the last 24 h'
  if (!Array.isArray(clients)) return base
  return `${base} · ${clients.length} bridge client${clients.length === 1 ? '' : 's'} paired`
}

function HermesAgent({ row }) {
  const n = row.profiles.length
  return (
    <div className="ov-agent">
      <div className="ov-avatar">H</div>
      <div className="ov-meta">
        <div className="ov-r1">
          <span className="ov-nm">Hermes</span>
          <window.OvPill tone={STATE_TONE[row.state]}>
            <span className="ov-d" />
            {row.state}
          </window.OvPill>
          <span className="ov-sub">{[row.version, row.pyLine].filter(Boolean).join(' · ') || '—'}</span>
        </div>
        {n > 0 && (
          <div className="ov-profiles">
            profiles
            {row.profiles.map((p) => (
              <span className="ov-p" key={p}>
                <span className="ov-dot up ov-d" />
                {p}
              </span>
            ))}
            <span>
              · {n} {n === 1 ? 'profile' : 'profiles'} serving
            </span>
          </div>
        )}
        <div className="ov-ctxrow">
          ctx
          {row.ctx ? (
            <>
              <span className="ov-track">
                <i style={{ width: row.ctx.pct + '%', background: row.ctx.pct > 85 ? 'var(--warn)' : 'var(--fg-3)' }} />
              </span>
              {fmtK(row.ctx.used)}/{fmtK(row.ctx.max)}
            </>
          ) : (
            ' —'
          )}
          <span>
            · persona {row.persona || '—'} · approvals {row.approvals || '—'}
          </span>
        </div>
        {row.channels.length > 0 && (
          <div className="ov-r1">
            {row.channels.map((c) => (
              <span className="ov-chip" key={c}>
                {c}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function PiAgent({ row }) {
  return (
    <div className="ov-agent">
      <div className="ov-avatar pi">π</div>
      <div className="ov-meta">
        <div className="ov-r1">
          <span className="ov-nm">Pi</span>
          <window.OvPill tone="dim">
            <span className="ov-d" />
            {row.label}
          </window.OvPill>
          <span className="ov-sub">{[row.version, row.adapter].filter(Boolean).join(' · ') || '—'}</span>
        </div>
        <div className="ov-sub">slot sync · dual memory · delegate — runs from a terminal, not from here</div>
      </div>
    </div>
  )
}

function OvAgents({ onGo }) {
  const usePersonas = window.__hal0UseAgentPersonas
  const agents = useAgents().data
  const hermesOv = useAgentOverview('hermes').data || null
  const piOv = useAgentOverview('pi').data || null
  const slots = useSlots().data
  const personas = (usePersonas ? usePersonas('hermes') : NO_DATA).data || null
  const runsQ = useBridgeRuns({ limit: 5 })
  const clients = useBridgeClients().data?.items
  const stats = useBoardStats().data

  const hermesRec = useMemo(
    () => (agents?.agents || []).find((a) => a.name === 'hermes' || a.id === 'hermes') || null,
    [agents],
  )
  const primary = window.__hal0PrimarySlot ? window.__hal0PrimarySlot(slots) : null
  const hermes = hermesRow(hermesOv, personas, primary, hermesRec)
  const pi = piRow(piOv)

  const nowS = Math.floor(Date.now() / 1000)
  const runs = (runsQ.data?.items || []).map((r) => runRow(r, nowS))
  const running = runs.filter((r) => r.dot === 'serving').length
  const board = inProgress(stats)

  return (
    <window.OvCard
      title="Agents"
      flush
      right={
        <button type="button" className="ov-lnk" onClick={() => onGo('agents')}>
          Agents →
        </button>
      }
      foot={
        <>
          <button type="button" className="ov-lnk" onClick={openAgentChat}>
            Open Agent Chat
          </button>
          <span className="sp" style={{ flex: 1 }} />
          <button type="button" className="ov-lnk" onClick={() => onGo('board')}>
            Kanban →
          </button>
        </>
      }
    >
      <HermesAgent row={hermes} />
      <PiAgent row={pi} />
      <div className="ov-card-h" style={{ borderTop: '1px solid var(--line-soft)' }}>
        <span className="ov-card-t">Delegated runs</span>
        <span className="ov-card-n">{running} running</span>
        <span className="sp" />
        <span className="ov-card-n">board · {board === null ? '—' : board + ' in progress'}</span>
      </div>
      {runs.length === 0 ? (
        <div className="ov-empty">{emptyRunsLine(clients)}</div>
      ) : (
        <div className="ov-runs">
          {runs.map((r) => (
            <div className={'ov-run' + (r.dot === 'serving' ? ' running' : '')} key={r.key}>
              <span className={'ov-dot ' + r.dot} />
              <div>
                <div className="ov-what">{r.what}</div>
                <div className="ov-who">{r.who}</div>
              </div>
              <span className="ov-t">{r.time}</span>
            </div>
          ))}
        </div>
      )}
    </window.OvCard>
  )
}

Object.assign(window, { OvAgents })
