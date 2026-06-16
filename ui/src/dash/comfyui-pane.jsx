// hal0 dashboard — ComfyUI "Image Gen" pane · V2 "Render hero".
//
// Ported from Design/design_handoff_comfyui_imagegen/design/ (Task 5.1).
// Mock data only — live API wiring is Task 5.2. Feed `data` prop with
// the RUN/QUEUE/GTT/RAM/STATS/ENGINE shape from comfy-core or the mock.
//
// Host must add class "comfy-page" to the mount wrapper for the blue
// --comfy accent scope to apply (see comfyui-pane.css).
//
// Empty-queue note (recall PR #845 lockup): the empty state must be
// in-flow with min-height, never position:absolute;inset:0 overlay.
//
// The only looping animation is @keyframes pulse (1.4s, ldot.generating).
// prefers-reduced-motion:reduce disables it via CSS `animation:none`.

import './comfyui-pane.css'
import {
  useComfyui,
  useComfyuiRenderCancel,
  useComfyuiRestart,
  useComfyuiWorkflowLaunch,
  transformComfyuiStatus,
  COMFYUI_FALLBACK,
} from '@/api/hooks/useComfyui'

const { useState, useEffect, useRef } = React

// ── icons (16×16, hal0 thin-line family) ─────────────────────────────────────
const CI = ({ d, size = 16, sw = 1.5, children, fill = 'none' }) => (
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

const CIcons = {
  comfy: (
    <CI>
      <circle cx="4" cy="4" r="2" />
      <circle cx="12" cy="6" r="2" />
      <circle cx="6" cy="12" r="2" />
      <path d="M6 4.5l4 1M5.4 10.2l5-3.6" />
    </CI>
  ),
  bolt: <CI d="M9 2L4 9h3l-1 5 5-7H8l1-5z" fill="currentColor" sw="0" />,
  image: (
    <CI>
      <rect x="2.5" y="3" width="11" height="10" rx="1.5" />
      <circle cx="6" cy="6.5" r="1.2" />
      <path d="M3 11l3-2.5 2.5 2 2-1.5L13 11.5" />
    </CI>
  ),
  video: (
    <CI>
      <rect x="2" y="4" width="9" height="8" rx="1.5" />
      <path d="M11 7l3-2v6l-3-2z" />
    </CI>
  ),
  layers: (
    <CI>
      <path d="M8 2l6 3-6 3-6-3 6-3z" />
      <path d="M2 8l6 3 6-3M2 11l6 3 6-3" />
    </CI>
  ),
  cube: (
    <CI>
      <path d="M8 2l5.5 3v6L8 14l-5.5-3V5L8 2z" />
      <path d="M8 8l5.5-3M8 8v6M8 8L2.5 5" />
    </CI>
  ),
  queue: <CI d="M3 4h10M3 8h10M3 12h6" />,
  mem: (
    <CI>
      <rect x="2" y="5" width="12" height="6" rx="1" />
      <path d="M5 5V3M8 5V3M11 5V3M5 13v-2M11 13v-2" />
    </CI>
  ),
  gauge: (
    <CI>
      <path d="M3 12a5 5 0 1 1 10 0" />
      <path d="M8 12l3-3.5" />
      <circle cx="8" cy="12" r="1" fill="currentColor" sw="0" />
    </CI>
  ),
  ext:  <CI d="M6 3H3v10h10v-3M9 3h4v4M9 9l4-4" />,
  stop: (
    <CI>
      <rect x="4" y="4" width="8" height="8" rx="1" />
    </CI>
  ),
  refresh: (
    <CI>
      <path d="M14 8a6 6 0 1 1-2-4.5" />
      <path d="M14 1v3.5h-3.5" />
    </CI>
  ),
  logs:  <CI d="M3 3h10M3 6h10M3 9h7M3 12h5" />,
  close: <CI d="M4 4l8 8M12 4l-4 4" />,
}

const Ci = ({ name, size = 16 }) =>
  CIcons[name] ? React.cloneElement(CIcons[name], { size }) : null

// ── reduced-motion detection ─────────────────────────────────────────────────
const REDUCE =
  typeof window !== 'undefined' &&
  window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

// ── shared tick (900ms; freezes under reduced-motion) ────────────────────────
function useTick(ms = 900) {
  const [t, setT] = useState(0)
  useEffect(() => {
    if (REDUCE) return
    const id = setInterval(() => setT((x) => x + 1), ms)
    return () => clearInterval(id)
  }, [ms])
  return t
}

// deterministic jitter for live readout breathing
const jit = (base, amp, t, ph = 0) =>
  REDUCE ? base : +(base + amp * Math.sin(t * 0.8 + ph)).toFixed(base < 10 ? 1 : 0)

// ── Radial Gauge — 270° sweep (same primitive as NPU widget) ─────────────────
function Gauge({ pct, label, sub, size = 116, warn = false }) {
  const sw = Math.round(size * 0.065)
  const r = size / 2 - sw - 4
  const cx = size / 2
  const cy = size / 2
  const C = 2 * Math.PI * r
  const arc = C * 0.75
  const fill = Math.max(0, Math.min(1, (pct || 0) / 100)) * arc
  return (
    <div className={'gauge' + (size < 140 ? ' sm' : '')} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle
          className="gtrack"
          cx={cx} cy={cy} r={r}
          strokeWidth={sw}
          strokeDasharray={`${arc} ${C}`}
          strokeLinecap="round"
          transform={`rotate(135 ${cx} ${cy})`}
        />
        <circle
          className={'gfill' + (warn ? ' warn' : '')}
          cx={cx} cy={cy} r={r}
          strokeWidth={sw}
          strokeDasharray={`${fill} ${C}`}
          transform={`rotate(135 ${cx} ${cy})`}
        />
      </svg>
      <div className="gc">
        <div className="pct">
          {Math.round(pct || 0)}
          <span className="s">%</span>
        </div>
        <div className="lbl">{label}</div>
        {sub ? <div className="sub">{sub}</div> : null}
      </div>
    </div>
  )
}

// ── Bar sparkline ─────────────────────────────────────────────────────────────
const SPARK_DEFAULT = [1.5, 1.7, 1.6, 1.8, 1.9, 1.7, 2.0, 1.8, 1.9, 2.1, 1.9, 2.0, 1.8, 1.9, 2.0, 1.9]

function BarSpark({ data = SPARK_DEFAULT, hotN = 4, style }) {
  const max = Math.max(...data, 1)
  return (
    <div className="cspark" style={style}>
      {data.map((v, i) => (
        <i
          key={i}
          className={i >= data.length - hotN ? 'hot' : ''}
          style={{ height: (v / max * 100) + '%' }}
        />
      ))}
    </div>
  )
}

// ── Block header ─────────────────────────────────────────────────────────────
function BlkH({ icon, acc, children, note }) {
  return (
    <div className="blk-h">
      <span className={'ic' + (acc ? ' acc' : '')}>
        <Ci name={icon} size={13} />
      </span>
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

// ── Step pips timeline ────────────────────────────────────────────────────────
function StepPips({ step, total }) {
  const pips = Array.from({ length: total }, (_, i) => {
    if (i < step - 1) return 'done'
    if (i === step - 1) return 'now'
    return ''
  })
  return (
    <div className="steps-pips">
      {pips.map((c, i) => (
        <span className={'pip ' + c} key={i}>
          {c ? <i /> : null}
        </span>
      ))}
    </div>
  )
}

// ── Workflows quick-launch ─────────────────────────────────────────────────────
const FLOWS_DEFAULT = [
  { ic: 'image',  a: 'text',   b: 'image',   tag: 'qwen-image',  name: 'qwen-image' },
  { ic: 'image',  a: 'image',  b: 'image',                        name: 'img2img' },
  { ic: 'video',  a: 'text',   b: 'video',   tag: 'wan 2.2',     name: 'wan2.2-t2v' },
  { ic: 'video',  a: 'image',  b: 'video',   tag: 'i2v',         name: 'wan2.2-i2v' },
  { ic: 'layers', a: 'still',  b: 'animate', tag: 'chain',       name: 'animate' },
  { ic: 'cube',   a: 'upscale',b: '4×',                          name: 'upscale-4x' },
]

function WorkflowsBlock({ flows = FLOWS_DEFAULT, onLaunch }) {
  return (
    <div>
      <BlkH icon="bolt" acc note="opens in ComfyUI ↗">workflows</BlkH>
      <div className="flows">
        {flows.map((f, i) => (
          <button
            className="flow"
            key={i}
            onClick={() => onLaunch && onLaunch(f.name)}
            data-workflow={f.name}
          >
            <span className="ic"><Ci name={f.ic} size={14} /></span>
            <span>{f.a}</span>
            <span className="arr">→</span>
            <span>{f.b}</span>
            {f.tag && <span className="tag">{f.tag}</span>}
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Models on share inventory ─────────────────────────────────────────────────
const INV_DEFAULT = [
  { n: 6,  l: 'checkpoints' },
  { n: 4,  l: 'video', u: true },
  { n: 11, l: 'loras' },
  { n: 3,  l: 'vae' },
]
const MODELS_DEFAULT = 'Wan 2.2 · Qwen-Image · HunyuanVideo 1.5 · LTX-2'

function ModelsBlock({ inv = INV_DEFAULT, models = MODELS_DEFAULT }) {
  return (
    <div>
      <BlkH icon="layers" note="manager ↗">models on share</BlkH>
      <div className="inv">
        {inv.map((m, i) => (
          <span className="inv-pill" key={i}>
            <b>{m.n}</b>
            {m.u ? <span className="u">{m.l}</span> : ` ${m.l}`}
          </span>
        ))}
      </div>
      <div className="inv-models">{models}</div>
    </div>
  )
}

// ── Mock data (handoff demo values) ─────────────────────────────────────────
export const COMFYUI_V2_MOCK = {
  engine: {
    name: 'ComfyUI',
    endpoint: ':8188',
    image: 'ghcr.io/hal0ai/comfyui@sha256:9f3c…b21a',
    restart: 'no',
  },
  run: {
    name: 'wan2.2-i2v',
    kind: '480p · 81 frames',
    pct: 72,
    eta: '~38s',
    node: 'KSampler (low-noise)',
    step: 3,
    total: 4,
    its: 1.9,
    loaded: 'wan-hi + wan-lo · umt5-xxl · wan-vae · 2 loras',
  },
  queue: [
    { name: 'qwen-image', kind: 'txt2img · 1328²' },
    { name: 'sdxl',       kind: 'upscale 4×' },
  ],
  gtt: { used: 54, ceil: 80 },
  ram: { used: 61, ceil: 96 },
  stats: { util: 97, temp: 71, clk: 2.7, its: 1.9 },
}

// ── Card header ───────────────────────────────────────────────────────────────
function CardHead({ engine, run, pct }) {
  const hasRun = run != null
  return (
    <div className="wcard-h">
      <span className="glyph"><Ci name="comfy" size={16} /></span>
      <span className="col">
        <span className="ttl">ComfyUI</span>
        <span className="sub">image-gen engine · docker</span>
      </span>
      <span className={'epill' + (hasRun ? ' generating' : '')}>
        <span className="dot" />
        {hasRun ? `generating · ${Math.round(pct)}%` : 'idle'}
      </span>
      <span className="cf-pill">
        <span className="d" />
        iGPU · exclusive
      </span>
      <span className="grow" />
      <span className="gpu-note">
        <Ci name="bolt" size={11} /> inference slots <span className="b">paused</span> while rendering
      </span>
      <span className="meta">docker · <b>{engine.endpoint}</b></span>
    </div>
  )
}

// ── Card footer ───────────────────────────────────────────────────────────────
function CardFoot({ engine, onRestart, onLogs }) {
  return (
    <div className="wfoot">
      <div className="foot-id">
        <span className="k">container</span>
        <span className="v acc">comfyui</span>
        <span className="sep">·</span>
        <span className="k">image</span>
        <span className="v">{engine.image}</span>
        <span className="sep">·</span>
        <span className="k">restart</span>
        <span className="v">{engine.restart}</span>
        <span className="sep">·</span>
        <span className="k">endpoint</span>
        <span className="v acc">{engine.endpoint}</span>
      </div>
      <span className="grow" />
      <span className="foot-ctrls">
        <button className="sctrl stop" title="Stop container"><Ci name="stop" size={12} /></button>
        <button className="sctrl restart" title="Restart" onClick={onRestart}><Ci name="refresh" size={12} /></button>
        <button className="sctrl" title="Logs" onClick={onLogs}><Ci name="logs" size={12} /></button>
      </span>
    </div>
  )
}

// ── Empty-queue state (in-flow, NO overlay) ───────────────────────────────────
// CRITICAL: must NOT be position:absolute;inset:0 — see PR #845 lockup.
// min-height keeps the block in-flow with visible height.
function EmptyQueueState({ comfyBaseUrl }) {
  return (
    <div className="queue-empty-state">
      <span>nothing queued · drop a workflow in</span>
      {comfyBaseUrl ? (
        <a className="rbtn acc" href={comfyBaseUrl} target="_blank" rel="noopener noreferrer" style={{ marginLeft: 8 }}>
          <Ci name="ext" size={12} /> Open ComfyUI ↗
        </a>
      ) : (
        <button className="rbtn acc" style={{ marginLeft: 8 }}>
          <Ci name="ext" size={12} /> Open ComfyUI ↗
        </button>
      )}
    </div>
  )
}

// ── Main ImageGenCard ─────────────────────────────────────────────────────────
export function ImageGenCard({
  mock = COMFYUI_V2_MOCK,
  onCancel,
  onRestart,
  onLogs,
  onLaunch,
  comfyBaseUrl,
}) {
  const { engine, run, queue, gtt, ram, stats } = mock
  const t = useTick(900)

  // Active render state (null = no render running)
  const hasRun = run != null

  // Jitter live readouts
  const pct  = hasRun ? Math.min(99, jit(run.pct, 1.2, t)) : 0
  const its  = hasRun ? jit(run.its, 0.18, t, 0.6) : 0
  const used = jit(gtt.used, 1.4, t)

  const gttPct  = used / gtt.ceil * 100
  const gttWarn = gttPct >= 80

  return (
    <div className="wcard active">
      <CardHead engine={engine} run={run} pct={pct} />
      <div className="wcard-b">

        {/* ── Render hero: preview frame + active-render progress ── */}
        <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 22, alignItems: 'stretch' }}>
          {/* Preview frame */}
          <div className="preview">
            <span className="scan" />
            {hasRun && comfyBaseUrl ? (
              <img
                src="/api/comfyui/preview"
                alt="latest render output"
                className="preview-img"
                style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', opacity: 0.7 }}
              />
            ) : (
              <span className="glyph"><Ci name="video" size={30} /></span>
            )}
            {hasRun ? (
              <span className="lab">
                <b>{run.name}</b><br />{run.kind}<br />
                live preview ↦ frame {Math.round(pct / 100 * 81)}/81
              </span>
            ) : (
              <span className="lab">no active render</span>
            )}
          </div>

          {/* Active render progress detail */}
          <div>
            <BlkH icon="bolt" acc note={hasRun ? `eta ${run.eta}` : undefined}>
              active render
            </BlkH>
            {hasRun ? (
              <div className="job" style={{ background: 'transparent', border: 'none', padding: 0, gap: 13 }}>
                <div className="job-h" style={{ alignItems: 'flex-end' }}>
                  <div className="col" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <span className="nm">
                      {run.name} <span className="kind">· {run.kind}</span>
                    </span>
                    <span className="job-loaded">{run.node} · step {run.step}/{run.total}</span>
                  </div>
                  <span className="grow" />
                  <span className="pct big">{Math.round(pct)}%</span>
                </div>
                <div className="gbar tall">
                  <i style={{ width: pct + '%' }} />
                </div>
                <StepPips step={run.step} total={run.total} />
                <div className="job-steps" style={{ marginTop: 2 }}>
                  <span className="node">loaded: {run.loaded}</span>
                  <span>{its} it/s</span>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                  <button className="rbtn" onClick={onCancel}><Ci name="stop" size={13} /> Cancel render</button>
                  {comfyBaseUrl ? (
                    <a className="rbtn acc" href={comfyBaseUrl} target="_blank" rel="noopener noreferrer">
                      <Ci name="ext" size={13} /> Open ComfyUI ↗
                    </a>
                  ) : (
                    <button className="rbtn acc"><Ci name="ext" size={13} /> Open ComfyUI ↗</button>
                  )}
                </div>
              </div>
            ) : (
              <div className="job-empty">engine idle · no job running</div>
            )}
          </div>
        </div>

        {/* ── Lower row: queue (2/3) + telemetry (1/3) ── */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '2fr 1fr',
          gap: 22,
          marginTop: 18,
          paddingTop: 16,
          borderTop: '1px solid var(--line-soft)',
        }}>
          {/* Queue */}
          <div>
            <BlkH icon="queue" note={`${hasRun ? '1 running' : '0 running'} · ${queue.length} pending`}>
              queue
            </BlkH>
            {queue.length === 0 && !hasRun ? (
              <EmptyQueueState comfyBaseUrl={comfyBaseUrl} />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
                {/* Running row */}
                {hasRun && (
                  <div className="qcard row running">
                    <span className="ldot generating" />
                    <span className="qjob">
                      <span className="qnm">{run.name}</span>
                      <span className="qkind">{run.kind} · {run.node}</span>
                    </span>
                    <span className="grow" />
                    <span className="qprog">
                      <span className="bar"><i style={{ width: pct + '%' }} /></span>
                      <span className="pc">{Math.round(pct)}%</span>
                    </span>
                    <span className="qspeed">{its} it/s</span>
                    <span className="ctrls">
                      <button className="sctrl stop" title="Cancel" onClick={onCancel}><Ci name="stop" size={12} /></button>
                      <button className="sctrl restart" title="Restart" onClick={onRestart}><Ci name="refresh" size={12} /></button>
                    </span>
                  </div>
                )}
                {/* Pending rows */}
                {queue.map((j, i) => (
                  <div className="qcard row pending" key={j.name}>
                    <span className="ldot pending" />
                    <span className="qjob">
                      <span className="qnm">{j.name}</span>
                      <span className="qkind">{j.kind}</span>
                    </span>
                    <span className="grow" />
                    <span className="stat">#{i + 1} · queued</span>
                    <span className="ctrls">
                      <button className="sctrl" title="Logs"><Ci name="logs" size={12} /></button>
                      <button className="sctrl" title="Remove"><Ci name="close" size={12} /></button>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Telemetry: GTT gauge + RAM spark + device metric grid */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              <Gauge
                pct={gttPct}
                label="gtt"
                sub={`${used} / ${gtt.ceil}`}
                size={116}
                warn={gttWarn}
              />
              <div style={{ minWidth: 0 }}>
                <div className="blk-h" style={{ margin: '0 0 5px' }}>system ram</div>
                <div className="tp-num" style={{ fontSize: 22 }}>
                  {ram.used}<span className="u">/ {ram.ceil} GB</span>
                </div>
                <BarSpark style={{ height: 24, marginTop: 8 }} />
              </div>
            </div>
            <div>
              <BlkH icon="gauge" note="iGPU">device</BlkH>
              <div className="mx2">
                <div className="cstat">
                  <span className="cl">util</span>
                  <span className="cv acc">{stats.util}<span className="u">%</span></span>
                </div>
                <div className="cstat">
                  <span className="cl">temp</span>
                  <span className="cv">{stats.temp}<span className="u">°C</span></span>
                </div>
                <div className="cstat">
                  <span className="cl">clock</span>
                  <span className="cv">{stats.clk}<span className="u">GHz</span></span>
                </div>
                <div className="cstat">
                  <span className="cl">speed</span>
                  <span className="cv acc">{stats.its}<span className="u">it/s</span></span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ── Workflows + Models lower section ── */}
        <div className="wcard-sub">
          <WorkflowsBlock onLaunch={onLaunch} />
          <ModelsBlock />
        </div>
      </div>

      <CardFoot engine={engine} onRestart={onRestart} onLogs={onLogs} />
    </div>
  )
}

// ── Pane wrapper (mount point from slots.jsx) ─────────────────────────────────
// Adds .comfy-v2-pane root + .comfy-page scope for blue accent CSS vars.
//
// Priority for data source (highest wins):
//   1. window.__comfyuiV2MockOverride  — e2e seam, set before mount
//   2. live API poll (useComfyui + transformComfyuiStatus)
//   3. COMFYUI_V2_MOCK                — static fallback for dev/storybook
//
// The mock override seam bypasses the hook entirely so e2e tests that inject
// window.__comfyuiV2MockOverride get deterministic rendering without needing
// to intercept /api/comfyui/status.
export function ComfyuiPane() {
  // Mock override seam (e2e / dev)
  const override =
    typeof window !== 'undefined' ? window.__comfyuiV2MockOverride : undefined

  // Live API poll — disabled when the override is set
  const { data: liveStatus } = useComfyui({ active: !override })

  // Derive pane data: override > live transform > static mock
  let paneData
  if (override) {
    paneData = override
  } else if (liveStatus) {
    paneData = transformComfyuiStatus(liveStatus)
  } else {
    paneData = COMFYUI_V2_MOCK
  }

  // Control mutations
  const cancelMutation = useComfyuiRenderCancel()
  const restartMutation = useComfyuiRestart()
  const launchMutation = useComfyuiWorkflowLaunch()

  // Derive the ComfyUI base URL from live status endpoint field
  const comfyBaseUrl = liveStatus?.endpoint
    ? liveStatus.endpoint.startsWith('http')
      ? liveStatus.endpoint
      : `http://127.0.0.1:8188`
    : undefined

  // .comfy-pane kept for backward-compat (some mount selectors still use it).
  return (
    <div className="comfy-v2-pane comfy-pane comfy-page">
      <ImageGenCard
        mock={paneData}
        onCancel={() => cancelMutation.mutate()}
        onRestart={() => restartMutation.mutate()}
        onLogs={() => {
          // Fetch logs and open in a basic alert for now; a logs drawer is a
          // future enhancement (the endpoint is wired, UI depth TBD).
          fetch('/api/comfyui/logs?tail=60')
            .then((r) => r.json())
            .then((d) => {
              if (Array.isArray(d?.lines) && d.lines.length > 0) {
                alert(d.lines.join('\n'))
              } else {
                alert('no logs available')
              }
            })
            .catch(() => alert('logs fetch failed'))
        }}
        onLaunch={(name) => launchMutation.mutate(name)}
        comfyBaseUrl={comfyBaseUrl}
      />
    </div>
  )
}

Object.assign(window, { ComfyuiPane, ImageGenCard })
