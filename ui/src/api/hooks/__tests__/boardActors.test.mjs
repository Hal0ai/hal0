// Dependency-free test for the board assignee/profile normaliser.
//
// Run: node ui/src/api/hooks/__tests__/boardActors.test.mjs
//
// Bug of record: React error #31 ("Objects are not valid as a React child")
// crashed the whole Operator Board to a black screen the moment a task was
// added. /api/board/{profiles,assignees} are proxied straight to Hermes,
// which returns actors shaped {name, on_disk, counts} — they carry `name`,
// NOT the `id`/`label` the UI assumed. board-view's `p.id ?? p` /
// `p.label ?? p.id ?? p` fallbacks then ended at the raw object, which React
// cannot render. normaliseActor must guarantee primitive id+label so a raw
// object can never reach a JSX child position.

import { normaliseAssignee, normaliseProfile } from "../boardActors.js";

let failures = 0;
const ok = (cond, msg) => { if (!cond) { failures += 1; console.error("  ✗ " + msg); } else { console.log("  ✓ " + msg); } };
const isPrimitive = (v) => v === null || (typeof v !== "object" && typeof v !== "function");

// 1. THE BUG: a Hermes actor {name, on_disk, counts} must yield primitive id/label.
{
  const a = normaliseAssignee({ name: "scout", on_disk: true, counts: { ready: 1 } });
  ok(a.id === "scout", "name → id");
  ok(a.label === "scout", "name → label");
  ok(isPrimitive(a.id) && isPrimitive(a.label), "id and label are primitives, never the raw object");
}

// 2. an explicit {id,label} actor is preserved unchanged.
{
  const a = normaliseAssignee({ id: "hermes", label: "Hermes" });
  ok(a.id === "hermes" && a.label === "Hermes", "explicit id/label preserved");
}

// 3. a bare string actor normalises to {id,label} of itself.
{
  const a = normaliseAssignee("nova");
  ok(a.id === "nova" && a.label === "nova", "string actor → {id,label}");
}

// 4. profiles carry a count; it is preserved and surfaced as `count`.
{
  const p = normaliseProfile({ name: "builder", on_disk: true, counts: { todo: 3 }, count: 5 });
  ok(p.id === "builder" && p.label === "builder", "profile name → id/label");
  ok(p.count === 5, "explicit count preserved");
}

// 5. a degenerate actor with no usable label still never returns an object.
{
  const a = normaliseAssignee({ on_disk: false, counts: {} });
  ok(isPrimitive(a.id) && isPrimitive(a.label), "no name/id/label → still primitive (no raw-object leak)");
}

if (failures) { console.error(`\n${failures} boardActors assertion(s) failed`); process.exit(1); }
else { console.log("\nall boardActors assertions passed"); }
