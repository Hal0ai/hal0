// slot-status.js — runtime-aware slot phase classifier (N1 unification).
//
// Single source of truth for the status vocabulary shared across:
//   • slotIndicator (slots.jsx)    → {cls, label, tooltip}
//   • stateChipClass (slot-modals.jsx) → "chip ok|warn|err|"
//   • LIVE_STATES (memory-map.jsx) → isLive boolean
//   • phase logic (slots.jsx)      → off|running|transitional button selector
//
// Phase vocabulary (both runtimes project into this):
//   missing      — slot defined but no image / model present yet
//   pulling      — image layer pull (container) or model download in progress
//   starting     — container systemd unit active but /health not yet ok;
//                  or lemonade slot warming up (warming/starting)
//   serving      — actively processing an in-flight request (GREEN)
//   ready        — model/container healthy, waiting for a prompt (YELLOW)
//   idle         — lemonade-evicted; hot-reload on next request (GREY)
//   stopped      — container or slot cleanly offline (GREY)
//   crashed      — error / failed unit (RED)
//
// Colour rule:
//   GREEN  = processing (serving) — actively doing work RIGHT NOW
//   YELLOW = resident / ready     — model in VRAM / container healthy, awaiting prompt
//   GREY   = not loaded           — disabled, stopped, idle, offline
//   RED    = error / crashed
//   AMBER  = transitional         — pulling / starting / unloading
//
// Both runtimes use the same phase enum; callers project it to their own
// output vocab via the thin helpers below. Adding a new runtime means
// extending slotPhase() only — no changes to the four callers.

const RECENTLY_LIVE_MS = 60 * 60 * 1000; // 1h stuck-SERVING threshold

/**
 * Derive a unified phase from a slot snapshot.
 *
 * @param {object} slot  - normalised slot dict from /api/slots
 * @param {number} [now] - epoch ms (injectable for tests)
 * @returns {{ phase: string, isLive: boolean, isCold: boolean }}
 *   phase   — one of: missing|pulling|starting|serving|ready|idle|stopped|crashed
 *   isLive  — heuristic "holds memory" flag for the dot vocabulary. NOTE:
 *             this is NOT the memory-map attribution test — it folds in
 *             enabled + lemonade_state and so DIVERGES from the legacy
 *             LIVE_STATES set for lemond slots. Memory-map MUST use
 *             isSlotLive() (below), which preserves LIVE_STATES exactly.
 *   isCold  — true for container slots (model swap = systemctl restart, not hot-swap)
 */
// Detect whether a slot is a container runtime.
// Primary signal: runtime="container" (from TOML / normalizeSlot).
// Fallback: container_status present (backend always emits this for container
// slots even when as_dict() doesn't yet include the `runtime` field).
// Tracked: #658 (add runtime/image/profile to slot serialisation).
function _isContainer(slot) {
  return String(slot?.runtime || "") === "container" || slot?.container_status != null;
}

export function slotPhase(slot, now = Date.now()) {
  const enabled = slot?.enabled !== false;
  const isContainer = _isContainer(slot);

  // Disabled overrides everything.
  if (!enabled) {
    return { phase: "stopped", isLive: false, isCold: isContainer };
  }

  if (isContainer) {
    return _containerPhase(slot, now);
  }
  return _lemondPhase(slot, now);
}

function _containerPhase(slot, now) {
  const cs = String(slot?.container_status || "stopped");
  const health = !!slot?.container_health;
  const state = String(slot?.state || "offline");

  // lemonade_state="disabled" means the SLOT is disabled — but the enabled
  // check above already handles that. Container slots can also carry a top-level
  // slot state value from the enrichment's `state` mirror.
  if (state === "error") {
    return { phase: "crashed", isLive: false, isCold: true };
  }
  if (cs === "crashed") {
    return { phase: "crashed", isLive: false, isCold: true };
  }
  if (cs === "pulling") {
    return { phase: "pulling", isLive: false, isCold: true };
  }
  if (cs === "starting" || (cs === "running" && !health)) {
    return { phase: "starting", isLive: false, isCold: true };
  }
  if (cs === "running" && health) {
    // Is it actively serving? Check last_used_at recency.
    const lastUsedMs = typeof slot?.last_used_at === "number"
      ? slot.last_used_at * 1000 : null;
    const deltaMs = lastUsedMs != null ? now - lastUsedMs : null;
    const recentlyServing =
      state === "serving" && (deltaMs == null || deltaMs <= RECENTLY_LIVE_MS);
    if (recentlyServing) {
      return { phase: "serving", isLive: true, isCold: true };
    }
    return { phase: "ready", isLive: true, isCold: true };
  }
  // stopped or unknown
  return { phase: "stopped", isLive: false, isCold: true };
}

function _lemondPhase(slot, now) {
  const state = String(slot?.state || "offline");
  const lemo = String(slot?.lemonade_state || "");
  const lastUsedMs = typeof slot?.last_used_at === "number"
    ? slot.last_used_at * 1000 : null;
  const deltaMs = lastUsedMs != null ? now - lastUsedMs : null;

  if (state === "error") {
    return { phase: "crashed", isLive: false, isCold: false };
  }
  if (lemo === "disabled") {
    return { phase: "stopped", isLive: false, isCold: false };
  }
  if (state === "pulling") {
    return { phase: "pulling", isLive: false, isCold: false };
  }
  if (state === "warming" || state === "starting" || state === "unloading") {
    return { phase: "starting", isLive: false, isCold: false };
  }
  if (state === "serving") {
    const stuck = deltaMs != null && deltaMs > RECENTLY_LIVE_MS;
    if (stuck) return { phase: "ready", isLive: true, isCold: false }; // hung guard → yellow
    return { phase: "serving", isLive: true, isCold: false };
  }
  if (lemo === "loaded" || lemo === "ready" || state === "ready") {
    return { phase: "ready", isLive: true, isCold: false };
  }
  if (lemo === "idle" || state === "idle") {
    return { phase: "idle", isLive: false, isCold: false };
  }
  return { phase: "stopped", isLive: false, isCold: false };
}

// ─── Thin projections consumed by each existing classifier ───────────────
//
// These replace the independent classification logic in the four sites.
// Each output format is IDENTICAL to what was there before for lemonade
// slots; we only add the container branch.

/**
 * Project slotPhase() → the {cls, label, tooltip} shape slotIndicator returns.
 *
 * slotIndicator() is the public function called from IndicatorDot and tests.
 * It MUST stay compatible with the existing test suite.
 */
export function slotIndicatorFromPhase(slot, now = Date.now()) {
  // For lemond slots: preserve the EXACT original logic (spec-pinned by
  // slot-indicator.spec.ts). slotIndicator() calls this function only for
  // container slots; for lemond slots it falls through to the old code.
  // (We can't inline the old logic perfectly in slotPhase because the
  // hung-SERVING guard maps to "stale" cls, not "ready" phase — so the
  // two vocabularies differ. Keep them separate to avoid breaking tests.)
  if (_isContainer(slot)) {
    return _containerIndicator(slot, now);
  }

  // Fallback: callers should not reach here; the original slotIndicator()
  // remains the canonical path for lemond slots. This projection is only
  // used from the container branch.
  const state = String(slot?.state || "offline");
  return { cls: "offline", label: state, tooltip: state };
}

function _containerIndicator(slot, now) {
  const cs = String(slot?.container_status || "stopped");
  const health = !!slot?.container_health;
  const state = String(slot?.state || "offline");
  const enabled = slot?.enabled !== false;
  const model = slot?.model || slot?.model_id || slot?.model_default || "";
  const errorMsg = slot?.metadata?.message || slot?.message || "";
  const lastUsedMs = typeof slot?.last_used_at === "number"
    ? slot.last_used_at * 1000 : null;
  const deltaMs = lastUsedMs != null ? now - lastUsedMs : null;

  if (!enabled) {
    return { cls: "offline", label: "off", tooltip: "Disabled" };
  }
  if (state === "error" || cs === "crashed") {
    return {
      cls: "error",
      label: "error",
      tooltip: errorMsg ? `Error: ${errorMsg}` : "Container failed",
    };
  }
  if (cs === "pulling") {
    return {
      cls: "warming",
      label: "pulling",
      tooltip: "Pulling container image…",
    };
  }
  if (cs === "starting" || (cs === "running" && !health)) {
    return {
      cls: "warming",
      label: "starting",
      tooltip: model ? `Starting container — ${model}…` : "Starting container…",
    };
  }
  if (cs === "running" && health) {
    // Actively serving?
    const recentlyServing =
      state === "serving" && (deltaMs == null || deltaMs <= RECENTLY_LIVE_MS);
    if (recentlyServing) {
      return {
        cls: "serving",
        label: "serving",
        tooltip: model ? `Serving ${model}` : "Serving",
      };
    }
    return {
      cls: "stale",
      label: "ready",
      tooltip: deltaMs != null
        ? `Ready — last used ${_formatAgo(deltaMs)}`
        : (model ? `Ready — ${model} healthy` : "Ready — container healthy"),
    };
  }
  // stopped
  return {
    cls: "offline",
    label: "stopped",
    tooltip: "Container stopped",
  };
}

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

/**
 * Project slotPhase() → stateChipClass-compatible CSS class.
 * Used in slot-modals.jsx to color lifecycle state chips.
 *
 * Signature is the same as the original stateChipClass(state) but extends
 * to handle container runtime phases.
 */
export function stateChipClassForSlot(slot) {
  if (!slot) return "chip";
  if (!_isContainer(slot)) {
    // For lemond slots the original stateChipClass is still called directly.
    return null; // sentinel: caller uses original stateChipClass
  }
  const { phase } = slotPhase(slot);
  if (phase === "serving" || phase === "ready") return "chip ok";
  if (phase === "starting" || phase === "pulling") return "chip warn";
  if (phase === "crashed") return "chip err";
  return "chip";
}

// The exact legacy LIVE_STATES set memory-map.jsx used: a lemond slot was
// "live" (attributed memory) iff its raw state string was one of these.
// This MUST stay byte-identical for lemond slots — slotPhase().isLive
// diverges (it folds in enabled + lemonade_state), so isSlotLive does NOT
// reuse it for the lemond path. Container slots get their own live rule.
const LEMOND_LIVE_STATES = new Set(["ready", "serving", "idle", "warming"]);

/**
 * isLive test for memory-map attribution. Replaces the old LIVE_STATES set.
 *
 * Lemond slots: EXACT equivalence to old `LIVE_STATES.has(slot.state)` —
 *   live iff state ∈ {ready, serving, idle, warming}. No enabled/lemonade_state
 *   folding (that would change which lemond slots get memory attribution).
 * Container slots: live iff running + healthy (slotPhase → ready|serving).
 */
export function isSlotLive(slot) {
  if (_isContainer(slot)) {
    const { phase } = slotPhase(slot);
    return phase === "ready" || phase === "serving";
  }
  // Lemond: preserve legacy LIVE_STATES.has(state) semantics exactly.
  return LEMOND_LIVE_STATES.has(String(slot?.state || "").toLowerCase());
}

export { RECENTLY_LIVE_MS };
