// MODELS ▸ Library & Downloads — WIRED (Phase-2 settings-seam lane).
//
// Was a bare "not yet wired — placeholder" stub (SWEEP §5). The full
// search/pull/catalog experience already lives on the main Models view
// (dash/models.jsx: HfSearchPanel, ModelRow, DownloadsPane) over real,
// already-shipped routes (/api/hf/search, /api/models/pulls, /api/models/
// {id}/pull). Rebuilding that UI a second time inside Settings would be
// net-new feature work AND a second owner for the same fact — so this page
// does NOT duplicate search/browse. It wires the two facts that genuinely
// belong on a Settings page:
//   - catalog size (read via the same useModels() the Models view uses)
//   - background pull/download status + cancel (spec (b): "downloads
//     bg/cancel = E(/api/models/pulls)") — reused directly from
//     dash/models.jsx's DownloadsPane (window-global; models.jsx is still
//     pre-ESM, so this follows the existing `window.X ? <window.X/> : null`
//     consumption pattern used elsewhere, e.g. dash/slots.jsx — no ESM
//     conversion of models.jsx, that's a separate lane).
// Search, variant selection, and extra-dir scan stay on the Models view
// (linked below) until §3 lands a settings-scoped surface for them.
import { useModels } from '@/api/hooks/useModels'

export function LibraryDownloadsPage() {
  const modelsQuery = useModels()
  const models = modelsQuery.data || []
  const installed = models.filter(m => m.installed).length

  return (
    <div className="s-section">
      <h2>Library &amp; Downloads</h2>
      <p className="desc">
        Model catalog size and background pull status. HuggingFace search, browsing, and starting a
        new pull live on the <a href="#models">Models</a> view — this page surfaces catalog state and
        lets you track/cancel downloads without leaving Settings. Storage location lives on{' '}
        <b>Data ▸ Storage</b>.
      </p>

      <div className="s-panel">
        <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
          <div className="k"><span>Catalog</span><FieldInfoIcon description="/api/models · blessed + pulled models" /></div>
        </div>
        {modelsQuery.isPending && (
          <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>Loading catalog…</div>
        )}
        {modelsQuery.isError && (
          <div className="err">{modelsQuery.error?.message || 'Failed to read /api/models'}</div>
        )}
        {!modelsQuery.isPending && !modelsQuery.isError && (
          <div className="s-row">
            <div className="k"><span>Models known to the catalog</span></div>
            <div className="v mono">{installed} installed / {models.length} total</div>
          </div>
        )}
      </div>

      <div className="s-panel" style={{ marginTop: 12 }}>
        <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
          <div className="k"><span>Downloads</span><FieldInfoIcon description="/api/models/pulls · background pull jobs, live" /></div>
        </div>
        {window.DownloadsPane ? <window.DownloadsPane /> : (
          <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>
            Downloads panel unavailable — open <a href="#models">Models</a> to manage pulls.
          </div>
        )}
      </div>
    </div>
  );
}
