// OBSERVABILITY ▸ Health & Stats — NEW nav slot per spec (e) MVP cut. No
// existing settings.jsx section covers this today. The backend routes exist
// (/api/stats/hardware|slots|power, /api/health, /api/metrics/prometheus,
// /api/slots/metrics — spec (d).5) but there's no settings-page UI yet.
// Building real charts/tables here is new feature work, out of scope for
// this refactor-to-parity pass — stub only, clearly labeled, same treatment
// as DoctorPage. (Telemetry on/off stays on the General page — unmoved.)
export function HealthStatsPage() {
  return (
    <div className="s-section">
      <h2>Health &amp; Stats</h2>
      <p className="desc">
        Live hardware / slot / power stats and health checks. The backend routes exist
        (<span className="mono">/api/stats/*</span>, <span className="mono">/api/health</span>,{" "}
        <span className="mono">/api/metrics/prometheus</span>) but there's no settings UI yet —
        tracked separately (spec §13).
      </p>
      <div className="s-panel">
        <div className="s-row" style={{padding: "18px 16px"}}>
          <span className="mono" style={{fontSize: 12, color: "var(--fg-4)"}}>not yet wired — placeholder</span>
        </div>
      </div>
    </div>
  );
}
