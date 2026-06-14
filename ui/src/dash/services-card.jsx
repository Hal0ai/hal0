// hal0 dashboard — ServicesCard (W5)
//
// §0 NO STUB — all data from real endpoints.
// Sources:
//   - useServicesHealth → GET /api/services/health (§2d, fail soft on 404)
//   - useComfyui → GET /api/comfyui/status (already exists, reachable)
//   - ComfyUI native /queue + /history fetched directly at http://${host}:8188
//     (same approach as comfyui-pane.jsx comfyHref() — not proxied by Vite)
//
// ComfyUI row expands inline to live job queue drawer.
// Gate: if useServicesHealth pending (404) → "source pending" body but still
//       render ComfyUI live-queue if comfyui/status reachable.
//
// Window-global: Object.assign(window, {ServicesCard})

import { useServicesHealth } from '@/api/hooks/useServicesHealth'
import { useComfyui, COMFYUI_FALLBACK } from '@/api/hooks/useComfyui'

const { useState, useEffect, useRef, useCallback } = React

// ── ComfyUI native base URL (mirrors comfyui-pane.jsx comfyHref) ─────────────
function comfyNativeBase() {
  const host =
    typeof window !== 'undefined' && window.location ? window.location.hostname : '127.0.0.1'
  return `http://${host}:8188`
}

// ── Inline SVG icons (16×16, 1.5px stroke — hal0 thin-line family) ───────────
function Ic({ d, children, size = 14, fill = 'none', sw = 1.5 }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill={fill}
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {d ? <path d={d} /> : children}
    </svg>
  )
}

const Icons = {
  ext:    <Ic d="M6 3H3v10h10v-3M9 3h4v4M9 9l4-4" />,
  chev:   <Ic d="M4 6l4 4 4-4" />,
  chevUp: <Ic d="M4 10l4-4 4 4" />,
  // Service identity glyphs
  comfy: (
    <Ic>
      <circle cx="4" cy="4" r="2" />
      <circle cx="12" cy="6" r="2" />
      <circle cx="6" cy="12" r="2" />
      <path d="M6 4.5l4 1M5.4 10.2l5-3.6" />
    </Ic>
  ),
  hermes: (
    <Ic>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 5v3l2 1.5" />
    </Ic>
  ),
  openwebui: (
    <Ic>
      <rect x="3" y="3" width="10" height="10" rx="2" />
      <path d="M6 8h4M6 10.5h2" />
    </Ic>
  ),
  n8n: (
    <Ic>
      <path d="M4 8a4 4 0 0 1 8 0" />
      <circle cx="4" cy="8" r="1.5" />
      <circle cx="12" cy="8" r="1.5" />
    </Ic>
  ),
  default: (
    <Ic>
      <circle cx="8" cy="8" r="5" />
      <path d="M8 5v3M8 11v.01" />
    </Ic>
  ),
}

function serviceIcon(id) {
  return Icons[id] ?? Icons.default
}

// ── Status pill ───────────────────────────────────────────────────────────────
function StatusPill({ up }) {
  return (
    <span className={'svc-pill' + (up ? ' svc-pill-up' : ' svc-pill-idle')}>
      <span className={'sdot ' + (up ? 'serving' : 'offline')} style={{ width: 6, height: 6 }} />
      {up ? 'up' : 'idle'}
    </span>
  )
}

// ── ComfyUI live job queue drawer ─────────────────────────────────────────────
// Polls ComfyUI native /queue + /history at 2s while mounted.
// Gate: if unreachable → "comfyui offline", no fake jobs.
function ComfyJobQueue({ comfyReachable }) {
  const [queueData, setQueueData] = useState(null) // null = loading/unknown
  const [offline, setOffline] = useState(false)
  const intervalRef = useRef(null)

  const poll = useCallback(async () => {
    if (!comfyReachable) {
      setOffline(true)
      setQueueData(null)
      return
    }
    const base = comfyNativeBase()
    try {
      const [qRes, hRes] = await Promise.all([
        fetch(`${base}/queue`, { signal: AbortSignal.timeout(3000) }),
        fetch(`${base}/history?max_items=10`, { signal: AbortSignal.timeout(3000) }),
      ])
      if (!qRes.ok) { setOffline(true); setQueueData(null); return }
      const q = await qRes.json()
      const h = hRes.ok ? await hRes.json() : {}
      setOffline(false)
      setQueueData({ queue: q, history: h })
    } catch {
      setOffline(true)
      setQueueData(null)
    }
  }, [comfyReachable])

  useEffect(() => {
    poll()
    intervalRef.current = setInterval(poll, 2000)
    return () => clearInterval(intervalRef.current)
  }, [poll])

  if (offline) {
    return (
      <div className="svc-comfy-drawer svc-comfy-offline">
        <span className="sdot offline" style={{ width: 6, height: 6 }} />
        comfyui offline
      </div>
    )
  }

  if (!queueData) {
    return (
      <div className="svc-comfy-drawer svc-comfy-loading">
        loading queue…
      </div>
    )
  }

  // Parse ComfyUI native /queue shape:
  // { queue_running: [[promptId, execId, {...}, {extra}, [outputs]], ...],
  //   queue_pending: [[promptId, execId, {...}, ...], ...] }
  const running = queueData.queue?.queue_running ?? []
  const pending = queueData.queue?.queue_pending ?? []
  const history = queueData.history ?? {}

  // Build job list: running first, then pending, then recent history
  const runningJobs = running.map((entry) => ({
    id: entry[1] ?? entry[0],
    promptId: entry[0],
    status: 'running',
    // ComfyUI /queue doesn't expose progress % or current node in the queue endpoint
    // (that's WS-only). Show what we have.
    workflow: entry[3]?.extra_pnginfo?.workflow?.name ?? `job ${String(entry[1] ?? '').slice(0, 8)}`,
    progress: null,
    currentNode: null,
  }))
  const pendingJobs = pending.map((entry) => ({
    id: entry[1] ?? entry[0],
    promptId: entry[0],
    status: 'queued',
    workflow: entry[3]?.extra_pnginfo?.workflow?.name ?? `job ${String(entry[1] ?? '').slice(0, 8)}`,
    progress: null,
    currentNode: null,
  }))
  // Recent history items (done jobs)
  const historyJobs = Object.entries(history)
    .slice(0, 5)
    .map(([id, h]) => ({
      id,
      promptId: id,
      status: h?.status?.status_str === 'success' ? 'done' : 'done',
      workflow: `job ${id.slice(0, 8)}`,
      progress: 100,
      currentNode: null,
    }))

  const allJobs = [...runningJobs, ...pendingJobs, ...historyJobs]

  if (allJobs.length === 0) {
    return (
      <div className="svc-comfy-drawer svc-comfy-empty">
        queue empty · idle
      </div>
    )
  }

  return (
    <div className="svc-comfy-drawer">
      {allJobs.map((job) => (
        <div key={job.id} className={'svc-job' + (job.status === 'running' ? ' svc-job-running' : '')}>
          <span
            className={'sdot ' + (
              job.status === 'running' ? 'serving' :
              job.status === 'done' ? 'svc-done' :
              'offline'
            )}
            style={{ width: 6, height: 6, flexShrink: 0 }}
          />
          <span className="svc-job-name">{job.workflow}</span>
          {job.progress != null && (
            <>
              <span className="svc-job-pct">{job.progress}%</span>
              <span className="svc-job-track">
                <span className="svc-job-fill" style={{ width: `${job.progress}%` }} />
              </span>
            </>
          )}
          {job.currentNode && (
            <span className="svc-job-node">{job.currentNode}</span>
          )}
          <span className={'svc-job-status ' + job.status}>{job.status}</span>
        </div>
      ))}
    </div>
  )
}

// ── Service row ───────────────────────────────────────────────────────────────
function ServiceRow({ svc, isComfy, comfyReachable, expanded, onToggle }) {
  return (
    <div className={'svc-row' + (isComfy && expanded ? ' svc-row-expanded' : '')}>
      <div className="svc-row-main">
        {/* Icon tile */}
        <span className="svc-icon-tile">
          {serviceIcon(svc.id)}
        </span>

        {/* Name + status pill */}
        <span className="svc-info">
          <span className="svc-name">{svc.name}</span>
          <StatusPill up={svc.up} />
        </span>

        {/* Role/sub line */}
        <span className="svc-detail">{svc.detail}</span>

        {/* Right stat */}
        {svc.stat && (
          <span className="svc-stat">
            <span className="svc-stat-val">{svc.stat.value}</span>
            <span className="svc-stat-label">{svc.stat.label}</span>
          </span>
        )}

        {/* Buttons */}
        <span className="svc-actions">
          {svc.url && (
            <button
              className="svc-btn"
              onClick={() => window.open(svc.url, '_blank', 'noopener')}
              title={`Open ${svc.name}`}
            >
              {Icons.ext}
            </button>
          )}
          {isComfy && (
            <button
              className={'svc-btn svc-expand-btn' + (expanded ? ' active' : '')}
              onClick={onToggle}
              title={expanded ? 'Collapse queue' : 'Expand queue'}
              aria-expanded={expanded}
            >
              {expanded ? Icons.chevUp : Icons.chev}
            </button>
          )}
        </span>
      </div>

      {/* ComfyUI inline queue drawer */}
      {isComfy && expanded && (
        <ComfyJobQueue comfyReachable={comfyReachable} />
      )}
    </div>
  )
}

// ── ServicesCard ──────────────────────────────────────────────────────────────
export function ServicesCard() {
  const { services, pending } = useServicesHealth()
  const comfyQ = useComfyui({ active: false })
  const comfySt = comfyQ.data ?? COMFYUI_FALLBACK
  const comfyReachable = comfySt.reachable

  const [comfyExpanded, setComfyExpanded] = useState(false)

  // If services health pending (404 not built yet) but comfyui/status IS
  // reachable, we still show the ComfyUI row from what we know.
  const showPendingGate = pending && !comfyReachable

  if (showPendingGate) {
    return (
      <DCard title="SERVICES">
        <div className="svc-pending">source pending</div>
      </DCard>
    )
  }

  // When services health is pending but ComfyUI is reachable, synthesize a
  // ComfyUI row from /api/comfyui/status data so the queue drawer works today.
  let rows = services
  const hasComfyFromHealth = services.some((s) => s.id === 'comfyui')

  if (!hasComfyFromHealth && comfyReachable) {
    // Synthesize from comfyui/status
    const synth = {
      id: 'comfyui',
      name: 'ComfyUI',
      up: comfySt.engine !== 'stopped' && comfySt.engine !== 'error',
      detail: `generation engine · ${comfySt.mode}`,
      url: `http://${typeof window !== 'undefined' ? window.location.hostname : '127.0.0.1'}:8188`,
      stat:
        comfySt.queue.running + comfySt.queue.pending > 0
          ? { label: 'jobs', value: String(comfySt.queue.running + comfySt.queue.pending) }
          : { label: 'mode', value: comfySt.mode },
    }
    rows = [synth, ...rows]
  }

  if (rows.length === 0 && pending) {
    return (
      <DCard title="SERVICES">
        <div className="svc-pending">source pending</div>
      </DCard>
    )
  }

  return (
    <DCard title="SERVICES">
      <div className="svc-list">
        {rows.map((svc) => {
          const isComfy = svc.id === 'comfyui'
          return (
            <ServiceRow
              key={svc.id}
              svc={svc}
              isComfy={isComfy}
              comfyReachable={comfyReachable}
              expanded={isComfy && comfyExpanded}
              onToggle={isComfy ? () => setComfyExpanded((v) => !v) : undefined}
            />
          )
        })}
      </div>
    </DCard>
  )
}

Object.assign(window, { ServicesCard })
