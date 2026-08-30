// hal0 dashboard — Services page (#services)
//
// Dedicated management surface for the companion services (OpenWebUI,
// ComfyUI, Hermes, Hindsight, n8n). Registry-driven from the backend:
//   - useServices → GET /api/services (5s poll, fail-soft "source pending")
//   - useServiceAction → POST /api/services/{id}/action (allow-listed
//     systemctl verbs; the backend registry decides what each service may do)
//   - useMdnsAdvertise → POST /api/services/mdns (avahi addon announcements)
//   - useUnitLogs → GET /api/logs?unit=… (journald drawer, on demand)
//
// §0 NO STUB — every dot/pill reflects a real probe; a service with no
// wired probe honestly shows "unmonitored". Lifecycle buttons render only
// for the verbs the backend advertises in `actions`.

import { useServices, useServiceAction, useMdnsAdvertise, useUnitLogs } from '@/api/hooks/useServices'
import { useComfyui, COMFYUI_FALLBACK } from '@/api/hooks/useComfyui'
import { useComponents, useComponentConverge } from '@/api/hooks/useComponents'
import { useUpdateJob } from '@/api/hooks/useUpdates'
import { componentForService, componentCell, RETRYABLE_STATUSES } from './components-pure'

const { useState, useEffect, useRef } = React

// ── Inline SVG icons (16×16, 1.5px stroke — hal0 thin-line family) ───────────
function SIc({ d, children, size = 14, sw = 1.5 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {d ? <path d={d} /> : children}
    </svg>
  )
}

const SIcons = {
  ext:     <SIc d="M6 3H3v10h10v-3M9 3h4v4M9 9l4-4" />,
  chev:    <SIc d="M4 6l4 4 4-4" />,
  chevUp:  <SIc d="M4 10l4-4 4 4" />,
  restart: <SIc d="M14 8a6 6 0 1 1-2-4.5M14 1v3.5h-3.5" />,
  play:    <SIc d="M5 3l8 5-8 5V3z" />,
  stop:    <SIc><rect x="4" y="4" width="8" height="8" rx="1" /></SIc>,
  logs:    <SIc d="M3 3h10M3 6h10M3 9h7M3 12h5" />,
  wifi:    <SIc><path d="M2 6.5a9 9 0 0 1 12 0M4.5 9.5a5.5 5.5 0 0 1 7 0" /><circle cx="8" cy="12.5" r="1" fill="currentColor" stroke="none" /></SIc>,
  comfy:   <SIc><circle cx="4" cy="4" r="2" /><circle cx="12" cy="6" r="2" /><circle cx="6" cy="12" r="2" /><path d="M6 4.5l4 1M5.4 10.2l5-3.6" /></SIc>,
  hermes:  <SIc><circle cx="8" cy="8" r="5.5" /><path d="M8 5v3l2 1.5" /></SIc>,
  openwebui: <SIc><rect x="3" y="3" width="10" height="10" rx="2" /><path d="M6 8h4M6 10.5h2" /></SIc>,
  hindsight: <SIc><path d="M6 2.5A2.5 2.5 0 0 0 3.5 5v.2A2.3 2.3 0 0 0 3 9.5 2.4 2.4 0 0 0 6 13.5V2.5z" /><path d="M10 2.5A2.5 2.5 0 0 1 12.5 5v.2A2.3 2.3 0 0 1 13 9.5a2.4 2.4 0 0 1-3 4V2.5z" /></SIc>,
  n8n:     <SIc><path d="M4 8a4 4 0 0 1 8 0" /><circle cx="4" cy="8" r="1.5" /><circle cx="12" cy="8" r="1.5" /></SIc>,
  default: <SIc><circle cx="8" cy="8" r="5" /><path d="M8 5v3M8 11v.01" /></SIc>,
}

function svcIcon(id) {
  return SIcons[id] ?? SIcons.default
}

// ── helpers ───────────────────────────────────────────────────────────────────

// "Fri 2026-07-03 09:12:01 UTC" (systemd ActiveEnterTimestamp) → "up 26h"
function uptimeFrom(since) {
  if (!since) return null
  const t = Date.parse(since.replace(/^[A-Za-z]{3} /, ''))
  if (Number.isNaN(t)) return null
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000))
  if (s < 90) return `up ${s}s`
  if (s < 90 * 60) return `up ${Math.round(s / 60)}m`
  if (s < 36 * 3600) return `up ${Math.round(s / 3600)}h`
  return `up ${Math.round(s / 86400)}d`
}

function StatePill({ svc }) {
  const cls = svc.up ? 'serving' : (svc.unit_state?.active_state === 'failed' ? 'error' : 'offline')
  const label = svc.up ? 'up' : (svc.unit_state?.active_state === 'failed' ? 'failed' : (svc.managed ? 'down' : '—'))
  return (
    <span className={'svc-pill' + (svc.up ? ' svc-pill-up' : ' svc-pill-idle')}>
      <span className={'sdot ' + cls} style={{ width: 6, height: 6 }} />
      {label}
    </span>
  )
}

// ── Component version cell + retry (Task 12, spec 2026-08-30 §4) ─────────────
// Joins the Services page to GET /api/updates/components via componentForService
// (services.jsx passes each row's matching component down by service_id).
// Retry (POST .../converge) is offered ONLY on RETRYABLE_STATUSES — a pending
// row never gets an update-now button, per spec §4's explicit decision that
// convergence is scheduled server-side, not operator-triggered from a pending
// state. A retry's job id is tracked with the same useUpdateJob poller the
// self-update apply flow uses (same backend job map — see updater.py's
// converge_component_route, which reuses _update_jobs()).
const TONE_COLOR = {
  ok: 'var(--ok)',
  pending: 'var(--warn)',
  failed: 'var(--err)',
  muted: 'var(--fg-4)',
}

function useComponentRetry(component) {
  const convergeMut = useComponentConverge()
  const [jobId, setJobId] = useState(null)
  const lastTerminalJobRef = useRef(null)
  const { job, terminal } = useUpdateJob(jobId)

  useEffect(() => {
    if (!terminal || !job || lastTerminalJobRef.current === job.id) return
    lastTerminalJobRef.current = job.id
    const label = component?.name || component?.id || 'component'
    if (job.state === 'applied') {
      window.__hal0Toast && window.__hal0Toast(`${label} converged`, 'ok')
    } else {
      const detail = job.error || job.error_code || 'unknown'
      window.__hal0Toast && window.__hal0Toast(`${label} retry failed: ${detail}`, 'err')
    }
  }, [terminal, job, component])

  const jobBusy = !!(job && (job.state === 'queued' || job.state === 'running'))

  const retry = () => {
    if (!component) return
    convergeMut.mutate(component.id, {
      onSuccess: (snap) => setJobId(snap?.id || null),
      onError: (err) => {
        window.__hal0Toast && window.__hal0Toast(`Retry failed to start — ${err?.message || 'see logs'}`, 'err')
      },
    })
  }

  return { retry, jobBusy, starting: convergeMut.isPending }
}

function ComponentVersionLine({ component }) {
  const { retry, jobBusy, starting } = useComponentRetry(component)
  const cell = componentCell(component)
  const retryable = RETRYABLE_STATUSES.includes(component.status)

  return (
    <div className="svcp-detail" data-testid={`svcp-component-${component.id}`}>
      <span className="mono" style={{ color: TONE_COLOR[cell.tone] }}>{cell.label}</span>
      {retryable && (
        <button
          className="btn ghost sm svcp-act"
          data-testid={`svcp-component-retry-${component.id}`}
          disabled={starting || jobBusy}
          onClick={retry}
          style={{ marginLeft: 8 }}
        >{jobBusy ? 'Retrying…' : 'Retry'}</button>
      )}
      {retryable && (component.error || component.remedy) && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 2 }}>
          {component.error && <div style={{ color: 'var(--err)' }}>{component.error}</div>}
          {component.remedy && <div>{component.remedy}</div>}
        </div>
      )}
    </div>
  )
}

// ── Logs drawer (journald tail via /api/logs?unit=…) ─────────────────────────
function LogsDrawer({ unit, open }) {
  const q = useUnitLogs(unit, open)
  if (!open) return null
  const lines = q.data?.lines ?? []
  return (
    <div className="svcp-logs" data-testid={`svcp-logs-${unit}`}>
      {q.isPending && <div className="svcp-logs-empty">loading logs…</div>}
      {!q.isPending && lines.length === 0 && (
        <div className="svcp-logs-empty">{q.data?.hint || 'no journal entries'}</div>
      )}
      {lines.length > 0 && (
        <pre className="svcp-logs-pre mono">{lines.slice(-120).join('\n')}</pre>
      )}
    </div>
  )
}

// ── Action buttons row ────────────────────────────────────────────────────────
function ActionButtons({ svc, onAction, busy }) {
  const has = (a) => svc.actions.includes(a)
  const running = svc.unit_state?.active_state === 'active'
  return (
    <>
      {has('restart') && (
        <button className="btn ghost sm svcp-act" disabled={busy}
          data-testid={`svcp-restart-${svc.id}`}
          onClick={() => onAction(svc.id, 'restart')}>
          {SIcons.restart} Restart
        </button>
      )}
      {has('start') && !running && (
        <button className="btn ghost sm svcp-act" disabled={busy}
          data-testid={`svcp-start-${svc.id}`}
          onClick={() => onAction(svc.id, 'start')}>
          {SIcons.play} Start
        </button>
      )}
      {has('stop') && running && (
        <button className="btn ghost sm svcp-act svcp-act-danger" disabled={busy}
          data-testid={`svcp-stop-${svc.id}`}
          onClick={() => {
            if (window.confirm(`Stop ${svc.name}? Clients using it will lose access until it is started again.`)) {
              onAction(svc.id, 'stop')
            }
          }}>
          {SIcons.stop} Stop
        </button>
      )}
    </>
  )
}

// ── One service card ──────────────────────────────────────────────────────────
function ServiceCard({ svc, onAction, busyId, actionMsg, comfyReachable, component }) {
  const [logsOpen, setLogsOpen] = useState(false)
  const [queueOpen, setQueueOpen] = useState(false)
  const busy = busyId === svc.id
  const uptime = svc.up ? uptimeFrom(svc.unit_state?.since) : null
  const isComfy = svc.id === 'comfyui'
  const CJQ = typeof ComfyJobQueue === 'function' ? ComfyJobQueue : null

  return (
    <div className={'svcp-card' + (svc.up ? ' svcp-card-up' : '')} data-testid={`svcp-card-${svc.id}`}>
      <div className="svcp-card-head">
        <span className="svc-icon-tile">{svcIcon(svc.id)}</span>
        <span className="svcp-title">
          <span className="svcp-name">{svc.name}</span>
          <StatePill svc={svc} />
          {uptime && <span className="svcp-uptime mono">{uptime}</span>}
        </span>
        {svc.url && (
          <button className="svc-btn" title={`Open ${svc.name}`}
            data-testid={`svcp-open-${svc.id}`}
            onClick={() => window.open(svc.url, '_blank', 'noopener')}>
            {SIcons.ext}
          </button>
        )}
      </div>

      <div className="svcp-desc">{svc.description}</div>
      <div className="svcp-detail">{svc.detail}{svc.stat ? ` · ${svc.stat.value} ${svc.stat.label}` : ''}</div>
      {component && <ComponentVersionLine component={component} />}

      <div className="svcp-meta mono">
        {svc.unit && <span className="svcp-meta-row">unit <b>{svc.unit}</b> · {svc.unit_state?.active_state ?? 'unknown'}{svc.unit_state?.unit_file_state && svc.unit_state.unit_file_state !== 'unknown' ? ` · ${svc.unit_state.unit_file_state}` : ''}</span>}
        {svc.url && <span className="svcp-meta-row">url <b>{svc.url}</b></span>}
        {svc.mdns_url && <span className="svcp-meta-row">mdns <b>{svc.mdns_url}</b></span>}
        {!svc.url && svc.loopback_port && <span className="svcp-meta-row">bind <b>127.0.0.1:{svc.loopback_port}</b> (loopback-only)</span>}
        {!svc.managed && <span className="svcp-meta-row svcp-unmanaged">external — not managed by hal0</span>}
      </div>

      {svc.hints.length > 0 && (
        <div className="svcp-hints">{svc.hints.map((h, i) => <span key={i}>ⓘ {h}</span>)}</div>
      )}

      <div className="svcp-actions">
        <ActionButtons svc={svc} onAction={onAction} busy={busy} />
        {svc.unit && (
          <button className={'btn ghost sm svcp-act' + (logsOpen ? ' on' : '')}
            data-testid={`svcp-logbtn-${svc.id}`}
            onClick={() => setLogsOpen(v => !v)}>
            {SIcons.logs} Logs {logsOpen ? SIcons.chevUp : SIcons.chev}
          </button>
        )}
        {isComfy && CJQ && (
          <button className={'btn ghost sm svcp-act' + (queueOpen ? ' on' : '')}
            data-testid="svcp-queue-comfyui"
            onClick={() => setQueueOpen(v => !v)}>
            Queue {queueOpen ? SIcons.chevUp : SIcons.chev}
          </button>
        )}
        {busy && <span className="svcp-busy mono">working…</span>}
      </div>
      {actionMsg && busyId === null && actionMsg.id === svc.id && (
        <div className={'svcp-result' + (actionMsg.ok ? '' : ' err')}>{actionMsg.text}</div>
      )}

      {isComfy && queueOpen && CJQ && <CJQ comfyReachable={comfyReachable} />}
      {svc.unit && <LogsDrawer unit={svc.unit} open={logsOpen} />}
    </div>
  )
}

// ── mDNS / discovery card ─────────────────────────────────────────────────────
function DiscoveryCard({ mdns, services }) {
  const advertiseMut = useMdnsAdvertise()
  if (!mdns) return null
  const advertised = mdns.advertised ?? []
  const on = advertised.length > 0
  const capable = services.filter(s => s.mdns_capable)
  return (
    <DCard title="DISCOVERY (mDNS)" data-testid="svcp-mdns">
      <div className="svcp-mdns">
        <div className="svcp-mdns-row">
          <span className={'sdot ' + (mdns.available ? 'serving' : 'offline')} style={{ width: 6, height: 6 }} />
          <span>avahi {mdns.available ? 'active' : 'inactive'}</span>
          <span className="svcp-mdns-host mono">{mdns.hostname}</span>
          <span className="vh-spacer" style={{ flex: 1 }} />
          <button className={'btn ghost sm svcp-act' + (on ? ' on' : '')}
            data-testid="svcp-mdns-toggle"
            disabled={advertiseMut.isPending || !mdns.available}
            onClick={() => advertiseMut.mutate(!on)}>
            {SIcons.wifi} {on ? 'Withdraw announcements' : 'Announce services'}
          </button>
        </div>
        <div className="svcp-mdns-sub">
          {mdns.base_advertised
            ? <>Dashboard announced as <b className="mono">{mdns.hostname}</b>.</>
            : <>No base <span className="mono">hal0.service</span> avahi file — the installer writes it when avahi is present.</>}
          {' '}
          {on
            ? <>Announcing: {capable.filter(s => advertised.includes(s.id)).map(s => s.name).join(', ') || '—'}.</>
            : <>Addon services ({capable.map(s => s.name).join(', ')}) are not announced — LAN clients need the raw <span className="mono">host:port</span>.</>}
        </div>
        {!mdns.available && (
          <div className="svcp-mdns-sub svcp-unmanaged">
            avahi-daemon is not active on this host — install/enable it (or add a static hosts entry) for .local discovery.
          </div>
        )}
        {advertiseMut.data?.message && (
          <div className="svcp-result err">{advertiseMut.data.message}</div>
        )}
      </div>
    </DCard>
  )
}

// ── Runner images card (Task 12) ──────────────────────────────────────────────
// `runner-images` is a component row with no `service_id` (it isn't a
// systemd-managed service — see hal0.components.registry) so it never joins
// to a ServiceCard. Its `detail[]` (per-runner-key resolved image, from
// converge_runner_images) is rendered here instead, with the same retry
// treatment as a per-service version cell.
function RunnerImagesCard({ component }) {
  if (!component) return null
  const { retry, jobBusy, starting } = useComponentRetry(component)
  const cell = componentCell(component)
  const retryable = RETRYABLE_STATUSES.includes(component.status)
  const detail = component.detail || []

  return (
    <DCard title="RUNNER IMAGES" data-testid="svcp-runner-images">
      <div className="svcp-mdns">
        <div className="svcp-mdns-row">
          <span className="mono" style={{ color: TONE_COLOR[cell.tone] }}>{cell.label}</span>
          <span className="vh-spacer" style={{ flex: 1 }} />
          {retryable && (
            <button
              className="btn ghost sm svcp-act"
              data-testid="svcp-component-retry-runner-images"
              disabled={starting || jobBusy}
              onClick={retry}
            >{jobBusy ? 'Retrying…' : 'Retry'}</button>
          )}
        </div>
        {detail.length > 0 && (
          <div className="svcp-mdns-sub mono">
            {detail.map(d => <div key={d.key}>{d.key} — {d.image}</div>)}
          </div>
        )}
        {retryable && (component.error || component.remedy) && (
          <div className="mono" style={{ fontSize: 11, marginTop: 4 }}>
            {component.error && <div style={{ color: 'var(--err)' }}>{component.error}</div>}
            {component.remedy && <div style={{ color: 'var(--fg-3)' }}>{component.remedy}</div>}
          </div>
        )}
      </div>
    </DCard>
  );
}

// ── ServicesView (page) ───────────────────────────────────────────────────────
function ServicesView() {
  const { services, mdns, pending } = useServices()
  const actionMut = useServiceAction()
  const comfyQ = useComfyui({ active: false })
  const comfyReachable = (comfyQ.data ?? COMFYUI_FALLBACK).reachable
  const [actionMsg, setActionMsg] = useState(null)

  // Task 12: fail-soft — an older daemon without GET /api/updates/components
  // resolves to `data: null`, so `componentRows` stays [] and every
  // componentForService() lookup below returns undefined (no version cells,
  // no runner-images card) rather than throwing.
  const componentsQ = useComponents()
  const componentRows = componentsQ.data?.components ?? null
  // `runner-images` carries `service_id: null` (it isn't a systemd-managed
  // service — hal0.components.registry) so it never joins via
  // componentForService; look it up by id instead.
  const runnerImagesComponent = (componentRows ?? []).find(r => r.id === 'runner-images')

  const busyId = actionMut.isPending ? actionMut.variables?.id ?? null : null

  const onAction = (id, action) => {
    setActionMsg(null)
    actionMut.mutate({ id, action }, {
      onSuccess: (res) => setActionMsg({ id, ok: res.ok, text: res.message }),
      onError: (err) => setActionMsg({ id, ok: false, text: String(err?.message || err) }),
    })
  }

  return (
    <div className="view svcp-view">
      <div className="vh">
        <span className="vh-eye mono">Companions</span>
        <h1>Services</h1>
        <span className="vh-spacer" />
      </div>

      {pending ? (
        <div className="svc-pending" data-testid="svcp-pending">source pending</div>
      ) : (
        <>
          <DiscoveryCard mdns={mdns} services={services} />
          <div className="svcp-grid">
            {services.map(svc => (
              <ServiceCard key={svc.id} svc={svc}
                onAction={onAction} busyId={busyId} actionMsg={actionMsg}
                comfyReachable={comfyReachable}
                component={componentForService(componentRows, svc.id)} />
            ))}
          </div>
          <RunnerImagesCard component={runnerImagesComponent} />
        </>
      )}
    </div>
  )
}

Object.assign(window, { ServicesView })
