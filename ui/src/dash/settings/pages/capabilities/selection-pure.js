// Pure helpers for capability-selection editing — no React, no API imports,
// so vitest can exercise them without a DOM.

// Id of a catalog picker row (rows are objects; legacy fixtures were bare strings).
export function rowId(m) {
  return m.id || m.model_id || m
}

// #1470: resolve the provider for the model id currently being edited.
// Prefer the catalog row (covers an unsaved local change); fall back to the
// persisted selection's provider only while the local id still matches what's
// saved. This keys engine-specific copy instead of hardcoding Kokoro facts.
export function resolveProvider(catalogItems, model, selection) {
  const row = catalogItems.find(m => rowId(m) === model)
  return row?.provider
    || (model && model === (selection?.model || "") ? selection?.provider : "") || ""
}

// #2026: ⬇ marker for pullable-but-absent catalog rows. A grouped picker row
// is "not downloaded" when every backend it advertises reports
// downloaded:false — applying it enabled can only 409
// (capability.model_not_downloaded) until the weights are pulled from the
// Models view. Rows without a backends list (legacy fixtures, free-text ids)
// are never marked.
export function rowNeedsPull(m) {
  const backends = m?.backends
  if (!Array.isArray(backends) || backends.length === 0) return false
  return backends.every(b => b?.downloaded === false)
}
