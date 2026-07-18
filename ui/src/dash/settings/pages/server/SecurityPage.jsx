// SERVER ▸ Security — NEW page, entirely blocked on auth (spec KB-1/§1: "No
// auth exists anywhere" — HAL0_API_KEY appears nowhere in src/hal0). Visible
// but disabled per P3-ui MVP scope: gate Security + Hardware Tuning as
// "coming with auth/tuning lanes" rather than half-build an unauthenticated
// admin surface.
export function SecurityPage() {
  return (
    <div className="s-section">
      <h2>Security &amp; Access</h2>
      <p className="desc">
        API keys, admin/client access, network-exposure policy. Coming with the auth lane
        (spec §1 / KB-1) — hal0 has no authentication surface today, so this page is a
        placeholder rather than a control panel with nothing to enforce.
      </p>
      <div className="s-panel">
        <div className="s-row" style={{padding: "18px 16px"}}>
          <span className="mono" style={{fontSize: 12, color: "var(--fg-4)"}}>
            ⛔ blocked — coming with the auth lane
          </span>
        </div>
      </div>
    </div>
  );
}
