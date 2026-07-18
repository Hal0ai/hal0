// ─── settings reload-class source (R5 data seam · REWORK §K) ──────────────────
//
// THE single frontend source declaring what a settings change requires to
// take effect — "reload class" in REWORK §K's "one explicit reload/restart
// classification source". The three classes mirror the backend
// `_settings_apply.REGISTRY` taxonomy (immediate / service-restart /
// manual-restart) so the UI badge, the confirm-gate, and the save toast all
// read from ONE place instead of each page re-deriving it inline.
//
// Two inputs, one authoritative merge:
//
//   1. The backend apply-plan registry (GET /api/settings/apply-plan) — the
//      authoritative class for every key the pydantic `Hal0Config` surface
//      covers. Fetched once via useApplyPlan(); wins on every conflict.
//
//   2. RELOAD_CLASS_FALLBACK below — frontend-declared classes for keys the
//      backend registry does NOT enumerate. These are settings the pages
//      write through OTHER typed surfaces (per-slot `/api/slots/*/config`,
//      per-model `/api/models/*` defaults) that never appear in the
//      `/api/settings` apply-plan, plus any new `Hal0Config` key whose
//      backend registry row hasn't landed yet.
//
// This closes spec risk #2 / the "NPU hardcoded amber chip" anti-pattern
// (old settings.jsx:1888): a key with no class silently rendered no badge —
// or a hand-rolled one that drifts. `reloadClassFor()` now always resolves a
// class (or an explicit null) from this one source, so a page can't quietly
// no-op an effect badge.

// The closed class enum. Kept aligned with `_settings_apply.APPLY_CLASSES`.
export const RELOAD_CLASSES = ['immediate', 'service-restart', 'manual-restart']

// Symbolic service names — mirror `_settings_apply.SERVICE_*` so the badge
// text ("⟳ restart slots") matches the backend-sourced rows exactly.
export const SERVICE_SLOTS = 'slots'
export const SERVICE_HAL0_API = 'hal0-api'

// Frontend-declared reload classes for keys OUTSIDE the /api/settings
// apply-plan registry. Keyed by the dotted path the owning page uses.
//
// Namespacing convention: keys that are NOT `Hal0Config` paths are prefixed
// with their owning surface (`slot.*`, `model.*`) so they can never collide
// with a real backend registry key (which is always a bare `Hal0Config`
// dotted path like `slots.publish_host`). The backend registry always wins,
// so listing a bare Hal0Config key here is a harmless no-op once the backend
// row lands — it's only a bridge for keys the backend hasn't classified.
export const RELOAD_CLASS_FALLBACK = {
  // ── per-slot config (`/api/slots/{name}/config`) ────────────────────────
  // Every one of these re-renders the slot's Quadlet/ExecStart from config on
  // the next (re)start, so the owning slot unit must be bounced to observe the
  // new value. This is exactly what the NPU page hard-coded as an amber chip.
  'slot.model.context_size': { apply_class: 'service-restart', services: [SERVICE_SLOTS] },
  'slot.npu.embed': { apply_class: 'service-restart', services: [SERVICE_SLOTS] },
  'slot.npu.asr': { apply_class: 'service-restart', services: [SERVICE_SLOTS] },

  // ── per-model launch defaults (`/api/models/{id}` → ModelDefaults) ──────
  // A model's stored launch defaults (ctx / gpu-layers / chat-template) are
  // baked into a slot's argv at launch (§7.1a resolve_argv). A change is
  // observed only when a slot serving that model is (re)started — the model
  // registry write itself is immediate, but the running slot keeps its argv.
  'model.defaults.context_size': { apply_class: 'service-restart', services: [SERVICE_SLOTS] },
  'model.defaults.n_gpu_layers': { apply_class: 'service-restart', services: [SERVICE_SLOTS] },
  'model.defaults.chat_template': { apply_class: 'service-restart', services: [SERVICE_SLOTS] },
}

/**
 * Merge the authoritative backend registry over the frontend fallback into
 * one lookup table. Backend rows win — the fallback only fills keys the
 * backend doesn't classify. Pages should read `client.registry` (already
 * merged) rather than calling this directly.
 *
 * @param {Record<string, {apply_class: string, services?: string[]}>} backendRegistry
 * @returns {Record<string, {apply_class: string, services: string[]}>}
 */
export function mergeRegistry(backendRegistry) {
  const merged = {}
  for (const [k, v] of Object.entries(RELOAD_CLASS_FALLBACK)) {
    merged[k] = { apply_class: v.apply_class, services: [...(v.services || [])] }
  }
  for (const [k, v] of Object.entries(backendRegistry || {})) {
    if (!v) continue
    merged[k] = { apply_class: v.apply_class, services: [...(v.services || [])] }
  }
  return merged
}

/**
 * Resolve the reload class for a dotted settings key against a (merged or raw
 * backend) registry, falling back to the frontend declaration. Returns the
 * entry `{apply_class, services}` or `null` when no source classifies the key
 * (a genuine "unknown" — the caller may render an informational chip).
 *
 * @param {string} dotKey
 * @param {Record<string, {apply_class: string, services?: string[]}>} [registry]
 * @returns {{apply_class: string, services: string[]} | null}
 */
export function reloadClassFor(dotKey, registry) {
  const fromRegistry = registry && registry[dotKey]
  if (fromRegistry) {
    return { apply_class: fromRegistry.apply_class, services: [...(fromRegistry.services || [])] }
  }
  const fromFallback = RELOAD_CLASS_FALLBACK[dotKey]
  if (fromFallback) {
    return { apply_class: fromFallback.apply_class, services: [...(fromFallback.services || [])] }
  }
  return null
}

export const isImmediate = (dotKey, registry) =>
  reloadClassFor(dotKey, registry)?.apply_class === 'immediate'

export const needsServiceRestart = (dotKey, registry) =>
  reloadClassFor(dotKey, registry)?.apply_class === 'service-restart'

export const needsManualRestart = (dotKey, registry) =>
  reloadClassFor(dotKey, registry)?.apply_class === 'manual-restart'
