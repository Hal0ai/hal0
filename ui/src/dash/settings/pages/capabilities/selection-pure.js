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
