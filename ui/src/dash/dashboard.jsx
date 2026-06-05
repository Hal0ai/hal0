// hal0 dashboard — Dashboard view (system overview)
//
// Phase B1: SnapshotStrip drives off `useSlots()`.
// Phase B2 (#200, fix/chat-surface-functional): the chat surface
// (Composer, ChatActive, ChatEmpty, PersonaPicker) moved to chat.jsx so
// real `/v1/chat/completions` wiring lives in its own file.
// Chat-page-overhaul: chat moved to its own `#chat` route.
// Home-redesign (2026-06-05): the verbose Hardware spread (Host/CPU/GPU/
// NPU/Memory cards) was demoted from the main area into a single
// condensed `SystemCard` in the sidebar (it also absorbs the old
// standalone HealthCard). The main area now leads with the live,
// actionable surface: a 50/50 Memory-map | Throughput row above the
// full-width slot snapshot.

import { useSlots } from '@/api/hooks/useSlots'
import { useLemondRollup } from '@/api/hooks/useLemonade'
import { useHardware } from '@/api/hooks/useHardware'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
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


// ─── System side card ───
//
// Condensed identity block: the five Hardware cards (Host/CPU/GPU/NPU/
// Memory) collapsed to one glanceable line each, plus a single live RAM
// glance and the folded-in lemond health row. All fields are LIVE
// (useHardware static probe + useStatsHardware live counters +
// useLemondRollup) — a field a probe can't source renders as "—" rather
// than a fabricated value, matching the old HardwareSection contract.
const round1 = (n) => Math.round(n * 10) / 10;
const mbToGb = (mb) => round1((mb || 0) / 1024);

// "—" for empty / missing rather than a blank cell.
function sval(v) {
  if (v == null) return "—";
  if (typeof v === "string") return v.trim() === "" ? "—" : v;
  return v;
}
// Join non-empty parts with " · " (e.g. distro · kernel).
function joinDot(...parts) {
  const out = parts.map((p) => (p == null ? "" : String(p).trim())).filter(Boolean);
  return out.length ? out.join(" · ") : "—";
}

function SysRow({ k, v, mono }) {
  return (
    <div className="sys-row">
      <span className="k">{k}</span>
      <span className={"v" + (mono ? " mono" : "")}>{v}</span>
    </div>
  );
}

function SystemCard() {
  const hw = useHardware();
  const stats = useStatsHardware();
  const H = hw.data;

  const ramTotalGb = (H ? H.ram.total : 0) || mbToGb(stats.data?.ram_total_mb);
  const ramUsedGb = stats.data?.ram_used_mb != null
    ? mbToGb(stats.data.ram_used_mb)
    : (H ? H.ram.used : 0);
  const hasRam = !!(ramTotalGb || ramUsedGb);

  // Active-backend chips — only render a backend the probe says is
  // capable, so the condensed row stays clean. Colour mirrors the
  // slot-snapshot + memory-map device hues (--dev-rocm / --dev-vulkan).
  const gpuChips = H && (H.vulkanCapable || H.computeCapable) ? (
    <span className="sub">
      {H.vulkanCapable && <span className="chip dev-vulkan">Vulkan ✓</span>}
      {H.vulkanCapable && H.computeCapable ? " " : null}
      {H.computeCapable && <span className="chip dev-rocm">ROCm ✓</span>}
    </span>
  ) : null;

  const npuVal = H
    ? (H.npu.present ? joinDot(H.npu.name || "XDNA", H.npu.driver) : "absent")
    : "—";

  // lemond health row removed (2026-06-05) — runtime status now lives solely
  // in the sidebar Runtime widget; the System card is hardware identity only.
  return (
    <div className="side-card sys-card">
      <div className="side-card-h">
        <span>System</span>
        <span className="right mono">read-only · live</span>
      </div>
      <div className="side-card-b sys-card-b">
        <SysRow k="host" v={<>{sval(H?.name)}{H?.uptime ? <span className="dim"> · up {H.uptime}</span> : null}</>} />
        <SysRow k="os" v={joinDot(H?.distro, H?.kernel)} mono />
        <SysRow k="cpu" v={<>{sval(H?.cpu)}{H?.cores ? <span className="sub">{H.cores}</span> : null}</>} />
        <SysRow k="gpu" v={<>{sval(H?.gpu)}{gpuChips}</>} />
        <SysRow k="npu" v={npuVal} mono />
        <SysRow k="ram" v={hasRam ? <><span className="num">{round1(ramUsedGb)}</span> / {round1(ramTotalGb)} GB</> : "—"} />
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

// ─── Dashboard view shell ───
//
// /dashboard is the system-overview page. Main area leads with the live
// surface — a 50/50 Memory-map | Throughput row above the full-width slot
// snapshot — while the sidebar holds the condensed SystemCard (host/cpu/
// gpu/npu/ram identity + folded-in lemond health). When /api/slots returns
// an empty list (fresh install, no bundle picked), we render a zero-slots
// empty state pointing at FirstRun instead.
function DashboardView({ slots: _slotsProp, onGo, showHero, onDismissHero }) {
  const slotsQuery = useSlots();
  // Single source of truth: the hook. The `slots` prop (HAL0_DATA seed
  // from main.jsx) is intentionally ignored so no fake slots flash on
  // load — we render a loading skeleton until the query resolves, then
  // either the real list or a confirmed-empty state.
  const slots = slotsQuery.data || [];
  const lemond = useLemondRollup();
  const hw = useHardware();
  // Live host identity for the hero + empty-state (was HAL0_DATA seed).
  const hostName = hw.data?.name || HAL0_DATA.host.name;
  const slotsLoading = slotsQuery.isLoading && !slotsQuery.data;
  // Real zero-slots detection: only when /api/slots has resolved to a
  // confirmed empty array. Still-loading (undefined) shows the skeleton.
  const noSlotsConfigured = Array.isArray(slotsQuery.data) && slotsQuery.data.length === 0;

  // Loading skeleton — no stub-on-load slot seeds; wait for real data.
  if (slotsLoading) {
    return (
      <div className="view">
        <div className="dash">
          <div className="dash-main">
            <div className="dash-5050">
              <MemoryMap variant="sidebar" />
              <ThroughputCard />
            </div>
            <div className="snap" aria-busy="true">
              <div className="snap-head">
                <span>Slot snapshot</span>
                <span className="ct mono dim">loading…</span>
              </div>
            </div>
          </div>
          <div className="dash-side">
            <SystemCard />
          </div>
        </div>
      </div>
    );
  }
  if (noSlotsConfigured) {
    return (
      <div className="view">
        <div className="dash-empty">
          <div className="dash-empty-glyph"><Wordmark size={56} /></div>
          <h1 className="mono">No models configured yet</h1>
          <p>hal0 is ready, but no slot has a model loaded. Pick a bundle to get going, or configure slots one at a time.</p>
          <div className="dash-empty-meta mono">
            <span><span style={{color: "var(--fg-3)"}}>host</span> {hostName}</span>
            <span style={{color: "var(--fg-5)"}}>·</span>
            <span><span style={{color: "var(--fg-3)"}}>ram</span> {hw.data?.ram.total || HAL0_DATA.host.ram.total} GB</span>
            {hw.data?.npu.present && <><span style={{color: "var(--fg-5)"}}>·</span><span><span style={{color: "var(--fg-3)"}}>npu</span> ready</span></>}
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
            <span className="mono" style={{color: "var(--fg-2)"}}>{hostName}</span>
          </div>
          <div className="spacer" />
          <span className="mono" style={{fontSize: 10, color: "var(--fg-4)"}}>steady · {slots.filter(s => s.state !== "empty").length} slots up · lemond {lemond.status}</span>
          <span className="close" onClick={onDismissHero} role="button" aria-label="Dismiss hero">{Icons.close}</span>
        </div>
      )}

      <div className="dash">
        <div className="dash-main">
          <div className="dash-5050">
            <MemoryMap variant="sidebar" />
            <ThroughputCard />
          </div>
          <SnapshotStrip slots={slots} onGo={onGo} />
        </div>
        <div className="dash-side">
          <SystemCard />
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { DashboardView, SnapshotStrip, SystemCard, ThroughputCard });
