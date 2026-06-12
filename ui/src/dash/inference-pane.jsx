// hal0 dashboard — Inference "engine" pane (slots-page Inference tab).
//
// The yellow-accented counterpart to the ComfyUI generation-engine pane
// (comfyui-pane.jsx). Where ComfyUI models ONE containerized generation
// engine, this pane is a summary engine-shell over the LLM/capability slot
// stack: a collapsed hero strip (memory map + active slot list + combined
// throughput) that expands to the full slot list with per-slot lifecycle
// controls, a model picker, and a by-device throughput split.
//
// Ported from the hal0 Design System exploration (inference-row/{infer-core,
// infer}.{jsx,css}). Presentational components are inlined here; all data is
// LIVE via the typed hooks:
//   - useSlots()           → the slot rollup (every non-image slot)
//   - useModels()          → the per-slot model picker (expanded list)
//   - useMemoryMapModel()  → per-slot resident memory (real mem_mb) + pool
//   - useHardware()        → the unified-RAM frame (124 GB) for the mem bar
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
import { useHardware } from '@/api/hooks/useHardware'
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

// ── memory map (one continuous segmented bar) ──────────────────────────────
// Reuses useMemoryMapModel()'s real per-slot resident memory (mem_mb) and
// colours. The frame is the unified RAM pool (≈124 GB); a tick marks the iGPU
// GTT carve-out (≈80 GB). A single honest "system" segment accounts for GTT
// in use beyond the named model weights (KV cache + runtime + buffers) — no
// fabricated KV/OS split.
function MemSegmented({ mm, hw, full }) {
  const [hi, setHi] = useStateI(null)
  const pool = mm.pool || {}
  const self = mm.self || {}
  const gttCapGb = pool.totalGb || 0
  const unifiedGb = hw?.data?.ram?.total || gttCapGb || 0
  const frame = unifiedGb || 1
  const modelSegs = (self.slots || []).filter((s) => s.bytesGb > 0)
  const modelUsedGb = self.modelUsedGb || 0
  const gttUsedGb = self.gttUsedGb || 0
  const gpuModelGb = modelSegs
    .filter((s) => s.device === 'rocm' || s.device === 'vulkan')
    .reduce((a, s) => a + s.bytesGb, 0)
  const systemOtherGb = Math.max(0, round1(gttUsedGb - gpuModelGb))

  const segs = [
    ...modelSegs.map((s) => ({
      key: s.name,
      label: s.name,
      gb: s.bytesGb,
      color: s.color,
    })),
    ...(systemOtherGb > 0
      ? [{ key: '__sys', label: 'system · KV + runtime', gb: systemOtherGb, color: 'var(--fg-5)' }]
      : []),
  ]
  const usedGb = round1(modelUsedGb + systemOtherGb)
  const freeGb = Math.max(0, round1(frame - usedGb))
  const pct = (gb) => (gb / frame) * 100
  let acc = 0
  const placed = segs.map((s) => {
    const left = (acc / frame) * 100
    const w = (s.gb / frame) * 100
    acc += s.gb
    return { ...s, left, w }
  })

  return (
    <div className="mem">
      <div className="blk-h">
        <span className="ic">
          <Ic name="mem" size={13} />
        </span>{' '}
        memory map
        <span className="grow" />
        <span className="note">unified · {frame.toFixed(0)} GB</span>
      </div>
      <div className="mem-h">
        <span>
          <b>{usedGb.toFixed(1)}</b> / {frame.toFixed(0)} <span className="ceil">GB resident</span>
        </span>
        <span className="free">{freeGb.toFixed(1)} GB free</span>
      </div>
      <div className="membar" style={{ marginTop: 14 }} onMouseLeave={() => setHi(null)}>
        {placed.map((s) => (
          <i
            key={s.key}
            className="seg-gap"
            style={{ width: s.w + '%', background: s.color }}
            onMouseEnter={() => setHi(s)}
          />
        ))}
        <i className="free" style={{ width: Math.max(0, 100 - pct(usedGb)) + '%' }} />
        {gttCapGb > 0 && gttCapGb < frame && (
          <span
            className="mem-tick"
            data-label={`GTT ${Math.round(gttCapGb)}`}
            style={{ left: pct(gttCapGb) + '%' }}
          />
        )}
        {hi && (
          <span
            className="mem-tip"
            style={{ left: Math.min(92, Math.max(8, hi.left + hi.w / 2)) + '%' }}
          >
            <span className="sw" style={{ background: hi.color }} />
            <b>{hi.label}</b> {hi.gb} GB
          </span>
        )}
      </div>
      {full && (
        <div className="mem-legend">
          {placed.map((s) => (
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

// ── throughput ─────────────────────────────────────────────────────────────
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

function TpBig({ value, ticks, peak, servingN }) {
  return (
    <div className="tp tp-big">
      <div className="blk-h">
        <span className="ic">
          <Ic name="activity" size={13} />
        </span>{' '}
        combined throughput
        <span className="grow" />
        <span className="note">{servingN} serving</span>
      </div>
      <div className="tp-num">
        {value == null ? '—' : value}
        <span className="u">tok/s</span>
      </div>
      <SparkBars data={ticks} />
      <div className="tp-sub">
        <span>last 60s</span>
        <span className="pk">{peak == null ? 'peak —' : `peak ${peak} t/s`}</span>
      </div>
    </div>
  )
}

function TpSplit({ igpu, flm, combined }) {
  const total = combined || 1
  return (
    <div className="tp">
      <div className="blk-h">
        <span className="ic">
          <Ic name="activity" size={13} />
        </span>{' '}
        throughput · by device
      </div>
      <div className="tp tp-mid" style={{ marginBottom: 4 }}>
        <div className="tp-num">
          {combined == null ? '—' : combined}
          <span className="u">tok/s combined</span>
        </div>
      </div>
      <div className="tp-split">
        <div className="sr">
          <span className="dlbl" style={{ color: 'var(--dev-vulkan)' }}>
            iGPU
          </span>
          <span className="dbar">
            <i style={{ width: (igpu / total) * 100 + '%', background: 'var(--dev-vulkan)' }} />
          </span>
          <span className="dval">{igpu || '—'}</span>
        </div>
        <div className="sr">
          <span className="dlbl" style={{ color: 'var(--dev-npu)' }}>
            FLM
          </span>
          <span className="dbar">
            <i style={{ width: (flm / total) * 100 + '%', background: 'var(--dev-npu)' }} />
          </span>
          <span className="dval">{flm || '—'}</span>
        </div>
      </div>
    </div>
  )
}

// ── slot list ────────────────────────────────────────────────────────────
function DevCell({ s, withProfile, onProfile }) {
  const dchip =
    devKind(s.device) === 'npu' ? (
      <span className="flm-chip">FLM · npu</span>
    ) : (
      <span className={'dchip ' + devKind(s.device)}>
        <span className="d" />
        {devKind(s.device)}
      </span>
    )
  if (!withProfile || !s.profile) return dchip
  return (
    <span className="prov">
      {dchip}
      <button className="profile-pill" title="Runtime profile — edit slot" onClick={onProfile}>
        {s.profile}
        <Ic name="chev" size={10} />
      </button>
    </span>
  )
}

function ModelPickerCell({ s, models, disabled, onSwap }) {
  if (s.type !== 'llm') return <span className="smodel">{s.model || '—'}</span>
  const opts = (Array.isArray(models) ? models : []).filter((m) => m.type === 'llm')
  const cur = s.model_id || s.model || ''
  const has = opts.some((m) => m.id === cur)
  return (
    <select
      className="slist-picker mono"
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

function SlotControls({ s, phase, busy, onStart, onStop, onRestart, onLogs, onEdit }) {
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
      <button className="sctrl" title="Logs" onClick={onLogs}>
        <Ic name="logs" size={13} />
      </button>
      <button className="sctrl" title="Edit" onClick={onEdit}>
        <Ic name="edit" size={13} />
      </button>
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

function SlotList({ rows, full, models, busyName, handlers }) {
  const tmplC = '10px 84px minmax(0,1fr) auto 76px 70px'
  const tmplF = '10px 90px minmax(0,1fr) auto 64px 60px 64px 70px 132px'
  const tmpl = full ? tmplF : tmplC
  return (
    <div className="slist">
      {full && (
        <div className="sh" style={{ gridTemplateColumns: tmpl }}>
          <span />
          <span>slot</span>
          <span>model</span>
          <span>device</span>
          <span style={{ textAlign: 'right' }}>mem</span>
          <span style={{ textAlign: 'right' }}>tok/s</span>
          <span style={{ textAlign: 'right' }}>ttft</span>
          <span style={{ textAlign: 'right' }}>ctx</span>
          <span style={{ textAlign: 'right' }}>actions</span>
        </div>
      )}
      {rows.map(({ s, ind }) => {
        const phase = slotCtrlPhase(s)
        const memGb = typeof s.mem_mb === 'number' && s.mem_mb > 0 ? round1(s.mem_mb / 1024) : null
        const tps = typeof s.metrics?.toks === 'number' && s.metrics.toks > 0 ? s.metrics.toks : null
        const ttft = typeof s.metrics?.ttft === 'number' && s.metrics.ttft > 0 ? s.metrics.ttft : null
        const busy = busyName === s.name
        return (
          <div
            className={'sr' + (phase === 'off' ? ' dim' : '')}
            key={s.name}
            style={{ gridTemplateColumns: tmpl }}
            data-testid={`infer-slot-${s.name}`}
          >
            <span className={'sdot ' + dotCls(ind)} title={ind.tooltip} />
            <span className="snm">
              {s.name}
              {s.isDefault && <span className="snm-star">★</span>}
            </span>
            {full ? (
              <ModelPickerCell
                s={s}
                models={models}
                disabled={busy}
                onSwap={(id) => handlers.onSwap(s, id)}
              />
            ) : (
              <span className="smodel">{s.model || '—'}</span>
            )}
            <DevCell s={s} withProfile={full} onProfile={() => handlers.onEdit(s)} />
            <span className="smem">
              {memGb == null ? '—' : memGb}
              {memGb != null && <span className="u"> GB</span>}
            </span>
            {!full ? (
              <span className={'stps' + (tps ? '' : ' muted')}>{tps || '—'}</span>
            ) : (
              <>
                <span className="met">
                  <span className={'mv' + (tps ? ' acc' : ' muted')}>{tps || '—'}</span>
                </span>
                <span className="met">
                  <span className={'mv' + (ttft ? '' : ' muted')}>{ttft ? ttft + 'ms' : '—'}</span>
                </span>
                <span className="met">
                  <span className={'mv' + (s.ctx_max ? '' : ' muted')}>{ctxText(s)}</span>
                </span>
                <SlotControls
                  s={s}
                  phase={phase}
                  busy={busy}
                  onStart={() => handlers.onStart(s)}
                  onStop={() => handlers.onStop(s)}
                  onRestart={() => handlers.onRestart(s)}
                  onLogs={() => handlers.onLogs(s)}
                  onEdit={() => handlers.onEdit(s)}
                />
              </>
            )}
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
  const hw = useHardware()
  const restartMut = useSlotRestart()
  const unloadMut = useSlotUnload()
  const loadMut = useSlotLoad()
  const swapMut = useSlotSwap()
  const [open, setOpen] = useStateI(false)
  const [busyName, setBusyName] = useStateI(null)

  // The Inference rollup is every non-image slot (chat + capabilities + the
  // NPU/FLM trio). Image generation is its own pane (ComfyuiPane).
  const allSlots = slotsQuery.data || []
  const slots = allSlots.filter((s) => (s.group || '') !== 'img')

  const rows = slots.map((s) => ({ s, ind: slotIndicatorFromPhase(s) }))
  const activeRows = rows.filter((r) => isSlotLive(r.s) || r.ind.cls === 'serving')
  const servingN = rows.filter((r) => r.ind.cls === 'serving').length
  const loadedN = rows.filter((r) => isSlotLive(r.s)).length

  const gpuN = slots.filter((s) => {
    const k = devKind(s.device)
    return k === 'rocm' || k === 'vulkan'
  }).length
  const npuN = slots.filter((s) => devKind(s.device) === 'npu').length

  // Combined throughput — summed tok/s across serving slots, with a client
  // ring buffer for the 60s spark (the backend exposes no rolling series).
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

  const igpuTps = Math.round(
    slots
      .filter((s) => devKind(s.device) !== 'npu')
      .reduce((a, s) => a + (typeof s.metrics?.toks === 'number' ? s.metrics.toks : 0), 0),
  )
  const flmTps = Math.round(
    slots
      .filter((s) => devKind(s.device) === 'npu')
      .reduce((a, s) => a + (typeof s.metrics?.toks === 'number' ? s.metrics.toks : 0), 0),
  )

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

          {/* collapsed strip — hidden when the pane is open */}
          <div className="infer-strip" data-testid="infer-strip">
            <div className="strip-split">
              <SlotList
                rows={activeRows}
                full={false}
                models={modelsQuery.data}
                busyName={busyName}
                handlers={handlers}
              />
              <div className="strip-divider">
                <MemSegmented mm={mm} hw={hw} full={false} />
                <TpBig value={value} ticks={ticks} peak={peak} servingN={servingN} />
              </div>
            </div>
          </div>

          {/* expandable body */}
          <div className="engine-body">
            <div className="inner">
              <div className="engine-b">
                <MemSegmented mm={mm} hw={hw} full />
                <SlotList
                  rows={rows}
                  full
                  models={modelsQuery.data}
                  busyName={busyName}
                  handlers={handlers}
                />
                <TpSplit igpu={igpuTps} flm={flmTps} combined={value} />
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
