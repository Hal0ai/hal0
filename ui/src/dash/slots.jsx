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
import { MemoryMap } from './memory-map'

const { useState: useStateS } = React;

// ─── Slot indicator dot ────────────────────────────────────────────────
//
// Maps a slot snapshot → ({ cls, label, tooltip }) for the status dot
// and the matching status chip. Single source of truth for the
// user-visible colour vocabulary (per dot-state spec, 2026-05-27):
//
//   error                                → "error"   (red)    — load/spawn failure; investigate
//   !enabled || lemo=disabled            → "offline" (grey)   — operator-disabled
//   warming / starting / pulling …       → "warming" (amber pulse)
//   serving + last_used_at fresh         → "serving" (green pulse) — actively processing
//   serving + last_used_at > 1h          → "stale"   (yellow) — possibly stuck request
//   loaded in VRAM (lemo=loaded|ready)   → "stale"   (yellow) — ready, awaiting prompt
//   evicted / idle (lemo=idle|idle)      → "stale"   (yellow) — auto-reloads on next request
//   offline (clean unload/swap/evict)    → "offline" (grey)
//
// GREEN fires ONLY during an active in-flight request. Yellow covers
// the entire "loaded and waiting" surface — in-VRAM and evicted both —
// because operators don't need a colour to tell those apart (the
// tooltip does). After a serving context manager exits, the slot
// transitions back to READY (yellow); 1h later the idle monitor demotes
// to IDLE (also yellow). The 1h timer in this file catches stuck-in-
// SERVING slots where a request never finished.
const RECENTLY_LIVE_MS = 60 * 60 * 1000; // 1h hung-request threshold for serving slots

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
  const lemo = String(slot?.lemonade_state || "");
  const enabled = slot?.enabled !== false;
  const lastUsedSec = typeof slot?.last_used_at === "number" ? slot.last_used_at : null;
  const lastUsedMs = lastUsedSec != null ? lastUsedSec * 1000 : null;
  const deltaMs = lastUsedMs != null ? now - lastUsedMs : null;
  const errorMsg = slot?.metadata?.message || slot?.message || "";
  const model = slot?.model || slot?.model_id || slot?.model_default || "";

  // Backend mismatch (ADR-0022): rely solely on the backend-computed flag.
  // The backend only sets backend_mismatch=true when BOTH declared_backend
  // and actual_backend are known and differ; we never recompute from the
  // device string (which is the gpu- form, not the bare backend token).
  const loaded = lemo === "loaded" || lemo === "ready" || state === "serving" || state === "ready";
  const backendMismatch = !!slot?.backend_mismatch;
  const declaredBackend = slot?.declared_backend || "";
  const actualBackend = slot?.actual_backend || "";

  if (state === "error") {
    const extraMsg = backendMismatch && declaredBackend && actualBackend
      ? ` — declared ${declaredBackend} but running ${actualBackend}`
      : "";
    return {
      cls: "error",
      label: "error",
      tooltip: errorMsg ? `Error: ${errorMsg}${extraMsg}` : `Error${extraMsg}`,
    };
  }
  if (!enabled || lemo === "disabled") {
    return {
      cls: "offline",
      label: "off",
      tooltip: "Disabled",
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
    // Hung-request guard: if a slot has been in SERVING for longer
    // than RECENTLY_LIVE_MS without a fresh last_used_at bump, it's
    // almost certainly stuck on a request that will never finish.
    // Revert to yellow with a "possibly stuck" tooltip — keeps green
    // honest as "actively processing right now", not "lit since
    // last week".
    const stuck = deltaMs != null && deltaMs > RECENTLY_LIVE_MS;
    if (stuck) {
      return {
        cls: "stale",
        label: "stuck?",
        tooltip: `Serving since ${_formatAgo(deltaMs)} — request may be stuck`,
      };
    }
    if (backendMismatch) {
      return {
        cls: "warning",
        label: "mismatch",
        tooltip: `Declared ${declaredBackend} but running ${actualBackend} — switch backend to reload`,
      };
    }
    return {
      cls: "serving",
      label: "serving",
      tooltip: model ? `Serving ${model}` : "Serving",
    };
  }
  // Slot is loaded and waiting for a prompt — YELLOW per the dot-state
  // spec ("active + available to receive prompts → yellow; green only
  // while actively processing"). In-VRAM vs evicted is a tooltip-only
  // distinction; the colour is the same so operators don't need to
  // squint to tell "ready" from "idle".
  if (lemo === "loaded" || state === "ready") {
    if (backendMismatch) {
      return {
        cls: "warning",
        label: "mismatch",
        tooltip: `Declared ${declaredBackend} but running ${actualBackend} — switch backend to reload`,
      };
    }
    return {
      cls: "stale",
      label: "ready",
      tooltip: deltaMs != null
        ? `Loaded — last used ${_formatAgo(deltaMs)}`
        : (model ? `Loaded — ${model} in VRAM` : "Loaded — model in VRAM"),
    };
  }
  // Lemonade-evicted slots arrive here as state=offline lemonade_state=idle:
  // the model is available but not in VRAM, lemonade hot-reloads on next request.
  if (lemo === "idle" || state === "idle") {
    return {
      cls: "stale",
      label: "idle",
      tooltip: "Idle — model not in VRAM, will hot-reload on next request",
    };
  }
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

  // Only render chips backed by a real slot-payload field. Dead chips
  // (req/min, xrt, prec, p50/lat, sec/min, avg, res, maxDocs, voice) were
  // never populated by the backend and always rendered blank/0 — dropped
  // (W6). When a real metric is momentarily absent (slot offline) show
  // an em-dash, never a fabricated 0.
  //
  // `size` is derived from metrics.mem (GB). Until BE-METRICS lands a
  // real resident-size field per modality, only show it when mem > 0;
  // otherwise em-dash rather than "0 MB".
  const sizeChip = () => {
    const memGb = typeof metrics.mem === "number" ? metrics.mem : 0;
    if (!memGb || memGb <= 0) return { l: "size", v: "—", u: "" };
    return memGb * 1024 < 1000
      ? { l: "size", v: (memGb * 1024).toFixed(0), u: "MB" }
      : { l: "size", v: memGb.toFixed(1), u: "GB" };
  };
  const num = (v, fallback = "—") =>
    v === null || v === undefined || v === "" ? fallback : v;

  const metricsRow = (() => {
    if (type === "llm") return [
      { l: "tok/s",  v: num(metrics.toks, 0), u: "", spark: slot.spark },
      { l: "ttft",   v: metrics.ttft ? metrics.ttft : "—", u: metrics.ttft ? "ms" : "" },
      { l: "ctx",    v: num(metrics.ctx, "—"), u: "" },
      { l: "kv",     v: metrics.kv === null || metrics.kv === undefined ? "—" : metrics.kv, u: metrics.kv === null || metrics.kv === undefined ? "" : "%", dim: metrics.kv === null || metrics.kv === undefined },
    ];
    if (type === "embedding") return [
      { l: "dim",     v: num(metrics.dim, "—"), u: "" },
      sizeChip(),
    ];
    if (type === "reranking") return [
      { l: "max/req", v: num(metrics.maxDocs, "—"), u: "" },
      sizeChip(),
    ];
    if (type === "transcription") return [
      sizeChip(),
    ];
    if (type === "tts") return [
      sizeChip(),
    ];
    if (type === "image") return [
      { l: "res",     v: num(metrics.res, "—"), u: "" },
      sizeChip(),
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
        {/* Backend mismatch (ADR-0022): amber chip surfaces the ACTUAL
            runtime backend when it differs from the declared one. Render
            only on the backend-computed flag + a present actual_backend. */}
        {slot.backend_mismatch && slot.actual_backend && (
          <span
            className={"chip dev-" + String(slot.actual_backend)}
            style={{borderColor: "var(--warn-line)", background: "var(--warn-soft)"}}
            title={`Declared ${slot.declared_backend || device} but running ${slot.actual_backend} — switch backend to reload`}
          >
            {slot.actual_backend} <span style={{color: "var(--warn)", marginLeft: 4}}>≠ declared</span>
          </span>
        )}
        {(() => {
          const ind = slotIndicator(slot);
          const chipColor = ind.cls === "warning" ? "var(--warn)"
            : ind.cls === "recent" ? "var(--ok)"
            : ind.cls === "serving" ? "var(--accent)"
            : ind.cls === "stale" || ind.cls === "warming" ? "var(--warn)"
            : ind.cls === "error" ? "var(--err)"
            : "var(--fg-3)";
          return <span className="chip" style={{color: chipColor}}>{ind.label}</span>;
        })()}
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
function SlotsView({ slotVariant, npuVariant, slotParam, onGo }) {
  const slotsQuery = useSlots();
  // Single source of truth: the hook. The Playwright apiMock fixture
  // fulfils /api/slots so mock-mode coverage is symmetric with live runs;
  // we no longer fall back to HAL0_DATA.slots (per slots-wireup brief).
  // No stub-on-load seeds: while the query is still resolving we show a
  // loading skeleton (below); a confirmed empty array shows a real
  // empty state — fake slots must never flash in.
  const slots = slotsQuery.data || [];
  const slotsLoading = slotsQuery.isLoading && !slotsQuery.data;
  const slotsEmpty = Array.isArray(slotsQuery.data) && slotsQuery.data.length === 0;
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

  // `onGo` may be omitted (some legacy call sites); fall through to hash
  // routing so the snapshot row clicks still navigate. Keeps the sidebar
  // working in tests/storybook-y harnesses that mount SlotsView directly.
  const goTo = onGo || ((r) => { window.location.hash = "#" + r; });

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

        <div className="dash">
          <div className="dash-main">
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
          </div>
          <div className="dash-side">
            <SnapshotStrip slots={slots} onGo={goTo} />
            <MemoryMap variant="sidebar" />
            <ThroughputCard />
          </div>
        </div>

        <CreateSlotModal
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          defaults={createDefaults}
          existingSlots={slots}
        />
      </div>
    );
  }

  // Loading skeleton — shown while /api/slots is still resolving so no
  // fake/stub slot cards flash before real data arrives.
  if (slotsLoading) {
    return (
      <div className="view">
        <div className="vh">
          <span className="vh-eye mono">Lifecycle</span>
          <h1>Slots</h1>
          <span className="vh-spacer" />
          <span className="hint mono dim">Loading slots…</span>
        </div>
        <div className="dash">
          <div className="dash-main">
            <div className="slots-grid" aria-busy="true">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="slot slot-skeleton" aria-hidden="true" />
              ))}
            </div>
          </div>
          <div className="dash-side">
            <MemoryMap variant="sidebar" />
          </div>
        </div>
      </div>
    );
  }

  // Real zero-slots empty state — only when the query has resolved to a
  // confirmed empty array (not still-loading).
  if (slotsEmpty) {
    return (
      <div className="view">
        <div className="vh">
          <span className="vh-eye mono">Lifecycle</span>
          <h1>Slots</h1>
          <span className="vh-spacer" />
          <button className="btn" onClick={() => setCreateOpen(true)}>{Icons.plus} New slot</button>
        </div>
        <div className="dash">
          <div className="dash-main">
            <div className="dash-empty">
              <h2 className="mono">No slots configured</h2>
              <p>No slot has a model loaded yet. Pick a bundle to get started, or create a slot one at a time.</p>
              <div className="dash-empty-cta">
                <button className="btn lg" onClick={() => window.location.hash = "#firstrun"}>Pick a bundle</button>
                <button className="btn ghost lg" onClick={() => setCreateOpen(true)}>{Icons.plus} New slot</button>
              </div>
            </div>
          </div>
          <div className="dash-side">
            <MemoryMap variant="sidebar" />
          </div>
        </div>
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

      <div className="dash">
        <div className="dash-main">
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
        </div>
        <div className="dash-side">
          <SnapshotStrip slots={slots} onGo={goTo} />
          <MemoryMap variant="sidebar" />
          <ThroughputCard />
        </div>
      </div>

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
