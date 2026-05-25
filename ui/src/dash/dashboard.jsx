// hal0 dashboard — Dashboard view (snapshot, hero, side cards)
//
// Phase B1: SnapshotStrip drives off `useSlots()`.
// Phase B2 (#200, fix/chat-surface-functional): the chat surface
// (Composer, ChatActive, ChatEmpty, PersonaPicker) moved to chat.jsx so
// real `/v1/chat/completions` wiring lives in its own file. This file
// owns the snapshot strip + memory map + throughput card + health card +
// the DashboardView shell that composes everything together.
//
// `ChatActive` / `ChatEmpty` are still referenced from JSX below; they
// arrive on `window` via chat.jsx's `Object.assign` (same window-globals
// pattern every dash/*.jsx module uses).

import { useSlots } from '@/api/hooks/useSlots'
import { useLemondRollup } from '@/api/hooks/useLemonade'
import { useHardware } from '@/api/hooks/useHardware'

const { useState: useStateD, useRef: useRefD, useEffect: useEffectD } = React;

// ─── Snapshot strip ───
function SnapshotStrip({ slots, onGo }) {
  return (
    <div className="snap">
      <div className="snap-head">
        <span>Slot snapshot</span>
        <span className="ct mono">{slots.filter(s => s.state === "ready" || s.state === "serving" || s.state === "idle").length}/{slots.length} ready</span>
        <span className="right mono" onClick={() => onGo("slots")}>Manage slots →</span>
      </div>
      <div className="snap-rows">
        {slots.map(s => (
          <div key={s.name} className="snap-row" onClick={() => onGo("slots/" + s.name)}>
            <span className={"dot " + s.state} />
            <span className="name mono">{s.name}</span>
            <span className="model mono">{s.model}</span>
            <span className={"chip dev-" + (s.device || "cpu").replace("gpu-", "")}>{s.device}</span>
            <span className="badge">
              {s.isDefault && <span className="chip outlined amber">default</span>}
              {s.coresident && <span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.06)"}}>coresident</span>}
              {s.cpuOnly && <span className="chip">[CPU]</span>}
            </span>
            <span className="num mono" style={{color: "var(--fg-3)", fontSize: 11, textAlign: "right"}}>
              {s.state === "serving" ? `${s.metrics.toks} tok/s` : s.state}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}


// ─── Memory / health side cards ───
function MemoryMap({ slots }) {
  // Live OS-level memory from /api/hardware (used / total). Per-slot
  // segments stay informational — they sum the bookkeeping each slot
  // reports in `metrics.mem`. The bar then splits into:
  //   [per-slot segments] + [other used] + [free]
  // so "used" tracks reality (e.g. shows the few GB the OS itself eats
  // when zero slots are loaded) rather than the static 128 GB the
  // HAL0_DATA fixture used to render.
  const hw = useHardware();
  const ram = hw.data?.ram;
  const fallbackTotal = HAL0_DATA.host.ram.total;
  const total = ram && ram.total > 0 ? ram.total : fallbackTotal;
  const loaded = slots.filter(s => s.state === "ready" || s.state === "serving" || s.state === "idle");
  const segs = loaded.map(s => ({ name: s.name, sz: s.metrics.mem || 0, color: s.device }));
  const slotsUsed = segs.reduce((a, s) => a + s.sz, 0);
  // Prefer the OS reading; fall back to the slot sum until /api/hardware
  // has resolved (matches the H = hwQuery.data || HAL0_DATA.host pattern
  // used by HardwareView in extras.jsx).
  const used = ram ? ram.used : slotsUsed;
  const free = Math.max(0, total - used);
  const otherUsed = Math.max(0, used - slotsUsed);
  const pct = n => total > 0 ? `${(n / total) * 100}%` : '0%';
  const colorFor = d => d === "npu" ? "var(--dev-npu)" : d === "cpu" ? "var(--dev-cpu)" : d === "gpu-vulkan" ? "var(--dev-vulkan)" : "var(--dev-rocm)";
  return (
    <div className="side-card">
      <div className="side-card-h">
        <span>Memory map</span>
        <span className="right mono">{used.toFixed(1)} / {total.toFixed(0)} GB</span>
      </div>
      <div className="side-card-b">
        <div className="memmap">
          <div className="memmap-h mono">
            <span>unified ram</span>
            <span><b>{free.toFixed(1)} GB</b> free</span>
          </div>
          <div className="memmap-bar">
            {segs.map((s, i) => (
              <i key={i} style={{ width: pct(s.sz), background: colorFor(s.color) }} />
            ))}
            {otherUsed > 0 && (
              <i style={{ width: pct(otherUsed), background: "var(--fg-5)" }} />
            )}
            <i style={{ width: pct(free), background: "var(--bg-4)" }} />
          </div>
          <div className="memmap-legend">
            {segs.map((s, i) => (
              <div key={i} className="ln mono">
                <span className="sw" style={{background: colorFor(s.color)}} />
                <span className="name">{s.name}</span>
                <span className="sz">{s.sz < 1 ? `${(s.sz * 1024).toFixed(0)} MB` : `${s.sz.toFixed(1)} GB`}</span>
              </div>
            ))}
            {otherUsed > 0 && (
              <div className="ln mono">
                <span className="sw" style={{background: "var(--fg-5)"}} />
                <span className="name">other</span>
                <span className="sz">{otherUsed.toFixed(1)} GB</span>
              </div>
            )}
            <div className="ln mono">
              <span className="sw" style={{background: "var(--bg-4)"}} />
              <span className="name">free</span>
              <span className="sz">{free.toFixed(1)} GB</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

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
  // When no sample has been observed yet, headline renders "—" and
  // the spark is empty per the dashboard's "no data" convention.
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

// ─── Dashboard view shell ───
function DashboardView({ chatState, setChatState, slots: slotsProp, persona, setPersona, onGo, showHero, onDismissHero, personaPlacement, composerState }) {
  // Phase B1: live slot list; fall back to the prop (HAL0_DATA.slots
  // from main.jsx) until /api/slots returns. Keeps the surface usable
  // on first paint and in mock dev.
  const slotsQuery = useSlots();
  const slots = (slotsQuery.data && slotsQuery.data.length > 0) ? slotsQuery.data : slotsProp;
  // Lemond rollup so the hero strip / chip read live state instead of
  // the static HAL0_DATA.lemond fixture.
  const lemond = useLemondRollup();
  // Skip-path: no slots configured → render empty hero, no chat surface
  if (chatState === "skip") {
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
            <span className="dim">. <span className="mono" style={{color: "var(--fg-2)"}}>{persona}</span> is your active persona</span>
            <span className="dim"> · last message <span className="mono">14:02:22</span></span>
          </div>
          <div className="spacer" />
          <span className="mono" style={{fontSize: 10, color: "var(--fg-4)"}}>steady · {slots.filter(s => s.state !== "empty").length} slots up · lemond {lemond.status}</span>
          <span className="close" onClick={onDismissHero} role="button" aria-label="Dismiss hero">{Icons.close}</span>
        </div>
      )}

      <div className="vh" style={{marginTop: showHero ? 4 : 0, marginBottom: 16, display: "flex", gap: 12, alignItems: "center"}}>
        <div className="mono" style={{display: "inline-flex", border: "1px solid var(--line)", borderRadius: "var(--rad-sm)", overflow: "hidden", fontSize: 11}}>
          <button
            onClick={() => setChatState("empty")}
            style={{padding: "5px 11px", background: chatState === "empty" ? "var(--accent-soft)" : "transparent", color: chatState === "empty" ? "var(--accent)" : "var(--fg-3)", border: "none", borderRight: "1px solid var(--line)", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}
          >empty composer</button>
          <button
            onClick={() => setChatState("active")}
            style={{padding: "5px 11px", background: chatState === "active" ? "var(--accent-soft)" : "transparent", color: chatState === "active" ? "var(--accent)" : "var(--fg-3)", border: "none", borderRight: "1px solid var(--line)", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}
          >active conversation</button>
          <button
            onClick={() => setChatState("skip")}
            style={{padding: "5px 11px", background: chatState === "skip" ? "var(--accent-soft)" : "transparent", color: chatState === "skip" ? "var(--accent)" : "var(--fg-3)", border: "none", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}
          >skip-path empty</button>
        </div>
        <span className="mono" style={{fontSize: 10, color: "var(--fg-4)"}}>← chat surface state · both ship</span>
      </div>

      <div className="dash">
        <div className="dash-main">
          {chatState === "empty"
            ? <ChatEmpty slots={slots} persona={persona} onPersona={setPersona} placement={personaPlacement} composerState={composerState} />
            : <ChatActive slots={slots} persona={persona} onPersona={setPersona} placement={personaPlacement} composerState={composerState} />}
        </div>
        <div className="dash-side">
          <SnapshotStrip slots={slots} onGo={onGo} />
          <MemoryMap slots={slots} />
          <ThroughputCard />
          <HealthCard />
        </div>
      </div>
    </div>
  );
}

// ChatActive/ChatEmpty/Composer/PersonaPicker live in chat.jsx and install
// themselves on window via their own Object.assign — DashboardView's JSX
// references them as globals just like every other dash module.
Object.assign(window, { DashboardView, SnapshotStrip, MemoryMap, HealthCard, ThroughputCard });
