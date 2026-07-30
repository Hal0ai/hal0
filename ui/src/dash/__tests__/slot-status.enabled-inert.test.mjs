// #1369: a slot's phase is derived PURELY from container/lifecycle state.
//
// Run: node ui/src/dash/__tests__/slot-status.enabled-inert.test.mjs
//
// This replaces slot-status.disabled-running.test.mjs, whose scenario — a slot
// that is "disabled" while its container is up and healthy — is no longer
// representable. `SlotConfig.enabled` is gone; activation IS having a model
// bound, and a slot with a live container necessarily has one. The classifier
// used to short-circuit on `enabled === false` and needed an escape hatch so a
// GPU-holding container wasn't rendered as plain "off"; deleting the branch
// deletes that whole class of contradiction.
//
// What has to hold now:
//   1. A stale `enabled: false` on the wire (a pre-migration payload, or an
//      old client) is INERT — it must not mask a live container.
//   2. Two snapshots that differ ONLY by `enabled` classify identically.
//   3. A model-less slot is the "grey tile": no model bound, nothing running.

import {
  slotIndicatorFromPhase,
  slotButtonPhase,
  isSlotLive,
  slotPhase,
} from "../slot-status.js";

let failures = 0;
const check = (cond, msg) => {
  if (!cond) { failures += 1; console.error("  ✗ " + msg); }
  else console.log("  ✓ " + msg);
};

const running = {
  name: "utility",
  state: "ready",
  container_status: "running",
  container_health: true,
  model_default: "qwen3-4b",
};
// Byte-identical except for the removed field.
const runningWithStaleFlag = { ...running, enabled: false };

const a = slotIndicatorFromPhase(running);
const b = slotIndicatorFromPhase(runningWithStaleFlag);

check(a.cls === b.cls && a.label === b.label && a.tooltip === b.tooltip,
  `a stale enabled:false must not change the indicator ` +
  `(clean=${a.cls}/${a.label}, stale=${b.cls}/${b.label})`);
check(a.cls === "serving" || a.cls === "stale",
  `a running+healthy container reads as live (got "${a.cls}")`);
check(!/disabl/i.test(b.tooltip),
  `no "disabled" wording survives (got "${b.tooltip}")`);

check(slotButtonPhase(running) === "running",
  `a running slot offers Stop/Restart`);
check(slotButtonPhase(runningWithStaleFlag) === "running",
  `a stale enabled:false must not suppress lifecycle actions`);

check(isSlotLive(running) === true, `running+healthy counts as live`);
check(isSlotLive(runningWithStaleFlag) === true,
  `a stale enabled:false must not hide memory attribution`);

// The grey tile: no model bound, container stopped.
const unconfigured = {
  name: "img",
  state: "offline",
  container_status: "stopped",
  container_health: false,
};
const ind = slotIndicatorFromPhase(unconfigured);
check(ind.cls === "offline" && ind.label === "stopped",
  `a model-less, stopped slot is offline/stopped (got "${ind.cls}/${ind.label}")`);
check(slotButtonPhase(unconfigured) === "off",
  `a stopped slot offers Start`);
check(slotPhase(unconfigured).phase === "stopped",
  `slotPhase agrees it is stopped`);
check(isSlotLive(unconfigured) === false, `a stopped slot is not live`);

// A stopped slot carrying the stale flag is also just stopped — the old code
// returned early here, so this pins that the removal didn't change it.
check(slotPhase({ ...unconfigured, enabled: false }).phase === "stopped",
  `stale enabled:false on a stopped slot is still just stopped`);

if (failures) {
  console.error(`\nFAILED: ${failures} assertion(s)`);
  process.exit(1);
}
console.log("OK: enabled is inert — phase is purely state-derived");
