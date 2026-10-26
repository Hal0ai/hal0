// hal0 dashboard — ServicesCard (W5)
//
// §0 NO STUB — all data from real endpoints.
// Sources:
//   - useServicesHealth → GET /api/services/health (§2d, fail soft on 404)
//   - useComfyui → GET /api/comfyui/status (already exists, reachable)
//
// ComfyUI's editor link follows the Image Gen pane's rule (image/ComfyEditorLink.jsx): ComfyUI
// listens on the box's loopback only, so the button opens it on the box and is disabled elsewhere.
// The browser never calls ComfyUI itself, and there is no placeholder status: before
// /api/comfyui/status answers, the card shows what the services health gave it.
// Gate: if useServicesHealth pending (404) → "source pending" body but still
//       render the ComfyUI row if comfyui/status reachable.
//
// Window-global: Object.assign(window, {ServicesCard})

import { useServicesHealth } from '@/api/hooks/useServicesHealth'
import { statusCopyForServiceState } from './status-copy'
import { useComfyui } from '@/api/hooks/useComfyui'
import { useComponents } from '@/api/hooks/useComponents'
import { pendingCount } from './components-pure'
import { EditorOpenButton } from './image/ComfyEditorLink'

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
// `title` carries the consequence-first sentence for the service's precise
// health word (services_health.py's up|stopped|down) beside the pill's own
// simplified up/idle label — see status-copy.ts. `state` is absent on a
// synthesized row (ComfyUI fallback) and on older backends, so fall back to
// `up`; `detail` lets an unwired-probe row ("unmonitored", #2250) say so
// instead of being described as crashed.
function StatusPill({ up, state, detail }) {
  return (
    <span
      className={'svc-pill' + (up ? ' svc-pill-up' : ' svc-pill-idle')}
      title={statusCopyForServiceState(state ?? (up ? 'up' : 'down'), detail)}
    >
      <span className={'sdot ' + (up ? 'serving' : 'offline')} style={{ width: 6, height: 6 }} />
      {up ? 'up' : 'idle'}
    </span>
  )
}

// ── Service row ───────────────────────────────────────────────────────────────
function ServiceRow({ svc }) {
  const isComfy = svc.id === 'comfyui'
  return (
    <div className="svc-row">
      <div className="svc-row-main">
        {/* Icon tile */}
        <span className="svc-icon-tile">
          {serviceIcon(svc.id)}
        </span>

        {/* Name + status pill */}
        <span className="svc-info">
          <span className="svc-name">{svc.name}</span>
          <StatusPill up={svc.up} state={svc.state} detail={svc.detail} />
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
          {isComfy ? (
            // Not `svc.url`: the health probe's address is the API's view of ComfyUI.
            <EditorOpenButton testid="svc-open-comfyui">{Icons.ext}</EditorOpenButton>
          ) : svc.url && (
            <button
              className="svc-btn"
              onClick={() => window.open(svc.url, '_blank', 'noopener')}
              title={`Open ${svc.name}`}
            >
              {Icons.ext}
            </button>
          )}
        </span>
      </div>
    </div>
  )
}

// ── ServicesCard ──────────────────────────────────────────────────────────────
export function ServicesCard() {
  const { services, pending } = useServicesHealth()
  const comfyQ = useComfyui({ active: false })
  // No placeholder status: until /api/comfyui/status answers there is no ComfyUI row of our own.
  const comfySt = comfyQ.data
  const comfyReachable = comfySt?.reachable === true

  // Task 12: "N updates pending" badge — fail-soft (older daemon without
  // GET /api/updates/components → componentRows null → pendingCount 0, no
  // badge, rest of the card unaffected).
  const componentsQ = useComponents()
  const componentRows = componentsQ.data?.components ?? null
  const pendingN = pendingCount(componentRows)
  const pendingBadge = pendingN > 0
    ? <a className="chip" href="#services" data-testid="svc-pending-badge"
        title={`${pendingN} component update${pendingN === 1 ? '' : 's'} pending`}>
        {pendingN} update{pendingN === 1 ? '' : 's'} pending
      </a>
    : null

  // If services health pending (404 not built yet) but comfyui/status IS
  // reachable, we still show the ComfyUI row from what we know.
  const showPendingGate = pending && !comfyReachable

  if (showPendingGate) {
    return (
      <DCard title="SERVICES" right={pendingBadge}>
        <div className="svc-pending">source pending</div>
      </DCard>
    )
  }

  // When services health is pending but ComfyUI is reachable, synthesize a
  // ComfyUI row from /api/comfyui/status data.
  let rows = services
  const hasComfyFromHealth = services.some((s) => s.id === 'comfyui')

  if (!hasComfyFromHealth && comfyReachable) {
    // Synthesize from comfyui/status
    const jobs = (comfySt.queue?.running ?? 0) + (comfySt.queue?.pending ?? 0)
    const synth = {
      id: 'comfyui',
      name: 'ComfyUI',
      up: comfySt.engine !== 'stopped' && comfySt.engine !== 'error',
      detail: `generation engine · ${comfySt.mode ?? "mode unknown"}`,
      url: null,
      stat:
        jobs > 0
          ? { label: 'jobs', value: String(jobs) }
          : { label: 'mode', value: comfySt.mode ?? 'unknown' },
    }
    rows = [synth, ...rows]
  }

  if (rows.length === 0 && pending) {
    return (
      <DCard title="SERVICES" right={pendingBadge}>
        <div className="svc-pending">source pending</div>
      </DCard>
    )
  }

  return (
    <DCard title="SERVICES" right={pendingBadge}>
      <div className="svc-list">
        {rows.map((svc) => (
          <ServiceRow key={svc.id} svc={svc} />
        ))}
      </div>
    </DCard>
  )
}

Object.assign(window, { ServicesCard })
