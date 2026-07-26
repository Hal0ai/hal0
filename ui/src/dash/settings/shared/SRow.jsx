// ─── shared row helper ───
//
// Not on the spec's explicit shared/ extraction list (ApplyBadge / schema
// engine / RestartApiPanel), but every page module below needs it — it was
// the generic key/value row layout used throughout the old settings.jsx.
// Extracted here rather than duplicated per-page so the row markup can't
// drift between pages during the split.
export const SRow = ({ k, sub, v, mono, children, actions }) => (
  <div className="s-row">
    <div className="k">
      <span>{k}</span>
      {sub && <FieldInfoIcon description={sub} />}
    </div>
    <div className={"v" + (mono ? " mono" : "")}>{children || v}</div>
    {actions && <div className="ac">{actions}</div>}
  </div>
);
