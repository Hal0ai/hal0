// hal0 dashboard — Dashboard view (system overview)
//
// Phase B1: SnapshotStrip drives off `useSlots()`.
// Phase B2 (#200, fix/chat-surface-functional): the chat surface
// (Composer, ChatActive, ChatEmpty, PersonaPicker) moved to chat.jsx so
// real `/v1/chat/completions` wiring lives in its own file.
// Chat-page-overhaul: chat moved to its own `#chat` route. The /dashboard
// view now hosts the full hardware surface (formerly the `#hardware`
// page, now retired) alongside the snapshot strip, memory map,
// throughput and health side cards.

import { useSlots } from '@/api/hooks/useSlots'
import { useLemondRollup } from '@/api/hooks/useLemonade'
import { useHardware } from '@/api/hooks/useHardware'
import { MemoryMap } from './memory-map'

const { useState: useStateD, useRef: useRefD, useEffect: useEffectD } = React;

// ─── Snapshot strip ───
// `slotIndicator` is the single source of truth for the dot vocabulary
// (defined in slots.jsx, published on window). Mirroring it here keeps
// the snapshot row's dot colour + status label aligned with each
// SlotCard — so a slot that reads "idle · yellow" on the slots page
// reads the same way in the sidebar.
function SnapshotStrip({ slots, onGo }) {
  const rows = slots.map(s => ({ slot: s, ind: slotIndicator(s) }));
  const readyCount = rows.filter(r => r.ind.cls === "serving" || r.ind.cls === "stale").length;
  return (
    <div className="snap">
      <div className="snap-head">
        <span>Slot snapshot</span>
        <span className="ct mono">{readyCount}/{slots.length} ready</span>
        <span className="right mono" onClick={() => onGo("slots")}>Manage slots →</span>
      </div>
      <div className="snap-rows">
        {rows.map(({ slot: s, ind }) => {
          const labelColor = ind.cls === "serving" ? "var(--accent)"
            : ind.cls === "stale" || ind.cls === "warming" ? "var(--warn)"
            : ind.cls === "error" ? "var(--err)"
            : "var(--fg-3)";
          const serving = ind.cls === "serving" && s.metrics?.toks != null;
          return (
            <div key={s.name} className="snap-row" onClick={() => onGo("slots/" + s.name)} title={ind.tooltip}>
              <span className={"dot " + ind.cls} />
              <span className="name mono">{s.name}</span>
              <span className="model mono">{s.model}</span>
              <span className={"chip dev-" + (s.device || "cpu").replace("gpu-", "")}>{s.device}</span>
              <span className="badge">
                {s.isDefault && <span className="chip outlined amber">default</span>}
                {s.coresident && <span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.06)"}}>coresident</span>}
                {s.cpuOnly && <span className="chip">[CPU]</span>}
              </span>
              <span className="num mono" style={{color: labelColor, fontSize: 11, textAlign: "right"}}>
                {serving ? `${s.metrics.toks} tok/s` : ind.label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}


// ─── Memory / health side cards ───
function HealthCard() {
  return (
    <div className="side-card">
      <div className="side-card-h">
        <span>Health</span>
        <span className="right mono">poll 287ms</span>
      </div>
      <div className="side-card-b" style={{paddingTop: 4, paddingBottom: 4}}>
        <div className="health-row">
          <span className="k">lemond</span>
          <span className="v up"><span className="dot" />up · {HAL0_DATA.lemond.version}</span>
        </div>
        <div className="health-row">
          <span className="k">hal0-api</span>
          <span className="v up"><span className="dot" />up · v0.2.1</span>
        </div>
        <div className="health-row">
          <span className="k">flm:npu</span>
          <span className="v up"><span className="dot" />trio · 0.9.42</span>
        </div>
        <div className="health-row">
          <span className="k">cognee</span>
          <span className="v up"><span className="dot" />2,847 records</span>
        </div>
        <div className="health-row">
          <span className="k">disk</span>
          <span className="v">412 GB free</span>
        </div>
      </div>
    </div>
  );
}

function ThroughputCard() {
  // Live last-request tok/s from /v1/stats (via useLemondRollup).
  // Lemonade does not expose a rolling-60s history — we build one
  // client-side by appending each new sample to an in-component ring
  // buffer (cap 21 entries to match the original spark width).
  const lemond = useLemondRollup();
  const value = lemond.lastTokPerSec;
  const historyRef = useRefD([]);
  const lastRef = useRefD(null);
  const [, force] = useStateD(0);
  useEffectD(() => {
    if (value == null) return;
    // Dedupe identical back-to-back samples so the spark only advances
    // when /v1/stats reports a new (or updated) measurement.
    if (lastRef.current === value) return;
    lastRef.current = value;
    historyRef.current = [...historyRef.current, value].slice(-21);
    force(n => n + 1);
  }, [value]);

  const ticks = historyRef.current;
  const hasData = value != null;
  const max = ticks.length > 0 ? Math.max(...ticks, 1) : 1;
  const peak = ticks.length > 0 ? Math.max(...ticks) : null;

  return (
    <div className="side-card">
      <div className="side-card-h">
        <span>Throughput</span>
        <span className="right mono">
          <b style={{color: hasData ? "var(--accent)" : "var(--fg-4)"}}>
            {hasData ? value.toFixed(1) : "—"}
          </b> tok/s
        </span>
      </div>
      <div className="side-card-b" style={{padding: "12px 16px"}}>
        <div className="spark">
          {ticks.map((t, i) => (
            <i key={i} style={{ height: `${(t / max) * 100}%`, opacity: i > ticks.length - 4 ? 1 : 0.5 + (i / ticks.length) * 0.5 }} />
          ))}
        </div>
        <div className="mono" style={{display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--fg-4)", marginTop: 6}}>
          <span>last request</span>
          <span>{peak != null ? `peak ${peak.toFixed(1)} t/s` : "no samples yet"}</span>
        </div>
      </div>
    </div>
  );
}

// ─── Hardware section ───
//
// Inherited from extras.jsx HardwareView when the standalone #hardware
// route was retired. The card primitives stay file-local — nothing else
// in the codebase rendered HwCard / HwRow.
function HardwareSection() {
  const hwQuery = useHardware();
  const H = hwQuery.data || HAL0_DATA.host;
  return (
    <div className="hw-section">
      <div className="vh" style={{marginBottom: 12}}>
        <span className="vh-eye mono">System</span>
        <h2 style={{margin: 0, fontSize: 18, fontWeight: 500, letterSpacing: "-0.02em"}}>Hardware</h2>
        <span className="vh-spacer" />
        <span className="hint mono">read-only · sourced from /v1/system-info</span>
      </div>

      <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16}}>
        <HwCard title="Host" eyebrow="machine">
          <HwRow k="hostname" v={H.name} />
          <HwRow k="kernel" v="Linux 6.17.13-11-pve" />
          <HwRow k="distro" v="Debian 13 (trixie)" />
          <HwRow k="uptime" v={H.uptime} />
          <HwRow k="boot id" v="b3f1a9e2-…-4c81" mono />
        </HwCard>

        <HwCard title="CPU" eyebrow="x86-64">
          <HwRow k="model" v={H.cpu} />
          <HwRow k="cores" v={`${H.cores}`} />
          <HwRow k="clock" v="3.0 GHz base · 5.1 GHz boost" />
          <HwRow k="cache" v="L3 · 64 MB" />
          <HwRow k="recommended" v={<span className="chip ok">llamacpp:cpu</span>} />
        </HwCard>

        <HwCard title="GPU" eyebrow="iGPU · unified memory" full>
          <HwRow k="device" v="AMD Radeon Graphics (gfx1151, Strix Halo)" />
          <HwRow k="vendor stack" v={<>ROCm <span style={{color: "var(--ok)"}}>6.4 ✓</span> · Vulkan <span style={{color: "var(--ok)"}}>present</span></>} />
          <HwRow k="vram model" v="unified · shares system RAM (128 GB)" />
          <HwRow k="recommended" v={<><span className="chip ok">llamacpp:rocm</span> <span className="chip ok">sdcpp:rocm</span></>} />
          <HwRow k="fallback" v={<span className="chip">llamacpp:vulkan</span>} sub="if ROCm fails to load a model" />
        </HwCard>

        <HwCard title="NPU" eyebrow="XDNA2 · coresident trio" full purple>
          <HwRow k="device" v="AMDXDNA2" />
          <HwRow k="topology" v={`${H.npu.columns} columns · ${H.npu.ctx} hardware context`} />
          <HwRow k="runtime" v={<><b>FLM v0.9.42</b> · trio mode (--asr 1 --embed 1)</>} />
          <HwRow k="currently loaded" v="gemma3:1b · whisper-v3-turbo · embed-gemma-300m" mono />
          <HwRow k="recommended" v={<span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.06)"}}>flm:npu</span>} />
        </HwCard>

        <HwCard title="Memory" eyebrow="unified" full>
          <HwRow k="total" v={<><span className="num">{H.ram.total}</span> GB</>} />
          <HwRow k="used" v={<><span className="num">{H.ram.used}</span> GB · 3 models loaded</>} />
          <HwRow k="free" v={<><span className="num" style={{color: "var(--ok)"}}>{H.ram.free}</span> GB</>} />
          <HwRow k="per-type budget" v="4 loaded models" />
          <div style={{padding: "10px 18px", borderTop: "1px solid var(--line-soft)"}}>
            <div style={{display: "flex", height: 6, borderRadius: 1, overflow: "hidden", background: "var(--bg-3)"}}>
              <div style={{width: `${(18.8 / H.ram.total) * 100}%`, background: "var(--dev-rocm)"}} />
              <div style={{width: `${(1.0 / H.ram.total) * 100}%`, background: "var(--dev-npu)"}} />
              <div style={{width: `${(0.35 / H.ram.total) * 100}%`, background: "var(--dev-rocm)", opacity: 0.6}} />
              <div style={{width: `${(0.4 / H.ram.total) * 100}%`, background: "var(--dev-cpu)"}} />
              <div style={{width: `${(33.45 / H.ram.total) * 100}%`, background: "var(--bg-4)"}} />
            </div>
            <div className="mono" style={{display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--fg-4)", marginTop: 6}}>
              <span>primary · agent · embed · tts · free</span>
              <span>{H.ram.used} / {H.ram.total} GB</span>
            </div>
          </div>
        </HwCard>

      </div>
    </div>
  );
}

function HardwareMemorySection() {
  return (
    <section className="hardware-mem">
      <MemoryMap variant="expanded" />
    </section>
  );
}

function HwCard({ title, eyebrow, children, full, purple }) {
  return (
    <div className="card" style={{
      gridColumn: full ? "span 2" : "auto",
      overflow: "hidden",
      ...(purple ? { borderColor: "rgba(200, 150, 255, 0.25)" } : {})
    }}>
      <div style={{padding: "14px 18px", borderBottom: "1px solid var(--line-soft)", background: "var(--bg)"}}>
        <div className="mono" style={{fontSize: 10, color: purple ? "var(--dev-npu)" : "var(--accent)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 4}}>{eyebrow}</div>
        <div className="mono" style={{fontSize: 16, fontWeight: 500, letterSpacing: "-0.02em"}}>{title}</div>
      </div>
      {children}
    </div>
  );
}

function HwRow({ k, v, mono, sub }) {
  return (
    <div style={{padding: "10px 18px", borderBottom: "1px solid var(--line-soft)", display: "grid", gridTemplateColumns: "180px 1fr", gap: 14}}>
      <div style={{fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)", textTransform: "lowercase", letterSpacing: "0.02em"}}>
        {k}
        {sub && <div style={{color: "var(--fg-5)", fontSize: 10, marginTop: 2}}>{sub}</div>}
      </div>
      <div className={mono ? "mono" : ""} style={{fontSize: 12.5, color: "var(--fg)"}}>{v}</div>
    </div>
  );
}

// ─── Dashboard view shell ───
//
// /dashboard is the system-overview page — hardware spread + slot snapshot
// + memory map + throughput + health. When /api/slots returns an empty
// list (fresh install, no bundle picked), we render a zero-slots empty
// state pointing at FirstRun instead.
function DashboardView({ slots: slotsProp, onGo, showHero, onDismissHero }) {
  const slotsQuery = useSlots();
  const slots = (slotsQuery.data && slotsQuery.data.length > 0) ? slotsQuery.data : slotsProp;
  const lemond = useLemondRollup();
  // Real zero-slots detection: only when /api/slots has resolved to a
  // confirmed empty array. Still-loading (undefined) keeps showing the
  // normal dashboard against fixtures.
  const noSlotsConfigured = Array.isArray(slotsQuery.data) && slotsQuery.data.length === 0;
  if (noSlotsConfigured) {
    return (
      <div className="view">
        <div className="dash-empty">
          <div className="dash-empty-glyph"><Wordmark size={56} /></div>
          <h1 className="mono">No models configured yet</h1>
          <p>hal0 is ready, but no slot has a model loaded. Pick a bundle to get going, or configure slots one at a time.</p>
          <div className="dash-empty-meta mono">
            <span><span style={{color: "var(--fg-3)"}}>host</span> {HAL0_DATA.host.name}</span>
            <span style={{color: "var(--fg-5)"}}>·</span>
            <span><span style={{color: "var(--fg-3)"}}>ram</span> {HAL0_DATA.host.ram.total} GB</span>
            <span style={{color: "var(--fg-5)"}}>·</span>
            <span><span style={{color: "var(--fg-3)"}}>npu</span> ready</span>
          </div>
          <div className="dash-empty-cta">
            <button className="btn lg" onClick={() => window.location.hash = "#firstrun"}>Pick a bundle</button>
            <button className="btn ghost lg" onClick={() => onGo("slots")}>Configure slots</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="view">
      {showHero && (
        <div className="hero-strip" style={{marginBottom: 16}}>
          <div className="greet">
            <span className="dim">Welcome back, </span>
            <b>halo</b>
            <span className="dim">. system steady on </span>
            <span className="mono" style={{color: "var(--fg-2)"}}>{HAL0_DATA.host.name}</span>
          </div>
          <div className="spacer" />
          <span className="mono" style={{fontSize: 10, color: "var(--fg-4)"}}>steady · {slots.filter(s => s.state !== "empty").length} slots up · lemond {lemond.status}</span>
          <span className="close" onClick={onDismissHero} role="button" aria-label="Dismiss hero">{Icons.close}</span>
        </div>
      )}

      <div className="dash">
        <div className="dash-main">
          <HardwareSection />
          <HardwareMemorySection />
        </div>
        <div className="dash-side">
          <SnapshotStrip slots={slots} onGo={onGo} />
          <MemoryMap variant="sidebar" />
          <ThroughputCard />
          <HealthCard />
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { DashboardView, SnapshotStrip, HealthCard, ThroughputCard, HardwareSection, HardwareMemorySection });
