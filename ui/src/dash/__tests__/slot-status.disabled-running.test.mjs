// Regression: a DISABLED slot whose container is still running+healthy must
// not render identically to a disabled-and-stopped slot.
//
// Run: node ui/src/dash/__tests__/slot-status.disabled-running.test.mjs
//
// Repro of the "ready state / indicators" bug: the operator disables a slot
// but its podman container is still Up (healthy) and serving requests (e.g.
// started manually to test, or left running after a disable that didn't
// reconcile the container). The classifier short-circuited on enabled===false
// and reported {cls:"offline", label:"off"} — byte-identical to a slot that is
// disabled AND stopped — so a live, GPU-consuming, request-serving container
// was invisible on the dashboard.
//
// Correct behaviour: surface the live container with a distinct, non-offline
// indicator while still signalling that the slot is operator-disabled.

import {
  slotIndicatorFromPhase,
  slotButtonPhase,
  isSlotLive,
} from "../slot-status.js";

let failures = 0;
const check = (cond, msg) => {
  if (!cond) { failures += 1; console.error("  ✗ " + msg); }
  else console.log("  ✓ " + msg);
};

// Disabled but the container is genuinely Up + healthy (the bug scenario).
const disabledRunning = {
  name: "utility",
  enabled: false,
  state: "ready",
  container_status: "running",
  container_health: true,
};
// Disabled AND stopped (must stay plain "off").
const disabledStopped = {
  name: "img",
  enabled: false,
  state: "ready",
  container_status: "stopped",
  container_health: false,
};

const live = slotIndicatorFromPhase(disabledRunning);
const dead = slotIndicatorFromPhase(disabledStopped);

check(live.cls !== "offline",
  `disabled+running dot must NOT be "offline" (got "${live.cls}")`);
check(live.cls !== dead.cls || live.label !== dead.label,
  `disabled+running must look different from disabled+stopped ` +
  `(running=${live.cls}/${live.label}, stopped=${dead.cls}/${dead.label})`);
check(/disabl/i.test(live.tooltip),
  `disabled+running tooltip must still say it's disabled (got "${live.tooltip}")`);
check(/run|serv/i.test(live.tooltip),
  `disabled+running tooltip must say the container is still live (got "${live.tooltip}")`);

// Disabled+stopped stays plain offline/off.
check(dead.cls === "offline" && dead.label === "off",
  `disabled+stopped stays offline/off (got "${dead.cls}/${dead.label}")`);

// Invariant: a disabled slot never offers lifecycle actions via the dot-derived
// button (the card renders a disabled note instead). Must hold for BOTH.
check(slotButtonPhase(disabledRunning) === "off",
  `disabled+running button phase stays "off"`);
check(slotButtonPhase(disabledStopped) === "off",
  `disabled+stopped button phase stays "off"`);

// A disabled-but-running container is genuinely holding memory → live for
// memory-map attribution; a stopped one is not.
check(isSlotLive(disabledRunning) === true,
  `disabled+running counts as live (holds GPU/VRAM)`);
check(isSlotLive(disabledStopped) === false,
  `disabled+stopped is not live`);

if (failures) {
  console.error(`\nFAILED: ${failures} assertion(s)`);
  process.exit(1);
}
console.log("OK: disabled-but-running slot is surfaced distinctly");
