// hal0 dashboard — DCard shell + StatusDot primitives (W1 foundation)
// Window-global module: exports DCard + StatusDot onto window for use by
// all other dash modules in the window-globals shim pattern.
//
// DCard  — generic card shell (header + body) for the overhaul grid.
// StatusDot — §3 four-state dot. Reuses slotIndicatorFromPhase from slot-status.js.
//
// Import order: must come after primitives.jsx in main.tsx (slot-status.js is
// a plain ES module and is imported directly, not via window).

import { slotIndicatorFromPhase } from './slot-status.js';

const { React: _React } = window;
// Use the window-installed React globals (same pattern as all other dash modules).
const { createElement: h } = React;

// ─── DCard ───────────────────────────────────────────────────────────────────
// Props:
//   icon        — optional ReactNode, rendered at 13×13 in .dcard-h-icon
//   title       — string, required (header label)
//   note        — optional string, right-of-title muted annotation
//   right       — optional ReactNode, far-right slot in header (controls)
//   children    — card body content
//   className   — appended to .dcard root
//   bodyClassName — appended to .dcard-b
//   noPad       — bool, removes body padding (e.g. slot-list card)
function DCard({ icon, title, note, right, children, className, bodyClassName, noPad }) {
  return (
    <div className={"dcard" + (className ? " " + className : "")}>
      <div className="dcard-h">
        {icon && <span className="dcard-h-icon">{icon}</span>}
        <span className="dcard-h-title">{title}</span>
        <span className="dcard-h-spacer" />
        {note && <span className="dcard-h-note">{note}</span>}
        {right && <span className="dcard-h-right">{right}</span>}
      </div>
      <div className={"dcard-b" + (noPad ? " no-pad" : "") + (bodyClassName ? " " + bodyClassName : "")}>
        {children}
      </div>
    </div>
  );
}

// ─── StatusDot ───────────────────────────────────────────────────────────────
// §3 four-state dot. Derives cls from a slot object OR accepts explicit
// phase/cls override strings for non-slot use cases.
//
// Props:
//   slot   — slot object from /api/slots (preferred; drives slotIndicatorFromPhase)
//   phase  — string override (maps to cls directly; used when no slot object)
//   cls    — string override (raw dot class; highest priority)
//   size   — number|string, dot diameter in px (default 8)
//   title  — tooltip override (defaults to slotIndicatorFromPhase tooltip)
//
// className contract (applied to <span>):
//   "sdot serving"  — green pulsing (actively processing)
//   "sdot stale"    — amber static (container ready, not serving = ready state)
//   "sdot warming"  — orange pulsing (warming/starting/pulling)
//   "sdot error"    — red static (error/crashed)
//   "sdot offline"  — grey static (idle/stopped/disabled)
//
// slotIndicatorFromPhase returns { cls, label, tooltip }
// cls values from slot-status.js: "serving" | "stale" | "warming" | "error" | "offline"

const _PHASE_TO_CLS = {
  serving:   "serving",
  ready:     "stale",    // "ready" phase → stale cls (amber, static)
  starting:  "warming",
  pulling:   "warming",
  idle:      "offline",
  stopped:   "offline",
  crashed:   "error",
  error:     "error",
  offline:   "offline",
  missing:   "offline",
};

function StatusDot({ slot, phase, cls: clsOverride, size = 8, title: titleOverride }) {
  let dotCls, tooltip;

  if (clsOverride) {
    // Raw cls passed directly — highest priority.
    dotCls = clsOverride;
    tooltip = titleOverride || dotCls;
  } else if (slot) {
    // Derive from real slot object via the canonical classifier.
    const ind = slotIndicatorFromPhase(slot);
    dotCls = ind.cls;
    tooltip = titleOverride || ind.tooltip;
  } else if (phase) {
    // Phase string override — map through phase→cls table.
    dotCls = _PHASE_TO_CLS[phase] || "offline";
    tooltip = titleOverride || phase;
  } else {
    dotCls = "offline";
    tooltip = titleOverride || "unknown";
  }

  const style = size !== 8 ? { width: size, height: size } : undefined;

  return (
    <span
      className={"sdot " + dotCls}
      title={tooltip}
      style={style}
      aria-label={tooltip}
    />
  );
}

Object.assign(window, { DCard, StatusDot });
