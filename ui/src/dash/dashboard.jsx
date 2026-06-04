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
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { MemoryMap, useMemoryMapModel } from './memory-map'

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
//
// Health rows are driven by real hooks only — no HAL0_DATA seeds. Each
// service that lacks a live data source has been removed rather than
// rendered with a fabricated "up · vX" line. Today the only wired signal
// is lemond (via useLemondRollup); add real rows here as hooks land.
function HealthCard() {
  const lemond = useLemondRollup();
  const upCls = lemond.status === "up" ? "up" : lemond.status === "down" ? "down" : "";
  const verb = lemond.status === "up" ? "up" : lemond.status === "down" ? "down" : "connecting";
  return (
    <div className="side-card">
      <div className="side-card-h">
        <span>Health</span>
      </div>
      <div className="side-card-b" style={{paddingTop: 4, paddingBottom: 4}}>
        <div className="health-row">
          <span className="k">lemond</span>
          <span className={"v " + upCls}>
            <span className="dot" />
            {verb}{lemond.status === "up" && lemond.version !== "—" ? ` · ${lemond.version}` : ""}
          </span>
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
//
// All five cards read LIVE data: the static probe (useHardware →
// /api/hardware) drives host/cpu/gpu/npu identity; the live counters
// (useStatsHardware → /api/stats/hardware, 2.5s) + per-slot attribution
// (useMemoryMapModel) drive the Memory card. No hardcoded versions,
// kernel strings, "currently loaded" trios, or fixed bar widths remain —
// fields a probe can't source render as "—" rather than a fabricated
// value.

// Render "—" for empty / missing fields rather than a blank cell, so a
// gap reads as "not reported" instead of a broken layout.
function val(v) {
  if (v == null) return "—";
  if (typeof v === "string") return v.trim() === "" ? "—" : v;
  if (typeof v === "number") return v === 0 ? "—" : v;
  return v;
}

// Device → segment colour, mirroring memory-map.jsx's file-local map so
// the Memory card's per-slot bar matches the (sidebar) memory map.
const DEVICE_COLOR = {
  npu: "var(--dev-npu)",
  cpu: "var(--dev-cpu)",
  vulkan: "var(--dev-vulkan)",
  rocm: "var(--dev-rocm)",
};
function deviceColor(d) {
  return DEVICE_COLOR[d] || "var(--dev-rocm)";
}

function HardwareSection() {
  const hwQuery = useHardware();
  const H = hwQuery.data;
  const stats = useStatsHardware();
  const mem = useMemoryMapModel();
  const slotsQuery = useSlots();
  const liveSlots = (slotsQuery.data || []).filter(
    (s) => ["ready", "serving", "idle", "warming"].includes((s.state || "").toLowerCase()),
  );

  // Loaded-model count comes from live slots, not a hardcoded "3".
  const loadedCount = liveSlots.length;

  // NPU "currently loaded" = live model ids on NPU-device slots. Empty
  // when nothing is loaded (the common idle state) — no static trio.
  const npuModels = liveSlots
    .filter((s) => (s.device || "").toLowerCase() === "npu")
    .map((s) => s.model)
    .filter(Boolean);

  // Memory: prefer live stats (2.5s) for system RAM; fall back to the
  // probe snapshot. Pool total + per-slot model attribution come from
  // the shared memory-map model so this card and the map never disagree.
  const round1 = (n) => Math.round(n * 10) / 10;
  const mbToGb = (mb) => round1((mb || 0) / 1024);
  // Total = system RAM (what the box reports it has), not the GTT pool —
  // matches the "system RAM" framing of the card. mem.pool is the GPU
  // GTT ceiling, which is a different (and confusingly larger) number.
  const ramTotalGb = (H ? H.ram.total : 0) || mem.pool.totalGb;
  const ramUsedGb = stats.data?.ram_used_mb != null ? mbToGb(stats.data.ram_used_mb) : (H ? H.ram.used : 0);
  const ramFreeGb = stats.data?.ram_available_mb != null
    ? mbToGb(stats.data.ram_available_mb)
    : (H ? H.ram.free : Math.max(0, round1(ramTotalGb - ramUsedGb)));
  const modelUsedGb = mem.self.modelUsedGb || 0;
  const memSlots = (mem.self.slots || []).filter((s) => s.bytesGb > 0);

  // GPU vendor-stack chips reflect the PROBE's capability flags, not a
  // baked-in "ROCm 6.4 ✓". Vulkan is the bundled default backend.
  const stackChips = H ? (
    <>
      <span className={"chip " + (H.computeCapable ? "ok" : "")}>
        ROCm {H.computeCapable ? "✓" : "—"}
      </span>{" "}
      <span className={"chip " + (H.vulkanCapable ? "ok" : "")}>
        Vulkan {H.vulkanCapable ? "✓" : "—"}
      </span>
    </>
  ) : "—";
  const gpuRecommend = H && H.vulkanCapable
    ? <span className="chip ok">llamacpp:vulkan</span>
    : H && H.computeCapable
      ? <span className="chip ok">llamacpp:rocm</span>
      : <span className="chip">llamacpp:cpu</span>;

  return (
    <div className="hw-section">
      <div className="vh" style={{marginBottom: 12}}>
        <span className="vh-eye mono">System</span>
        <h2 style={{margin: 0, fontSize: 18, fontWeight: 500, letterSpacing: "-0.02em"}}>Hardware</h2>
        <span className="vh-spacer" />
        <span className="hint mono">read-only · live from /api/hardware + /api/stats/hardware</span>
      </div>

      <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16}}>
        <HwCard title="Host" eyebrow="machine">
          <HwRow k="hostname" v={val(H?.name)} />
          <HwRow k="platform" v={val(H?.platformLabel)} />
          <HwRow k="kernel" v={val(H?.kernel)} mono />
          <HwRow k="distro" v={val(H?.distro)} />
          <HwRow k="uptime" v={val(H?.uptime)} />
        </HwCard>

        <HwCard title="CPU" eyebrow="processor">
          <HwRow k="model" v={val(H?.cpu)} />
          <HwRow k="cores" v={val(H?.cores)} />
          <HwRow k="vendor" v={val(H?.gpuVendor ? H.gpuVendor.toUpperCase() : "")} />
        </HwCard>

        <HwCard title="GPU" eyebrow="iGPU · unified memory" full>
          <HwRow k="device" v={val(H?.gpu)} />
          <HwRow k="vendor stack" v={stackChips} />
          <HwRow
            k="vram model"
            v={H?.gttTotalMb ? <>unified · GTT pool {mbToGb(H.gttTotalMb)} GB</> : "unified · shares system RAM"}
          />
          <HwRow k="recommended" v={gpuRecommend} />
        </HwCard>

        <HwCard title="NPU" eyebrow="XDNA" full purple>
          <HwRow k="present" v={H ? (H.npu.present ? "yes" : "no") : "—"} />
          <HwRow k="device" v={val(H?.npu.name)} />
          <HwRow k="driver" v={val(H?.npu.driver)} mono />
          {(H?.npu.columns || H?.npu.ctx) ? (
            <HwRow k="topology" v={`${H.npu.columns} columns · ${H.npu.ctx} hardware context`} />
          ) : null}
          <HwRow
            k="currently loaded"
            v={npuModels.length ? npuModels.join(" · ") : "none loaded"}
            mono
          />
        </HwCard>

        <HwCard title="Memory" eyebrow="unified" full>
          <HwRow k="pool total" v={<><span className="num">{val(round1(ramTotalGb))}</span> GB</>} />
          <HwRow
            k="system RAM"
            v={<><span className="num">{round1(ramUsedGb)}</span> GB used · <span className="num" style={{color: "var(--ok)"}}>{round1(ramFreeGb)}</span> GB free</>}
          />
          <HwRow
            k="model memory"
            v={<><span className="num">{round1(modelUsedGb)}</span> GB · {loadedCount} {loadedCount === 1 ? "model" : "models"} loaded</>}
          />
          <div style={{padding: "10px 18px", borderTop: "1px solid var(--line-soft)"}}>
            <div style={{display: "flex", height: 6, borderRadius: 1, overflow: "hidden", background: "var(--bg-3)"}}>
              {memSlots.map((s) => (
                <div
                  key={s.name}
                  title={`${s.name} · ${round1(s.bytesGb)} GB`}
                  style={{width: `${(s.bytesGb / (ramTotalGb || 1)) * 100}%`, background: deviceColor(s.device)}}
                />
              ))}
              <div style={{width: `${Math.max(0, 100 - (modelUsedGb / (ramTotalGb || 1)) * 100)}%`, background: "var(--bg-4)"}} />
            </div>
            <div className="mono" style={{display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--fg-4)", marginTop: 6}}>
              <span>{memSlots.length ? memSlots.map((s) => s.name).join(" · ") + " · free" : "no models loaded"}</span>
              <span>{round1(modelUsedGb)} / {round1(ramTotalGb)} GB</span>
            </div>
          </div>
        </HwCard>

      </div>
    </div>
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
            <HardwareSection />
          </div>
          <div className="dash-side">
            <div className="snap" aria-busy="true">
              <div className="snap-head">
                <span>Slot snapshot</span>
                <span className="ct mono dim">loading…</span>
              </div>
            </div>
            <MemoryMap variant="sidebar" />
            <ThroughputCard />
            <HealthCard />
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
          <HardwareSection />
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

Object.assign(window, { DashboardView, SnapshotStrip, HealthCard, ThroughputCard, HardwareSection });
