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
// Serving metrics (tok/s, ttft) are sticky per slot+model: a stopped slot
// keeps its last live reading (plain, not amber) rather than clearing.
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
import { useModelsFeasibility } from '@/api/hooks/useModelsFeasibility'
import { useProfiles } from '@/api/hooks/useProfiles'
import { useSystemInfo } from '@/api/hooks/useRuntimes'
import { hostHwFlags, runnerOptions } from './hw-cascade.js'
import { profileApplyPreview } from './slot-modals.jsx'
import { runtimeChips } from './profiles.jsx'
import { ConfirmDialog } from './primitives.jsx'
import { isUpstreamModel } from '@/lib/normalizeApiModel'
import { useMemoryMapModel } from './memory-map'
import { slotIndicatorFromPhase, isSlotLive, imageStatusChip } from './slot-status.js'
import { SlotBreakerChip } from './breaker-chip.jsx'
import { slotModelRow } from './slots/slot-shared.js'
import { useCardReorder } from './slots/card-order.js'
import { RichSelect } from './rich-select.jsx'
import { feasibilityHint } from './feasibility-copy'
import { slotsUsingModel } from './model-usage.js'
// devKind — one shared, meta-aware helper (src/lib/deviceMeta.ts); replaces
// the copy this file used to carry (and the verbatim clones in slot-list.jsx
// and npu-pane.jsx).
import { devKind } from '@/lib/deviceMeta'

const { useState: useStateI, useRef: useRefI } = React

// Last live serving metrics per slot+model, so a paused/stopped slot keeps
// showing its most recent real reading instead of collapsing to an em-dash.
// Module-level on purpose: survives card unmount/remount within the session;
// a page reload starts clean.
const lastServingMetrics = new Map()

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
// The synthetic option id for the picker's last row. Not a profile name —
// profile names match ^[a-z0-9][a-z0-9_-]{0,31}$ (primitives.jsx NAME_RE), so
// the double underscores can never collide with a real one.
const PROFILE_EDIT_ROW = '__edit_slot__'

// Rows for the card's profile picker: name + one solid chip per runtime lane
// (profiles.jsx's runtimeChips — the SAME renderer the Profiles view, the slot
// drawer's Profile select and the apply preview use, so a lane can't read
// differently here) + the profile's intent line.
//
// Filtered exactly like the drawer's picker: `supported_slot_types` first
// (slot-modals.jsx:1496-1500, mirroring the backend's profile_fits_slot —
// model_fit.py:80). The drawer additionally splits device_class/backend
// matches into `fit` vs a cross-device group; the card lists the type-fitting
// set flat and lets the apply preview below announce a lane move, per
// warn-never-block.
function profileCardOptions({ s, profiles, backends }) {
  const cur = s.profile || ''
  const fit = (profiles || []).filter(
    (p) => !Array.isArray(p.supported_slot_types) || p.supported_slot_types.includes(s.type),
  )
  const opts = []
  // A slot with no profile keeps today's pill word as its own row, so the
  // trigger has something to render and "no profile" stays a listed state
  // rather than a placeholder. (Drawer twin: `none: !slot.profile`.)
  if (!cur) opts.push({ id: '', row: 'default', desc: 'no profile — the slot launches on its device default' })
  // A persisted profile that no longer resolves (deleted/renamed, or filtered
  // out by type) stays listed as its own plain row — provenance is never
  // dropped just because the catalogue moved on.
  if (cur && !fit.some((p) => p.name === cur)) opts.push({ id: cur, row: cur })
  for (const p of fit) {
    opts.push({
      id: p.name,
      row: (
        <span className="prof-row">
          <span className="prof-row-name">{p.name}</span>
          <span className="pf-be-row">
            {runtimeChips(p, backends).map((c) => (
              <span key={c.key} className="pf-be mono" style={c.hue ? { '--bk': c.hue } : null}>
                {c.label}
              </span>
            ))}
          </span>
        </span>
      ),
      desc: p.intent || undefined,
    })
  }
  // Today's pill gesture — "the pill opens the slot editor" — stays reachable
  // as the last row rather than being replaced by the dropdown.
  opts.push({
    id: PROFILE_EDIT_ROW,
    row: '✎ Edit slot…',
    desc: 'full editor — flags, runtime, lifecycle',
  })
  return opts
}

// provider tag — a joined [ device | PROFILE ] control. The device chip is
// static; the profile side is a real picker over /api/profiles, and a pick
// routes through a consequence confirm (the drawer's own `profileApplyPreview`
// lines) before anything is written. Cancel writes nothing.
//
// ── THE APPLY PATH, VERIFIED FROM SOURCE (mockup callout F) ────────────────
// The slot drawer persists a profile pick as exactly TWO plain mutations,
// with no drawer-only state between them:
//   1. PUT /api/slots/{name}/config {profile: name|null} — assembled at
//      slot-modals.jsx:1120 and fired at :1130 through useSlotEdit
//      (api/hooks/useSlots.ts:617). The route is a PURE config write with no
//      lifecycle side effect (api/routes/slots.py:1386, note at :1441), and
//      the server re-derives the slot's `device` from the new profile itself
//      (slots/config_write.py:444 → _reconcile_device_profile(merged,
//      {"profile"}) at :91) — the drawer sends no device of its own.
//   2. POST /api/slots/{name}/restart, fired in the BACKGROUND (never
//      awaited) because `changes.profile` is part of `hwChanged`
//      (slot-modals.jsx:1097-1103, restart at :1143) via useSlotRestart.
// Both are reachable from any component, so this is branch (a) of the plan's
// riddle: Apply performs the write here, one gesture, no half-applied state.
// The drawer's extra Save-time gates guard fields this control cannot touch —
// a device+profile pair edited together (slot-modals.jsx:1075) and a bound
// model stranded by a device switch the operator drove (:1242) — and the one
// consequence that does reach a card pick, a lane move, is announced by the
// preview's `lane` line instead of blocking (§4 warn-never-block).
export function DevCell({ s, onProfile, modelFlags }) {
  const kind = devKind(s.device)
  const profilesQuery = useProfiles()
  const systemInfoQuery = useSystemInfo()
  const editMut = useSlotEdit()
  const restartMut = useSlotRestart()
  // The picked-but-not-yet-applied profile name; null = no confirm open.
  // Nothing is written while this holds a value — it IS the "before the
  // confirm" state.
  const [pending, setPending] = useStateI(null)
  const dchip =
    kind === 'npu' ? (
      <span className="flm-chip">FLM · npu</span>
    ) : (
      <span className={'dchip ' + kind}>
        <span className="sw" />
        {kind}
      </span>
    )
  const cur = s.profile || ''
  const profiles = Array.isArray(profilesQuery.data) ? profilesQuery.data : []
  const backends = systemInfoQuery.data?.backends ?? {}
  const pendingRow = pending ? profiles.find((p) => p.name === pending) || null : null
  // Same cascade the drawer's preview reads (slot-modals.jsx:1466-1472), fed
  // the slot's PERSISTED device — the card has no pending-device state of its
  // own, so there is nothing to preview but the write it is about to make.
  const preview = pendingRow
    ? profileApplyPreview({
        profile: pendingRow,
        backends,
        options: runnerOptions({
          backends,
          device: s.device || '',
          slotType: s.type,
          hw: hostHwFlags(systemInfoQuery.data?.hardware),
        }).options,
        baselineRunner: s.binary || '',
        currentDevice: s.device || '',
        modelFlags: modelFlags || '',
        currentProfileFlags: profiles.find((p) => p.name === cur)?.flags || '',
      })
    : null
  const onApply = () => {
    const next = pending
    if (next == null) return
    setPending(null)
    editMut.mutate(
      { name: s.name, body: { profile: next || null } },
      {
        onError: (err) =>
          toast(`${s.name}: profile apply failed — ${err?.message || 'see logs'}`, 'warn'),
        onSuccess: () => {
          restartMut.mutate(s.name, {
            onError: (err) =>
              toast(`${s.name}: restart failed — ${err?.message || 'see logs'}`, 'warn'),
          })
          toast(`${s.name} → profile "${next}" — restarting in the background`, 'info')
        },
      },
    )
  }
  return (
    <React.Fragment>
      <span className="prov" onClick={(e) => e.stopPropagation()}>
        {dchip}
        <RichSelect
          className="profile-pill"
          value={cur}
          options={profileCardOptions({ s, profiles, backends })}
          aria-label={`Runtime profile for ${s.name}`}
          data-testid={`infer-profile-${s.name}`}
          onChange={(id) => {
            if (id === PROFILE_EDIT_ROW) {
              onProfile()
              return
            }
            if (id !== cur) setPending(id)
          }}
        />
      </span>
      <ConfirmDialog
        open={pending != null}
        title={`Apply profile "${pending}"`}
        confirmLabel="Apply profile"
        cancelLabel="Cancel"
        footerNote="Nothing is written until you apply."
        onCancel={() => setPending(null)}
        onConfirm={onApply}
        message={
          <div className="hint sl-apply" data-testid="infer-profile-preview">
            <div>
              Applying <b>{pending}</b> to this slot:
            </div>
            {preview && (
              <React.Fragment>
                <div className="sl-apply-line">
                  · runtime →{' '}
                  {preview.runtime.unchanged ? (
                    <React.Fragment>
                      <b>unchanged</b>
                      {preview.runtime.title ? (
                        <React.Fragment>
                          {' '}
                          — already on <b>{preview.runtime.title}</b>
                        </React.Fragment>
                      ) : (
                        ' — this profile pins none'
                      )}
                    </React.Fragment>
                  ) : (
                    <b>{preview.runtime.title}</b>
                  )}
                  <span className="pf-be-row">
                    {runtimeChips(pendingRow, backends).map((c) => (
                      <span
                        key={c.key}
                        className="pf-be mono"
                        style={c.hue ? { '--bk': c.hue } : null}
                      >
                        {c.label}
                      </span>
                    ))}
                  </span>
                </div>
                {preview.lane && (
                  <div className="sl-apply-line">
                    · lane →{' '}
                    {preview.lane.unchanged ? (
                      <React.Fragment>
                        <b>unchanged</b> ({preview.lane.from})
                      </React.Fragment>
                    ) : (
                      <React.Fragment>
                        {preview.lane.from} → <b>{preview.lane.to}</b>, this slot leaves the{' '}
                        {preview.lane.from} lane
                      </React.Fragment>
                    )}
                  </div>
                )}
                {preview.flags > 0 && (
                  <div className="sl-apply-line">
                    · flags → profile tune{' '}
                    <b>
                      replaces {preview.flags} flag{preview.flags === 1 ? '' : 's'}
                    </b>{' '}
                    on this slot
                  </div>
                )}
                <div className="sl-apply-line">
                  · slot <b>restarts</b> to apply
                </div>
              </React.Fragment>
            )}
          </div>
        }
      />
    </React.Fragment>
  )
}

// Indeterminate container-image chip (#1939). Renders ONLY for
// `image_status: "unknown"` — the backend saying it could not read the
// container image store (rootful hal0-podman-ro seam rc 66, missing sudoers
// grant, probe timeout), which is a different claim from "missing" (podman
// was asked and said no).
//
// Neutral and dashed on purpose: this card's colour vocabulary is RED =
// error, AMBER = transitional/degraded, GREY = not loaded. An unreadable
// image store is none of those, and painting it as one would restate in CSS
// exactly the confident-lie the tri-state was added to end. The classifier
// itself lives in slot-status.js so this component and the (retired) grid
// card cannot drift.
export function SlotImageUnknownChip({ s }) {
  const chip = imageStatusChip(s)
  if (!chip) return null
  return (
    <span
      className={chip.cls}
      data-testid="slot-image-unknown"
      title={chip.tooltip}
      style={{
        color: 'var(--fg-3)',
        borderStyle: 'dashed',
        borderColor: 'var(--fg-3)',
      }}
    >
      {chip.label}
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

// ── card model picker rich rows (card-dropdowns Task 1) ────────────────────
// The one non-"chat" capability worth calling out as a modality tag on the
// row's name line (mockup panel 01 callout C) — plain chat/tool-calling/coding
// models get no modality tag. "asr" is the display label for either
// capability spelling the backend may send (transcription | asr).
const MODEL_MODALITY_CAPS = ['vision', 'embed', 'rerank', 'asr', 'tts', 'image']
function modelModalityTag(m) {
  const caps = Array.isArray(m?.capabilities) ? m.capabilities : []
  if (caps.includes('transcription') || caps.includes('asr')) return 'asr'
  return MODEL_MODALITY_CAPS.find((c) => c !== 'asr' && caps.includes(c)) || null
}

// Size in whole-tenth GB from `size_bytes`, matching model-drawer's facts-band
// rounding (Number(size_bytes) / 1024**3, one decimal) — one authority for
// "how big is this model" so the two surfaces can't disagree. null when the
// row carries no size at all (never fabricate a 0.0 GB).
function modelSizeGb(m) {
  const b = Number(m?.size_bytes)
  return b > 0 ? (b / 1024 ** 3).toFixed(1) : null
}

// Right-aligned GTT fit chip for a `/api/models/feasibility` result row — tone
// comes from the shared `feasibilityHint` mapper (one source of truth for
// verdict→tone), so this can't drift from the drawer's own hint copy. An
// absent row (never probed, or a verdict of "unknown") renders no chip at
// all — warn-never-block means a missing signal must not read as an error.
function modelFitChip(row) {
  if (!row) return null
  const hint = feasibilityHint(row)
  if (!hint.tone) return null
  const gb = Math.round((row.needed_mb ?? 0) / 1024)
  if (hint.tone === 'ok') return <span className="chip ok">● fits · ~{gb} GB</span>
  if (hint.tone === 'warn') return <span className="chip warn">◐ tight · ~{gb} GB</span>
  return <span className="chip err">○ won't fit · ~{gb} GB</span>
}

// Build the card model picker's RichSelect options from the already-filtered
// llm/local candidate list. `usedBy` excludes THIS slot from its count/names —
// binding count is only interesting for OTHER slots — but marks the row
// sensibly when it IS this slot's current model ("used by this slot" rather
// than "used by 0 slots", which would misreport a real binding as unbound).
function modelPickerOptions({ s, opts, cur, has, allSlots, verdictFor }) {
  const list = []
  if (cur && !has) {
    list.push({ id: cur, row: s.model || cur })
  }
  if (!cur) {
    list.push({ id: '', row: '—' })
  }
  for (const m of opts) {
    const quant = m.quant || null
    const modTag = modelModalityTag(m)
    const usedBy = slotsUsingModel(allSlots, m.id).filter((u) => u.name !== s.name)
    const isCurrent = m.id === cur
    const names = usedBy.map((u) => u.name).join(', ')
    const usedByText = isCurrent
      ? usedBy.length > 0
        ? `used by this slot + ${usedBy.length} other${usedBy.length === 1 ? '' : 's'} (${names})`
        : 'used by this slot'
      : usedBy.length > 0
        ? `used by ${usedBy.length} slot${usedBy.length === 1 ? '' : 's'} (${names})`
        : 'used by 0 slots'
    const sizeGb = modelSizeGb(m)
    const descBits = []
    if (sizeGb) descBits.push(`${sizeGb} GB`)
    descBits.push(usedByText)
    if (m.provider_effective && m.provider_effective !== 'llama-server') {
      descBits.push(m.provider_effective)
    }
    list.push({
      id: m.id,
      row: (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {m.longName || m.id}
          </span>
          {quant && <span className="chip amber">{quant}</span>}
          {modTag && <span className="chip info">{modTag}</span>}
        </span>
      ),
      right: verdictFor ? verdictFor(m.id) : null,
      desc: descBits.join(' · '),
    })
  }
  return list
}

// full cards get a real model picker (RichSelect, wired to useModels + the
// GTT feasibility probe); non-LLM slots keep their static model line.
export function ModelPicker({ s, models, allSlots, disabled, onSwap }) {
  const feasibility = useModelsFeasibility()
  // Fires the batch probe on the FIRST dropdown open only (mockup callout A)
  // — the slot's ctx_max is fixed for the life of this card, so a second open
  // has nothing new to learn and would just re-ask the same question.
  const firedRef = useRefI(false)
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
  const ctx = typeof s.ctx_max === 'number' && s.ctx_max > 0 ? s.ctx_max : undefined
  const verdictFor = (id) => {
    const row = (feasibility.data?.results || []).find((r) => r.model_id === id)
    return modelFitChip(row)
  }
  const options = modelPickerOptions({ s, opts, cur, has, allSlots, verdictFor })
  return (
    <span className="model-picker-cell" onClick={(e) => e.stopPropagation()}>
      <RichSelect
        className="model-picker"
        value={cur}
        options={options}
        disabled={disabled}
        aria-label={`Model for ${s.name}`}
        data-testid={`infer-model-${s.name}`}
        onOpenChange={(open) => {
          if (!open || firedRef.current) return
          const ids = opts.map((m) => m.id)
          if (ids.length === 0) return
          firedRef.current = true
          feasibility.mutate({
            models: ids.map((id) => ({ model_id: id, ...(ctx ? { ctx } : {}) })),
          })
        }}
        onChange={(id) => {
          if (id && id !== cur) onSwap(id)
        }}
      />
    </span>
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
//   modelFlags — the bound model's stamped tune (registry row
//               `defaults.extra_args`), read only by the profile picker's apply
//               preview to count the flags a profile's tune would replace.
//               Absent = the preview counts against an empty base tune.
//   grip / dragging / dropProps — drag-to-reorder wiring (see slots/card-order).
//               Omit `grip` and the card carries no handle at all, which is how
//               every non-reorderable caller renders it.
export function SlotScard({
  s, ind, full, modelNode, controls, phase, onEdit, onEditModel, modelName, modelFlags,
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
  // Serving metrics are sticky: when serving stops the API zeroes them, but
  // the card keeps the last live reading (keyed per slot+model so a model
  // swap never inherits the previous model's numbers). Only a live reading
  // gets the amber .acc emphasis.
  const tpsLive = typeof s.metrics?.toks === 'number' && s.metrics.toks > 0 ? s.metrics.toks : null
  const ttftLive = typeof s.metrics?.ttft === 'number' && s.metrics.ttft > 0 ? s.metrics.ttft : null
  const metricKey = s.name + '::' + (s.model || '')
  if (tpsLive != null || ttftLive != null) {
    const prev = lastServingMetrics.get(metricKey) || {}
    lastServingMetrics.set(metricKey, {
      tps: tpsLive ?? prev.tps,
      ttft: ttftLive ?? prev.ttft,
    })
  }
  const held = lastServingMetrics.get(metricKey) || {}
  const tps = tpsLive ?? held.tps ?? null
  const ttft = ttftLive ?? held.ttft ?? null
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
        {/* Device + profile pill gets its OWN row, directly under the model
            control, instead of sharing the bottom action bar. It's a long,
            variable-length control (custom profile names can run well past
            "vulkan"/"rocm") and packing it into .scard-foot alongside the
            image/mem chips and the lifecycle buttons was squeezing the
            buttons onto a cramped wrapped line on any card with a longer
            profile name or an image-unknown chip present. Giving it a row of
            its own means .scard-foot only ever holds short, fixed-width
            chips + controls, so the controls stop competing for space. */}
        <div className="scard-profile-row">
          <DevCell s={s} onProfile={onEdit} modelFlags={modelFlags} />
        </div>
        {full && (
          <div className="scard-meta">
            <div className="m">
              <div className="l">tok/s</div>
              <div className={'v' + (tpsLive ? ' acc' : tps ? '' : ' muted')}>{tps || '—'}</div>
            </div>
            <div className="m">
              <div className="l">ttft</div>
              <div className={'v' + (ttft ? '' : ' muted')}>{ttft ? ttft + 'ms' : '—'}</div>
            </div>
            <div className="m">
              <div className="l">mem</div>
              <div className={'v' + (memGb != null ? '' : ' muted')} style={{ fontSize: 12 }}>
                {memGb != null ? memGb + ' GB' : '—'}
              </div>
            </div>
          </div>
        )}
        <div className={'scard-foot' + (full ? '' : ' bare')}>
          {/* #2038: breaker first — when the slot is deliberately refusing
              loads, that is the most actionable thing on the card. */}
          <SlotBreakerChip s={s} />
          <BackendMismatch s={s} onEdit={onEdit} />
          <SlotImageUnknownChip s={s} />
          {full && typeof s.ctx_max === 'number' && s.ctx_max > 0 && (
            <span className="tag-chip" title={`ctx used / max · ${ctxText(s)}`}>
              {kCtx(s.ctx_max)} ctx
            </span>
          )}
          <span className="grow" />
          {controls}
        </div>
      </div>
    </div>
  )
}

function SlotCards({ rows, full, models, allSlots, busyName, handlers, loading, modelRows }) {
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
            allSlots={allSlots}
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
            modelFlags={modelRow?.defaults?.extra_args || ''}
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
        {/* #1939 follow-up: the utility tier runs the same container images
            as the headline tier, so it can hit the same unreadable image
            store. Rendering the chip only on SlotScard made the seam failure
            invisible for every embed / rerank / stt / tts slot — the tiers
            differ in density, not in what they are allowed to hide. */}
        <SlotImageUnknownChip s={s} />
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

  // Free-memory headroom for the slots status line — MUST share the memory
  // ruler's basis (telemetry-header.jsx ThRuler) so the two numbers on the
  // same screen agree. `mm.self.modelUsedGb` is the reconciled per-slot sum
  // (real mem_mb when the backend reports it) the ruler's "free" is derived
  // from; `mm.self.gttUsedGb` is a raw host-wide GTT stat that (a) is zeroed
  // outright on a box with no rocm-smi and (b) counts non-hal0 GPU users the
  // ruler never counts — either way it disagreed with the ruler (#1900).
  const gttCapGb = mm.pool?.totalGb || 0
  const gttFreeGb = Math.max(0, Math.round(gttCapGb - (mm.self?.modelUsedGb || 0)))

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
                allSlots={allSlots}
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
