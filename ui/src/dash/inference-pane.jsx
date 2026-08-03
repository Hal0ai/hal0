// hal0 dashboard — Inference "engine" pane (slots-page Inference tab).
//
// The yellow-accented counterpart to the ComfyUI generation-engine pane
// (comfyui-pane.jsx). Where ComfyUI models ONE containerized generation
// engine, this pane is a summary engine-shell over the iGPU/CPU slot stack,
// implementing the P2 *card* direction from the design handoff
// (design_handoff_inference_slots/P2-inference-pane.html): ALL pane slots
// as full cards (model picker · tok/s · ttft · ctx · per-slot controls)
// and a right-aligned status line. The page-level memory + throughput hero
// now lives in the TelemetryHeader card (telemetry-header.jsx), mounted by
// SlotsView above the tabs.
//
// NPU/FLM slots are cordoned off to the NPU · FLM stack pane below — they
// live on the NPU budget, not the GTT carve-out, so they appear in neither
// this pane's cards nor its memory bar (the sec-label still counts them as
// a pointer to that pane).
//
// All data is LIVE via the typed hooks:
//   - useSlots()           → the slot rollup (non-image, non-NPU)
//   - useModels()          → the per-slot model picker (full cards)
//   - useMemoryMapModel()  → per-slot resident memory (real mem_mb) + GTT pool
//   - useSlot{Restart,Unload,Load,Swap} → real lifecycle mutations
// Absent metrics render an em-dash; the pane never fabricates a number.
//
// Per the design voice: lowercase mono labels, no emoji in the chrome,
// em-dash for any metric the backend hasn't reported.

import {
  useSlots,
  useSlotRestart,
  useSlotUnload,
  useSlotLoad,
  useSlotSwap,
  useSlotEdit,
} from '@/api/hooks/useSlots'
import { useModels } from '@/api/hooks/useModels'
import { isUpstreamModel } from '@/lib/normalizeApiModel'
import { useMemoryMapModel } from './memory-map'
import { slotIndicatorFromPhase, isSlotLive } from './slot-status.js'
import { slotModelRow } from './slots/slot-shared.js'
import { useCardReorder } from './slots/card-order.js'
// devKind — one shared, meta-aware helper (src/lib/deviceMeta.ts); replaces
// the copy this file used to carry (and the verbatim clones in slot-list.jsx
// and npu-pane.jsx).
import { devKind } from '@/lib/deviceMeta'

const { useState: useStateI } = React

// ── icons (16×16, thin-line family — ported from the design's infer-core) ──
const II = ({ d, size = 16, sw = 1.5, children, fill = 'none' }) => (
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
const IIcons = {
  slots: (
    <II>
      <rect x="2.5" y="3" width="11" height="3" rx="1" />
      <rect x="2.5" y="6.6" width="11" height="3" rx="1" />
      <rect x="2.5" y="10.2" width="11" height="3" rx="1" />
    </II>
  ),
  mem: (
    <II>
      <rect x="2" y="5" width="12" height="6" rx="1" />
      <path d="M5 5V3M8 5V3M11 5V3M5 13v-2M11 13v-2" />
    </II>
  ),
  activity: <II d="M2 8h3l2-4 2 8 2-4h3" />,
  chev: <II d="M4 6l4 4 4-4" />,
  plus: <II d="M8 3v10M3 8h10" />,
  logs: <II d="M3 3h10M3 6h10M3 9h7M3 12h5" />,
  refresh: (
    <II>
      <path d="M14 8a6 6 0 1 1-2-4.5" />
      <path d="M14 1v3.5h-3.5" />
    </II>
  ),
  stop: (
    <II>
      <rect x="4" y="4" width="8" height="8" rx="1" />
    </II>
  ),
  play: <II d="M5 3.4l8 4.6-8 4.6V3.4z" />,
  edit: <II d="M3 13l3-1 7-7-2-2-7 7-1 3z" />,
  star: <II d="M8 2.2l1.8 3.7 4 .6-2.9 2.8.7 4-3.6-1.9-3.6 1.9.7-4L2.2 6.5l4-.6z" />,
  ext: <II d="M6 3H3v10h10v-3M9 3h4v4M9 9l4-4" />,
}
const Ic = ({ name, size = 16 }) =>
  IIcons[name] ? React.cloneElement(IIcons[name], { size }) : null

const round1 = (n) => Math.round((n || 0) * 10) / 10
const toast = (msg, kind = 'info') =>
  typeof window !== 'undefined' && window.__hal0Toast && window.__hal0Toast(msg, kind)

// Utility (support) slot types — the non-conversational tier that renders as
// the compact mini-card row below the headline chat/agent cards. Placement is
// derived from the slot's capability type, never a hand-set label, so a
// mislabeled slot can't escape its tier. Anything else (llm) is a headline slot;
// image is its own pane.
const UTIL_TYPES = new Set(['embedding', 'reranking', 'tts', 'transcription'])
function isUtil(s) {
  return UTIL_TYPES.has(String(s?.type || '').toLowerCase())
}

// Phase → dot class (reuses the design's .sdot vocabulary). Derived from the
// shared slot-status classifier so the dot matches the rest of the page.
function dotCls(ind) {
  switch (ind.cls) {
    case 'serving':
      return 'serving'
    case 'stale':
      return 'ready'
    case 'warming':
      return 'warming'
    case 'error':
      return 'error'
    default:
      return 'offline'
  }
}

// ctx "used / max" in k-tokens. Em-dash when no ctx_max is configured, and
// an em-dash for the used side when the live counter hasn't reported.
const kCtx = (n) => `${Math.round(n / 1024)}k`
function ctxText(s) {
  const max = typeof s.ctx_max === 'number' && s.ctx_max > 0 ? s.ctx_max : null
  if (!max) return '—'
  const used = typeof s.metrics?.ctx === 'number' && s.metrics.ctx > 0 ? s.metrics.ctx : null
  return `${used ? kCtx(used) : '—'} / ${kCtx(max)}`
}

// a labelled block header reused across mem / throughput / slots
function SubLabel({ icon, note, children }) {
  return (
    <div className="blk-h">
      <span className="ic">
        <Ic name={icon} size={13} />
      </span>{' '}
      {children}
      {note != null && (
        <>
          <span className="grow" />
          <span className="note">{note}</span>
        </>
      )}
    </div>
  )
}

// ── card grip ───────────────────────────────────────────────────────────
// Drag handle for reordering the slot cards — a small dotted tab hanging from
// the top-centre edge of the card, in the same border/`--bg-2` chrome as the
// rest of the card. Rest state is faint; it comes up on card hover and on
// keyboard focus. Arrow keys move the card one place (the accessible path, and
// the only one on touch — native HTML5 DnD is pointer-only).
function CardGrip({ name, grip }) {
  return (
    <button
      type="button"
      className="card-grip"
      title="Drag to reorder — or use the arrow keys"
      aria-label={`Reorder ${name} — drag, or use the arrow keys`}
      data-testid={`infer-grip-${name}`}
      {...grip}
    >
      <svg width="16" height="8" viewBox="0 0 16 8" fill="currentColor" aria-hidden="true">
        {[3, 8, 13].map((cx) => (
          <React.Fragment key={cx}>
            <circle cx={cx} cy="3" r="1" />
            <circle cx={cx} cy="6" r="1" />
          </React.Fragment>
        ))}
      </svg>
    </button>
  )
}

// ── slot cards ──────────────────────────────────────────────────────────
// provider tag — a joined [ device | PROFILE ] control. The profile pill
// surfaces the slot's runtime profile (slot.profile, resolved from
// /etc/hal0/profiles.toml by the backend) and opens the slot editor.
function DevCell({ s, onProfile }) {
  const kind = devKind(s.device)
  const dchip =
    kind === 'npu' ? (
      <span className="flm-chip">FLM · npu</span>
    ) : (
      <span className={'dchip ' + kind}>
        <span className="sw" />
        {kind}
      </span>
    )
  return (
    <span className="prov">
      {dchip}
      <button
        className="profile-pill"
        title="Runtime profile — edit slot"
        onClick={onProfile}
        data-testid={`infer-profile-${s.name}`}
      >
        {s.profile || 'default'}
        <Ic name="chev" size={10} />
      </button>
    </span>
  )
}

// Backend-mismatch chip — mirrors slots.jsx SlotCard: when the slot reports
// backend_mismatch + actual_backend, render an amber chip surfacing the ACTUAL
// runtime backend. Backend identity is owned by the slot's profile, so the chip
// opens the slot editor's profile picker (via onEdit) rather than a one-click
// switch — the legacy backend-switch endpoint was removed in WS-5.
function BackendMismatch({ s, onEdit }) {
  if (!s.backend_mismatch || !s.actual_backend) return null
  const declared = s.declared_backend || s.backend || s.device
  return (
    <button
      type="button"
      className="tag-chip"
      style={{
        color: 'var(--warn)',
        borderColor: 'var(--warn-line)',
        background: 'var(--warn-soft)',
        cursor: 'pointer',
      }}
      title={`Declared ${declared} but running ${s.actual_backend} — edit the slot's profile to change its backend`}
      onClick={(e) => {
        e.stopPropagation()
        onEdit && onEdit()
      }}
      data-testid={`infer-backend-mismatch-${s.name}`}
    >
      {s.actual_backend} ≠ declared
    </button>
  )
}

// full cards get a real model picker (a styled <select> wired to useModels);
// non-LLM slots keep their static model line.
export function ModelPicker({ s, models, disabled, onSwap }) {
  if (s.type !== 'llm')
    return (
      <div className="smodel" title={s.model || ''}>
        {s.model || '—'}
      </div>
    )
  // Upstream-advertised rows can't be bound to a slot (no local file) —
  // same exclusion as slot-modals' compatibleModels().
  const opts = (Array.isArray(models) ? models : []).filter(
    (m) => m.type === 'llm' && !isUpstreamModel(m),
  )
  const cur = s.model_id || s.model || ''
  const has = opts.some((m) => m.id === cur)
  return (
    <select
      className="model-picker mono"
      value={cur}
      disabled={disabled}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => {
        const id = e.target.value
        if (id && id !== cur) onSwap(id)
      }}
      aria-label={`Model for ${s.name}`}
    >
      {cur && !has && <option value={cur}>{s.model || cur}</option>}
      {!cur && <option value="">—</option>}
      {opts.map((m) => (
        <option key={m.id} value={m.id}>
          {m.longName || m.id}
        </option>
      ))}
    </select>
  )
}

// per-slot controls — Start/Stop are mutually exclusive by running state;
// compact (collapsed) cards get the minimal set (no Logs/Edit).
export function SlotControls({
  phase,
  busy,
  compact,
  onStart,
  onStop,
  onRestart,
  onLogs,
  onEdit,
  // SC-4: promote this slot to its type's default. Rendered only when
  // `onSetDefault` is supplied AND the slot isn't already the default — a slot
  // that already holds the marker has nothing to promote.
  onSetDefault,
}) {
  const running = phase !== 'off'
  return (
    <span className="slot-ctrls" onClick={(e) => e.stopPropagation()}>
      {running ? (
        <button
          className="sctrl stop"
          title="Stop"
          onClick={onStop}
        >
          <Ic name="stop" size={13} />
        </button>
      ) : (
        <button className="sctrl start" title="Start" disabled={busy} onClick={onStart}>
          <Ic name="play" size={13} />
        </button>
      )}
      <button
        className="sctrl restart"
        title="Restart"
        disabled={busy || phase === 'transitional' || !running}
        onClick={onRestart}
      >
        <Ic name="refresh" size={13} />
      </button>
      {!compact && (
        <button className="sctrl" title="Logs" onClick={onLogs}>
          <Ic name="logs" size={13} />
        </button>
      )}
      {onSetDefault && (
        <button
          className="sctrl"
          title="Set as default for this slot type"
          disabled={busy}
          onClick={onSetDefault}
        >
          <Ic name="star" size={13} />
        </button>
      )}
      {!compact && (
        <button className="sctrl" title="Edit" onClick={onEdit}>
          <Ic name="edit" size={13} />
        </button>
      )}
    </span>
  )
}

// classify a slot into the lifecycle phase the controls key off (mirrors the
// SlotCard logic so Start/Stop/Restart match the per-slot card).
export function slotCtrlPhase(slot) {
  if (slot.container_status != null) {
    const cs = String(slot.container_status)
    const health = !!slot.container_health
    if (cs === 'starting' || cs === 'pulling' || (cs === 'running' && !health)) return 'transitional'
    if (cs === 'running' && health) return 'running'
    return 'off'
  }
  const st = slot.state
  if (st === 'warming' || st === 'starting' || st === 'pulling' || st === 'unloading')
    return 'transitional'
  if (st === 'serving' || st === 'ready') return 'running'
  return 'off'
}

// One slot card — the canonical "latest slot card" surface. Shared by the
// InferencePane slot list and the NPU·FLM stack so the two never drift.
//   modelNode — the model row (a <ModelPicker> for swappable LLM slots, or a
//               static label for fixed-model roles like FLM ASR/embed).
//   controls  — a <SlotControls> node (the caller wires the lifecycle verbs;
//               for the NPU trio these map to stack-load / modality-toggle).
//   phase     — overrides the lifecycle phase (NPU coresident roles derive it
//               from their modality toggle, not the slot's own state).
//   onEditModel / modelName — inline model-edit affordance. Omit both and the
//               model row renders exactly as before (the NPU stack does).
//   grip / dragging / dropProps — drag-to-reorder wiring (see slots/card-order).
//               Omit `grip` and the card carries no handle at all, which is how
//               every non-reorderable caller renders it.
export function SlotScard({
  s, ind, full, modelNode, controls, phase, onEdit, onEditModel, modelName,
  grip, dragging, dropProps,
}) {
  const dot = dotCls(ind)
  const ph = phase || slotCtrlPhase(s)
  // A live-ish card whose /api/slots enrichment hasn't landed yet (bare
  // /api/status union entry) — pulse the metrics area so the values reading
  // as "—" are legible as loading, not as a real zero. `_enriched === false`
  // is only ever set by reconcileEnrichment; undefined (e.g. NPU/stack cards
  // that never union with /api/status) never trips this.
  const pending = s._enriched === false && ph !== 'off'
  const memGb = typeof s.mem_mb === 'number' && s.mem_mb > 0 ? round1(s.mem_mb / 1024) : null
  const tps = typeof s.metrics?.toks === 'number' && s.metrics.toks > 0 ? s.metrics.toks : null
  const ttft = typeof s.metrics?.ttft === 'number' && s.metrics.ttft > 0 ? s.metrics.ttft : null
  return (
    <div
      className={
        'scard ' + dot + (ph === 'off' ? ' dim' : '') + (pending ? ' pending' : '') +
        (dragging ? ' dragging' : '')
      }
      data-testid={`infer-slot-${s.name}`}
      {...(dropProps || {})}
    >
      {grip}
      <div className="scard-h">
        <span className={'sdot ' + dot} title={ind.tooltip} />
        <span className="snm">{s.name}</span>
        <span className="sport">{s.port ? ':' + s.port : ''}</span>
      </div>
      <div className="scard-b">
        {/* Inline model edit sits OUTSIDE the model control, never nested in it:
            the LLM model row is a <select> and the pencil must not compete with
            that picker's own click/keyboard gesture. `stopPropagation` is
            belt-and-braces on top of the separate hit area. Disabled when the
            bound model isn't resolvable to a registry row — ModelDrawer needs
            the row, not an id, and renders nothing for null. */}
        {onEditModel ? (
          <div className="smodel-row">
            {modelNode}
            <button
              className="sctrl scard-model-edit"
              data-testid={`infer-model-edit-${s.name}`}
              disabled={!modelName}
              title={
                modelName
                  ? `Edit model ${modelName} — launch flags, chat template, caps`
                  : 'No model bound — nothing to edit'
              }
              aria-label={modelName ? `Edit model ${modelName}` : 'Edit model'}
              onClick={(e) => {
                e.stopPropagation()
                onEditModel()
              }}
            >
              <Ic name="edit" size={13} />
            </button>
          </div>
        ) : (
          modelNode
        )}
        {full && (
          <div className="scard-meta">
            <div className="m">
              <div className="l">tok/s</div>
              <div className={'v' + (tps ? ' acc' : ' muted')}>{tps || '—'}</div>
            </div>
            <div className="m">
              <div className="l">ttft</div>
              <div className={'v' + (ttft ? '' : ' muted')}>{ttft ? ttft + 'ms' : '—'}</div>
            </div>
            <div className="m">
              <div className="l">ctx</div>
              <div className={'v' + (s.ctx_max ? '' : ' muted')} style={{ fontSize: 12 }}>
                {ctxText(s)}
              </div>
            </div>
          </div>
        )}
        <div className={'scard-foot' + (full ? '' : ' bare')}>
          <DevCell s={s} onProfile={onEdit} />
          <BackendMismatch s={s} onEdit={onEdit} />
          {full && memGb != null && <span className="tag-chip">{memGb} GB</span>}
          <span className="grow" />
          {controls}
        </div>
      </div>
    </div>
  )
}

function SlotCards({ rows, full, models, busyName, handlers, loading, modelRows }) {
  // Operator-arranged order + drag wiring. Called before the early returns so
  // the hook order is stable across the loading/empty/populated renders.
  const reorder = useCardReorder('inference.chat', rows)
  if (!rows.length) {
    if (loading)
      return (
        <div className={'scards ' + (full ? 'full' : 'compact')}>
          {[0, 1].map((i) => (
            <div key={i} className="slot-skeleton" />
          ))}
        </div>
      )
    return <div className="scards-empty">no inference slots — create one to start</div>
  }
  return (
    <div className={'scards ' + (full ? 'full' : 'compact')}>
      {reorder.rows.map(({ s, ind }) => {
        const busy = busyName === s.name
        const modelNode = full ? (
          <ModelPicker
            s={s}
            models={models}
            disabled={busy}
            onSwap={(id) => handlers.onSwap(s, id)}
          />
        ) : (
          <div className="smodel" title={s.model || ''}>
            {s.model || '—'}
          </div>
        )
        const controls = (
          <SlotControls
            phase={slotCtrlPhase(s)}
            busy={busy}
            compact={!full}
            onStart={() => handlers.onStart(s)}
            onStop={() => handlers.onStop(s)}
            onRestart={() => handlers.onRestart(s)}
            onLogs={() => handlers.onLogs(s)}
            onEdit={() => handlers.onEdit(s)}
            onSetDefault={
              s.default === true ? undefined : () => handlers.onSetDefault(s)
            }
          />
        )
        // The bound registry row, resolved through the shared helper the slot
        // drawer uses. null = unbound or not in the list yet → the pencil is
        // disabled rather than opening an empty editor.
        const modelRow = modelRows ? modelRows(s) : null
        return (
          <SlotScard
            key={s.name}
            s={s}
            ind={ind}
            full={full}
            modelNode={modelNode}
            controls={controls}
            onEdit={() => handlers.onEdit(s)}
            onEditModel={
              handlers.onEditModel ? () => handlers.onEditModel(s) : undefined
            }
            modelName={modelRow ? modelRow.longName || modelRow.name || modelRow.id : ''}
            grip={<CardGrip name={s.name} grip={reorder.gripProps(s.name)} />}
            dragging={reorder.dragName === s.name}
            dropProps={reorder.dropProps(s.name)}
          />
        )
      })}
    </div>
  )
}

// Utility tier — compact mini cards for the support slots (embed / rerank /
// voice). No meta row, no model picker: just the dot + name + port header and
// a minimal model + Start/Stop/Restart control cluster (SlotControls compact).
function MiniCard({ s, ind, busy, handlers, grip, dragging, dropProps }) {
  const dot = dotCls(ind)
  const ph = slotCtrlPhase(s)
  return (
    <div
      className={'mcard ' + dot + (dragging ? ' dragging' : '')}
      data-testid={`infer-slot-${s.name}`}
      {...(dropProps || {})}
    >
      {grip}
      <div className="mcard-h">
        <span className={'sdot ' + dot} title={ind.tooltip} />
        <span className="snm">{s.name}</span>
        <span className="sport">{s.port ? ':' + s.port : ''}</span>
      </div>
      <div className="mcard-b">
        <span className="smodel" title={s.model || ''}>
          {s.model || '—'}
        </span>
        <SlotControls
          phase={ph}
          busy={busy}
          onStart={() => handlers.onStart(s)}
          onStop={() => handlers.onStop(s)}
          onRestart={() => handlers.onRestart(s)}
          onLogs={() => handlers.onLogs(s)}
          onEdit={() => handlers.onEdit(s)}
          onSetDefault={
            s.default === true ? undefined : () => handlers.onSetDefault(s)
          }
        />
      </div>
    </div>
  )
}

function MiniCards({ rows, busyName, handlers }) {
  // Utility tier arranges independently of the headline tier — its own scope
  // key. Hook first: `rows` can be empty on the very first render.
  const reorder = useCardReorder('inference.util', rows)
  if (!rows.length) return null
  return (
    <div className="util-mini">
      {reorder.rows.map(({ s, ind }) => (
        <MiniCard
          key={s.name}
          s={s}
          ind={ind}
          busy={busyName === s.name}
          handlers={handlers}
          grip={<CardGrip name={s.name} grip={reorder.gripProps(s.name)} />}
          dragging={reorder.dragName === s.name}
          dropProps={reorder.dropProps(s.name)}
        />
      ))}
    </div>
  )
}

// The page-level hero band (iGPU GTT memory map + combined-throughput tile)
// was replaced by the TelemetryHeader card (telemetry-header.jsx) at the top
// of SlotsView — its MemGtt / GpuGauge / TpTile tiles went with it.

export function InferencePane() {
  const slotsQuery = useSlots()
  const modelsQuery = useModels()
  const mm = useMemoryMapModel()
  const restartMut = useSlotRestart()
  const unloadMut = useSlotUnload()
  const loadMut = useSlotLoad()
  const swapMut = useSlotSwap()
  // SC-4 "Set as default" row action — the same PUT /config the drawer's
  // Pinned toggle uses. check_default_uniqueness REFUSES a second
  // default=true rather than demoting the incumbent, so `handlers.onSetDefault`
  // below does the demote-then-promote itself (two PUTs).
  const editMut = useSlotEdit()
  const [busyName, setBusyName] = useStateI(null)
  // Inline model edit — the ModelDrawer is mounted ONCE here (the pane owns the
  // cards) and driven by the picked registry row, matching how models.jsx
  // drives it. Row, not id: ModelDrawer renders nothing for a null model.
  // There is no UI/overlay store in this app; drawer open-state is local
  // useState in the nearest view owner.
  const [modelEditRow, setModelEditRow] = useStateI(null)

  // The Inference rollup is the iGPU/CPU slot stack. Image generation is its
  // own pane (ComfyuiPane); NPU/FLM slots are cordoned off to the NPU · FLM
  // stack pane below — they appear here only as the sec-label FLM count.
  const allSlots = slotsQuery.data || []
  // Cold start: no cached data yet. Render neutral skeletons instead of the
  // "no inference slots — create one" empty state, which would otherwise flash
  // in for one poll before the first payload lands and get replaced.
  const loading = slotsQuery.isLoading && !slotsQuery.data
  const nonImg = allSlots.filter((s) => String(s?.type) !== 'image')
  const slots = nonImg.filter((s) => devKind(s.device) !== 'npu')
  const npuN = nonImg.length - slots.length

  const rows = slots.map((s) => ({ s, ind: slotIndicatorFromPhase(s) }))
  const servingN = rows.filter((r) => r.ind.cls === 'serving').length
  const loadedN = rows.filter((r) => isSlotLive(r.s)).length

  // Tier split — headline = the conversational LLM slots; utility = the support
  // slots (embed / rerank / tts / transcription). Keyed off slot.type so the
  // split can't be thrown off by a mislabeled group. This pane is always
  // expanded (no accordion), so the utility tier shows ALL its slots; the live
  // count drives the SubLabel note.
  const headlineRows = rows.filter((r) => !isUtil(r.s))
  const utilRows = rows.filter((r) => isUtil(r.s))

  const gpuN = slots.filter((s) => {
    const k = devKind(s.device)
    return k === 'rocm' || k === 'vulkan'
  }).length

  // GTT headroom for the slots status line (the memory map's frame).
  const gttCapGb = mm.pool?.totalGb || 0
  const gttFreeGb = Math.max(0, Math.round(gttCapGb - (mm.self?.gttUsedGb || 0)))

  // Fire-and-forget lifecycle action (mirrors SlotsView/PR #781): fire the
  // mutation, toast immediately, and let the slots poll reflect the phase.
  // `busy` marks the in-flight action to gate Start against a double-trigger;
  // Stop is never gated (see SlotControls) so a slow load stays cancelable.
  const run = (name, mut, args, okMsg) => {
    setBusyName(name)
    mut.mutate(args, {
      onError: (err) =>
        toast(err?.message ? `${name}: ${err.message}` : `${name}: action failed`, 'warn'),
      onSettled: () => setBusyName(null),
    })
    toast(okMsg, 'info')
  }

  const handlers = {
    onStart: (s) => run(s.name, loadMut, s.name, `Starting ${s.name}…`),
    onStop: (s) => run(s.name, unloadMut, s.name, `Stopping ${s.name}…`),
    onRestart: (s) => run(s.name, restartMut, s.name, `Restarting ${s.name}…`),
    onSwap: (s, model_id) =>
      run(s.name, swapMut, { name: s.name, model_id }, `Swapping ${s.name}…`),
    onEdit: (s) => {
      window.location.hash = '#slots/' + s.name
    },
    // SC-4 allows exactly one default=true slot per type and REFUSES a write
    // that would land a second one — it does not silently demote. There is no
    // atomic promote endpoint, so re-pointing the default is demote-then-
    // promote. If the promote fails we restore the incumbent rather than
    // leaving the type with no default at all.
    onSetDefault: async (s) => {
      const prev = (allSlots || []).find(
        (p) => p?.name && p.name !== s.name && p?.type === s.type && p?.default === true,
      )
      try {
        if (prev) await editMut.mutateAsync({ name: prev.name, body: { default: false } })
        try {
          await editMut.mutateAsync({ name: s.name, body: { default: true } })
        } catch (err) {
          if (prev) {
            await editMut
              .mutateAsync({ name: prev.name, body: { default: true } })
              .catch(() => {})
          }
          throw err
        }
        toast(`${s.name} is now the default ${s.type || 'slot'}`, 'ok')
      } catch (err) {
        toast(
          err?.message ? `${s.name}: ${err.message}` : `${s.name}: could not set default`,
          'warn',
        )
      }
    },
    onLogs: (s) => {
      window.dispatchEvent(new CustomEvent('hal0:slot-logs', { detail: { name: s.name } }))
    },
    // Open the model editor for this slot's bound model — no close → Models
    // page → find the row → reopen round-trip.
    onEditModel: (s) => {
      const row = slotModelRow(s, modelsQuery.data)
      if (row) setModelEditRow(row)
    },
  }

  // Per-slot bound-row lookup handed to the cards so the pencil can be disabled
  // (and labelled) without every card re-scanning the list itself.
  const modelRowFor = (s) => slotModelRow(s, modelsQuery.data)

  const newSlot = () => window.dispatchEvent(new CustomEvent('hal0:create-slot'))
  const openLogs = () => {
    window.location.hash = '#logs'
  }

  return (
    <>
    <div className="infer-pane">
      <div className="proto">
        {/* Single NPU-card-style header row — the old two-row stack (sec-label
            above + "Inference / inference engine · podman" title block + green
            loaded epill) collapsed into one heading. */}
        <div className={'engine' + (loadedN > 0 ? ' active' : '')}>
          <div className="engine-h">
            <span className="engine-glyph">
              <Ic name="slots" size={16} />
            </span>
            <span className="sec-label">
              <b>Inference Engine</b>
              <span className="dim">·</span>
              <span className="meta">slots</span>
              <span className="mono" style={{ color: 'var(--comfy)' }}>
                {gpuN} iGPU
              </span>
              {npuN > 0 && (
                <>
                  <span className="dim">·</span>
                  <span className="mono" style={{ color: 'var(--dev-npu)' }}>
                    {npuN} FLM
                  </span>
                </>
              )}
            </span>
            <span className="grow" style={{ flex: 1 }} />
            <span className="eh-right">
              <button className="rbtn" onClick={newSlot} title="Create a new slot">
                <Ic name="plus" size={13} /> Slot
              </button>
              <button className="rbtn ghost-comfy" onClick={openLogs} title="Open the logs view">
                <Ic name="logs" size={13} /> Logs ↗
              </button>
            </span>
          </div>

          {/* All slots as full cards — always visible (freed from the old
              collapse/expand accordion). The memory + throughput hero now lives
              in the page-level TelemetryHeader card above the tabs. */}
          <div className="engine-b" data-testid="infer-slots">
            <div>
              <SubLabel
                icon="slots"
                note={`${headlineRows.length} · chat · agent`}
              >
                chat · agent
              </SubLabel>
              <SlotCards
                rows={headlineRows}
                full
                loading={loading}
                models={modelsQuery.data}
                modelRows={modelRowFor}
                busyName={busyName}
                handlers={handlers}
              />
            </div>
            {utilRows.length > 0 && (
              <div data-testid="infer-util">
                <SubLabel
                  icon="mem"
                  note={`${utilRows.length} support slots`}
                >
                  utility · embed · rerank · voice
                </SubLabel>
                <MiniCards
                  rows={utilRows}
                  busyName={busyName}
                  handlers={handlers}
                />
              </div>
            )}
            <div className="body-status">
              {servingN} serving
              {gttCapGb > 0 ? ` · ${gttFreeGb} GB free` : ''}
            </div>
          </div>

          {/* footer — engine identity (caret expander removed) */}
          <div className="engine-foot">
            <div className="foot-id">
              <span className="k">runtime</span>
              <span className="v comfy">hal0</span>
              <span className="sep">·</span>
              <span className="k">backend</span>
              <span className="v">podman</span>
              <span className="sep">·</span>
              <span className="k">slots</span>
              <span className="v">{slots.length}</span>
              <span className="sep">·</span>
              <span className="k">gateway</span>
              <span className="v comfy">:8080</span>
            </div>
          </div>
        </div>
      </div>
    </div>
    {/* Inline model editor for the card pencil — ONE instance for the whole
        pane, driven by the picked row. Not docked: nothing is stacked beneath
        it here, so it takes the normal flush-right position and its own dim
        scrim (contrast the slot edit drawer, which docks it — slot-modals.jsx).
        Rendered as a sibling of .infer-pane so its `position: fixed` can't be
        captured by a transformed ancestor. */}
    <ModelDrawer
      open={!!modelEditRow}
      onClose={() => setModelEditRow(null)}
      model={modelEditRow}
    />
    </>
  )
}

Object.assign(window, { InferencePane })
