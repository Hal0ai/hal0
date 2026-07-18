// MODELS ▸ Library & Downloads — NEW nav slot per spec (e) MVP cut. There is
// no existing settings.jsx section for this today: model storage location
// lives on the DATA▸Storage page (StoragePage.jsx, moved from
// StorageSection); HF search/pull/variant UI (spec (b): MODELS▸Library&Downloads
// = /api/hf/search + /api/models/pulls, existing backend routes) has no
// frontend surface yet. Building that UI is real new feature work, out of
// scope for this refactor-to-parity pass — stub only, clearly labeled, same
// treatment as DoctorPage.
export function LibraryDownloadsPage() {
  return (
    <div className="s-section">
      <h2>Library &amp; Downloads</h2>
      <p className="desc">
        HuggingFace search, pulls, and catalog management. The backend routes exist
        (<span className="mono">/api/hf/search</span>, <span className="mono">/api/models/pulls</span>) but there's no
        settings UI yet — tracked separately (spec §3). Model storage location lives on the
        Storage page (Data ▸ Storage).
      </p>
      <div className="s-panel">
        <div className="s-row" style={{padding: "18px 16px"}}>
          <span className="mono" style={{fontSize: 12, color: "var(--fg-4)"}}>not yet wired — placeholder</span>
        </div>
      </div>
    </div>
  );
}
