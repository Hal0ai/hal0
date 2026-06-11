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
  useSlotLoad,
  useSlotSwap,
  useSlotEdit,
  useSlotImagePull,
} from '@/api/hooks/useSlots'
import { useModels } from '@/api/hooks/useModels'
import { useLemonadeConfig, useLemonadeConfigSet } from '@/api/hooks/useLemonadeConfig'
import { MemoryMap } from './memory-map'
import { slotIndicatorFromPhase, isSlotLive } from './slot-status.js'

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
//   evicted / idle (lemo=idle|idle)      → "offline" (grey)   — not in VRAM; loads on demand
//   offline (clean unload/swap/evict)    → "offline" (grey)
//
// Colour follows VRAM RESIDENCY, not configuration (truthful-display,
// 2026-06-04, supersedes the 2026-05-27 spec): GREEN = actively
// processing an in-flight request; YELLOW = model genuinely resident in
// VRAM (loaded/ready, awaiting a prompt); GREY = nothing loaded —
// disabled, cleanly offline, or evicted/idle (lemonade hot-reloads on
// the next request). Evicted vs disabled is a label/tooltip distinction,
// not a colour one, so the dashboard never paints a not-loaded slot in a
// "warm" colour. After a serving context manager exits the slot returns
// to READY (yellow, still in VRAM); it only goes grey once lemonade
// evicts it. The 1h timer in this file catches stuck-in-SERVING slots
// where a request never finished.
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
  // N1 (container branch): delegate container slots to the unified helper.
  // Lemond slots continue through the original logic below so all existing
  // tests remain green with no changes to their expected cls/label/tooltip.
  //
  // Detection: runtime="container" (from TOML / normalizeSlot) OR
  // container_status present (backend always emits this for container slots
  // even before `runtime` is included in as_dict serialisation).
  const runtime = String(slot?.runtime || "lemonade");
  if (runtime === "container" || slot?.container_status != null) {
    return slotIndicatorFromPhase(slot, now);
  }

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
      cls: "offline",
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
  Object.assign(window, { slotIndicator, IndicatorDot, RECENTLY_LIVE_MS, isSlotLive });
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

// ─── Container image pull progress bar ────────────────────────────
// Shows while image_status === "pulling" (backend-polled) or while an
// explicit Re-pull is in flight from the error banner.
// Distinct from the model-download bar — this is a ~6GB OCI layer pull,
// one-time per image tag.
function SlotImagePullBar({ slot }) {
  const isContainer = slot?.runtime === "container" || slot?.container_status != null;
  const imageStatus = slot?.image_status;
  const pulling = imageStatus === "pulling";
  if (!isContainer || !pulling) return null;
  // image tag short form for the label.
  const imgFull = slot?.image || null;
  const imgShort = imgFull ? imgFull.split("/").pop() : null;
  const label = `Pulling image${imgShort ? ` ${imgShort}` : ""}…`;
  return (
    <div style={{marginTop: 6, marginBottom: 2}}>
      <div
        aria-live="polite"
        style={{fontFamily: "var(--jbm)", fontSize: 10.5, color: "var(--fg-3)", marginBottom: 3}}
      >
        {label}
      </div>
      <div style={{height: 3, background: "var(--bg-2)", borderRadius: 2, overflow: "hidden"}}>
        <div
          role="progressbar"
          aria-valuenow={0}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={label}
          style={{
            height: "100%",
            width: "40%",
            background: "var(--accent)",
            borderRadius: 2,
            animation: "hal0-indeterminate 1.4s ease infinite",
          }}
        />
      </div>
    </div>
  );
}

// ─── SlotCard (instrument variant) ───
function SlotCard({
  slot,
  onSwap,
  onEdit,
  onRestart,
  onUnload,
  onStart,
  onSwapPick,
  onViewLogs,
  swapOpen,
  onCloseSwap,
  onToggleEnabled,
  errorMsg,
  busy,
}) {
  const { type, device, model, state, isDefault, coresident, cpuOnly, metrics } = slot;
  // Spec 1 / C3: a slot is enabled unless explicitly off. Disabled slots fade,
  // hide lifecycle buttons, and sort to the end of the grid (SlotsView).
  const enabled = slot.enabled !== false;
  // Lifecycle phase drives which action buttons render (design 2026-06-04):
  // running (loaded/serving) -> Stop+Restart; off (not loaded) -> Start;
  // transitional (warming/pulling/unloading/starting) -> actions disabled.
  //
  // N1: container slots project from container_status; lemond slots use the
  // original lemonade_state / state logic so button behavior is unchanged.
  // Detect container runtime: prefer the explicit `runtime` field (set by
  // slot TOML / normalizeSlot default). Also gate on container_status != null
  // as a fallback signal — the live /api/slots response always emits
  // container_status for container slots even if the `runtime` field is not
  // yet included in the serialised payload (see slot manager as_dict()).
  // #658 backend task: ensure `runtime`, `image`, `profile` are emitted.
  const isContainer = slot.runtime === "container" || slot.container_status != null;
  let phase;
  if (isContainer) {
    const cs = String(slot?.container_status || "stopped");
    const health = !!slot?.container_health;
    const cRunning = cs === "running" && health;
    const cTransitional = cs === "starting" || cs === "pulling" || (cs === "running" && !health);
    phase = cTransitional ? "transitional" : cRunning ? "running" : "off";
  } else {
    const lemoState = String(slot?.lemonade_state || "");
    const slotRunning = lemoState === "loaded" || lemoState === "ready" || state === "serving" || state === "ready";
    const slotTransitional = state === "warming" || state === "starting" || state === "pulling" || state === "unloading";
    phase = slotTransitional ? "transitional" : slotRunning ? "running" : "off";
  }
  const isLlm = type === "llm";

  // Only render chips backed by a real slot-payload field. Dead chips
  // (req/min, xrt, prec, p50/lat, sec/min, avg, res, maxDocs, voice) were
  // never populated by the backend and always rendered blank/0 — dropped
  // (W6). When a real metric is momentarily absent (slot offline) show
  // an em-dash, never a fabricated 0.
  //
  // Only LLM slots carry a metrics row. The non-LLM capability cards
  // (embedding/reranking/transcription/tts/image) used to show sparse
  // dim/size/res chips that were mostly em-dashes; they only added card
  // height, so the row is dropped for those types to keep the capability
  // cards close to the compact NPU trio height.
  const num = (v, fallback = "—") =>
    v === null || v === undefined || v === "" ? fallback : v;

  const metricsRow = (() => {
    if (type === "llm") {
      // For container slots: show live tok/s vs profile bench reference if available
      // (e.g. "48 / ~52 tok/s" so a degraded container is obvious).
      const benchToks = typeof slot?.bench_toks_per_sec === "number"
        ? slot.bench_toks_per_sec : null;
      const toksDisplay = isContainer && benchToks
        ? `${num(metrics.toks, 0)} / ~${Math.round(benchToks)}`
        : num(metrics.toks, 0);
      return [
        { l: "tok/s", v: toksDisplay, u: "", spark: slot.spark },
        { l: "ttft",  v: metrics.ttft ? metrics.ttft : "—", u: metrics.ttft ? "ms" : "" },
        { l: "ctx",   v: num(metrics.ctx, "—"), u: "" },
        { l: "kv",    v: metrics.kv === null || metrics.kv === undefined ? "—" : metrics.kv, u: metrics.kv === null || metrics.kv === undefined ? "" : "%", dim: metrics.kv === null || metrics.kv === undefined },
      ];
    }
    return [];
  })();

  return (
    <div className={"slot" + (state === "serving" ? " serving" : "") + (swapOpen ? " swap-open" : "") + (enabled ? "" : " slot--disabled")}>
      <div className="slot-h">
        <IndicatorDot slot={slot} />
        <div className="slot-name">
          <span className="nm">{slot.name}</span>
        </div>
        <div className="right">
          {isDefault && <div className="default-badge">★ default</div>}
          {coresident && <span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.06)"}}>coresident</span>}
          {/* C3: enabled toggle — stays full-opacity + interactive even when
              the card is faded, so a disabled slot can be re-enabled.
              A11y: the hidden <input type=checkbox> is the focusable AT
              surface (role=checkbox + aria-label). The visible track span
              is aria-hidden so AT doesn't announce it twice. NpuSwitch
              pattern: focus-visible ring is handled in dashboard.css via
              :focus-visible on the hidden input. */}
          <label
            className="slot-enable-toggle"
            title={enabled ? "Disable slot" : "Enable slot"}
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="checkbox"
              checked={enabled}
              disabled={!!busy}
              onChange={() => onToggleEnabled && onToggleEnabled(!enabled)}
              aria-label={enabled ? "Disable slot" : "Enable slot"}
            />
            <span className="slot-enable-track" aria-hidden="true" />
          </label>
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
        {/* N5: runtime micro-tag distinguishes container from lemond so
            operators understand why model-swap is a cold restart vs hot. */}
        {isContainer && (
          <span className="chip slot-runtime-tag" title="Container runtime — model swap requires restart">
            container
          </span>
        )}
        {/* Container: image-tag chip (replaces device chip + backend mismatch block).
            Show the image tag truncated; full ref on hover.
            NOTE: `image` and `profile` are TOML fields that as_dict() does not
            yet serialise in /api/slots — tracked in #658 (backend: emit runtime
            + image + profile in slot serialisation). The chip degrades gracefully
            to "no image" until that lands. container_status is always present. */}
        {isContainer ? (() => {
          const imgFull = slot.image || slot.profile || null;
          const imgShort = imgFull ? imgFull.split("/").pop() : null;
          // #663: surface running-vs-configured image drift on the container
          // chip (the lemond backend-mismatch block below never ran for
          // container slots). actual_image + image_mismatch come from
          // _container_state_enrichment via `podman inspect`.
          const imgMismatch = !!slot.image_mismatch && !!slot.actual_image;
          const runShort = slot.actual_image ? slot.actual_image.split("/").pop() : null;
          if (!imgShort) {
            return (
              <span className="chip dim" title="Image/profile not yet emitted by backend (#658)">
                {slot.profile ? `profile:${slot.profile}` : "no image"}
              </span>
            );
          }
          return (
            <span
              className="chip slot-image-tag mono"
              title={imgMismatch
                ? `Configured ${imgFull} but running ${slot.actual_image} — reload the slot to apply the declared image.`
                : imgFull}
              style={imgMismatch
                ? {maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", borderColor: "var(--warn-line)", background: "var(--warn-soft)"}
                : {maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}
            >
              {imgShort}
              {imgMismatch && <span style={{color: "var(--warn)", marginLeft: 4}}>≠ running {runShort}</span>}
            </span>
          );
        })() : (
          <>
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
          </>
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
      {metricsRow.length > 0 && (
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
      )}
      {/* Container image pull progress — shown when image_status === "pulling"
          (backend-polled), distinct from model download. */}
      <SlotImagePullBar slot={slot} />
      {/* N3: touch-action:manipulation prevents 300ms tap-delay on mobile
          while keeping pan/pinch-to-zoom intact (no `touch-action: none`). */}
      <div className="slot-actions" style={{touchAction: "manipulation"}}>
        {/* C3: a disabled slot has no running child to Start/Stop/Restart —
            hide the lifecycle buttons; the card's toggle is the way back on. */}
        {!enabled ? (
          <span className="slot-disabled-note mono">disabled</span>
        ) : phase === "off" ? (
          <button
            className="btn ghost sm"
            disabled={!!busy}
            onClick={() => onStart && onStart()}
          >{Icons.start} Start</button>
        ) : (
          <>
            <button
              className="btn ghost sm"
              disabled={!!busy || phase === "transitional"}
              onClick={() => onUnload && onUnload()}
            >{Icons.unload} Stop</button>
            <button
              className="btn ghost sm"
              disabled={!!busy || phase === "transitional"}
              onClick={() => onRestart && onRestart()}
            >{Icons.restart} Restart</button>
          </>
        )}
        <button className="btn ghost sm" onClick={() => onViewLogs && onViewLogs()}>{Icons.logs} Logs</button>
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

// ─── flm_args parsing helpers (pure) ───
//
// The FLM trio's coresident modalities are driven by lemond's
// `flm_args` string ("--asr <0|1> --embed <0|1>"), set via
// POST /api/lemonade/config and applied at the next FLM load. We parse
// the live string to drive the toggles and recompose it on flip.
// The backend now accepts explicit 0/1 for both flags, so we always
// emit both keys (absence must never silently disable a modality).
function parseFlmArgs(str) {
  const s = typeof str === "string" ? str : "";
  const asrM = s.match(/--asr\s+(\d)/);
  const embM = s.match(/--embed\s+(\d)/);
  return {
    // Default ON when the flag is absent — matches the seeded trio
    // ("--asr 1 --embed 1") so an empty/unparsed config reads as the
    // full coresident stack rather than silently-off.
    asr: asrM ? asrM[1] === "1" : true,
    embed: embM ? embM[1] === "1" : true,
  };
}
function composeFlmArgs({ asr, embed }) {
  return `--asr ${asr ? 1 : 0} --embed ${embed ? 1 : 0}`;
}

// FLM models live in their own namespace (registry seed backend:"flm",
// upstream:"npu"). Filter /api/models defensively across the field
// shapes the registry/upstream rows can present, then narrow by the
// dispatcher `type` vocabulary the picker is for. Never offer GGUFs.
function isFlmModel(m) {
  const backends = Array.isArray(m?.backends) ? m.backends : [];
  return (
    backends.includes("flm") ||
    m?.backend === "flm" ||
    m?.runtime === "flm" ||
    m?.upstream === "npu"
  );
}
// `normalizeApiModel` derives `type` from the plural `capabilities`
// array and discards any backend-set type. FLM seed rows carry a
// SINGULAR `capability` ("chat"|"embed"|"asr"), so when `capabilities`
// is empty `type` lands as "" — fall back to the singular field so the
// pickers still populate. (Dispatcher vocab: chat→llm, embed→embedding,
// asr/transcription→transcription.)
function modelSlotType(m) {
  if (m?.type) return m.type;
  const cap = String(m?.capability || "").toLowerCase();
  if (cap === "chat") return "llm";
  if (cap === "embed" || cap === "embeddings") return "embedding";
  if (cap === "asr" || cap === "transcription") return "transcription";
  if (cap === "rerank") return "reranking";
  if (cap === "tts") return "tts";
  if (cap === "image") return "image";
  return "";
}
function flmModelsByType(models, type) {
  return (Array.isArray(models) ? models : [])
    .filter(isFlmModel)
    .filter(m => modelSlotType(m) === type);
}

const NPU_CHIP = {
  color: "var(--dev-npu)",
  borderColor: "rgba(200,150,255,0.30)",
  background: "rgba(200,150,255,0.06)",
};

function slotIsLoaded(slot) {
  const lemo = String(slot?.lemonade_state || "");
  const state = String(slot?.state || "");
  return lemo === "loaded" || lemo === "ready" || state === "serving" || state === "ready";
}

// A small native-looking select for the FLM model pickers.
function NpuModelSelect({ value, models, disabled, onChange }) {
  const opts = Array.isArray(models) ? models : [];
  const hasCurrent = value && opts.some(m => m.id === value);
  return (
    <select
      className="input mono npu-sel"
      value={value || ""}
      disabled={disabled}
      onChange={e => onChange && onChange(e.target.value)}
    >
      {/* Keep the live model selectable even if the catalog hasn't
          surfaced it (offline /api/models, un-catalogued FLM tag). */}
      {value && !hasCurrent && <option value={value}>{value}</option>}
      {!value && <option value="">—</option>}
      {opts.map(m => (
        <option key={m.id} value={m.id}>{m.longName || m.id}</option>
      ))}
    </select>
  );
}

// A11y-friendly on/off switch (matches the prototype's visual language).
function NpuSwitch({ on, disabled, label, onClick }) {
  return (
    <button
      type="button"
      className="npu-switch"
      role="switch"
      aria-checked={!!on}
      aria-label={label}
      disabled={disabled}
      data-on={on ? "1" : "0"}
      onClick={onClick}
    >
      <span className="knob" />
    </button>
  );
}

// One modality mini-card inside the bracketed trio.
//
// `readOnlyModel` modalities (ASR/embed) render the served model as a
// read-only label instead of a picker: the FLM trio serves all three
// roles from one `flm serve` process and the asr/embed model is fixed by
// the --asr/--embed flags — the request `model` field is ignored by FLM
// (verified 2026-06-06), so a picker there would be cosmetic. Chat (the
// anchor) stays a real picker.
function NpuModalityCard({ icon, label, slot, on, fixed, models, busy, onToggle, onPickModel, readOnlyModel }) {
  return (
    <div className="slot npu-mod" data-on={on ? "1" : "0"}>
      <div className="slot-h">
        <span className="npu-mod-icon" aria-hidden="true">{icon}</span>
        <div className="slot-name"><span className="nm">{label}</span></div>
        <div className="right">
          {fixed
            ? <span className="chip" style={{...NPU_CHIP, fontSize: 10}}>always</span>
            : <NpuSwitch on={on} disabled={busy} label={`Toggle ${label}`} onClick={onToggle} />}
        </div>
      </div>
      <div className="npu-mod-body">
        {readOnlyModel ? (
          <div
            className="npu-mod-fixed mono"
            title="Served by the FLM trio — the model is fixed by the --asr/--embed flags on this FLM build, not separately selectable."
          >
            {/* Prefer the slot's CONFIGURED model (model_default) over the live
                model_id: an NPU-trio modality is never loaded as its own
                process, so its live model_id stays stale on the pre-trio GGUF.
                The configured FLM tag is what the anchor actually serves. */}
            <span className="npu-fixed-model">{slot?.modelDefault || slot?.model || "—"}</span>
            <span className="npu-fixed-tag" aria-hidden="true">FLM</span>
          </div>
        ) : (
          <NpuModelSelect
            value={slot?.model || ""}
            models={models}
            disabled={!on || busy || !slot}
            onChange={onPickModel}
          />
        )}
      </div>
    </div>
  );
}

// ─── NPU · FLM Stack — Variant B (bracketed trio control surface) ───
//
// THE npu rendering. One FLM process packs chat + ASR + embed coresident
// (the trio boots together when the NPU chat slot loads with
// flm_args "--asr 1 --embed 1"). This section lets the operator pick the
// FLM chat model, toggle ASR/embed modalities, and load/unload the whole
// stack — keyed off device=="npu" (never literal slot names).
function NpuFlmStack({ slots }) {
  const npuSlots = slots.filter(s => s.device === "npu");
  // Hooks must run unconditionally (rules-of-hooks) — gate render below.
  const cfgQuery = useLemonadeConfig();
  const cfgSet = useLemonadeConfigSet();
  const modelsQuery = useModels();
  const swapMut = useSlotSwap();
  const loadMut = useSlotLoad();
  const unloadMut = useSlotUnload();
  const editMut = useSlotEdit();
  const [pending, setPending] = useStateS(false);
  const [busy, setBusy] = useStateS(false);

  if (!npuSlots.length) return null;

  const chat = npuSlots.find(s => s.type === "llm");
  const asr = npuSlots.find(s => s.type === "transcription");
  const embed = npuSlots.find(s => s.type === "embedding");
  const anySlot = chat || npuSlots[0];

  const coresGroup = npuTrioGroupLabel(npuSlots);
  const backendUrl = npuTrioBackendUrl(npuSlots);
  const childPort = anySlot?.port ?? null;

  const flmArgsLive = typeof cfgQuery.data?.flm_args === "string" ? cfgQuery.data.flm_args : "";
  const parsed = parseFlmArgs(flmArgsLive);

  // Only chat (the FLM anchor) is a real model choice — the operator picks
  // which model `flm serve` runs. ASR/embed are served coresident off that
  // one process with the model fixed by the --asr/--embed flags, so they
  // render a read-only label (NpuModalityCard `readOnlyModel`) instead of a
  // picker — no asr/embed model list to compute.
  const allModels = modelsQuery.data || [];
  const chatModels = flmModelsByType(allModels, "llm");

  const loaded = chat ? slotIsLoaded(chat) : npuSlots.some(slotIsLoaded);
  // Live flm.args string for the footer — pending toggles preview the
  // string that WILL apply on the next load.
  const previewArgs = composeFlmArgs(parsed);

  const toast = (msg, kind = "warn") =>
    window.__hal0Toast && window.__hal0Toast(msg, kind);

  const run = async (fn) => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      toast(err?.message ? err.message : "NPU action failed", "warn");
    } finally {
      setBusy(false);
    }
  };

  // Master power — load/unload the whole stack via the chat (anchor) slot.
  // Loading applies the current flm_args, so it clears the pending hint.
  const onMaster = () => {
    if (!chat) { toast("No NPU chat slot to load", "warn"); return; }
    run(async () => {
      if (loaded) {
        await unloadMut.mutateAsync(chat.name);
      } else {
        await loadMut.mutateAsync(chat.name);
      }
      // Either edge resolves the pending flm_args: a load applies them,
      // an unload tears down the process that held the stale args.
      setPending(false);
    });
  };

  // Reload to apply pending flm_args (unload+load the anchor slot).
  const onReload = () => {
    if (!chat) return;
    run(async () => {
      if (loaded) await unloadMut.mutateAsync(chat.name);
      await loadMut.mutateAsync(chat.name);
      setPending(false);
    });
  };

  const onPickChat = (model_id) => {
    if (!chat || !model_id || model_id === chat.model) return;
    run(() => swapMut.mutateAsync({ name: chat.name, model_id }));
  };

  // Toggle a coresident modality: recompose flm_args (flip the one flag,
  // keep the other), POST it to lemond, AND flip the shadow slot's
  // `enabled` so dispatch gating (v1.py _is_npu_trio_request) stays in
  // sync. flm_args apply at the next load → mark pending.
  const onToggleModality = (which, slot) => {
    const next = { ...parsed, [which]: !parsed[which] };
    run(async () => {
      await cfgSet.mutateAsync({ flm_args: composeFlmArgs(next) });
      if (slot) {
        await editMut.mutateAsync({ name: slot.name, body: { enabled: next[which] } });
      }
      setPending(true);
    });
  };

  // No onPickAsr/onPickEmbed: those modalities are read-only labels (the
  // FLM trio fixes their model via flags). Chat keeps onPickChat above.

  return (
    <div className="npu-stack">
      <div className="npu-stack-h">
        <span className="title mono">NPU · FLM Stack</span>
        <span className="chip" style={NPU_CHIP}>
          <span className="dot" style={{width: 5, height: 5, background: "currentColor", boxShadow: "0 0 6px currentColor"}} />
          coresident · boots together
        </span>
        <span className="npu-stack-spacer" />
        <span className="npu-stack-master-lbl mono">master</span>
        <NpuSwitch on={loaded} disabled={busy || !chat} label="Load/unload FLM stack" onClick={onMaster} />
      </div>

      <div className="npu-bracket">
        <div className="npu-bracket-rail" aria-hidden="true" />
        <div className="npu-trio">
          <NpuModalityCard
            icon="💬" label="Chat" slot={chat} on fixed
            models={chatModels} busy={busy} onPickModel={onPickChat}
          />
          <NpuModalityCard
            icon="🎙" label="ASR" slot={asr} on={parsed.asr} readOnlyModel
            busy={busy}
            onToggle={() => onToggleModality("asr", asr)}
          />
          <NpuModalityCard
            icon="🧬" label="Embed" slot={embed} on={parsed.embed} readOnlyModel
            busy={busy}
            onToggle={() => onToggleModality("embed", embed)}
          />
        </div>
      </div>

      <div className="npu-stack-foot mono">
        <code className="npu-args">flm.args = "{previewArgs}"</code>
        <span className="sep">·</span>
        <span className="item">port :{childPort ?? "—"}{backendUrl ? <span title={backendUrl}> · {backendUrl}</span> : null}</span>
        {coresGroup && <><span className="sep">·</span><span className="item">{coresGroup}</span></>}
        {pending && (
          <>
            <span className="npu-stack-spacer" />
            <span className="npu-pending" title="flm_args apply on the next FLM load">⟳ reload to apply</span>
            <button className="btn ghost sm" disabled={busy || !chat} onClick={onReload}>Reload</button>
          </>
        )}
      </div>
    </div>
  );
}

// ─── Slots view ───
function SlotsView({ slotVariant, slotParam, onGo }) {
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
  const [logsForSlot, setLogsForSlot] = useStateS(null);
  const [busyName, setBusyName] = useStateS(null);
  const { active: activeBanners } = useBanners();
  const skipPath = !!activeBanners["skip-path"];

  const restartMut = useSlotRestart();
  const unloadMut = useSlotUnload();
  const loadMut = useSlotLoad();
  const swapMut = useSlotSwap();
  const editMut = useSlotEdit();

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

  // Open the live log drawer for a slot — fired by the command palette's
  // "View logs — <slot>" action (which routes here first).
  React.useEffect(() => {
    const onLogs = (e) => { const n = e && e.detail && e.detail.name; if (n) setLogsForSlot(n); };
    window.addEventListener("hal0:slot-logs", onLogs);
    return () => window.removeEventListener("hal0:slot-logs", onLogs);
  }, []);

  // Close menus on outside click
  React.useEffect(() => {
    const off = () => { setSwapName(null); };
    document.addEventListener("click", off);
    return () => document.removeEventListener("click", off);
  }, []);

  const groups = {
    chat:  slots.filter(s => s.group === "chat"),
    embed: slots.filter(s => s.group === "embed"),
    voice: slots.filter(s => s.group === "voice"),
    img:   slots.filter(s => s.group === "img"),
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
      onSwap={(e) => { e.stopPropagation(); setSwapName(swapName === s.name ? null : s.name); }}
      onCloseSwap={() => setSwapName(null)}
      onToggleEnabled={async (next) => {
        // C3: instant-apply enabled flip. Query invalidation re-renders the
        // card from server truth; on error we leave server state untouched and
        // toast (e.g. the npu-exclusivity 409 when enabling a 2nd NPU LLM).
        setBusyName(s.name);
        try {
          await editMut.mutateAsync({ name: s.name, body: { enabled: next } });
          toast(`${s.name} ${next ? "enabled" : "disabled"}`, "ok");
        } catch (err) {
          toast(err?.message ? `${s.name}: ${err.message}` : `${s.name}: toggle failed`, "warn");
        } finally {
          setBusyName(null);
        }
      }}
      onEdit={() => { window.location.hash = "#slots/" + s.name; }}
      onRestart={() =>
        runMutation(s.name, restartMut, s.name, `Restarting ${s.name}`)
      }
      onUnload={() =>
        runMutation(s.name, unloadMut, s.name, `Unloaded ${s.name}`)
      }
      onStart={() =>
        runMutation(s.name, loadMut, s.name, `Starting ${s.name}`)
      }
      onSwapPick={(m) =>
        runMutation(
          s.name,
          swapMut,
          { name: s.name, model_id: m.id },
          `Swapping ${s.name} → ${m.longName || m.id}`,
        )
      }
      onViewLogs={() => { setLogsForSlot(s.name); }}
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

  // C6: stable-sort enabled slots before disabled ones, preserving the
  // existing order within each bucket. Array.prototype.sort is stable, so a
  // 0/1 key keeps the original type/role ordering intact otherwise. Pairs
  // with the faded card so disabled slots sink to the end of their section.
  const enabledFirst = (items) =>
    items.slice().sort((a, b) => (a?.enabled === false ? 1 : 0) - (b?.enabled === false ? 1 : 0));

  const renderGroup = (label, rawItems, opts = {}) => {
    const items = enabledFirst(rawItems);
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
        <div className={"slots-grid" + (slotVariant === "spec" ? " spec" : "") + (opts.quarter ? " quarter" : "")}>
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
          {renderGroup("Chat", groups.chat)}
          {/* Capabilities (C7): embedding/reranking/transcription/tts cards are
              content-light, so they render in a denser 4-up quarter-width grid
              instead of two separate full-width Embed/Voice sections. NPU
              modalities (group "npu") are excluded by grouping — they live in
              the dedicated NPU/FLM stack section below. */}
          {renderGroup("Capabilities", [...groups.embed, ...groups.voice], { quarter: true })}
          {renderGroup("Image", groups.img)}

          {slots.some(s => s.device === "npu") && (
            <section style={{marginBottom: 24}}>
              <div className="sec">
                <h2>NPU<span className="ct mono">trio · 1 process · 3 roles</span></h2>
                <div className="rule" />
              </div>
              <NpuFlmStack slots={slots} />
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

Object.assign(window, { SlotsView, SlotCard, SlotListRow, NpuFlmStack, Spark });
