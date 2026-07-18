// DIAGNOSTICS ▸ Doctor — NEW nav slot per task scope ("Add Doctor page in
// MINIMAL form; if nothing exists, a stub page that's clearly labeled").
// settings.jsx had no Doctor section — `hal0 doctor` exists as a CLI only
// (cli/doctor_commands.py / cli/doctor_verify.py); there's no HTTP
// `GET /api/doctor` with stable diagnosis IDs yet (spec (d).3, gated §21.4).
// Minimal stub, not a fabricated diagnostics UI.
export function DoctorPage() {
  return (
    <div className="s-section">
      <h2>Doctor</h2>
      <p className="desc">
        Environment + install health checks. Today this only exists as the{" "}
        <span className="mono">hal0 doctor</span> CLI command — there's no HTTP endpoint with
        stable diagnosis IDs yet to back a UI (spec §21.4). Run{" "}
        <span className="mono">hal0 doctor</span> on the host in the meantime.
      </p>
      <div className="s-panel">
        <div className="s-row" style={{padding: "18px 16px"}}>
          <span className="mono" style={{fontSize: 12, color: "var(--fg-4)"}}>not yet wired — placeholder</span>
        </div>
      </div>
    </div>
  );
}
