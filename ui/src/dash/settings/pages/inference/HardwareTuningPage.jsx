// INFERENCE ▸ Hardware Tuning — NEW "Danger Zone" page, entirely blocked
// per spec Decision D4 (§2/§21.1): Strix-Halo host tuning (gttsize / iommu /
// tuned / ppfeaturemask / ttm / swappiness) is scoped to the PVE host,
// outside hal0's install — surfaced as guided script generation, not a
// hal0 process mutating the host. Visible but disabled per P3-ui MVP scope.
export function HardwareTuningPage() {
  return (
    <div className="s-section">
      <h2>Hardware Tuning</h2>
      <p className="desc">
        Strix-Halo GTT size / IOMMU / tuned profile / ppfeaturemask / TTM / swappiness — host-level
        knobs, applied via a guided script + preview-diff + scheduled reboot (spec §2/§21.1, decision
        D4: manual runbook, not an in-process host mutation). Coming with the tuning lane.
      </p>
      <div className="s-panel">
        <div className="s-row" style={{padding: "18px 16px"}}>
          <span className="mono" style={{fontSize: 12, color: "var(--fg-4)"}}>
            🔒 blocked — coming with the tuning lane
          </span>
        </div>
      </div>
    </div>
  );
}
