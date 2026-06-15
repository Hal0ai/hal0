// Dependency-free consistency test for the slot-card status vocabulary.
//
// Run: node ui/src/dash/__tests__/slot-status.consistency.test.mjs
//
// The dashboard slot card renders three signals from the same slot snapshot:
//   • the status dot           (slotIndicatorFromPhase → {cls})
//   • the Start/Stop button    (slotButtonPhase → off|running|transitional)
// Historically the button reimplemented its own phase table inline in
// slots.jsx, which DIVERGED from the dot for `idle` and `unloading` — the
// card could show an "offline" dot beside a "Stop" button. This test pins
// the invariant that both derive from ONE classifier and can never
// contradict, across every state × enrichment combination.

import {
  slotIndicatorFromPhase,
  slotButtonPhase,
} from "../slot-status.js";

let failures = 0;
const fail = (msg) => {
  failures += 1;
  console.error("  ✗ " + msg);
};

// Frozen clock so last_used_at recency is deterministic.
const NOW = 1_700_000_000_000;

// cls → the button phase it MUST map to. serving/stale = loaded&healthy →
// you can Stop it; warming = transitional (buttons disabled); offline/error
// = not running → Start.
const CLS_TO_PHASE = {
  serving: "running",
  stale: "running",
  warming: "transitional",
  offline: "off",
  error: "off",
};

// Build the full matrix: bare snapshots (no container_status, the stale
// /api/status union entry) AND container-enriched snapshots.
const BARE_STATES = [
  "offline", "pulling", "starting", "warming", "unloading",
  "serving", "ready", "idle", "error",
];
const cases = [];
for (const state of BARE_STATES) {
  cases.push({ label: `bare:${state}`, slot: { name: "chat", state } });
}
// Enriched permutations.
for (const cs of ["running", "starting", "pulling", "stopped", "crashed"]) {
  for (const health of [true, false]) {
    cases.push({
      label: `enriched:${cs}/health=${health}`,
      slot: { name: "chat", state: "ready", container_status: cs, container_health: health },
    });
  }
}
// Disabled slot (button branch is bypassed in the UI, but the helper must
// still return a non-running phase so nothing offers "Stop" on a disabled slot).
cases.push({ label: "disabled", slot: { name: "chat", state: "ready", container_status: "running", container_health: true, enabled: false } });

console.log(`slot-status consistency: ${cases.length} cases`);
for (const { label, slot } of cases) {
  const ind = slotIndicatorFromPhase(slot, NOW);
  const phase = slotButtonPhase(slot, NOW);
  const expected = slot.enabled === false ? "off" : CLS_TO_PHASE[ind.cls];
  if (expected === undefined) {
    fail(`${label}: dot cls "${ind.cls}" has no defined button mapping`);
    continue;
  }
  if (phase !== expected) {
    fail(`${label}: dot=${ind.cls} → button expected "${expected}" but got "${phase}"`);
  }
}

if (failures) {
  console.error(`\nFAILED: ${failures} inconsistency(ies)`);
  process.exit(1);
}
console.log("OK: dot and button phase are consistent across all states");
