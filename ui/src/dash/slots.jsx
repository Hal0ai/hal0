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
import { useComfyui } from '@/api/hooks/useComfyui'
import { ActivityLog } from './activity-log.jsx'
import { ComfyuiPane } from './comfyui-pane.jsx'
import {
  InferencePane,
  InferenceHeroBand,
  SlotScard,
  ModelPicker,
  SlotControls,
  slotCtrlPhase,
} from './inference-pane.jsx'
import { slotIndicatorFromPhase, slotButtonPhase, isSlotLive } from './slot-status.js'
import { prettyProfile } from './profile-names.js'

const { useState: useStateS } = React;

// ─── Slot indicator dot ────────────────────────────────────────────────
//
// Maps a slot snapshot → ({ cls, label, tooltip }) for the status dot
// and the matching status chip. Single source of truth for the
// user-visible colour vocabulary (per dot-state spec, 2026-05-27):
//
//   error / crashed unit                 → "error"   (red)    — investigate
//   !enabled                             → "offline" (grey)   — operator-disabled
//   pulling / starting …                 → "warming" (amber pulse)
//   serving + last_used_at fresh         → "serving" (green pulse) — actively processing
//   serving + last_used_at > 1h          → "stale"   (yellow) — possibly stuck request
//   running + healthy                    → "stale"   (yellow) — ready, awaiting prompt
//   stopped (auto-reloads on request)    → "offline" (grey)
//
// Colour follows CONTAINER RESIDENCY, not configuration (truthful-
// display, 2026-06-04): GREEN = actively processing an in-flight
// request; YELLOW = container running + healthy (awaiting a prompt);
// GREY = not running — disabled or stopped (auto-reloads on the next
// request). Stopped vs disabled is a label/tooltip distinction, not a
// colour one, so the dashboard never paints a not-running slot in a
// "warm" colour. The 1h timer catches stuck-in-SERVING slots where a
// request never finished.
const RECENTLY_LIVE_MS = 60 * 60 * 1000; // 1h hung-request threshold for serving slots

function slotIndicator(slot, now = Date.now()) {
  // N1: container classification is the only path. A slot snapshot that
  // hasn't been enriched with container_status yet falls back to its bare
  // state string inside slotIndicatorFromPhase.
  return slotIndicatorFromPhase(slot, now);
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
  const { type, device, model, state, isDefault, coresident, metrics } = slot;
  // Spec 1 / C3: a slot is enabled unless explicitly off. Disabled slots fade,
  // hide lifecycle buttons, and sort to the end of the grid (SlotsView).
  const enabled = slot.enabled !== false;
  // Lifecycle phase drives which action buttons render (design 2026-06-04):
  // running (container healthy/serving) -> Stop+Restart; off -> Start;
  // transitional (pulling/starting/unloading) -> actions disabled.
  //
  // Derived from slotButtonPhase() in slot-status.js — the SAME classifier
  // that drives the status dot (IndicatorDot → slotIndicatorFromPhase). This
  // used to be an inline state table here that diverged from the dot for
  // `idle`/`unloading` snapshots, producing an "offline" dot beside a "Stop"
  // button. Sharing one classifier makes that contradiction impossible.
  // Enriched (container_status present) vs bare (/api/status union entry)
  // fallback is handled inside the classifier.
  const isContainer = slot.runtime === "container" || slot.container_status != null;
  const phase = slotButtonPhase(slot);
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
        {/* Primary identity chip: the pretty profile name (ROCm / Vulkan /
            ROCm-MTP / FLM / TTS / ComfyUI), coloured by silicon class.
            GPU slots colour by their backend (rocm/vulkan); non-GPU slots
            colour by device_class (npu/cpu/img). Using backend directly
            would mis-colour non-GPU slots — the serializer lifts a broad
            backend token onto every slot (e.g. img reports backend "rocm",
            flm reports "flm"), so device_class is the correct key off-GPU.
            Replaces the redundant gpu-rocm device-tag string. */}
        {slot.profile && (() => {
          // device_class is profile-derived and may be absent; normalise the
          // `device` enum (gpu-rocm/gpu-vulkan → gpu) as a fallback so a
          // profile-less GPU slot still colours by its backend, not "cpu".
          const cls = slot.device_class
            || ((slot.device || "").startsWith("gpu") ? "gpu" : (slot.device || ""));
          const colorKey = cls === "gpu"
            ? (slot.backend || "rocm")
            : (cls || "cpu");
          return (
            <span
              className={"chip dev-" + String(colorKey).replace("gpu-", "")}
              title={`Profile: ${slot.profile}`}
            >
              {prettyProfile(slot.profile)}
            </span>
          );
        })()}
        <span className="chip">{type}</span>
        {/* N5: runtime micro-tag — model swap on a container slot is a
            cold restart, not a hot swap. */}
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
        {(() => {
          const imgFull = slot.image || slot.profile || null;
          const imgShort = imgFull ? imgFull.split("/").pop() : null;
          // #663: surface running-vs-configured image drift on the container
          // chip. actual_image + image_mismatch come from
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
        })()}
        {/* Backend mismatch (ADR-0022): amber chip surfaces the ACTUAL runtime
            backend when it differs from the declared one. Container slots are
            the only real slots, so this now renders alongside the image-tag
            chip (previously trapped in the dead non-container branch). */}
        {slot.backend_mismatch && slot.actual_backend && (
          <span
            className={"chip dev-" + String(slot.actual_backend)}
            style={{borderColor: "var(--warn-line)", background: "var(--warn-soft)"}}
            title={`Declared ${slot.declared_backend || slot.backend || device} but running ${slot.actual_backend} — switch backend to reload`}
          >
            {slot.actual_backend} <span style={{color: "var(--warn)", marginLeft: 4}}>≠ declared</span>
          </span>
        )}
        {(() => {
          // Colour aligned to the slotIndicatorFromPhase() vocabulary
          // (slot-status.js): serving|stale|warming|error|offline. The old
          // map keyed on "warning"/"recent" which that classifier never
          // emits, so every chip fell through to the default grey.
          const ind = slotIndicator(slot);
          const chipColor = ind.cls === "serving" ? "var(--ok)"
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
  // Restart + Edit were dead (stopPropagation-only) — wire them to the real
  // restart mutation and the Edit drawer (via the #slots/:name route, the
  // same path SlotCard uses). `onEdit` is optional; fall through to hash.
  const restartMut = useSlotRestart();
  const goEdit = onEdit || (() => { window.location.hash = "#slots/" + slot.name; });
  // Fire-and-forget restart — never block the row on the model reload.
  const onRestart = () => {
    restartMut.mutate(slot.name, {
      onError: (err) =>
        window.__hal0Toast && window.__hal0Toast(
          err?.message ? `${slot.name}: ${err.message}` : `${slot.name}: restart failed`, "warn"),
    });
    window.__hal0Toast && window.__hal0Toast(`Restarting ${slot.name}…`, "info");
  };
  const tps = type === "llm" ? `${metrics.toks || 0} t/s` :
              type === "embedding" ? `${metrics.rpm} r/m` :
              type === "transcription" ? `${metrics.xrt} xrt` :
              type === "image" ? `${metrics.avg}s avg` :
              `${metrics.rpm || 0} r/m`;
  return (
    <div className="slot-list-row" onClick={goEdit}>
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
        <button
          className="btn ghost sm"
          title="Restart"
          onClick={e => { e.stopPropagation(); onRestart(); }}
        >{Icons.restart}</button>
        <button
          className="btn ghost sm"
          title="Edit"
          onClick={e => { e.stopPropagation(); goEdit(); }}
        >{Icons.edit}</button>
      </span>
    </div>
  );
}

// Helpers — pull live values off the enriched slot dicts the backend
// returns (slots.py:_container_state_enrichment). Missing field → em-dash
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
  if (slot?.container_status != null) return slot.container_status === "running";
  const state = String(slot?.state || "");
  return state === "serving" || state === "ready";
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

// ─── NPU · FLM Stack — Variant B (bracketed trio control surface) ───
//
// THE npu rendering. One FLM container packs chat + ASR + embed coresident
// (the trio boots together when the NPU slot container starts). This
// section lets the operator pick the FLM chat model, toggle ASR/embed
// modalities, and load/unload the whole stack — keyed off device=="npu"
// (never literal slot names).
function NpuFlmStack({ slots }) {
  const npuSlots = slots.filter(s => (s.device_class || s.device) === "npu");
  // Hooks must run unconditionally (rules-of-hooks) — gate render below.
  const modelsQuery = useModels();
  const swapMut = useSlotSwap();
  const loadMut = useSlotLoad();
  const unloadMut = useSlotUnload();
  const editMut = useSlotEdit();
  const restartMutNpu = useSlotRestart();
  const [npuOpen, setNpuOpen] = useStateS(false);

  if (!npuSlots.length) return null;

  const chat = npuSlots.find(s => s.type === "llm");
  const asr = npuSlots.find(s => s.type === "transcription");
  const embed = npuSlots.find(s => s.type === "embedding");
  const anySlot = chat || npuSlots[0];

  const coresGroup = npuTrioGroupLabel(npuSlots);
  const backendUrl = npuTrioBackendUrl(npuSlots);
  const childPort = anySlot?.port ?? null;

  // Toggle state comes from slot.npu (TOML-backed {asr,embed}).
  const parsed = { asr: !!(chat?.npu?.asr), embed: !!(chat?.npu?.embed) };

  // Only chat (the FLM anchor) is a real model choice — the operator picks
  // which model `flm serve` runs. ASR/embed are served coresident off that
  // one process with the model fixed by the --asr/--embed flags, so they
  // render a read-only label (NpuModalityCard `readOnlyModel`) instead of a
  // picker — no asr/embed model list to compute.
  const allModels = modelsQuery.data || [];
  const chatModels = flmModelsByType(allModels, "llm");

  const loaded = chat ? slotIsLoaded(chat) : npuSlots.some(slotIsLoaded);

  const toast = (msg, kind = "warn") =>
    window.__hal0Toast && window.__hal0Toast(msg, kind);

  // Fire-and-forget NPU action — the FLM load/restart blocks for the whole
  // stack warm-up (seconds), so awaiting it froze the trio's controls and the
  // master switch for the entire load. Fire it, toast immediately, and let the
  // 5s slots poll reflect the transition. (Mirrors the SlotsView/PR #781
  // non-blocking pattern.)
  const fire = (mut, args, msg) => {
    mut.mutate(args, {
      onError: (err) => toast(err?.message ? err.message : "NPU action failed", "warn"),
    });
    toast(msg, "info");
  };

  // Master power — load/unload the whole stack via the chat (anchor) slot.
  // Flip-to-cancel: if the stack is loaded OR mid-load (transitional), the
  // master fires an unload so a slow warm-up can be aborted without waiting.
  const onMaster = () => {
    if (!chat) { toast("No NPU chat slot to load", "warn"); return; }
    const p = slotCtrlPhase(chat);
    const stopping = loaded || p === "running" || p === "transitional";
    fire(
      stopping ? unloadMut : loadMut,
      chat.name,
      stopping ? "Unloading NPU stack…" : "Loading NPU stack…",
    );
  };

  const onPickChat = (model_id) => {
    if (!chat || !model_id || model_id === chat.model) return;
    fire(swapMut, { name: chat.name, model_id }, "Swapping NPU chat model…");
  };

  // Toggle a coresident modality (Phase A): write the flip to TOML via
  //   PUT /api/slots/{name}/config  body: { npu: { [which]: next } }
  // then trigger an explicit slot restart so the container picks up the
  // new config (orchestrator/API NEVER auto-restarts — ADR decision).
  // The existing state chip streams the transition; no new UI needed.
  const onToggleModality = (which) => {
    if (!chat) { toast("No NPU chat slot", "warn"); return; }
    const nextVal = !(parsed[which]);
    // Await only the fast config write (surfaces a TOML/validation error
    // inline); then FIRE the cold restart that picks up the new modality —
    // never block the trio on the FLM reload.
    editMut.mutate(
      { name: chat.name, body: { npu: { [which]: nextVal } } },
      {
        onSuccess: () => {
          fire(
            restartMutNpu,
            chat.name,
            `${which} ${nextVal ? "enabled" : "disabled"} — restarting NPU stack…`,
          );
        },
        onError: (err) =>
          toast(err?.message ? err.message : "NPU config write failed", "warn"),
      },
    );
  };

  // No onPickAsr/onPickEmbed: those modalities are read-only labels (the
  // FLM trio fixes their model via flags). Chat keeps onPickChat above.

  // ── trio rendered as the canonical slot card (SlotScard, shared with the
  //    InferencePane). The trio reality is preserved: chat is the loadable
  //    anchor (model picker + stack load/unload/restart); asr/embed are
  //    coresident roles whose Start/Stop maps to the modality toggle (the
  //    "drop embedding" affordance) with an FLM-fixed model label. ──
  const dispatchLogs = (name) =>
    name && window.dispatchEvent(new CustomEvent("hal0:slot-logs", { detail: { name } }));
  const goEdit = (name) => { if (name) window.location.hash = "#slots/" + name; };

  const chatCardNode = chat && (
    <SlotScard
      key="chat" s={chat} ind={slotIndicator(chat)} full
      modelNode={<ModelPicker s={chat} models={chatModels} disabled={!chat} onSwap={onPickChat} />}
      controls={
        <SlotControls
          phase={slotCtrlPhase(chat)} busy={false} compact={false}
          onStart={() => fire(loadMut, chat.name, `Starting ${chat.name}…`)}
          onStop={() => fire(unloadMut, chat.name, `Stopping ${chat.name}…`)}
          onRestart={() => fire(restartMutNpu, chat.name, `Restarting ${chat.name}…`)}
          onLogs={() => dispatchLogs(chat.name)}
          onEdit={() => goEdit(chat.name)}
        />
      }
      onEdit={() => goEdit(chat.name)}
    />
  );

  const modalityCardNode = (which, slot) => {
    const on = !!parsed[which];
    const phase = (loaded && on) ? "running" : "off";
    const ind = !loaded
      ? { cls: "offline", label: "off", tooltip: "NPU stack not loaded" }
      : on
      ? { cls: "stale", label: "coresident", tooltip: "Served coresident by the FLM trio" }
      : { cls: "offline", label: "off", tooltip: `${which} modality disabled` };
    const s = slot || { name: which, type: which === "asr" ? "transcription" : "embedding", metrics: {} };
    const tgt = slot?.name || chat?.name;
    return (
      <SlotScard
        key={which} s={s} ind={ind} phase={phase} full
        modelNode={
          <div className="smodel mono npu-mod-fixed" title="Model fixed by the FLM build (--asr/--embed flags) — not separately selectable.">
            <span className="npu-fixed-model">{slot?.modelDefault || slot?.model || "—"}</span>
            <span className="npu-fixed-tag">FLM</span>
          </div>
        }
        controls={
          <SlotControls
            phase={phase} busy={false} compact={false}
            onStart={() => onToggleModality(which)}
            onStop={() => onToggleModality(which)}
            onRestart={() => fire(restartMutNpu, chat?.name, "Restarting NPU stack…")}
            onLogs={() => dispatchLogs(tgt)}
            onEdit={() => goEdit(tgt)}
          />
        }
        onEdit={() => goEdit(tgt)}
      />
    );
  };

  // ── derived display fields for the engine shell ──
  // Combined NPU resident memory — sum of per-slot mem_mb (the trio shares one
  // FLM process, so its weight shows on the loaded anchor). Em-dash, never a
  // fabricated 0, when nothing is resident.
  const npuMemMb = npuSlots.reduce(
    (a, s) => a + (typeof s.mem_mb === "number" ? s.mem_mb : 0), 0,
  );
  const npuMemGb = npuMemMb > 0 ? Math.round((npuMemMb / 1024) * 10) / 10 : null;
  const chatModel = chat?.model || chat?.modelDefault || "—";
  const epillCls = loaded ? "running" : "stopped";
  const epillLabel = loaded ? "loaded · 3 roles" : "unloaded";

  // small inline glyphs (purple chip + chevron) — keeps the pane self-contained
  const ChipGlyph = (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
         strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3.5" y="3.5" width="9" height="9" rx="1" />
      <rect x="6" y="6" width="4" height="4" rx="0.5" />
      <path d="M6 3.5v-1.5M10 3.5v-1.5M6 14v-1.5M10 14v-1.5M3.5 6h-1.5M3.5 10h-1.5M14 6h1.5M14 10h1.5" />
    </svg>
  );
  const ChevGlyph = (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"
         strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 6l4 4 4-4" />
    </svg>
  );

  return (
    <div className="npu-pane">
      <div className="proto">
        <div className="sec-label">
          <b>NPU Stack</b>
          <span className="dim">·</span>
          <span className="mono" style={{color: "var(--dev-npu)"}}>FLM</span>
          <span className="dim">·</span>
          <span className="meta">coresident</span>
          <span className="dim">·</span>
          <span className="meta">1 process · 3 roles</span>
          <span className="grow" style={{flex: 1}} />
          <span className="meta">{loaded ? "npu · loaded" : "npu · idle"}</span>
        </div>

        <div className={"engine" + (loaded ? " active" : "") + (npuOpen ? " open" : "")}>
          <div className="engine-h">
            <span className="engine-glyph">{ChipGlyph}</span>
            <span className="col">
              <span className="engine-title">NPU · FLM Stack</span>
              <span className="engine-sub">coresident · 1 process · 3 roles</span>
            </span>
            <span className={"epill " + epillCls} data-testid="npu-epill">
              <span className="dot" />
              {epillLabel}
            </span>
            <span className="grow" style={{flex: 1}} />
            <span className="eh-right">
              <span className="npu-master-lbl">master</span>
              <NpuSwitch on={loaded} disabled={!chat} label="Load/unload FLM stack" onClick={onMaster} />
            </span>
          </div>

          {/* collapsed telemetry strip — hidden when the pane is open */}
          <div className="collapsed-prog" data-testid="npu-strip">
            <div className="tel-strip">
              <span className="tel">
                <span className="l">mem</span>
                <span className="v comfy">{npuMemGb == null ? "—" : npuMemGb}<span className="u"> GB</span></span>
              </span>
              <span className="tel">
                <span className="l">chat</span>
                <span className="v" style={{maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{chatModel}</span>
              </span>
              <span className="tel">
                <span className="l">asr</span>
                <span className="dotline"><span className={"rdot" + (parsed.asr ? " on" : "")} /><span className="v">{parsed.asr ? "on" : "off"}</span></span>
              </span>
              <span className="tel">
                <span className="l">embed</span>
                <span className="dotline"><span className={"rdot" + (parsed.embed ? " on" : "")} /><span className="v">{parsed.embed ? "on" : "off"}</span></span>
              </span>
              <span className="tel">
                <span className="l">port</span>
                <span className="v">:{childPort ?? "—"}</span>
              </span>
            </div>
          </div>

          {/* expandable body — the FLM trio rendered as canonical slot cards
              (SlotScard, shared with the InferencePane). */}
          <div className="engine-body">
            <div className="inner">
              <div className="engine-b">
                <div className="npu-stack">
                  <div className="scards full npu-scards">
                    {chatCardNode}
                    {modalityCardNode("asr", asr)}
                    {modalityCardNode("embed", embed)}
                  </div>

                  <div className="npu-stack-foot mono">
                    <code className="npu-args">npu = asr:{parsed.asr ? "on" : "off"} · embed:{parsed.embed ? "on" : "off"}</code>
                    <span className="sep">·</span>
                    <span className="item">port :{childPort ?? "—"}{backendUrl ? <span title={backendUrl}> · {backendUrl}</span> : null}</span>
                    {coresGroup && <><span className="sep">·</span><span className="item">{coresGroup}</span></>}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* footer — flm identity + caret expand control */}
          <div className="engine-foot has-q">
            <div className="foot-id">
              <span className="k">runtime</span>
              <span className="v comfy">flm serve</span>
              <span className="sep">·</span>
              <span className="k">port</span>
              <span className="v">:{childPort ?? "—"}</span>
              {coresGroup && <>
                <span className="sep">·</span>
                <span className="k">group</span>
                <span className="v comfy">{coresGroup}</span>
              </>}
            </div>
            <button
              className="qcaret"
              onClick={() => setNpuOpen(o => !o)}
              aria-expanded={npuOpen}
              data-testid="npu-qcaret"
            >
              <span className="q">
                {ChipGlyph} {npuOpen ? "collapse" : "trio"}
                <span className="qn">3</span>
              </span>
              <span className="car">{ChevGlyph}</span>
            </button>
          </div>
        </div>
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
  // Card-grid source: only real slots (kind === "local"). /api/slots also
  // merges synthetic upstream pseudo-entries (kind:"slot", e.g. the `hal0`
  // router with no profile/runtime) — they belong to the upstream-visibility
  // feature consumed by the sidebar, NOT the SlotCard grid, where they'd
  // render as broken phantom cards. Pure render filter; the payload is
  // unchanged (sidebar widgets still see the full `slots`).
  const cardSlots = slots.filter(s => (s.kind ?? "local") === "local" && !s._synthetic);
  const [createOpen, setCreateOpen] = useStateS(false);
  const [createDefaults, setCreateDefaults] = useStateS({});
  const [editName, setEditName] = useStateS(null);
  const [swapName, setSwapName] = useStateS(null);
  const [logsForSlot, setLogsForSlot] = useStateS(null);
  const [busyName, setBusyName] = useStateS(null);
  // Slots-page tabs: "inference" (chat/embed/voice/npu) vs "image" (the ComfyUI
  // generation engine pane). ComfyUI is one container engine, not per-model
  // slots, and is mutually exclusive with the LLM stack — so it gets its own
  // tab instead of a SlotCard in the Image group.
  const [tab, setTab] = useStateS(
    slotParam === "endpoints" || slotParam === "profiles" ? slotParam : "inference",
  );
  const comfyQuery = useComfyui({ active: tab === "image" });
  const comfyLive = comfyQuery.data?.container?.state === "running";
  const { active: activeBanners } = useBanners();
  const skipPath = !!activeBanners["skip-path"];

  const restartMut = useSlotRestart();
  const unloadMut = useSlotUnload();
  const loadMut = useSlotLoad();
  const swapMut = useSlotSwap();
  const editMut = useSlotEdit();

  const toast = (msg, kind = "info") =>
    window.__hal0Toast && window.__hal0Toast(msg, kind);

  // Fire-and-forget lifecycle action. The backend load/restart/unload/swap
  // POST blocks for the whole model-load (seconds-to-minutes); awaiting it
  // froze the card AND left Stop disabled for the entire load, so a user
  // couldn't cancel a slow-loading slot. Instead we FIRE the mutation (mutate,
  // not mutateAsync), toast immediately, and let the 5s slots poll drive the
  // transitional → running phase. `busy` marks the action in-flight (gates
  // Start/Restart against a double-trigger) but never gates Stop — see
  // SlotCard — so cancel stays live throughout the load. Errors surface via
  // toast since there's no spinner to clear. (Mirrors the non-blocking
  // save/swap from PR #781.)
  const runMutation = (name, mut, args, okMsg) => {
    setBusyName(name);
    mut.mutate(args, {
      onError: (err) =>
        toast(
          err?.message ? `${name}: ${err.message}` : `${name}: action failed`,
          "warn",
        ),
      onSettled: () => setBusyName(null),
    });
    toast(okMsg, "info");
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

  // v0.5 nav: sidebar sub-links #slots/endpoints and #slots/profiles select the
  // matching tab; navigating back to bare #slots (or a slot-name param) drops
  // out of a sub-tab back to Inference.
  React.useEffect(() => {
    if (slotParam === "endpoints" || slotParam === "profiles") setTab(slotParam);
    else setTab((t) => (t === "endpoints" || t === "profiles" ? "inference" : t));
  }, [slotParam]);

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

  // Section assignment is derived from the slot's device_class (now always
  // emitted by the serializer) rather than the legacy null `group` field:
  //   gpu  → Chat
  //   npu  → NPU/FLM stack (rendered by NpuFlmStack, excluded here)
  //   img  → Image-Gen tab (ComfyUI pane)
  //   else (embedding/reranking/transcription/tts capability slots) →
  //         Capabilities. GPU-backed embed/rerank slots are LLM-adjacent
  //         capabilities, so the Capabilities bucket is keyed off type, not
  //         device_class, for the non-chat/non-npu/non-img remainder.
  // device_class is profile-derived and may be absent (no profile, or a
  // legacy device-only slot); fall back to the `device` enum, normalising
  // its gpu-rocm/gpu-vulkan variants down to the bare "gpu" class.
  const dc = (s) => {
    if (s.device_class) return s.device_class;
    const d = s.device || "";
    return d.startsWith("gpu") ? "gpu" : d;
  };
  const groups = {
    chat:  cardSlots.filter(s => dc(s) === "gpu" && s.type === "llm"),
    caps:  cardSlots.filter(s =>
             dc(s) !== "npu" && dc(s) !== "img" &&
             ["embedding", "reranking", "transcription", "tts"].includes(s.type)),
    img:   cardSlots.filter(s => dc(s) === "img" || s.type === "image"),
  };

  const editSlot = (slots || []).find(s => s.name === editName);
  const logsSlot = logsForSlot
    ? (slots || []).find(s => s.name === logsForSlot)
    : null;

  // Seeded slot identities for the skip-path empty layout.
  const SEEDED = [
    { name: "primary", type: "llm",           device: "gpu-rocm", group: "chat"  },
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
        runMutation(s.name, restartMut, s.name, `Restarting ${s.name}…`)
      }
      onUnload={() =>
        runMutation(s.name, unloadMut, s.name, `Stopping ${s.name}…`)
      }
      onStart={() =>
        runMutation(s.name, loadMut, s.name, `Starting ${s.name}…`)
      }
      onSwapPick={(m) =>
        runMutation(
          s.name,
          swapMut,
          { name: s.name, model_id: m.id },
          `Swapping ${s.name} → ${m.longName || m.id}…`,
        )
      }
      onViewLogs={() => { setLogsForSlot(s.name); }}
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
            <ActivityLog />
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
            <ActivityLog />
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
              <p>No slot has a model loaded yet. Run <span className="mono">hal0 setup</span> in your terminal to configure a bundle, or create a slot one at a time.</p>
              <div className="dash-empty-cta">
                <button className="btn lg" onClick={() => setCreateOpen(true)}>{Icons.plus} New slot</button>
              </div>
            </div>
          </div>
          <div className="dash-side">
            <ActivityLog />
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

      {/* Memory map + combined-throughput band — lifted out of the Inference
          engine shell to the top of the page, above the tabs, so iGPU GTT usage
          and live throughput stay visible regardless of which tab is active. */}
      <InferenceHeroBand />

      {/* Inference ⇄ Image Gen tabs. Tab 1 holds every non-image slot; tab 2 is
          the ComfyUI generation engine pane (one container, not per-model
          slots), which replaces the old Image-group SlotCard. */}
      <div className="slot-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "inference"}
          className={"slot-tab infer" + (tab === "inference" ? " on" : "")}
          onClick={() => { setTab("inference"); if (slotParam) window.location.hash = "#slots"; }}
        >
          <span>Inference</span>
          <span className="slot-tab-ct num">{cardSlots.length - groups.img.length}</span>
        </button>
        <button
          role="tab"
          aria-selected={tab === "image"}
          className={"slot-tab comfy" + (tab === "image" ? " on" : "")}
          onClick={() => { setTab("image"); if (slotParam) window.location.hash = "#slots"; }}
        >
          <span className={"slot-tab-dot" + (comfyLive ? " live" : "")} />
          <span>Image Gen</span>
        </button>
        <button
          role="tab"
          aria-selected={tab === "endpoints"}
          className={"slot-tab" + (tab === "endpoints" ? " on" : "")}
          onClick={() => { window.location.hash = "#slots/endpoints"; }}
        >
          <span>Endpoints</span>
        </button>
        <button
          role="tab"
          aria-selected={tab === "profiles"}
          className={"slot-tab" + (tab === "profiles" ? " on" : "")}
          onClick={() => { window.location.hash = "#slots/profiles"; }}
        >
          <span>Profiles</span>
        </button>
      </div>

      {tab === "endpoints" ? (
        <div className="conn">
          {window.LocalEndpointsPanel ? <window.LocalEndpointsPanel /> : null}
        </div>
      ) : tab === "profiles" ? (
        window.ProfilesView ? <window.ProfilesView /> : null
      ) : (
      <div className="dash">
        <div className="dash-main">
          {tab === "image" ? (
            <ComfyuiPane />
          ) : (
            <>
              {/* Inference "engine" pane — a summary engine-shell (yellow accent)
                  over the whole LLM/capability slot stack: collapsed hero
                  (memory map + active slots + combined throughput) that expands
                  to the full slot list with per-slot lifecycle controls + a
                  model picker + a by-device throughput split. The InferencePane
                  is now the single per-slot surface for every iGPU/CPU inference
                  slot (chat/LLM + embedding/reranking/transcription/tts) — the
                  standalone "Chat" and "Capabilities" SlotCard grids are dropped;
                  they only duplicated rows the pane already lists. */}
              <InferencePane />

              {/* NPU · FLM stack — its own engine-shell pane (purple accent),
                  parallel to the Inference + ComfyUI panes. No section h2: the
                  pane supplies its own sec-label + engine header. Keyed off
                  device_class "npu" (serializer-emitted), with the legacy
                  device === "npu" kept as a fallback. */}
              {cardSlots.some(s => dc(s) === "npu") && <NpuFlmStack slots={cardSlots} />}
            </>
          )}
        </div>
        <div className="dash-side">
          <ActivityLog />
        </div>
      </div>
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

Object.assign(window, { SlotsView, SlotCard, SlotListRow, NpuFlmStack, Spark });
