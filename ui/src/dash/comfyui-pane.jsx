// hal0 dashboard — ComfyUI "generation engine" pane (slots-page Image-Gen tab).
//
// Ported from the hal0 Design System exploration
// (Design System/explorations/comfyui-row/{comfyui-pane,row}.{html,jsx}). The
// design models ComfyUI as ONE containerized generation engine — not per-model
// slots — that is mutually exclusive with the LLM stack on the single iGPU.
//
// What's wired live (read-only, via useComfyui → /api/comfyui/status):
//   - engine state + which mode owns the GPU (docker + systemd)
//   - GTT / RAM gauges + memory pressure (ComfyUI /system_stats)
//   - queue depth (ComfyUI /queue)
//   - model inventory counts (verified file counts on the share)
// The switchover toggle opens a blast-radius confirm dialog, then calls the
// feature-gated POST /api/comfyui/switchover (202 + background scripts; the
// status poll's `switchover` block drives the transitional UI — never an
// optimistic flip; 501 toast when the host gate is off).
//
// Deliberately NOT wired yet (need ComfyUI's WS /ws + AMDGPUMonitor, or the
// privileged path): per-node progress %, it/s, GPU util/temp/clocks, per-job
// queue names, and the container start/stop/restart controls. Those render in a
// disabled/"—" state so the layout stays faithful without inventing numbers.

import { useComfyui, useComfyuiSwitchover, COMFYUI_FALLBACK } from '@/api/hooks/useComfyui'

const { useState } = React

// ── icons (16×16, 1.5 stroke — hal0 thin-line family + generation glyphs).
// Ported from the design's row.jsx so the pane is self-contained.
const RI = ({ d, size = 16, sw = 1.5, children, fill = 'none' }) => (
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
const RIcons = {
  comfy: (
    <RI>
      <circle cx="4" cy="4" r="2" />
      <circle cx="12" cy="6" r="2" />
      <circle cx="6" cy="12" r="2" />
      <path d="M6 4.5l4 1M5.4 10.2l5-3.6" />
    </RI>
  ),
  stop: (
    <RI>
      <rect x="4" y="4" width="8" height="8" rx="1" />
    </RI>
  ),
  ext: <RI d="M6 3H3v10h10v-3M9 3h4v4M9 9l4-4" />,
  image: (
    <RI>
      <rect x="2.5" y="3" width="11" height="10" rx="1.5" />
      <circle cx="6" cy="6.5" r="1.2" />
      <path d="M3 11l3-2.5 2.5 2 2-1.5L13 11.5" />
    </RI>
  ),
  video: (
    <RI>
      <rect x="2" y="4" width="9" height="8" rx="1.5" />
      <path d="M11 7l3-2v6l-3-2z" />
    </RI>
  ),
  layers: (
    <RI>
      <path d="M8 2l6 3-6 3-6-3 6-3z" />
      <path d="M2 8l6 3 6-3M2 11l6 3 6-3" />
    </RI>
  ),
  cube: (
    <RI>
      <path d="M8 2l5.5 3v6L8 14l-5.5-3V5L8 2z" />
      <path d="M8 8l5.5-3M8 8v6M8 8L2.5 5" />
    </RI>
  ),
  bolt: <RI d="M9 2L4 9h3l-1 5 5-7H8l1-5z" fill="currentColor" />,
  queue: <RI d="M3 4h10M3 8h10M3 12h6" />,
  cancel: (
    <RI>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M6 6l4 4M10 6l-4 4" />
    </RI>
  ),
  chev: <RI d="M4 6l4 4 4-4" />,
  mem: (
    <RI>
      <rect x="2" y="5" width="12" height="6" rx="1" />
      <path d="M5 5V3M8 5V3M11 5V3M5 13v-2M11 13v-2" />
    </RI>
  ),
  logs: <RI d="M3 3h10M3 6h10M3 9h7M3 12h5" />,
  refresh: (
    <RI>
      <path d="M14 8a6 6 0 1 1-2-4.5" />
      <path d="M14 1v3.5h-3.5" />
    </RI>
  ),
  close: <RI d="M4 4l8 8M12 4l-4 4" />,
  warn: (
    <RI>
      <path d="M8 2l6 11H2L8 2z" />
      <path d="M8 7v3M8 12v0.01" />
    </RI>
  ),
}
const Ic = ({ name, size = 16 }) =>
  RIcons[name] ? React.cloneElement(RIcons[name], { size }) : null

function Toggle({ on, comfy = false, busy = false }) {
  return (
    <span
      className={'toggle' + (on ? ' on' : '') + (comfy ? ' comfy' : '') + (busy ? ' busy' : '')}
    >
      <span className="knob" />
    </span>
  )
}

function EngineGlyph() {
  return (
    <span className="engine-glyph">
      <Ic name="comfy" size={16} />
    </span>
  )
}

// GTT (iGPU) gauge over the 80GB ceiling + a RAM bar under it. `null` used
// values render an em-dash bar rather than a fabricated fill.
function GttGauge({ usedGb, ceil = 80, ramGb, ramCeil = 96, pressure = false }) {
  const pct = usedGb == null ? 0 : Math.min(100, (usedGb / ceil) * 100)
  const ramPct = ramGb == null ? 0 : Math.min(100, (ramGb / ramCeil) * 100)
  return (
    <div className="gauge">
      <div className="gauge-h">
        <span>GTT (iGPU)</span>
        <span>
          <b>{usedGb == null ? '—' : usedGb}</b> / {ceil} <span className="ceil">GB</span>
        </span>
      </div>
      <div className="gauge-track">
        <i className={pressure ? 'warnz' : 'comfy'} style={{ width: pct + '%' }} />
      </div>
      <div className="gauge-h" style={{ marginTop: 4 }}>
        <span style={{ color: 'var(--fg-4)' }}>RAM</span>
        <span style={{ color: 'var(--fg-3)' }}>
          {ramGb == null ? '—' : ramGb} / {ramCeil} GB
        </span>
      </div>
      <div className="gauge-track" style={{ height: 6 }}>
        <i className="os" style={{ width: ramPct + '%' }} />
      </div>
      {pressure && (
        <div className="gauge-note pressure">⚠ memory pressure — no swap on this host</div>
      )}
    </div>
  )
}

// ComfyUI's own web UI lives on the runtime host's :8188 (the dashboard is
// served from :8080), so links derive from the current hostname.
function comfyHref() {
  const host =
    typeof window !== 'undefined' && window.location ? window.location.hostname : '127.0.0.1'
  return `http://${host}:8188`
}

function toast(msg, kind) {
  if (typeof window !== 'undefined' && window.__hal0Toast) window.__hal0Toast(msg, kind)
}

const STATE_LABEL = {
  stopped: 'stopped',
  starting: 'starting…',
  running: 'running · idle',
  generating: 'generating',
  error: 'error',
}

// Switchover confirm — states the blast radius before flipping the iGPU.
function SwitchoverConfirm({ target, queuePending, busy, onCancel, onConfirm }) {
  const toGen = target === 'generation'
  return (
    <div className="cf-scrim" onClick={onCancel}>
      <div className="cf" onClick={(e) => e.stopPropagation()}>
        <div className="cf-h">
          <div className={'cf-eye' + (toGen ? '' : ' warn')}>
            {toGen ? 'ComfyUI · take the iGPU' : 'Inference · restore the LLM stack'}
          </div>
          <h3 className="cf-title">
            {toGen ? 'Switch to generation mode?' : 'Switch back to inference mode?'}
          </h3>
        </div>
        <div className="cf-b">
          <p className="cf-lede">
            {toGen ? (
              <>
                Only one of <span className="mono">{'{ inference, ComfyUI }'}</span> can hold the
                single iGPU. Switching stops the LLM runtime and starts the ComfyUI container.
              </>
            ) : (
              <>
                This stops the ComfyUI container and restarts the LLM runtime. In-flight renders
                are not interrupted by the dashboard — drain the queue first.
              </>
            )}
          </p>
          {toGen && (
            <div className="cf-blast">
              <div className="row">
                <span className="ic">
                  <Ic name="warn" size={14} />
                </span>
                Telegram / Discord bots go dark until you switch back.
              </div>
              <div className="row">
                <span className="ic">
                  <Ic name="warn" size={14} />
                </span>
                Background memory extraction pauses (NPU gemma3-4b via lemonade); it recovers
                automatically. Embeddings + rerank are CPU-pinned and unaffected.
              </div>
            </div>
          )}
          {!toGen && queuePending > 0 && (
            <div className="cf-guard">
              <Ic name="warn" size={14} /> {queuePending} job{queuePending === 1 ? '' : 's'} still
              queued — they will be dropped when the container stops.
            </div>
          )}
          <div className="cf-steps">
            {toGen ? (
              <>
                <span className="n">1</span> <span className="cmd">stop-inference.sh</span>{' '}
                <span className="arr">→</span> <span className="cmd">comfy-up.sh</span>
              </>
            ) : (
              <>
                <span className="n">1</span> <span className="cmd">comfy-down.sh</span>{' '}
                <span className="arr">→</span> <span className="cmd">start-inference.sh</span>
              </>
            )}
          </div>
        </div>
        <div className="cf-f">
          <span className="note">runs root-owned scripts on the runtime host</span>
          <span className="grow" style={{ flex: 1 }} />
          <button className="rbtn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            className={'rbtn ' + (toGen ? 'primary' : 'danger')}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? 'switching…' : toGen ? 'Switch to generation' : 'Switch to inference'}
          </button>
        </div>
      </div>
    </div>
  )
}

const FLOWS = [
  { ic: 'image', a: 'text', b: 'image', note: 'qwen-image' },
  { ic: 'image', a: 'image', b: 'image' },
  { ic: 'video', a: 'text', b: 'video', note: 'wan 2.2' },
  { ic: 'video', a: 'image', b: 'video', note: 'i2v' },
  { ic: 'layers', a: 'still', b: 'animate', note: 'chain' },
  { ic: 'cube', a: 'upscale', b: '4×' },
]

export function ComfyuiPane() {
  const q = useComfyui()
  const sw = useComfyuiSwitchover()
  const st = q.data || COMFYUI_FALLBACK
  const [open, setOpen] = useState(false)
  const [confirm, setConfirm] = useState(null) // target mode or null

  const gen = st.mode === 'generation'
  const containerUp = st.container?.state === 'running'
  const engine = st.engine || 'stopped'
  // A switch in flight overrides the snapshot state: the pane's poll is what
  // tracks the transition to terminal (202 + background scripts server-side).
  const switching = !!st.switchover?.active
  const switchError = st.switchover?.error || null
  const stateLabel = switching
    ? `switching to ${st.switchover.target}…`
    : STATE_LABEL[engine] || engine
  const mem = st.memory
  const gtt = mem?.gtt_used_gb ?? null
  const gttCeil = mem?.gtt_ceil_gb ?? 80
  const ram = mem?.ram_used_gb ?? null
  const ramCeil = mem?.ram_ceil_gb ?? 96
  const pressure = !!mem?.pressure
  const running = st.queue?.running || 0
  const pending = st.queue?.pending || 0
  const queueTotal = running + pending
  const inv = st.inventory

  const href = comfyHref()
  const openComfy = () => window.open(href, '_blank', 'noopener')

  const doSwitch = async () => {
    const target = confirm
    try {
      // The confirm dialog already warned that queued renders drop — that
      // consent is what authorizes force when tearing down a busy queue.
      const force = target === 'inference' && queueTotal > 0 ? true : undefined
      await sw.mutateAsync({ mode: target, force })
      toast(`Switching to ${target} mode…`, 'ok')
      setConfirm(null)
      setTimeout(() => q.refetch(), 1500)
    } catch (err) {
      setConfirm(null)
      // Hal0Error carries the backend envelope's code directly (e.g.
      // comfyui.switchover_disabled / comfyui.busy) + the HTTP status.
      const code = String(err?.code || '')
      if (code === 'comfyui.switchover_disabled' || err?.status === 501) {
        toast(
          'ComfyUI switchover is disabled on this host — set HAL0_COMFYUI_SWITCHOVER_ENABLED=1 ' +
            'on hal0-api to enable it.',
          'warn'
        )
      } else if (code === 'comfyui.busy') {
        toast('Renders are still running or queued — drain the queue first.', 'warn')
      } else if (code === 'comfyui.switch_in_progress') {
        toast('A switchover is already in progress — wait for it to finish.', 'warn')
      } else {
        toast(err?.message ? `switchover failed: ${err.message}` : 'switchover failed', 'warn')
      }
    }
  }

  return (
    <div className="comfy-pane">
      <div className="proto">
        {/* section label */}
        <div className="sec-label">
          <b>Generation Engine</b>
          <span className="dim">·</span>
          <span className="mono" style={{ color: 'var(--comfy)' }}>
            ComfyUI
          </span>
          <span className="dim">·</span>
          <span className="meta">1 container</span>
          <span className="dim">·</span>
          <span className="meta">exclusive iGPU</span>
          <span className="grow" style={{ flex: 1 }} />
          <span className="meta">{containerUp ? 'docker · :8188' : 'docker · stopped'}</span>
        </div>

        <div className={'engine' + (gen ? ' active' : '') + (open ? ' open' : '')}>
          {/* header */}
          <div className="engine-h">
            <EngineGlyph />
            <span className="col">
              <span className="engine-title">ComfyUI</span>
              <span className="engine-sub">generation engine · docker</span>
            </span>
            <span className={'epill ' + (switching ? 'starting' : engine)}>
              <span className="dot" />
              {stateLabel}
            </span>
            {switchError && !switching && (
              <span className="meta" style={{ color: 'var(--warn)' }} title={switchError}>
                last switch failed
              </span>
            )}
            <span className="grow" style={{ flex: 1 }} />
            <span className="eh-right">
              <button
                className="rbtn ghost-comfy"
                disabled={!containerUp}
                style={!containerUp ? { opacity: 0.4 } : null}
                onClick={openComfy}
                title={containerUp ? 'Open ComfyUI' : 'ComfyUI is not running'}
              >
                <Ic name="ext" size={13} /> Open ↗
              </button>
              <span
                className="sw-wrap"
                onClick={() => !switching && setConfirm(gen ? 'inference' : 'generation')}
                style={{ cursor: switching ? 'wait' : 'pointer' }}
                title={
                  switching
                    ? 'Switchover in progress…'
                    : 'Switch the iGPU between inference and generation'
                }
              >
                <span className={'mode' + (!gen ? ' llm-on' : '')}>inference</span>
                <Toggle on={gen} comfy busy={sw.isPending || switching} />
                <span className={'mode' + (gen ? ' on' : '')}>generation</span>
              </span>
            </span>
          </div>

          {/* collapsed telemetry strip — real read-only metrics inline */}
          {containerUp && !open && (
            <div className="collapsed-prog">
              <div className="tel-strip">
                <span className="tel">
                  <span className="l">GTT</span>
                  <span className="minibar">
                    <i
                      style={{
                        width: (gtt == null ? 0 : (gtt / gttCeil) * 100) + '%',
                        background: pressure ? 'var(--warn)' : 'var(--comfy)',
                      }}
                    />
                  </span>
                  <span className={'v' + (pressure ? ' warn' : '')}>
                    {gtt == null ? '—' : gtt}
                    <span className="u">/{gttCeil} GB</span>
                  </span>
                </span>
                <span className="tel">
                  <span className="l">RAM</span>
                  <span className="v">
                    {ram == null ? '—' : ram}
                    <span className="u">/{ramCeil} GB</span>
                  </span>
                </span>
                <span className="tel">
                  <span className="l">queue</span>
                  {queueTotal > 0 ? (
                    <span className="qn">{queueTotal}</span>
                  ) : (
                    <span className="v dimx">idle</span>
                  )}
                  {running > 0 && (
                    <span className="v dimx" style={{ fontSize: 11 }}>
                      {running} running
                    </span>
                  )}
                </span>
                <span className="tel">
                  <span className="l">engine</span>
                  <span className="v comfy">{engine}</span>
                </span>
                {pressure && (
                  <span className="tel">
                    <span className="v warn" style={{ fontSize: 11 }}>
                      ⚠ no swap
                    </span>
                  </span>
                )}
              </div>
            </div>
          )}

          {/* expandable body */}
          <div className="engine-body">
            <div className="inner">
              <div className="engine-b">
                {/* active job — read-only summary (live per-node progress needs WS) */}
                <div className="subcard">
                  <div className="subcard-h">
                    <Ic name="bolt" size={13} /> active job
                  </div>
                  <div className="mono dimx" style={{ fontSize: 12, padding: '2px 0' }}>
                    {!containerUp
                      ? 'engine stopped · switch to generation to run jobs'
                      : running > 0
                        ? `${running} job${running === 1 ? '' : 's'} running · open ComfyUI for live per-node progress ↗`
                        : 'engine idle · no job running'}
                  </div>
                </div>

                {/* queue (counts from /queue; per-job names live in ComfyUI) */}
                <div className="queue">
                  <div className="queue-h">
                    <Ic name="queue" size={13} /> queue
                    <span style={{ color: 'var(--fg-5)', marginLeft: 2 }}>· {pending} pending</span>
                    <span className="grow" style={{ flex: 1 }} />
                    <span
                      className="clear"
                      onClick={openComfy}
                      style={{ cursor: 'pointer' }}
                      title="Manage the queue in ComfyUI"
                    >
                      open queue ↗
                    </span>
                  </div>
                  <div className="queue-empty">
                    {queueTotal === 0
                      ? 'nothing queued · drop a workflow in ComfyUI to enqueue'
                      : `${running} running · ${pending} pending — manage in ComfyUI ↗`}
                  </div>
                </div>

                {/* bottom 50/50 — memory + iGPU | models on share + workflows */}
                <div className="subgrid" style={{ gridTemplateColumns: '1fr 1fr' }}>
                  <div className="subcard">
                    <div className="subcard-h">
                      <Ic name="mem" size={13} /> memory <span style={{ color: 'var(--fg-5)' }}>· iGPU</span>
                    </div>
                    <GttGauge usedGb={gtt} ceil={gttCeil} ramGb={ram} ramCeil={ramCeil} pressure={pressure} />
                    <div className="gpu-stats">
                      <span className="gs">
                        <b style={{ color: 'var(--comfy)' }}>{st.reachable ? engine : '—'}</b>
                        <span className="u">state</span>
                      </span>
                      <span className="gs">
                        <b>{st.inference?.lemonade ? 'up' : 'down'}</b>
                        <span className="u">lemonade</span>
                      </span>
                      <span className="gs">
                        <b>{containerUp ? 'up' : 'down'}</b>
                        <span className="u">container</span>
                      </span>
                    </div>
                  </div>
                  <div className="subcard">
                    <div className="subcard-h">
                      <Ic name="layers" size={13} /> models on share
                      <span className="grow" style={{ flex: 1 }} />
                      <span className="rchip comfy" onClick={openComfy} style={{ cursor: 'pointer' }}>
                        manager ↗
                      </span>
                    </div>
                    {inv ? (
                      <div className="inv">
                        <span className="inv-pill">
                          <b>{inv.checkpoints ?? 0}</b> checkpoints
                        </span>
                        <span className="inv-pill">
                          <b>{inv.diffusion ?? 0}</b> <span className="u">diffusion</span>
                        </span>
                        <span className="inv-pill">
                          <b>{inv.loras ?? 0}</b> loras
                        </span>
                        <span className="inv-pill">
                          <b>{inv.vae ?? 0}</b> vae
                        </span>
                      </div>
                    ) : (
                      <div className="mono dimx" style={{ fontSize: 11 }}>
                        model share not mounted
                      </div>
                    )}

                    <div className="card-section">
                      <div className="subcard-h">
                        <span style={{ color: 'var(--comfy)' }}>
                          <Ic name="bolt" size={13} />
                        </span>{' '}
                        workflows
                        <span className="grow" style={{ flex: 1 }} />
                        <span
                          className="dimx"
                          style={{ fontSize: 10, textTransform: 'none', letterSpacing: 0 }}
                        >
                          opens in ComfyUI ↗
                        </span>
                      </div>
                      <div className="flows">
                        {FLOWS.map((f, i) => (
                          <button key={i} className="flow" onClick={openComfy}>
                            <span className="ic">
                              <Ic name={f.ic} size={14} />
                            </span>
                            <span>{f.a}</span>
                            <span className="arr">→</span>
                            <span>{f.b}</span>
                            {f.note && (
                              <span className="dimx" style={{ fontSize: 10, marginLeft: 2 }}>
                                {f.note}
                              </span>
                            )}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>

                {/* container controls — write ops need the privileged path; shown
                    disabled until that's wired, so the layout stays honest. */}
                <div className="row-flex">
                  <button className="rbtn" disabled style={{ opacity: 0.4 }} title="needs the privileged switchover path">
                    <Ic name="stop" size={13} /> Stop container
                  </button>
                  <button className="rbtn" disabled style={{ opacity: 0.4 }} title="needs the privileged switchover path">
                    <Ic name="refresh" size={13} /> Restart
                  </button>
                  <button className="rbtn" onClick={openComfy} title="Open ComfyUI logs / console">
                    <Ic name="logs" size={13} /> Logs ↗
                  </button>
                  <span className="grow" style={{ flex: 1 }} />
                  <span className="mono dimx" style={{ fontSize: 11 }}>
                    {st.inference?.lemonade ? 'inference holds the iGPU' : containerUp ? 'generation holds the iGPU' : 'iGPU idle'}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* footer — container identity + queue/caret expand control */}
          <div className="engine-foot has-q">
            <div className="foot-id">
              <span className="k">container</span>
              <span className="v comfy">{st.container?.name || 'comfyui'}</span>
              <span className="sep">·</span>
              <span className="k">state</span>
              <span className="v">{st.container?.state || 'absent'}</span>
              <span className="sep">·</span>
              <span className="k">mode</span>
              <span className="v comfy">{st.mode}</span>
              <span className="sep">·</span>
              <span className="k">endpoint</span>
              <span className="v comfy">{st.endpoint || '—'}</span>
            </div>
            <button
              className={'qcaret' + (queueTotal === 0 ? ' empty' : '')}
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
            >
              <span className="q">
                <Ic name="queue" size={13} /> queue
                {queueTotal > 0 ? (
                  <span className="qn">{queueTotal}</span>
                ) : (
                  <span className="qrun">empty</span>
                )}
                {running > 0 && <span className="qrun">· {running} running</span>}
              </span>
              <span className="car">
                <Ic name="chev" size={13} />
              </span>
            </button>
          </div>
        </div>
      </div>

      {confirm && (
        <SwitchoverConfirm
          target={confirm}
          queuePending={pending}
          busy={sw.isPending}
          onCancel={() => setConfirm(null)}
          onConfirm={doSwitch}
        />
      )}
    </div>
  )
}

Object.assign(window, { ComfyuiPane })
