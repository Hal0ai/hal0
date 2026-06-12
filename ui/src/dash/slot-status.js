// slot-status.js — container slot phase classifier (N1 unification).
//
// Single source of truth for the status vocabulary shared across:
//   • slotIndicator (slots.jsx)    → {cls, label, tooltip}
//   • stateChipClass (slot-modals.jsx) → "chip ok|warn|err|"
//   • isSlotLive (memory-map.jsx)  → isLive boolean
//   • phase logic (slots.jsx)      → off|running|transitional button selector
//
// Phase vocabulary:
//   missing      — slot defined but no image / model present yet
//   pulling      — container image layer pull in progress
//   starting     — container systemd unit active but /health not yet ok
//   serving      — actively processing an in-flight request (GREEN)
//   ready        — container healthy, waiting for a prompt (YELLOW)
//   idle         — loaded but quiet; serves on next request (GREY)
//   stopped      — container cleanly offline (GREY)
//   crashed      — error / failed unit (RED)
//
// Colour rule:
//   GREEN  = processing (serving) — actively doing work RIGHT NOW
//   YELLOW = resident / ready     — container healthy, awaiting prompt
//   GREY   = not loaded           — disabled, stopped, idle, offline
//   RED    = error / crashed
//   AMBER  = transitional         — pulling / starting / unloading
//
// Every slot is a podman container. A slot snapshot that hasn't been
// enriched with `container_status` yet (e.g. a stale /api/status union
// entry) falls back to classification on its bare `state` string.

const RECENTLY_LIVE_MS = 60 * 60 * 1000; // 1h stuck-SERVING threshold

/**
 * Derive a unified phase from a slot snapshot.
 *
 * @param {object} slot  - normalised slot dict from /api/slots
 * @param {number} [now] - epoch ms (injectable for tests)
 * @returns {{ phase: string, isLive: boolean, isCold: boolean }}
 *   phase   — one of: missing|pulling|starting|serving|ready|idle|stopped|crashed
 *   isLive  — heuristic "holds memory" flag for the dot vocabulary. NOTE:
 *             this folds in `enabled`, so it can diverge from the memory-map
 *             attribution test — memory-map MUST use isSlotLive() (below).
 *   isCold  — always true (model swap = container restart, not hot-swap)
 */
export function slotPhase(slot, now = Date.now()) {
  const enabled = slot?.enabled !== false;

  // Disabled overrides everything.
  if (!enabled) {
    return { phase: "stopped", isLive: false, isCold: true };
  }
  return _containerPhase(slot, now);
}

function _containerPhase(slot, now) {
  const state = String(slot?.state || "offline");

  // Fallback path: no container enrichment yet — classify on the bare
  // state string so stale /api/status union entries still render sanely.
  if (slot?.container_status == null) {
    return _statePhase(state, slot, now);
  }

  const cs = String(slot.container_status);
  const health = !!slot?.container_health;

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

// State-string fallback for slots without container enrichment.
function _statePhase(state, slot, now) {
  const lastUsedMs = typeof slot?.last_used_at === "number"
    ? slot.last_used_at * 1000 : null;
  const deltaMs = lastUsedMs != null ? now - lastUsedMs : null;

  if (state === "error") {
    return { phase: "crashed", isLive: false, isCold: true };
  }
  if (state === "pulling") {
    return { phase: "pulling", isLive: false, isCold: true };
  }
  if (state === "warming" || state === "starting" || state === "unloading") {
    return { phase: "starting", isLive: false, isCold: true };
  }
  if (state === "serving") {
    const stuck = deltaMs != null && deltaMs > RECENTLY_LIVE_MS;
    if (stuck) return { phase: "ready", isLive: true, isCold: true }; // hung guard → yellow
    return { phase: "serving", isLive: true, isCold: true };
  }
  if (state === "ready") {
    return { phase: "ready", isLive: true, isCold: true };
  }
  if (state === "idle") {
    return { phase: "idle", isLive: false, isCold: true };
  }
  return { phase: "stopped", isLive: false, isCold: true };
}

// ─── Thin projections consumed by each existing classifier ───────────────

/**
 * Project slotPhase() → the {cls, label, tooltip} shape slotIndicator returns.
 *
 * slotIndicator() is the public function called from IndicatorDot and tests.
 * It MUST stay compatible with the existing test suite.
 */
export function slotIndicatorFromPhase(slot, now = Date.now()) {
  return _containerIndicator(slot, now);
}

function _containerIndicator(slot, now) {
  const cs = String(slot?.container_status || "stopped");
  const hasContainerStatus = slot?.container_status != null;
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
  if (cs === "pulling" || state === "pulling") {
    return {
      cls: "warming",
      label: "pulling",
      tooltip: "Pulling container image…",
    };
  }
  if (
    cs === "starting" ||
    (hasContainerStatus && cs === "running" && !health) ||
    (!hasContainerStatus && (state === "warming" || state === "starting"))
  ) {
    return {
      cls: "warming",
      label: "starting",
      tooltip: model ? `Starting container — ${model}…` : "Starting container…",
    };
  }
  const live = hasContainerStatus
    ? cs === "running" && health
    : state === "serving" || state === "ready" || state === "idle";
  if (live) {
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
    tooltip: "Container stopped (auto-reloads on next request)",
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
 */
export function stateChipClassForSlot(slot) {
  if (!slot) return "chip";
  const { phase } = slotPhase(slot);
  if (phase === "serving" || phase === "ready") return "chip ok";
  if (phase === "starting" || phase === "pulling") return "chip warn";
  if (phase === "crashed") return "chip err";
  return "chip";
}

/**
 * isLive test for memory-map attribution.
 *
 * Container-enriched slots: live iff running + healthy (slotPhase →
 * ready|serving). Un-enriched snapshots: live iff the bare state string is
 * one of {ready, serving, idle, warming} (legacy LIVE_STATES semantics).
 */
const STATE_LIVE_FALLBACK = new Set(["ready", "serving", "idle", "warming"]);

export function isSlotLive(slot) {
  if (slot?.container_status != null) {
    const { phase } = slotPhase(slot);
    return phase === "ready" || phase === "serving";
  }
  return STATE_LIVE_FALLBACK.has(String(slot?.state || "").toLowerCase());
}

export { RECENTLY_LIVE_MS };
