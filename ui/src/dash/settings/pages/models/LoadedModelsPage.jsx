// MODELS ▸ Loaded Models — default slot assignment per modality + slot-pool
// runtime limits. Extracted verbatim from settings.jsx SlotsSection (P3-ui
// split phase 1), relabelled to match the target IA (spec (e) MVP: "Loaded
// Models" — maps to MODELS▸Loaded, /api/slots/* per spec (b)). The `id`
// stays "slots" (unchanged) so existing #settings/slots deep links keep
// working; only the nav label + group changed.
import { useState } from 'react'
import { useSlots, useSlotEdit } from '@/api/hooks/useSlots'
import { useSettingsClient } from '../../data/settingsClient.js'
import { AdvRow, _schemaField, _getIn, _deepMergePatch, _advCoerce } from '../../shared/SchemaRow.jsx'

const RUNTIME_KEYS = [
  "slots.max_slots", "slots.port_range_start", "slots.port_range_end",
  "slots.idle_timeout_s", "slots.evict_pressure_mb", "slots.publish_host",
];
// Overrides the (stale/missing) schema description for a couple of keys —
// scoped to this page only (was a settings.jsx-wide ADV_DESC_OVERRIDE table;
// narrowed here since AdvRow no longer does a global lookup — see
// shared/SchemaRow.jsx).
const RUNTIME_DESC_OVERRIDE = {
  "slots.publish_host":
    "Host address slot ports publish on. 127.0.0.1 = loopback-only (default, safe): slots are reachable only via hal0-api/Traefik. " +
    "0.0.0.0 exposes every slot's raw port directly on your LAN (e.g. http://<host>.local:<port>), bypassing the reverse-proxy front door — " +
    "only widen this on a trusted network. A specific interface IP binds just that address. Applies on the next slot restart.",
};

export function LoadedModelsPage() {
  // --- Slot defaults (moved from former DefaultSlotsSection) ---
  const slotsQuery = useSlots();
  const editSlot = useSlotEdit();
  const slots = slotsQuery.data || [];
  const byType = {};
  for (const s of slots) { (byType[s.type] ||= []).push(s); }
  const types = Object.keys(byType).filter(t => byType[t].length >= 2).sort();

  const setDefault = async (type, name) => {
    const sibs = byType[type] || [];
    const prev = sibs.find(s => s.isDefault && s.name !== name);
    try {
      await editSlot.mutateAsync({ name, body: { default: true } });
      if (prev) await editSlot.mutateAsync({ name: prev.name, body: { default: false } });
      window.__hal0Toast && window.__hal0Toast(`Default ${type} slot → ${name}`, "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Couldn't set default — ${e?.message || "see logs"}`, "err");
    }
  };

  // --- Slots runtime (moved from Advanced: slots.max_slots etc.) ---
  // R5 data seam: one typed client instead of four ad-hoc hooks.
  const { settings, update, schema: schemaQuery, registry } = useSettingsClient({ schema: true });
  const schema = schemaQuery.data || null;
  const live = settings.data || null;

  const runtimeFields = {};
  for (const k of RUNTIME_KEYS) runtimeFields[k] = _schemaField(schema, k);

  const [buf, setBuf] = useState({});
  const onChange = (dotKey, value) => setBuf(b => ({ ...b, [dotKey]: value }));

  const runtimeDirty = Object.keys(buf).filter(k => {
    const { ok, value } = _advCoerce(runtimeFields[k], buf[k]);
    if (!ok) return true;
    const cur = _getIn(live, k);
    return value !== (cur === undefined ? (runtimeFields[k]?.default ?? null) : cur);
  });
  const invalidKeys = runtimeDirty.filter(k => !_advCoerce(runtimeFields[k], buf[k]).ok);
  const canSaveRuntime = runtimeDirty.length > 0 && invalidKeys.length === 0 && !update.isPending;

  const doSaveRuntime = async () => {
    let patch = {};
    for (const k of runtimeDirty) {
      const { value } = _advCoerce(runtimeFields[k], buf[k]);
      patch = _deepMergePatch(patch, k.split(".").reverse().reduce((acc, part) => ({ [part]: acc }), value));
    }
    try {
      await update.mutateAsync(patch);
      setBuf({});
      window.__hal0Toast && window.__hal0Toast("Slots runtime settings saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  return (
    <div className="s-section">
      <h2>Loaded Models</h2>
      <p className="desc">
        Default slot assignments per modality and runtime limits for the slot pool.
      </p>

      {/* --- Default slots --- */}
      {slotsQuery.isPending && (
        <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading slots…</div>
      )}
      {slotsQuery.isError && (
        <div className="err">{slotsQuery.error?.message || "Failed to load slots"}</div>
      )}
      {!slotsQuery.isPending && !slotsQuery.isError && types.length > 0 && (
        <div className="s-panel" style={{marginBottom: 12}}>
          <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
            <div className="k"><span>Default slots</span><FieldInfoIcon description="For each modality with multiple slots, pick the one that serves type-routed requests" /></div>
          </div>
          {types.map(type => {
            const cur = (byType[type].find(s => s.isDefault) || {}).name || "";
            return (
              <div className="default-slot-row form-row s-row" key={type}>
                <div className="k"><span>{type}</span></div>
                <div className="v">
                  <select
                    className="input mono"
                    value={cur}
                    disabled={editSlot.isPending}
                    onChange={e => { const n = e.target.value; if (n && n !== cur) setDefault(type, n); }}
                    style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}
                  >
                    {byType[type].map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
                  </select>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {!slotsQuery.isPending && !slotsQuery.isError && types.length === 0 && (
        <p className="hint" style={{fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)", marginBottom: 12}}>No modality has multiple slots yet.</p>
      )}

      {/* --- Runtime --- */}
      <div className="s-panel">
        <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
          <div className="k"><span>Runtime</span><FieldInfoIcon description="hal0.toml [slots]" /></div>
        </div>
        {RUNTIME_KEYS.map(k => (
          <AdvRow
            key={k}
            dotKey={k}
            field={runtimeFields[k]}
            live={_getIn(live, k)}
            buf={buf[k]}
            onChange={onChange}
            registry={registry}
            description={RUNTIME_DESC_OVERRIDE[k]}
          />
        ))}
        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {runtimeDirty.length > 0 && (
            <button className="btn ghost sm" onClick={() => setBuf({})}>Reset</button>
          )}
          <button className="btn sm" disabled={!canSaveRuntime} onClick={doSaveRuntime}>
            {update.isPending ? "Saving…" : "Save runtime"}
          </button>
        </div>
      </div>
    </div>
  );
}
