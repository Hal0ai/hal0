// ─── schema-driven settings row engine ───────────────────────────────────────
//
// Extracted verbatim from settings.jsx (~2061-2187, P3-ui split phase 1).
// Renders a single hal0.toml key from the live GET /api/settings/schema
// (pydantic field types/bounds/descriptions) so page copy can't drift from
// the backend and new constraints apply without frontend edits.
//
// Risk #5 (spec): the original file read `ApplyBadge`/`SRow` as implicit
// window-globals. Threaded here as real ES imports instead.
import { ApplyBadge } from './ApplyBadge.jsx'
import { SRow } from './SRow.jsx'

// Resolve $ref / single-allOf indirection in a pydantic JSON schema node.
export function _schemaResolve(schema, node) {
  let guard = 0;
  while (node && node.$ref && guard++ < 10) {
    node = node.$ref.replace(/^#\//, "").split("/").reduce((o, k) => (o ? o[k] : null), schema);
  }
  if (node && Array.isArray(node.allOf) && node.allOf.length === 1) {
    const inner = _schemaResolve(schema, node.allOf[0]) || {};
    const { allOf, ...rest } = node;
    return { ...inner, ...rest };
  }
  return node;
}

// Walk a dotted key ("slots.max_slots") to its field schema. Flattens
// Optional[T] (anyOf [T, null]) into T + {nullable:true}.
export function _schemaField(schema, dotKey) {
  if (!schema) return null;
  let node = schema;
  for (const part of dotKey.split(".")) {
    node = _schemaResolve(schema, node);
    node = node && node.properties ? node.properties[part] : null;
    if (!node) return null;
  }
  const wrapper = node;
  let f = { ..._schemaResolve(schema, node) };
  if (Array.isArray(f.anyOf)) {
    const nonNull = f.anyOf.find(a => a && a.type !== "null") || {};
    const nullable = f.anyOf.some(a => a && a.type === "null");
    const { anyOf, ...rest } = f;
    f = { ...nonNull, ...rest, nullable };
  }
  if (!f.description && wrapper.description) f.description = wrapper.description;
  return f;
}

export const _getIn = (obj, dotKey) =>
  dotKey.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

export const _deepMergePatch = (a, b) => {
  const out = { ...a };
  for (const k of Object.keys(b)) {
    const both = a && typeof a[k] === "object" && a[k] && !Array.isArray(a[k])
      && typeof b[k] === "object" && b[k] && !Array.isArray(b[k]);
    out[k] = both ? _deepMergePatch(a[k], b[k]) : b[k];
  }
  return out;
};

// Buffer string → typed value per the field schema. Returns {ok, value}.
export function _advCoerce(f, raw) {
  if (!f) return { ok: true, value: raw };
  if (f.type === "boolean") return { ok: true, value: !!raw };
  if (f.type === "integer" || f.type === "number") {
    const s = String(raw).trim();
    // Empty → null only when null is the field's actual default: the TOML
    // writer drops None values (exclude_none), so persisting null for a
    // field with a non-null default silently reverts on the next reload.
    if (s === "") return f.nullable && f.default == null ? { ok: true, value: null } : { ok: false };
    if (f.type === "integer" && !/^-?\d+$/.test(s)) return { ok: false };
    const n = f.type === "integer" ? parseInt(s, 10) : parseFloat(s);
    if (isNaN(n)) return { ok: false };
    if (f.minimum != null && n < f.minimum) return { ok: false };
    if (f.maximum != null && n > f.maximum) return { ok: false };
    if (f.exclusiveMinimum != null && n <= f.exclusiveMinimum) return { ok: false };
    return { ok: true, value: n };
  }
  return { ok: true, value: String(raw) };
}

export const _advInputStyle = {
  fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)",
  border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px",
};

// `options` lets a page override enum choices per-key (e.g. memory.engine's
// validator also accepts "cognee"/"mem0", which the factory silently maps to
// hindsight — offering them would lie, so the page passes its own allowlist).
// `descriptions` lets a page override a stale/missing schema description.
export function AdvRow({ dotKey, field, live, buf, onChange, registry, label: labelOverride, options: optionsOverride, description: descriptionOverride }) {
  const label = labelOverride || dotKey.split(".").slice(1).join(".");
  const desc = descriptionOverride || field?.description || "";
  const shortDesc = desc.length > 150 ? desc.slice(0, 147) + "…" : desc;
  const options = optionsOverride || field?.enum || null;
  const isBool = field?.type === "boolean";
  const isNum = field?.type === "integer" || field?.type === "number";
  const current = buf !== undefined ? buf : (isBool ? live === true : live == null ? "" : String(live));
  let control;
  if (isBool) {
    control = (
      <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
        <input type="checkbox" checked={!!current} onChange={e => onChange(dotKey, e.target.checked)} style={{accentColor: "var(--accent)"}} />
        <span>{current ? "enabled" : "disabled"}</span>
      </label>
    );
  } else if (options) {
    control = (
      <select value={current} onChange={e => onChange(dotKey, e.target.value)} style={_advInputStyle}>
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  } else if (isNum) {
    const bad = !_advCoerce(field, current).ok;
    control = (
      <input
        type="number" value={current}
        min={field.minimum} max={field.maximum}
        step={field.type === "number" ? "any" : 1}
        onChange={e => onChange(dotKey, e.target.value)}
        placeholder={field.default != null ? String(field.default) : ""}
        className="mono"
        style={{..._advInputStyle, width: 120, borderColor: bad ? "var(--err)" : "var(--line)"}}
      />
    );
  } else {
    control = (
      <input
        value={current} onChange={e => onChange(dotKey, e.target.value)}
        placeholder={field?.default != null ? String(field.default) : ""}
        className="mono" style={{..._advInputStyle, width: 260}}
      />
    );
  }
  return (
    <SRow
      k={label}
      sub={<span title={desc}>{shortDesc}</span>}
      v={control}
      actions={<ApplyBadge settingsKey={dotKey} registry={registry} />}
    />
  );
}
