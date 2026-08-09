// AI Capabilities page — shared presentational helpers. Replaces the
// statusChip / select-style copies VoicePage and ImageGenPage each carried
// (they were duplicated verbatim, flagged in the P3-ui split).
// FieldInfoIcon is a window global (dash/primitives.jsx) — used unimported,
// same as every settings page.
import { SRow } from '../../shared/SRow.jsx'
import { rowId } from './selection-pure.js'

export const selStyle = { fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px" }
export const inputStyle = (width) => ({ ...selStyle, width })

export function statusChip(st) {
  const color = st === "ready" || st === "serving" ? "var(--ok)" : st === "starting" || st === "warming" ? "var(--warn)" : "var(--fg-4)"
  return <span className="chip mono" style={{borderColor: color, color, fontSize: 10, padding: "1px 6px"}}>{st}</span>
}

export function PanelHeader({ title, info, chip, onToggle, open }) {
  return (
    <div
      className="s-row"
      style={{paddingBottom: 4, borderBottom: "1px solid var(--line)", cursor: onToggle ? "pointer" : undefined}}
      onClick={onToggle}
    >
      <div className="k">
        {onToggle && <span className="mono" style={{marginRight: 6, color: "var(--fg-4)"}}>{open ? "▾" : "▸"}</span>}
        <span>{title}</span>
        <FieldInfoIcon description={info} />
      </div>
      <div className="v">{chip}</div>
    </div>
  )
}

// `probe` is the panel's capabilities query (sel.capsQuery). While it is
// failing or still loading, Save is gated (#1467 — never write against
// unknown live state); without this note that gate reads as a dead grey
// button with no explanation, so say why and offer a Retry.
export function PanelFooter({ dirty, onReset, onSave, disabled, saving, label, probe }) {
  const note = probe?.isError
    ? <>
        <span style={{color: "var(--err)"}}>capability probe failed — Save disabled</span>
        <button className="btn ghost sm" style={{marginLeft: 8}} onClick={() => probe.refetch()}>Retry</button>
      </>
    : probe?.isLoading
      ? <span style={{color: "var(--fg-4)"}}>loading capability state…</span>
      : null
  return (
    <div style={{display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8, padding: "8px 12px 4px"}}>
      {note && <span className="mono" style={{marginRight: "auto", fontSize: 11, display: "inline-flex", alignItems: "center"}}>{note}</span>}
      {dirty && <button className="btn ghost sm" onClick={onReset}>Reset</button>}
      <button className="btn sm" disabled={disabled} onClick={onSave}>{saving ? "Saving…" : label}</button>
    </div>
  )
}

export function EnabledRow({ enabled, setEnabled }) {
  return <SRow k="Enabled" v={
    <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} style={{accentColor: "var(--accent)"}} />
  } />
}

export function ModelRow({ items, value, onChange, placeholder, emptyHint }) {
  return <SRow k="Model" v={
    items.length > 0 ? (
      <select value={value} onChange={e => onChange(e.target.value)} style={selStyle}>
        <option value="">— unset —</option>
        {value && !items.some(m => rowId(m) === value) && (
          // Saved selection missing from the catalog (uninstalled model,
          // stale registry) — surface it instead of silently rendering the
          // select as "— unset —" while the form state still holds the id.
          <option value={value}>{value} (saved)</option>
        )}
        {items.map(m => <option key={rowId(m)} value={rowId(m)}>{rowId(m)}</option>)}
      </select>
    ) : (
      <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
        className="mono" style={inputStyle(260)} />
    )
  } sub={items.length === 0 ? emptyHint : undefined} />
}
