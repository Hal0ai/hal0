// hal0 dashboard — Slots view (SlotCard, NPU trio variants, group sections)
//
// Phase B1 → slots wireup: live slot list + per-slot lifecycle mutations
// via the typed `useSlots` family. The `slots` prop (HAL0_DATA fallback)
// is no longer consulted — the hook is the single source of truth.
// Mock-mode coverage is provided by the Playwright `apiMock` fixture
// which fulfils /api/slots with HAL0_DATA-shaped JSON.

import {
  useSlots,
  useSlotRestart,
  useSlotUnload,
  useSlotSwap,
} from '@/api/hooks/useSlots'

const { useState: useStateS } = React;

// ─── Slot indicator dot ────────────────────────────────────────────────
//
// Maps a slot snapshot → ({ cls, label, tooltip }) for the status dot
// rendered in SlotCard / SlotListRow. Single source of truth for the
// "what colour does this slot deserve" question.
//
// Mapping (matches PR feat/slot-state-indicator-dots):
//   ready + last_used_at within RECENTLY_LIVE_MS → "recent" (green)
//   ready + older / never used                   → "stale"  (yellow)
//   warming / starting / pulling / unloading     → "warming" (amber, pulses)
//   serving                                      → "serving" (cyan, pulses — pre-existing)
//   idle                                         → "stale"  (yellow — semantically "loaded, not serving")
//   error                                        → "error"  (red)
//   offline / anything else                      → "offline" (grey)
//
// The "1h recently live" window leans on the backend's in-memory
// `last_used_at` (bumped by SlotManager.serving on every dispatched
// request). When hal0-api restarts, `last_used_at` is null for every
// slot until the first new request lands — we render that as "stale"
// (yellow), which matches operator intuition: we don't know whether
// the slot was hit during downtime.
const RECENTLY_LIVE_MS = 60 * 60 * 1000; // 1h window — kept as a named const

function _formatAgo(deltaMs) {
  if (deltaMs < 0) return "just now";
  const s = Math.floor(deltaMs / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function slotIndicator(slot, now = Date.now()) {
  const state = String(slot?.state || "offline");
  const lastUsedSec = typeof slot?.last_used_at === "number" ? slot.last_used_at : null;
  const lastUsedMs = lastUsedSec != null ? lastUsedSec * 1000 : null;
  const deltaMs = lastUsedMs != null ? now - lastUsedMs : null;
  const errorMsg = slot?.metadata?.message || slot?.message || "";
  const model = slot?.model || slot?.model_id || slot?.model_default || "";

  if (state === "error") {
    return {
      cls: "error",
      label: "error",
      tooltip: errorMsg ? `Error: ${errorMsg}` : "Error",
    };
  }
  if (
    state === "warming" ||
    state === "starting" ||
    state === "pulling" ||
    state === "unloading"
  ) {
    const verb =
      state === "pulling" ? "Pulling"
        : state === "unloading" ? "Unloading"
          : "Warming up";
    return {
      cls: "warming",
      label: state,
      tooltip: model ? `${verb} ${model}…` : `${verb}…`,
    };
  }
  if (state === "serving") {
    // Pre-existing serving dot behaviour: cyan + pulse (CSS unchanged).
    return {
      cls: "serving",
      label: "serving",
      tooltip: model ? `Serving ${model}` : "Serving",
    };
  }
  if (state === "ready") {
    if (deltaMs != null && deltaMs <= RECENTLY_LIVE_MS) {
      return {
        cls: "recent",
        label: "ready",
        tooltip: `Loaded, last used ${_formatAgo(deltaMs)}`,
      };
    }
    return {
      cls: "stale",
      label: "ready",
      tooltip: deltaMs != null
        ? `Loaded, idle (${_formatAgo(deltaMs)})`
        : "Loaded — no requests since hal0-api started",
    };
  }
  if (state === "idle") {
    return {
      cls: "stale",
      label: "idle",
      tooltip: deltaMs != null
        ? `Idle (${_formatAgo(deltaMs)})`
        : "Idle",
    };
  }
  // offline + anything unknown
  return {
    cls: "offline",
    label: state,
    tooltip: state === "offline" ? "Offline" : `State: ${state}`,
  };
}

function IndicatorDot({ slot }) {
  const ind = slotIndicator(slot);
  return <span className={"dot " + ind.cls} title={ind.tooltip} />;
}

// Expose for window-scope JSX (legacy pattern in this codebase) + tests.
if (typeof window !== "undefined") {
  Object.assign(window, { slotIndicator, IndicatorDot, RECENTLY_LIVE_MS });
}

// ─── Mini sparkline for slot card ───
function Spark({ data, height = 18 }) {
  if (!data || data.length === 0) return null;
  const max = Math.max(...data, 1);
  return (
    <div className="spark" style={{ height }}>
      {data.map((v, i) => (
        <i key={i} style={{ height: `${Math.max((v / max) * 100, 8)}%` }} />
      ))}
    </div>
  );
}

// ─── SlotCard (instrument variant) ───
function SlotCard({
  slot,
  onSwap,
  onEdit,
  onOverflow,
  onRestart,
  onUnload,
  onSwapPick,
  onViewLogs,
  onDelete,
  swapOpen,
  onCloseSwap,
  menuOpen,
  onCloseMenu,
  errorMsg,
  busy,
}) {
  const { type, device, model, state, isDefault, coresident, cpuOnly, metrics } = slot;
  const isLlm = type === "llm";

  const metricsRow = (() => {
    if (type === "llm") return [
      { l: "tok/s",  v: metrics.toks, u: "", spark: slot.spark },
      { l: "ttft",   v: metrics.ttft ? metrics.ttft : "—", u: metrics.ttft ? "ms" : "" },
      { l: "ctx",    v: metrics.ctx, u: "" },
      { l: "kv",     v: metrics.kv === null ? "—" : metrics.kv, u: metrics.kv === null ? "" : "%", dim: metrics.kv === null },
    ];
    if (type === "embedding") return [
      { l: "req/min", v: metrics.rpm, u: "" },
      { l: "p50",     v: metrics.lat || "—", u: metrics.lat ? "ms" : "" },
      { l: "dim",     v: metrics.dim, u: "" },
      { l: "size",    v: metrics.mem * 1024 < 1000 ? (metrics.mem * 1024).toFixed(0) : metrics.mem.toFixed(1), u: metrics.mem * 1024 < 1000 ? "MB" : "GB" },
    ];
    if (type === "reranking") return [
      { l: "req/min", v: metrics.rpm, u: "" },
      { l: "p50",     v: metrics.lat, u: "ms" },
      { l: "max/req", v: metrics.maxDocs, u: "" },
      { l: "size",    v: (metrics.mem * 1024).toFixed(0), u: "MB" },
    ];
    if (type === "transcription") return [
      { l: "req/min", v: metrics.rpm, u: "" },
      { l: "xrt",     v: metrics.xrt, u: "" },
      { l: "prec",    v: metrics.precision, u: "" },
      { l: "size",    v: (metrics.mem * 1024).toFixed(0), u: "MB" },
    ];
    if (type === "tts") return [
      { l: "req/min", v: metrics.rpm, u: "" },
      { l: "sec/min", v: metrics.secs, u: "" },
      { l: "voice",   v: metrics.voice, u: "" },
      { l: "size",    v: (metrics.mem * 1024).toFixed(0), u: "MB" },
    ];
    if (type === "image") return [
      { l: "req/min", v: metrics.rpm, u: "" },
      { l: "avg",     v: metrics.avg, u: "s" },
      { l: "res",     v: metrics.res, u: "" },
      { l: "size",    v: metrics.mem.toFixed(1), u: "GB" },
    ];
    return [];
  })();

  return (
    <div className={"slot" + (state === "serving" ? " serving" : "")}>
      <div className="slot-h">
        <IndicatorDot slot={slot} />
        <div className="slot-name">
          <span className="nm">{slot.name}</span>
        </div>
        <div className="right" style={{position: "relative"}}>
          {isDefault && <div className="default-badge">★ default</div>}
          {coresident && <span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.06)"}}>coresident</span>}
          <button className="more" onClick={e => { e.stopPropagation(); onOverflow && onOverflow(); }}>{Icons.more}</button>
          {menuOpen && (
            <SlotOverflowMenu
              slot={slot}
              onClose={onCloseMenu}
              onViewLogs={onViewLogs}
              onDelete={onDelete}
            />
          )}
        </div>
      </div>
      <div className="slot-model mono" onClick={onSwap} style={{position: "relative"}}>
        <span className="mid">{model}</span>
        <span className="chev">{Icons.chev}</span>
        {swapOpen && (
          <InlineSwapPopover
            slot={slot}
            open={swapOpen}
            onClose={onCloseSwap}
            onPick={onSwapPick}
          />
        )}
      </div>
      <div className="slot-chips">
        <span className="chip">{type}</span>
        <span className={"chip dev-" + (device || "cpu").replace("gpu-", "")}>{device}</span>
        {cpuOnly && <span className="chip">[CPU]</span>}
        <span className="chip" style={{color: state === "serving" ? "var(--accent)" : state === "ready" ? "var(--ok)" : state === "idle" ? "var(--fg-3)" : "var(--fg-3)"}}>
          {state}
        </span>
      </div>
      <div className="slot-metrics">
        {metricsRow.map((m, i) => (
          <div key={i} className="slot-met">
            <div className="l">{m.l}</div>
            <div className={"v mono num" + (m.dim ? " dim" : "")}>
              {m.v}<span className="u">{m.u}</span>
            </div>
            {i === 0 && isLlm && slot.spark && <Spark data={slot.spark} height={12} />}
          </div>
        ))}
      </div>
      <div className="slot-actions">
        <button
          className="btn ghost sm"
          disabled={!!busy}
          onClick={() => onRestart && onRestart()}
        >{Icons.restart} Restart</button>
        <button
          className="btn ghost sm"
          disabled={!!busy}
          onClick={() => onUnload && onUnload()}
        >{Icons.unload} Unload</button>
        <button className="btn ghost sm" onClick={onEdit}>{Icons.edit} Edit</button>
        <span className="spacer" />
      </div>
      {errorMsg && <div style={{marginTop: 4}}><ErrorSlotCardBanner slot={slot} message={errorMsg} /></div>}
    </div>
  );
}

// ─── SlotCard compact list variant ───
function SlotListRow({ slot, onEdit }) {
  const { type, device, model, state, isDefault, metrics } = slot;
  const tps = type === "llm" ? `${metrics.toks || 0} t/s` :
              type === "embedding" ? `${metrics.rpm} r/m` :
              type === "transcription" ? `${metrics.xrt} xrt` :
              type === "image" ? `${metrics.avg}s avg` :
              `${metrics.rpm || 0} r/m`;
  return (
    <div className="slot-list-row" onClick={onEdit}>
      <IndicatorDot slot={slot} />
      <span className="nm">
        {slot.name}
        {isDefault && <span className="chip outlined amber" style={{fontSize: 9, padding: "1px 4px"}}>def</span>}
      </span>
      <span className="ml">{model}</span>
      <span className="ch">
        <span className="chip">{type}</span>
        <span className={"chip dev-" + (device || "cpu").replace("gpu-", "")}>{device}</span>
      </span>
      <span className="met">
        <b>{tps}</b>
        {type === "llm" && metrics.ttft && <span>· {metrics.ttft}ms ttft</span>}
        {type === "llm" && metrics.ctx && <span>· {metrics.ctx} ctx</span>}
      </span>
      <span className="ac">
        <button className="btn ghost sm" onClick={e => { e.stopPropagation(); }}>{Icons.restart}</button>
        <button className="btn ghost sm" onClick={e => { e.stopPropagation(); }}>{Icons.edit}</button>
      </span>
    </div>
  );
}

// Helpers — pull live values off the enriched slot dicts the backend
// returns (slots.py:_lemonade_state_enrichment). Missing field → em-dash
// rather than an invented value (per brief: no fabricated metrics).
function npuTrioGroupLabel(slots) {
  for (const s of slots) {
    if (typeof s.coresident_group === "string" && s.coresident_group) {
      return s.coresident_group;
    }
  }
  return null;
}
function npuTrioBackendUrl(slots) {
  // Trio shares one process; any slot with backend_url reports it.
  for (const s of slots) {
    if (typeof s.backend_url === "string" && s.backend_url) return s.backend_url;
  }
  return null;
}
function npuTrioBackendBadge(slots) {
  for (const s of slots) {
    const b = s.backend || s.metadata?.backend || s.provider;
    if (typeof b === "string" && b) return b;
  }
  return null;
}
function chatMetric(slot, key) {
  const v = slot?.metrics?.[key];
  return v === undefined || v === null || v === "" ? "—" : v;
}

// ─── NPU trio — Block variant (default per brief) ───
function NpuBlock({ slots }) {
  const npuSlots = slots.filter(s => s.device === "npu");
  if (!npuSlots.length) return null;
  const chat = npuSlots.find(s => s.type === "llm");
  const flm = chat || npuSlots[0];
  const coresGroup = npuTrioGroupLabel(npuSlots);
  const backendUrl = npuTrioBackendUrl(npuSlots);
  const backendName = npuTrioBackendBadge(npuSlots);
  return (
    <div className="card npu-card live">
      <div className="npu-h">
        <span className="npu-glyph mono">NPU</span>
        <span className="title mono">
          FLM trio<span className="sub">one process · three roles · {chat ? chat.model : "no chat model"} active</span>
        </span>
        <div className="right">
          {coresGroup && (
            <span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.08)"}}>
              <span className="dot" style={{width: 5, height: 5, background: "currentColor", boxShadow: "0 0 6px currentColor"}} />
              {coresGroup}
            </span>
          )}
          <span className="pid mono">
            pid {flm?.pid ?? "—"} · port :{flm?.port ?? "—"}
            {backendUrl ? <> · <span title={backendUrl}>{backendUrl}</span></> : null}
          </span>
        </div>
      </div>
      <div className="npu-body">
        {npuSlots.map((s, i) => {
          const lemState = s.lemonade_state || s.state || "—";
          const isLead = s.type === "llm";
          return (
            <div key={s.name} className={"npu-subrow" + (isLead ? " lead" : "")}>
              <span className={"dot " + (i === 0 ? "ready" : "coresident")} />
              <div className="role mono">
                {s.name}
                <span className="sub">{s.type}</span>
              </div>
              <div className="model mono">
                {s.model}
                {isLead && <span className="chev">{Icons.chev}</span>}
              </div>
              <div className="met mono">
                {isLead && (
                  <span>
                    <b>{chatMetric(s, "toks")}</b> tok/s · TTFT <b>{chatMetric(s, "ttft")}</b>ms · KV <b>{chatMetric(s, "kv")}</b>%
                  </span>
                )}
                {s.type === "transcription" && (
                  <span><b>{chatMetric(s, "xrt")}</b> xrt · {s.metrics?.precision || "—"}</span>
                )}
                {s.type === "embedding" && (
                  <span>{s.metrics?.dim || "—"}-dim · {lemState}</span>
                )}
              </div>
              <div className="st">
                <span className="chip" style={{color: isLead ? "var(--ok)" : "var(--dev-npu)", borderColor: isLead ? "var(--ok-line)" : "rgba(200,150,255,0.30)", background: isLead ? "var(--ok-soft)" : "rgba(200,150,255,0.06)"}}>
                  {lemState}{isLead && s.isDefault ? " · default" : ""}
                </span>
              </div>
            </div>
          );
        })}
      </div>
      <div className="npu-foot mono">
        <span className="item">backend <b>{backendName || "—"}</b></span>
        <span style={{color: "var(--fg-5)"}}>·</span>
        <span className="item">group <b>{coresGroup || "—"}</b></span>
        <span style={{color: "var(--fg-5)"}}>·</span>
        <span className="item">disabling stt-npu/embed-npu frees a role at next FLM restart</span>
      </div>
    </div>
  );
}

// ─── NPU trio — Reactor variant ───
function NpuReactor({ slots }) {
  const npuSlots = slots.filter(s => s.device === "npu");
  if (!npuSlots.length) return null;
  const chat = npuSlots.find(s => s.type === "llm");
  const stt = npuSlots.find(s => s.type === "transcription");
  const emb = npuSlots.find(s => s.type === "embedding");
  const coresGroup = npuTrioGroupLabel(npuSlots);
  const backendUrl = npuTrioBackendUrl(npuSlots);
  const backendName = npuTrioBackendBadge(npuSlots);
  return (
    <div className="card npu-card live">
      <div className="npu-h">
        <span className="npu-glyph mono">NPU</span>
        <span className="title mono">FLM trio<span className="sub">reactor view · one process driving three roles</span></span>
        <div className="right">
          {coresGroup && (
            <span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.08)"}}>
              <span className="dot" style={{width: 5, height: 5, background: "currentColor", boxShadow: "0 0 6px currentColor"}} />
              {coresGroup}
            </span>
          )}
          <span className="pid mono">pid {chat?.pid ?? "—"}</span>
        </div>
      </div>
      <div className="npu-reactor">
        <div className="reactor-core">
          <div className="reactor-disc">
            <div className="lbl">
              backend<b>{backendName || "—"}</b>
              <div style={{marginTop: 4, color: "var(--fg-4)"}}>{backendUrl || "—"}</div>
            </div>
          </div>
          <div className="reactor-meta">group {coresGroup || "—"}</div>
        </div>
        <div className="reactor-roles">
          {chat && (
            <div className="reactor-role lead">
              <span className="dot ready" />
              <div className="lbl">
                {chat.name}
                <span className="sub">chat · llm{chat.isDefault ? " · default" : ""}</span>
              </div>
              <div className="md">{chat.model}</div>
              <div className="met">
                <div><b style={{color: "var(--fg)"}}>{chatMetric(chat, "toks")}</b> tok/s</div>
                <div style={{color: "var(--fg-4)"}}>KV {chatMetric(chat, "kv")}%</div>
              </div>
            </div>
          )}
          {stt && (
            <div className="reactor-role">
              <span className="dot coresident" />
              <div className="lbl">
                {stt.name}
                <span className="sub">transcription · passenger</span>
              </div>
              <div className="md">{stt.model}</div>
              <div className="met">
                <div><b style={{color: "var(--fg)"}}>{chatMetric(stt, "xrt")}</b> xrt</div>
                <div style={{color: "var(--fg-4)"}}>{stt.metrics?.precision || "—"}</div>
              </div>
            </div>
          )}
          {emb && (
            <div className="reactor-role">
              <span className="dot coresident" />
              <div className="lbl">
                {emb.name}
                <span className="sub">embedding · passenger</span>
              </div>
              <div className="md">{emb.model}</div>
              <div className="met">
                <div><b style={{color: "var(--fg)"}}>{emb.metrics?.dim || "—"}</b> dim</div>
                <div style={{color: "var(--fg-4)"}}>{emb.lemonade_state || emb.state || "—"}</div>
              </div>
            </div>
          )}
        </div>
      </div>
      <div className="npu-foot mono">
        <span className="item">backend <b>{backendName || "—"}</b></span>
        <span style={{color: "var(--fg-5)"}}>·</span>
        <span className="item">group <b>{coresGroup || "—"}</b></span>
        <span style={{color: "var(--fg-5)"}}>·</span>
        <span className="item">pauses voice + embed on chat-model swap</span>
      </div>
    </div>
  );
}

// ─── Slots view ───
function SlotsView({ slotVariant, npuVariant, slotParam }) {
  const slotsQuery = useSlots();
  // Single source of truth: the hook. The Playwright apiMock fixture
  // fulfils /api/slots so mock-mode coverage is symmetric with live runs;
  // we no longer fall back to HAL0_DATA.slots (per slots-wireup brief).
  const slots = slotsQuery.data || [];
  const [createOpen, setCreateOpen] = useStateS(false);
  const [createDefaults, setCreateDefaults] = useStateS({});
  const [editName, setEditName] = useStateS(null);
  const [swapName, setSwapName] = useStateS(null);
  const [menuName, setMenuName] = useStateS(null);
  const [logsForSlot, setLogsForSlot] = useStateS(null);
  const [busyName, setBusyName] = useStateS(null);
  const { active: activeBanners } = useBanners();
  const skipPath = !!activeBanners["skip-path"];

  const restartMut = useSlotRestart();
  const unloadMut = useSlotUnload();
  const swapMut = useSlotSwap();

  const toast = (msg, kind = "info") =>
    window.__hal0Toast && window.__hal0Toast(msg, kind);

  const runMutation = async (name, mut, args, okMsg) => {
    setBusyName(name);
    try {
      await mut.mutateAsync(args);
      toast(okMsg, "ok");
    } catch (err) {
      toast(
        err?.message ? `${name}: ${err.message}` : `${name}: action failed`,
        "warn",
      );
    } finally {
      setBusyName(null);
    }
  };

  // Open Edit drawer when route is #slots/:name
  React.useEffect(() => {
    if (slotParam) {
      const exists = (slots || []).find(s => s.name === slotParam);
      if (exists) setEditName(slotParam);
    } else {
      setEditName(null);
    }
  }, [slotParam, slots]);

  // Listen for the N hotkey via global event (wired by main.jsx)
  React.useEffect(() => {
    const onOpen = (e) => {
      const d = (e && e.detail) || {};
      setCreateDefaults(d);
      setCreateOpen(true);
    };
    window.addEventListener("hal0:create-slot", onOpen);
    return () => window.removeEventListener("hal0:create-slot", onOpen);
  }, []);

  // Close menus on outside click
  React.useEffect(() => {
    const off = () => { setSwapName(null); setMenuName(null); };
    document.addEventListener("click", off);
    return () => document.removeEventListener("click", off);
  }, []);

  const groups = {
    chat:  slots.filter(s => s.group === "chat"),
    embed: slots.filter(s => s.group === "embed"),
    voice: slots.filter(s => s.group === "voice"),
    img:   slots.filter(s => s.group === "img"),
    npu:   slots.filter(s => s.group === "npu"),
  };

  const editSlot = (slots || []).find(s => s.name === editName);
  const logsSlot = logsForSlot
    ? (slots || []).find(s => s.name === logsForSlot)
    : null;

  // Seeded slot identities for the skip-path empty layout.
  const SEEDED = [
    { name: "primary", type: "llm",           device: "gpu-rocm", group: "chat"  },
    { name: "coder",   type: "llm",           device: "gpu-rocm", group: "chat"  },
    { name: "embed",   type: "embedding",     device: "gpu-rocm", group: "embed" },
    { name: "rerank",  type: "reranking",     device: "gpu-rocm", group: "embed" },
    { name: "stt",     type: "transcription", device: "cpu",      group: "voice" },
    { name: "tts",     type: "tts",           device: "cpu",      group: "voice" },
    { name: "img",     type: "image",         device: "gpu-rocm", group: "img"   },
  ];
  const openCreatePrefilled = (def) => { setCreateDefaults(def); setCreateOpen(true); };

  const slotWithState = (s, errorMsg) => (
    <SlotCard
      key={s.name}
      slot={s}
      errorMsg={errorMsg}
      busy={busyName === s.name}
      swapOpen={swapName === s.name}
      onSwap={(e) => { e.stopPropagation(); setSwapName(swapName === s.name ? null : s.name); setMenuName(null); }}
      onCloseSwap={() => setSwapName(null)}
      menuOpen={menuName === s.name}
      onOverflow={() => { setMenuName(menuName === s.name ? null : s.name); setSwapName(null); }}
      onCloseMenu={() => setMenuName(null)}
      onEdit={() => { window.location.hash = "#slots/" + s.name; }}
      onRestart={() =>
        runMutation(s.name, restartMut, s.name, `Restarting ${s.name}`)
      }
      onUnload={() =>
        runMutation(s.name, unloadMut, s.name, `Unloaded ${s.name}`)
      }
      onSwapPick={(m) =>
        runMutation(
          s.name,
          swapMut,
          { name: s.name, model_id: m.id },
          `Swapping ${s.name} → ${m.longName || m.id}`,
        )
      }
      onViewLogs={() => { setLogsForSlot(s.name); setMenuName(null); }}
      onDelete={() => { setEditName(s.name); setMenuName(null); }}
    />
  );

  // Skip-path layout: render six seeded empty cards under their default groups.
  if (skipPath) {
    const seededByGroup = {
      chat:  SEEDED.filter(s => s.group === "chat"),
      embed: SEEDED.filter(s => s.group === "embed"),
      voice: SEEDED.filter(s => s.group === "voice"),
      img:   SEEDED.filter(s => s.group === "img"),
    };
    return (
      <div className="view">
        <div className="vh">
          <span className="vh-eye mono">Lifecycle</span>
          <h1>Slots</h1>
          <span className="vh-spacer" />
          <span className="hint mono" style={{color: "var(--accent)"}}>skip-path · six slots seeded · none configured</span>
          <button className="btn ghost" onClick={() => window.location.hash = "#firstrun"}>Pick a bundle instead</button>
          <button className="btn" onClick={() => setCreateOpen(true)}>{Icons.plus} New slot</button>
        </div>

        {["chat", "embed", "voice", "img"].map(g => {
          const cards = seededByGroup[g];
          if (!cards.length) return null;
          return (
            <section key={g} style={{marginBottom: 24}}>
              <div className="sec">
                <h2>{g[0].toUpperCase() + g.slice(1)}<span className="ct mono">{cards.length}</span></h2>
                <div className="rule" />
              </div>
              <div className="slots-grid">
                {cards.map(c => (
                  <EmptySlotCard
                    key={c.name}
                    name={c.name}
                    type={c.type}
                    device={c.device}
                    group={c.group}
                    onConfigure={() => openCreatePrefilled({ name: c.name, type: c.type, device: c.device, group: c.group })}
                  />
                ))}
              </div>
            </section>
          );
        })}

        <CreateSlotModal
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          defaults={createDefaults}
          existingSlots={slots}
        />
      </div>
    );
  }

  const renderSlot = (s) => slotVariant === "list"
    ? <SlotListRow key={s.name} slot={s} />
    : slotVariant === "spec"
      ? <SlotCard key={s.name} slot={s} />
      : <SlotCard key={s.name} slot={s} />;

  const renderGroup = (label, items) => {
    if (!items.length) return null;
    if (slotVariant === "list") {
      return (
        <section key={label} style={{marginBottom: 18}}>
          <div className="sec">
            <h2>{label}<span className="ct mono">{items.length}</span></h2>
            <div className="rule" />
          </div>
          <div className="slots-list">
            <div className="slots-list-h">
              <span />
              <span>name</span>
              <span>model</span>
              <span>type · device</span>
              <span>metrics</span>
              <span style={{textAlign: "right"}}>actions</span>
            </div>
            {items.map(s => <SlotListRow key={s.name} slot={s} />)}
          </div>
        </section>
      );
    }
    return (
      <section key={label} style={{marginBottom: 24}}>
        <div className="sec">
          <h2>{label}<span className="ct mono">{items.length}</span></h2>
          <div className="rule" />
        </div>
        <div className={"slots-grid" + (slotVariant === "spec" ? " spec" : "")}>
          {items.map(s => {
            // Demo: show error banner on a single slot if a banner-state would fire
            const errMsg = (window.__hal0Banners && window.__hal0Banners.get && window.__hal0Banners.get()["model-missing"] && s.name === "primary")
              ? "sha256 mismatch on shard 2 — verify the model on /models then retry"
              : null;
            return slotWithState(s, errMsg);
          })}
        </div>
      </section>
    );
  };

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Lifecycle</span>
        <h1>Slots</h1>
        <span className="vh-spacer" />
        <span className="hint">Press <kbd>N</kbd> to create</span>
        <button className="btn" onClick={() => setCreateOpen(true)}>{Icons.plus} New slot</button>
      </div>

      {renderGroup("Chat",  groups.chat)}
      {renderGroup("Embed", groups.embed)}
      {renderGroup("Voice", groups.voice)}
      {renderGroup("Image", groups.img)}

      {groups.npu.length > 0 && (
        <section style={{marginBottom: 24}}>
          <div className="sec">
            <h2>NPU<span className="ct mono">trio · 1 process · 3 roles</span></h2>
            <div className="rule" />
          </div>
          {npuVariant === "reactor" ? <NpuReactor slots={slots} /> : <NpuBlock slots={slots} />}
        </section>
      )}

      <CreateSlotModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        defaults={createDefaults}
        existingSlots={slots}
      />
      <EditSlotDrawer
        open={!!editSlot}
        slot={editSlot}
        onClose={() => { setEditName(null); window.location.hash = "#slots"; }}
      />
      <SlotLogsDrawer
        open={!!logsSlot}
        slot={logsSlot}
        onClose={() => setLogsForSlot(null)}
      />
    </div>
  );
}

Object.assign(window, { SlotsView, SlotCard, SlotListRow, NpuBlock, NpuReactor, Spark });
