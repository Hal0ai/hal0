// Curated model "type" tags surfaced as toggles in the model edit pane.
//
// These are the operator-meaningful, behaviour-driving tags — distinct from
// provenance tags (`user-added`, `curated`) and quant/arch markers
// (`rocmfp4`, `strix`) that a model may also carry. The edit pane only lets
// operators flip the curated set; everything else is preserved verbatim.
//
//   mtp          → gates the per-slot MTP toggle (rocm dense speculative decode)
//   moe          → feeds the arbiter's is_moe context sizing
//   tool-calling → chat/agent routing signal
//   reasoning    → chat routing signal
//   coder        → coder-slot auto-selection
//   vision       → multimodal; normally GGUF-derived, toggle forces it on
export const MODEL_TYPE_TAGS = [
  "mtp",
  "moe",
  "tool-calling",
  "reasoning",
  "coder",
  "vision",
];

/**
 * Split a model's tag list into the curated types that are currently set and
 * the remaining (non-curated) tags to preserve untouched.
 *
 * @param {string[]|undefined} tags
 * @param {string[]} curated
 * @returns {{ selected: string[], other: string[] }}
 */
export function splitModelTags(tags, curated = MODEL_TYPE_TAGS) {
  const list = Array.isArray(tags) ? tags : [];
  const curatedSet = new Set(curated);
  return {
    selected: curated.filter((t) => list.includes(t)),
    other: list.filter((t) => !curatedSet.has(t)),
  };
}

/**
 * Recombine preserved (non-curated) tags with the operator's selected types
 * into the tag list to persist. Provenance-first ordering keeps the PUT diff
 * stable across saves, and de-dupes defensively.
 *
 * @param {string[]} otherTags  non-curated tags to preserve
 * @param {string[]} selectedTypes  curated types the operator turned on
 * @returns {string[]}
 */
export function mergeModelTags(otherTags, selectedTypes) {
  const seen = new Set();
  const out = [];
  for (const t of [...(otherTags || []), ...(selectedTypes || [])]) {
    if (!seen.has(t)) {
      seen.add(t);
      out.push(t);
    }
  }
  return out;
}
