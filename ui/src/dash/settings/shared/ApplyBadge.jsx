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
//   service-restart → amber "⟳ restart <service>"
//   manual-restart  → red "⚠ manual restart"
//
// Extracted verbatim from settings.jsx (P3-ui split, phase 1) — no
// window-global dependency, this component was always self-contained.
export function ApplyBadge({ settingsKey, registry }) {
  const entry = registry && registry[settingsKey];
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
        fontSize: 10,
        padding: "2px 8px",
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
      {isServiceRestart && (svc ? `⟳ restart ${svc}` : "⟳ restart")}
      {isManualRestart && "⚠ manual restart"}
    </span>
  );
}
