// Dependency-free tests for the Models catalog sort / tag-filter helpers
// (WS-13) used by the toolbar.
//
// Run: node ui/src/dash/__tests__/model-sort.test.mjs
//
// These pin the behaviours the UI relies on: unknown sort keys sink to the
// end (so a "sort by params" doesn't shove registry rows above real values),
// the sort is stable, and the "added" label stays humane.

import {
  MODEL_SORT_FIELDS,
  parseParamCount,
  modelSizeBytes,
  modelSortKey,
  sortModels,
  fmtAdded,
} from "../model-sort.js";

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
const ok = (cond, msg) => {
  if (!cond) fail(msg);
};

// ── param parsing ───────────────────────────────────────────────────
eq(parseParamCount("27B"), 27e9, "27B → 27e9");
eq(parseParamCount("350M"), 350e6, "350M → 350e6");
eq(parseParamCount("1.5B"), 1.5e9, "1.5B → 1.5e9");
eq(parseParamCount("74K"), 74e3, "74K → 74e3");
eq(parseParamCount("8"), 8e9, "bare number defaults to billions");
eq(parseParamCount("nonsense"), null, "unparseable → null");
eq(parseParamCount(undefined), null, "undefined → null");

// ── size resolution ─────────────────────────────────────────────────
eq(modelSizeBytes({ size_bytes: 1234 }), 1234, "size_bytes wins");
eq(modelSizeBytes({ size: "1 GB" }), 1024 ** 3, "legacy size string parsed");
eq(modelSizeBytes({}), null, "no size → null");

// ── sort: unknown keys sink, stable ─────────────────────────────────
{
  const rows = [
    { id: "b", name: "b", params: "7B" },
    { id: "a", name: "a", params: undefined }, // unknown params
    { id: "c", name: "c", params: "27B" },
  ];
  eq(
    sortModels(rows, "params", "desc").map((m) => m.id),
    ["c", "b", "a"],
    "params desc: real values ordered, unknown sinks last",
  );
  eq(
    sortModels(rows, "params", "asc").map((m) => m.id),
    ["b", "c", "a"],
    "params asc: unknown STILL sinks last (not floated by direction)",
  );
  eq(
    sortModels(rows, "name", "asc").map((m) => m.id),
    ["a", "b", "c"],
    "name asc alphabetical",
  );
}
{
  // stability: equal keys keep input order
  const rows = [
    { id: "x", name: "same" },
    { id: "y", name: "same" },
    { id: "z", name: "same" },
  ];
  eq(
    sortModels(rows, "name", "asc").map((m) => m.id),
    ["x", "y", "z"],
    "equal keys preserve input order (stable)",
  );
}
{
  // input is not mutated
  const rows = [{ id: "b", name: "b" }, { id: "a", name: "a" }];
  sortModels(rows, "name", "asc");
  eq(rows.map((m) => m.id), ["b", "a"], "sortModels does not mutate input");
}

// ── modelSortKey ────────────────────────────────────────────────────
eq(modelSortKey({ name: "Foo" }, "name"), "foo", "name key lowercased");
eq(modelSortKey({ created: 100 }, "added"), 100, "added key = created ts");
eq(modelSortKey({ created: 0 }, "added"), null, "created 0 → null (unknown)");

// ── added label ─────────────────────────────────────────────────────
{
  const now = 1_700_000_000; // fixed clock (seconds)
  eq(fmtAdded(now, now), "today", "same instant → today");
  eq(fmtAdded(now - 3 * 86400, now), "3d ago", "3 days → 3d ago");
  eq(fmtAdded(0, now), "—", "0 → unknown dash");
  eq(fmtAdded(undefined, now), "—", "undefined → unknown dash");
  eq(fmtAdded(now + 86400, now), "—", "future → dash (no negative ages)");
}

// ── the sort field vocabulary is what the toolbar expects ───────────
eq(
  MODEL_SORT_FIELDS.map((f) => f.id),
  ["name", "size", "params", "added"],
  "sort field ids match the toolbar dropdown",
);

if (failures) {
  console.error(`\n${failures} assertion(s) failed`);
  process.exit(1);
}
console.log("✓ model-sort: all assertions passed");
