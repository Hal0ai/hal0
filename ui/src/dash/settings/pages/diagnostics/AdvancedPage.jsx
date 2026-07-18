// DIAGNOSTICS ▸ Advanced — low-level dispatcher + activity-log tuning, plus
// the hal0-api restart control. Extracted verbatim from settings.jsx
// AdvancedSection (P3-ui split phase 1). The `id` stays "advanced"
// (unchanged) so #settings/advanced deep links keep working.
//
// Closes the hal0.toml ↔ UI parity gap: every [dispatcher] / [activity] key
// that previously required `hal0 config edit`. Controls are rendered FROM
// THE SERVER SCHEMA (GET /api/settings/schema — pydantic field types,
// bounds, and descriptions), so copy can't drift and new constraints apply
// without frontend edits. Saves go through the same deep-merging
// PUT /api/settings as the rest of the page; per-key effect chips come from
// the apply-plan registry, and any dirty manual-restart key routes through a
// confirm gate before the write.
import { useState, Fragment } from 'react'
import { useSettings, useSettingsUpdate, useSettingsSchema, useApplyPlan } from '@/api/hooks/useSettings'
import { ConfirmDialog } from '../../../primitives.jsx'
import { RestartApiPanel } from '../../shared/RestartApiPanel.jsx'
import { AdvRow, _schemaField, _getIn, _deepMergePatch, _advCoerce } from '../../shared/SchemaRow.jsx'

const ADV_GROUPS = [
  { title: "Dispatcher", sub: "hal0.toml [dispatcher] · upstream routing tunables", keys: [
    "dispatcher.prefetch_timeout_s", "dispatcher.prefetch_parallel_cap",
  ]},
  { title: "Activity log", sub: "hal0.toml [activity] · durable audit trail", keys: [
    "activity.enabled", "activity.retention_days", "activity.max_rows",
  ]},
];
// Overrides replace the schema description where it's stale or missing —
// page-scoped (was part of settings.jsx-wide ADV_DESC_OVERRIDE table).
const ACTIVITY_DESC_OVERRIDE = {
  "activity.enabled": "Record config changes and state transitions to the durable activity log.",
  "activity.retention_days": "Days of activity history to keep before pruning. The HAL0_ACTIVITY_RETENTION_DAYS env var, if set, overrides this value.",
  "activity.max_rows": "Hard cap on stored activity rows (minimum 100).",
};

export function AdvancedPage() {
  const settings = useSettings();
  const update = useSettingsUpdate();
  const schemaQuery = useSettingsSchema();
  const applyPlanQuery = useApplyPlan();
  const registry = applyPlanQuery.data?.registry || {};
  const schema = schemaQuery.data || null;
  const live = settings.data || null;

  // Edit buffer — dotKey → raw control value; only touched keys present.
  const [buf, setBuf] = useState({});
  const [confirmKeys, setConfirmKeys] = useState(null);
  const onChange = (dotKey, value) => setBuf(b => ({ ...b, [dotKey]: value }));

  const allKeys = ADV_GROUPS.flatMap(g => g.keys);
  const fields = {};
  for (const k of allKeys) fields[k] = _schemaField(schema, k);

  // A key is dirty when its coerced buffer value differs from the live one.
  const dirtyKeys = Object.keys(buf).filter(k => {
    const { ok, value } = _advCoerce(fields[k], buf[k]);
    if (!ok) return true; // invalid counts as dirty so Save stays visible (but disabled)
    const cur = _getIn(live, k);
    return value !== (cur === undefined ? (fields[k]?.default ?? null) : cur);
  });
  const invalidKeys = dirtyKeys.filter(k => !_advCoerce(fields[k], buf[k]).ok);
  const canSave = dirtyKeys.length > 0 && invalidKeys.length === 0 && !update.isPending;

  const doSave = async () => {
    let patch = {};
    for (const k of dirtyKeys) {
      const { value } = _advCoerce(fields[k], buf[k]);
      patch = _deepMergePatch(patch, k.split(".").reverse().reduce((acc, part) => ({ [part]: acc }), value));
    }
    // Restart hint comes from the client-side registry over the keys we're
    // about to write — computed BEFORE the buffer clears. (The PUT response
    // also carries _hal0.apply_plan, but deriving locally keeps the toast
    // correct against older backends that only matched top-level body keys.)
    const needsRestart = dirtyKeys.some(k => registry[k] && registry[k].apply_class !== "immediate");
    try {
      await update.mutateAsync(patch);
      setBuf({});
      window.__hal0Toast && window.__hal0Toast(
        needsRestart ? "Saved — restart hal0-api (below) to apply the marked changes" : "Advanced settings saved",
        needsRestart ? "warn" : "ok",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const onSaveClick = () => {
    const manual = dirtyKeys.filter(k => registry[k]?.apply_class === "manual-restart");
    if (manual.length > 0) { setConfirmKeys(manual); return; }
    doSave();
  };

  const loading = settings.isPending || schemaQuery.isPending;

  return (
    <div className="s-section">
      <h2>Advanced</h2>
      <p className="desc">
        Low-level dispatcher and activity log tuning. Slot runtime moved to Loaded Models; memory
        moved to Memory. Effect chips show whether a change applies live or needs a restart.
      </p>

      {loading && <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading config schema…</div>}
      {(settings.isError || schemaQuery.isError) && (
        <div className="err">{settings.error?.message || schemaQuery.error?.message || "Failed to load settings"}</div>
      )}

      {!loading && !settings.isError && !schemaQuery.isError && (
        <>
          {ADV_GROUPS.map(g => (
            <Fragment key={g.title}>
              <div className="s-panel" style={{marginBottom: 12}}>
                <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
                  <div className="k"><span>{g.title}</span><span className="sub">{g.sub}</span></div>
                </div>
                {g.keys.map(k => (
                  <AdvRow
                    key={k}
                    dotKey={k}
                    field={fields[k]}
                    live={_getIn(live, k)}
                    buf={buf[k]}
                    onChange={onChange}
                    registry={registry}
                    description={ACTIVITY_DESC_OVERRIDE[k]}
                  />
                ))}
              </div>
            </Fragment>
          ))}

          <div style={{marginTop: 2, marginBottom: 18, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
            <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
              Stored at <span style={{color: "var(--fg-3)"}}>/etc/hal0/hal0.toml</span>
              {dirtyKeys.length > 0 && (
                <span style={{marginLeft: 8, color: invalidKeys.length ? "var(--err)" : "var(--warn)"}}>
                  · {invalidKeys.length ? `${invalidKeys.length} invalid value${invalidKeys.length === 1 ? "" : "s"}` : `${dirtyKeys.length} unsaved change${dirtyKeys.length === 1 ? "" : "s"}`}
                </span>
              )}
            </span>
            <div style={{display: "inline-flex", gap: 8}}>
              <button className="btn ghost sm" disabled={dirtyKeys.length === 0 || update.isPending} onClick={() => setBuf({})}>Reset</button>
              <button className="btn" disabled={!canSave} onClick={onSaveClick}>{update.isPending ? "Saving…" : "Save changes"}</button>
            </div>
          </div>

          <RestartApiPanel />

          <ConfirmDialog
            open={!!confirmKeys}
            onCancel={() => setConfirmKeys(null)}
            onConfirm={() => { setConfirmKeys(null); doSave(); }}
            title="Manual restart required"
            message={
              <span>
                {confirmKeys && confirmKeys.length === 1
                  ? <>The setting <b className="mono">{confirmKeys[0]}</b> requires</>
                  : <>These settings ({confirmKeys && confirmKeys.map(k => <b className="mono" key={k}>{k} </b>)}) require</>}{" "}
                a <b>manual operator restart</b> to take effect. Values are persisted now — use the
                restart control below (or <span className="mono">systemctl restart hal0-api</span>) to apply them.
              </span>
            }
            confirmLabel="Save anyway"
          />
        </>
      )}
    </div>
  );
}
