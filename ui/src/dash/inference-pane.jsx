// hal0 dashboard — Inference "engine" pane (slots-page Inference tab).
//
// The yellow-accented counterpart to the ComfyUI generation-engine pane
// (comfyui-pane.jsx). Where ComfyUI models ONE containerized generation
// engine, this pane is a summary engine-shell over the iGPU/CPU slot stack,
// implementing the P2 *card* direction from the design handoff
// (design_handoff_inference_slots/P2-inference-pane.html):
//   · collapsed = compact hero — iGPU GTT memory map + combined-throughput
//     tile + the active (serving/ready) slots as compact cards
//   · expanded  = the same hero pinned on top, then ALL pane slots as full
//     cards (model picker · tok/s · ttft · ctx · per-slot controls) and a
//     right-aligned status line
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
// Throughput history is a client ring buffer (the ThroughputCard pattern) —
// the backend exposes no rolling-60s series. Absent metrics render an
// em-dash; the pane never fabricates a number.
//
// Per the design voice: lowercase mono labels, no emoji in the chrome,
// em-dash for any metric the backend hasn't reported.

import {
  useSlots,
  useSlotRestart,
  useSlotUnload,
  useSlotLoad,
  useSlotSwap,
} from '@/api/hooks/useSlots'
import { useModels } from '@/api/hooks/useModels'
import { useMemoryMapModel } from './memory-map'
import { slotIndicatorFromPhase, isSlotLive } from './slot-status.js'

const { useState: useStateI, useRef: useRefI, useEffect: useEffectI } = React

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
  ext: <II d="M6 3H3v10h10v-3M9 3h4v4M9 9l4-4" />,
}
const Ic = ({ name, size = 16 }) =>
  IIcons[name] ? React.cloneElement(IIcons[name], { size }) : null

const round1 = (n) => Math.round((n || 0) * 10) / 10
const toast = (msg, kind = 'info') =>
  typeof window !== 'undefined' && window.__hal0Toast && window.__hal0Toast(msg, kind)

// Normalize a slot.device string to the chip's device-kind token.
function devKind(device) {
  const d = String(device || '').toLowerCase()
  if (d === 'npu') return 'npu'
  if (d === 'cpu') return 'cpu'
  if (d.includes('vulkan')) return 'vulkan'
  if (d.includes('rocm') || d.startsWith('gpu')) return 'rocm'
  return 'cpu'
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

// ── memory — the iGPU GTT carve-out track (P2's MemDual, iGPU-only) ────────
// Reuses useMemoryMapModel()'s real per-slot resident memory (mem_mb) and
// colours. The frame is the GTT pool ceiling (~80 GB carve-out on the
// appliance); NPU/FLM models are NOT in this bar — they live on the NPU
// budget. A single honest "system" segment accounts for GTT in use beyond
// the named model weights (KV cache + runtime + buffers) — no fabricated
// KV/OS split. Each segment carries a native title (name · GB).
function MemGtt({ mm, full }) {
  const pool = mm.pool || {}
  const self = mm.self || {}
  const capGb = pool.totalGb || 0
  const frame = capGb || 1
  const gpuSegs = (self.slots || [])
    .filter((s) => (s.device === 'rocm' || s.device === 'vulkan') && s.bytesGb > 0)
    .sort((a, b) => b.bytesGb - a.bytesGb)
  const gpuModelGb = gpuSegs.reduce((a, s) => a + s.bytesGb, 0)
  const gttUsedGb = self.gttUsedGb || 0
  const systemGb = Math.max(0, round1(gttUsedGb - gpuModelGb))
  const usedGb = round1(Math.max(gttUsedGb, gpuModelGb))
  const segs = [
    ...gpuSegs.map((s) => ({ key: s.name, label: s.name, gb: s.bytesGb, color: s.color })),
    ...(systemGb > 0
      ? [{ key: '__sys', label: 'system · KV + runtime', gb: systemGb, color: 'var(--fg-5)' }]
      : []),
  ]
  const freeGb = Math.max(0, round1(frame - usedGb))
  const pct = (gb) => (gb / frame) * 100

  return (
    <div className="mem">
      <SubLabel icon="mem" note={capGb ? `${Math.round(capGb)} GB carve-out` : '—'}>
        memory · iGPU GTT
      </SubLabel>
      <div className="mtrack">
        <div className="mt-h">
          <span className="lbl">
            <span className="dchip vulkan">
              <span className="d" />
              iGPU
            </span>{' '}
            GTT carve-out
          </span>
          <span className="val">
            <b>{usedGb.toFixed(1)}</b> / {Math.round(capGb)} GB
          </span>
        </div>
        <div className="membar tall" data-testid="infer-membar">
          {segs.map((s) => (
            <i
              key={s.key}
              className="seg-gap"
              style={{ width: pct(s.gb) + '%', background: s.color }}
              title={`${s.label} · ${s.gb} GB`}
            />
          ))}
          <i className="free" style={{ width: Math.max(0, 100 - pct(usedGb)) + '%' }} />
        </div>
      </div>
      {full && (
        <div className="mem-legend">
          {segs.map((s) => (
            <div className="ln" key={s.key}>
              <span className="sw" style={{ background: s.color }} />
              <span className="nm">{s.label}</span>
              <span className="gb">
                <b>{s.gb}</b> GB
              </span>
            </div>
          ))}
          <div className="ln">
            <span className="sw free" />
            <span className="nm">free</span>
            <span className="gb">
              <b>{freeGb.toFixed(1)}</b> GB
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

// ── throughput tile (P2's TpTile) ──────────────────────────────────────────
function SparkBars({ data = [], hotN = 4 }) {
  const max = Math.max(...data, 1)
  if (!data.length) return <div className="spark2" />
  return (
    <div className="spark2">
      {data.map((v, i) => (
        <i
          key={i}
          className={i >= data.length - hotN ? 'hot' : ''}
          style={{ height: (v / max) * 100 + '%' }}
        />
      ))}
    </div>
  )
}

function TpTile({ value, ticks, peak, servingN }) {
  return (
    <div className="tp-tile" data-testid="infer-tp">
      <div className="blk-h" style={{ margin: 0 }}>
        <span className="ic">
          <Ic name="activity" size={13} />
        </span>{' '}
        combined throughput
      </div>
      <div className="tp-row">
        <div className="tp tp-mid">
          <div className="tp-num">
            {value == null ? '—' : value}
            <span className="u">tok/s</span>
          </div>
        </div>
        <div className="tp-aside">
          <span>{peak == null ? 'peak —' : `peak ${peak}`}</span>
          <span className="pk">{servingN} serving</span>
        </div>
      </div>
      <SparkBars data={ticks} />
    </div>
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
        <span className="d" />
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

// full cards get a real model picker (a styled <select> wired to useModels);
// non-LLM slots keep their static model line.
function ModelPicker({ s, models, disabled, onSwap }) {
  if (s.type !== 'llm')
    return (
      <div className="smodel" title={s.model || ''}>
        {s.model || '—'}
      </div>
    )
  const opts = (Array.isArray(models) ? models : []).filter((m) => m.type === 'llm')
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
function SlotControls({ phase, busy, compact, onStart, onStop, onRestart, onLogs, onEdit }) {
  const running = phase !== 'off'
  return (
    <span className="slot-ctrls" onClick={(e) => e.stopPropagation()}>
      {running ? (
        <button
          className="sctrl stop"
          title="Stop"
          disabled={busy || phase === 'transitional'}
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
function slotCtrlPhase(slot) {
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

function SlotCards({ rows, full, models, busyName, handlers }) {
  if (!rows.length)
    return <div className="scards-empty">no active slots — expand to start one</div>
  return (
    <div className={'scards ' + (full ? 'full' : 'compact')}>
      {rows.map(({ s, ind }) => {
        const phase = slotCtrlPhase(s)
        const dot = dotCls(ind)
        const memGb = typeof s.mem_mb === 'number' && s.mem_mb > 0 ? round1(s.mem_mb / 1024) : null
        const tps = typeof s.metrics?.toks === 'number' && s.metrics.toks > 0 ? s.metrics.toks : null
        const ttft = typeof s.metrics?.ttft === 'number' && s.metrics.ttft > 0 ? s.metrics.ttft : null
        const busy = busyName === s.name
        const spill = dot === 'serving' ? (tps ? `${tps} tok/s` : 'serving') : dot
        const ctrls = (
          <SlotControls
            phase={phase}
            busy={busy}
            compact={!full}
            onStart={() => handlers.onStart(s)}
            onStop={() => handlers.onStop(s)}
            onRestart={() => handlers.onRestart(s)}
            onLogs={() => handlers.onLogs(s)}
            onEdit={() => handlers.onEdit(s)}
          />
        )
        return (
          <div
            className={'scard ' + dot + (phase === 'off' ? ' dim' : '')}
            key={s.name}
            data-testid={`infer-slot-${s.name}`}
          >
            <div className="scard-h">
              <span className={'sdot ' + dot} title={ind.tooltip} />
              <span className="snm">{s.name}</span>
              <span className={'spill ' + dot}>{spill}</span>
            </div>
            <div className="scard-b">
              {full ? (
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
                    <div
                      className={'v' + (s.ctx_max ? '' : ' muted')}
                      style={{ fontSize: 12 }}
                    >
                      {ctxText(s)}
                    </div>
                  </div>
                </div>
              )}
              <div className={'scard-foot' + (full ? '' : ' bare')}>
                <DevCell s={s} onProfile={() => handlers.onEdit(s)} />
                {full && memGb != null && <span className="tag-chip">{memGb} GB</span>}
                <span className="grow" />
                {ctrls}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function InferencePane() {
  const slotsQuery = useSlots()
  const modelsQuery = useModels()
  const mm = useMemoryMapModel()
  const restartMut = useSlotRestart()
  const unloadMut = useSlotUnload()
  const loadMut = useSlotLoad()
  const swapMut = useSlotSwap()
  const [open, setOpen] = useStateI(false)
  const [busyName, setBusyName] = useStateI(null)

  // The Inference rollup is the iGPU/CPU slot stack. Image generation is its
  // own pane (ComfyuiPane); NPU/FLM slots are cordoned off to the NPU · FLM
  // stack pane below — they appear here only as the sec-label FLM count.
  const allSlots = slotsQuery.data || []
  const nonImg = allSlots.filter((s) => (s.group || '') !== 'img')
  const slots = nonImg.filter((s) => devKind(s.device) !== 'npu')
  const npuN = nonImg.length - slots.length

  const rows = slots.map((s) => ({ s, ind: slotIndicatorFromPhase(s) }))
  // collapsed view: serving + ready only (warming/idle wait for the expand)
  const compactRows = rows.filter((r) => {
    const d = dotCls(r.ind)
    return d === 'serving' || d === 'ready'
  })
  const servingN = rows.filter((r) => r.ind.cls === 'serving').length
  const loadedN = rows.filter((r) => isSlotLive(r.s)).length

  const gpuN = slots.filter((s) => {
    const k = devKind(s.device)
    return k === 'rocm' || k === 'vulkan'
  }).length

  // Combined throughput — summed tok/s across this pane's serving slots
  // (NPU/FLM throughput belongs to the NPU pane), with a client ring buffer
  // for the spark (the backend exposes no rolling series).
  const toksVals = slots
    .map((s) => s?.metrics?.toks)
    .filter((t) => typeof t === 'number' && t > 0)
  const value = toksVals.length ? Math.round(toksVals.reduce((a, b) => a + b, 0)) : null
  const historyRef = useRefI([])
  const lastRef = useRefI(null)
  const [, force] = useStateI(0)
  useEffectI(() => {
    if (value == null) return
    if (lastRef.current === value) return
    lastRef.current = value
    historyRef.current = [...historyRef.current, value].slice(-21)
    force((n) => n + 1)
  }, [value])
  const ticks = historyRef.current
  const peak = ticks.length ? Math.max(...ticks) : null

  // GTT headroom for the expanded status line (the memory map's frame).
  const gttCapGb = mm.pool?.totalGb || 0
  const gttFreeGb = Math.max(0, Math.round(gttCapGb - (mm.self?.gttUsedGb || 0)))

  const run = async (name, mut, args, okMsg) => {
    setBusyName(name)
    try {
      await mut.mutateAsync(args)
      toast(okMsg, 'ok')
    } catch (err) {
      toast(err?.message ? `${name}: ${err.message}` : `${name}: action failed`, 'warn')
    } finally {
      setBusyName(null)
    }
  }

  const handlers = {
    onStart: (s) => run(s.name, loadMut, s.name, `Starting ${s.name}`),
    onStop: (s) => run(s.name, unloadMut, s.name, `Unloaded ${s.name}`),
    onRestart: (s) => run(s.name, restartMut, s.name, `Restarting ${s.name}`),
    onSwap: (s, model_id) =>
      run(s.name, swapMut, { name: s.name, model_id }, `Swapping ${s.name}`),
    onEdit: (s) => {
      window.location.hash = '#slots/' + s.name
    },
    onLogs: (s) => {
      window.dispatchEvent(new CustomEvent('hal0:slot-logs', { detail: { name: s.name } }))
    },
  }

  const epillCls = servingN > 0 ? 'serving' : loadedN > 0 ? 'ready' : 'stopped'
  const epillLabel =
    servingN > 0
      ? `${servingN} serving · ${loadedN} loaded`
      : loadedN > 0
        ? `${loadedN} loaded`
        : 'idle'

  const newSlot = () => window.dispatchEvent(new CustomEvent('hal0:create-slot'))
  const openLogs = () => {
    window.location.hash = '#logs'
  }

  const hero = (full) => (
    <div className="hero-band">
      <MemGtt mm={mm} full={full} />
      <TpTile value={value} ticks={ticks} peak={peak} servingN={servingN} />
    </div>
  )

  return (
    <div className="infer-pane">
      <div className="proto">
        <div className="sec-label">
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
          <span className="grow" style={{ flex: 1 }} />
          <span className="meta">podman · :8080</span>
        </div>

        <div className={'engine' + (loadedN > 0 ? ' active' : '') + (open ? ' open' : '')}>
          <div className="engine-h">
            <span className="engine-glyph">
              <Ic name="slots" size={16} />
            </span>
            <span className="col">
              <span className="engine-title">Inference</span>
              <span className="engine-sub">inference engine · podman</span>
            </span>
            <span className={'epill ' + epillCls} data-testid="infer-epill">
              <span className="dot" />
              {epillLabel}
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

          {/* collapsed strip — compact hero, hidden when the pane is open */}
          <div className="infer-strip" data-testid="infer-strip">
            {hero(false)}
            <div>
              <SubLabel icon="slots" note={`${loadedN} loaded`}>
                active slots
              </SubLabel>
              <SlotCards
                rows={compactRows}
                full={false}
                models={modelsQuery.data}
                busyName={busyName}
                handlers={handlers}
              />
            </div>
          </div>

          {/* expandable body — hero pinned on top, then all slots as full cards */}
          <div className="engine-body">
            <div className="inner">
              <div className="engine-b">
                {hero(true)}
                <div>
                  <SubLabel icon="slots" note={`all ${rows.length} · full cards`}>
                    slots
                  </SubLabel>
                  <SlotCards
                    rows={rows}
                    full
                    models={modelsQuery.data}
                    busyName={busyName}
                    handlers={handlers}
                  />
                </div>
                <div className="body-status">
                  {servingN} serving
                  {gttCapGb > 0 ? ` · ${gttFreeGb} GB free` : ''}
                </div>
              </div>
            </div>
          </div>

          {/* footer — engine identity + caret expand control */}
          <div className="engine-foot has-q">
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
            <button
              className="qcaret"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              data-testid="infer-qcaret"
            >
              <span className="q">
                <Ic name="slots" size={13} /> {open ? 'collapse' : 'all slots'}
                <span className="qn">{slots.length}</span>
                <span className="qrun">· {servingN} serving</span>
              </span>
              <span className="car">
                <Ic name="chev" size={13} />
              </span>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

Object.assign(window, { InferencePane })
