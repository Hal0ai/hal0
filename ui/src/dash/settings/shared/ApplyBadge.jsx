// ─── per-key apply badge (issue #552) ────────────────────────────────────────
//
// Shared apply-class chip style for settings rows.
// The registry is fetched once via useApplyPlan(); the component is
// purely presentational — it looks up the key, picks a colour, and
// renders the chip. If the registry hasn't loaded yet or the key is
// unknown, renders nothing so the row layout stays clean.
//
// Badge legend:
//   immediate       → green "live"
//   service-restart → amber "⟳" icon only — the affected service is in the
//                     hover title ("Requires restarting <service>…"), not the
//                     chip text (operator request: the repeated "restart
//                     hal0-api" label was visual noise on dense pages)
//   manual-restart  → red "⚠ manual restart"
//
// Extracted verbatim from settings.jsx (P3-ui split, phase 1) — no
// window-global dependency, this component was always self-contained.
//
// R5 data seam: the class now resolves through the ONE reload-class source
// (`reloadClassFor`), not a raw `registry[key]` lookup. That closes the
// "NPU hardcoded amber chip" anti-pattern (spec risk #2) — a key the backend
// apply-plan doesn't enumerate but the frontend fallback classifies (per-slot
// / per-model keys) now renders its real badge instead of silently nothing.
import { reloadClassFor } from '../data/reloadClass.js'

export function ApplyBadge({ settingsKey, registry }) {
  const entry = reloadClassFor(settingsKey, registry);
  if (!entry) return null;
  const cls = entry.apply_class;
  const isImmediate = cls === "immediate";
  const isServiceRestart = cls === "service-restart";
  const isManualRestart = cls === "manual-restart";
  const svc = isServiceRestart && entry.services && entry.services[0] ? entry.services[0] : null;
  return (
    <span
      className="chip"
      style={{
        fontFamily: "var(--jbm)",
        fontSize: isServiceRestart ? 13 : 10,
        lineHeight: isServiceRestart ? 1 : undefined,
        padding: isServiceRestart ? "2px 6px" : "2px 8px",
        whiteSpace: "nowrap",
        color: isImmediate ? "var(--ok)" : isServiceRestart ? "var(--warn)" : "var(--err)",
        borderColor: isImmediate ? "var(--ok)" : isServiceRestart ? "var(--warn)" : "var(--err)",
        background: isImmediate
          ? "rgba(46,204,113,0.08)"
          : isServiceRestart
            ? "rgba(255,176,0,0.08)"
            : "rgba(231,76,60,0.08)",
      }}
      title={
        isImmediate
          ? "Applied immediately on save — no restart needed"
          : isServiceRestart
            ? `Requires restarting ${svc || "service"} to take effect`
            : "Requires a manual operator restart to take effect"
      }
    >
      {isImmediate && "live"}
      {isServiceRestart && "⟳"}
      {isManualRestart && "⚠ manual restart"}
    </span>
  );
}
