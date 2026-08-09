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

export function PanelFooter({ dirty, onReset, onSave, disabled, saving, label }) {
  return (
    <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
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
        {items.map(m => <option key={rowId(m)} value={rowId(m)}>{rowId(m)}</option>)}
      </select>
    ) : (
      <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
        className="mono" style={inputStyle(260)} />
    )
  } sub={items.length === 0 ? emptyHint : undefined} />
}
