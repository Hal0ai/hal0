// Dependency-free tests for the model "type" tag helpers used by the
// model edit pane (RecipeEditorModal).
//
// Run: node ui/src/dash/__tests__/model-types.test.mjs
//
// The edit pane lets operators flip a curated set of capability tags while
// PRESERVING any provenance/quant tags the model already carries. These tests
// pin the split (prefill) + merge (save) round-trip so a future refactor can't
// silently clobber tags like `user-added`.

import {
  MODEL_TYPE_TAGS,
  splitModelTags,
  mergeModelTags,
} from "../model-types.js";

let failures = 0;
const fail = (msg) => {
  failures += 1;
  console.error("  ✗ " + msg);
};
const eq = (a, b, msg) => {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    fail(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
  }
};

// ── split: prefill from a model's tags ──────────────────────────────
{
  const { selected, other } = splitModelTags(["user-added", "coder", "rocmfp4", "mtp"]);
  eq(selected, ["mtp", "coder"], "split selects curated in canonical order");
  eq(other, ["user-added", "rocmfp4"], "split preserves non-curated tags");
}
{
  const { selected, other } = splitModelTags(undefined);
  eq(selected, [], "split tolerates undefined tags (selected)");
  eq(other, [], "split tolerates undefined tags (other)");
}
{
  const { selected } = splitModelTags(["MTP"]); // wrong case is NOT curated
  eq(selected, [], "split is case-sensitive — 'MTP' is not the curated 'mtp'");
}

// ── merge: save union, provenance-first, deduped ────────────────────
{
  eq(
    mergeModelTags(["user-added", "coder"], ["coder", "mtp"]),
    ["user-added", "coder", "mtp"],
    "merge preserves provenance, adds new types, de-dupes overlap",
  );
}
{
  eq(mergeModelTags([], ["mtp"]), ["mtp"], "merge with no other tags = just types");
}
{
  eq(mergeModelTags(["user-added"], []), ["user-added"], "merge with no types keeps provenance");
}

// ── round-trip: split then merge with the same selection is identity ─
{
  const tags = ["user-added", "coder", "strix"];
  const { selected, other } = splitModelTags(tags);
  // toggling nothing: merge(other, selected) preserves the original set
  eq(
    mergeModelTags(other, selected).sort(),
    [...tags].sort(),
    "split→merge round-trip preserves the full tag set",
  );
}

// ── the curated set is what we expect ───────────────────────────────
eq(
  MODEL_TYPE_TAGS,
  ["mtp", "moe", "tool-calling", "reasoning", "coder", "vision"],
  "curated type set matches the agreed list",
);

if (failures) {
  console.error(`\n${failures} assertion(s) failed`);
  process.exit(1);
}
console.log("✓ model-types: all assertions passed");
