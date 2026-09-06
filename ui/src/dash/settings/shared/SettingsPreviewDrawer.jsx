// ─── settings preview drawer (#1967, #2195, #2203, #1511) ──────────────────
//
// Renders the ChangeSet POST /api/settings/preview returns — before/after
// per key, kind (added/removed/changed), and reload class per key — so the
// operator sees exactly what a save will write BEFORE committing. The apply
// (PUT /api/settings) returns the identical shape under `_hal0.changeset`
// (same server function, hal0.api._settings_changeset.compute_settings_
// changeset, behind both), so this drawer can never promise something the
// apply doesn't keep.
//
// `Drawer` is a window global (dash/primitives.jsx), used unimported per
// the established convention (see capabilities/shared.jsx's FieldInfoIcon
// note) — right-side drawer, never a new modal dialog, per COMMON.md.
import { ApplyBadge } from './ApplyBadge.jsx'

function formatChangeValue(v) {
  if (v === null || v === undefined) {
    return <span className="mono" style={{color: "var(--fg-4)"}}>—</span>;
  }
  if (typeof v === "object") {
    return <span className="mono">{JSON.stringify(v)}</span>;
  }
  return <span className="mono">{String(v)}</span>;
}

const KIND_LABEL = { added: "added", removed: "removed", changed: "changed" };

/**
 * @param {{
 *   open: boolean,
 *   onClose: () => void,
 *   changes: Array<{path: string, before: unknown, after: unknown, kind: string, apply_class: string|null, services: string[]}>,
 *   unknown?: string[],
 *   onConfirm: () => void,
 *   confirming?: boolean,
 * }} props
 */
export function SettingsPreviewDrawer({ open, onClose, changes, unknown, onConfirm, confirming = false }) {
  const rows = changes || [];
  // Fake a registry keyed by path so ApplyBadge — which normally resolves a
  // key against the shared apply-plan registry — can render straight off
  // this ChangeSet's own apply_class/services instead of a second lookup.
  const registry = {};
  for (const c of rows) registry[c.path] = { apply_class: c.apply_class, services: c.services };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      eyebrow="Review before apply"
      title={rows.length === 1 ? "1 setting will change" : `${rows.length} settings will change`}
      width={480}
      foot={
        <>
          <span style={{color: "var(--fg-4)"}}>Values persist immediately on Apply.</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" disabled={confirming || rows.length === 0} onClick={onConfirm}>
              {confirming ? "Applying…" : "Apply"}
            </button>
          </span>
        </>
      }
    >
      {rows.length === 0 && (
        <div style={{color: "var(--fg-4)", fontSize: 12, padding: "8px 0"}}>No changes to apply.</div>
      )}
      {rows.map(c => (
        <div key={c.path} className="s-row" style={{alignItems: "flex-start", flexWrap: "wrap"}}>
          <div className="k" style={{flexBasis: "100%"}}>
            <span className="mono">{c.path}</span>
            <span
              className="mono"
              style={{marginLeft: 8, fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.04em"}}
            >
              {KIND_LABEL[c.kind] || c.kind}
            </span>
          </div>
          <div className="v" style={{display: "flex", alignItems: "center", gap: 6}}>
            {formatChangeValue(c.before)}
            <span style={{color: "var(--fg-4)"}}>→</span>
            {formatChangeValue(c.after)}
          </div>
          <div className="ac"><ApplyBadge settingsKey={c.path} registry={registry} /></div>
        </div>
      ))}
      {unknown && unknown.length > 0 && (
        <div className="err" style={{marginTop: 12, fontSize: 11}}>
          No reload-effect data for: {unknown.join(", ")}
        </div>
      )}
    </Drawer>
  );
}
