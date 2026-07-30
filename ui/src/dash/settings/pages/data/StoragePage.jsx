// DATA ▸ Storage — model storage path + auto-scan (spec (b) DATA▸
// Storage/Offline: models_dir/disk = E+migrate). Extracted verbatim from
// settings.jsx StorageSection (P3-ui split phase 1). The `id` stays
// "storage" (unchanged) so #settings/storage deep links keep working.
//
// Models (v0.3 single-source-of-truth `[models].store`). Replaces the
// two-field roots + pull_root surface from PR #313 with ONE Storage
// location field, with a confirmation modal when the prior path has data
// ("Move N models from A to B?").
//
// The remaining toggles (auto_scan_on_start, file_extensions) keep writing
// through the generic PUT /api/settings since they don't need the
// propagation / migration plumbing.
import { useState, useEffect } from 'react'
import {
  useModelStore,
  useModelStoreSet,
  useModelStoreMigrate,
} from '@/api/hooks/useSettings'
import { useSettingsClient } from '../../data/settingsClient.js'
import { ConfirmDialog } from '../../../primitives.jsx'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { SRow } from '../../shared/SRow.jsx'

function _fmtBytes(n) {
  if (!n || n < 0) return "—";
  if (n < 1024) return n + " B";
  if (n < 1024 ** 2) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 ** 3) return (n / 1024 ** 2).toFixed(1) + " MB";
  return (n / 1024 ** 3).toFixed(2) + " GB";
}

export function StoragePage() {
  // R5 data seam: one typed client for settings/update/reload/registry; the
  // model-store migrate hooks stay dedicated (separate typed surface).
  const { settings, update, reload, registry } = useSettingsClient();
  const storeQuery = useModelStore();
  const storeSet = useModelStoreSet();
  const storeMigrate = useModelStoreMigrate();
  const liveModels = settings.data?.models;
  const storeState = storeQuery.data;

  // Single edit buffer for the storage path. Auto-scan is a separate
  // PATCH so a Save on storage doesn't accidentally toggle it.
  const [storePath, setStorePath] = useState("");
  const [autoScan, setAutoScan] = useState(true);
  // Migration confirmation dialog state. ``pendingPlan`` holds the
  // dry-run response so the modal can render N files / M bytes without
  // a second round-trip.
  const [pendingPlan, setPendingPlan] = useState(null);
  // Manual-restart confirm gate — for any future key classified
  // manual-restart; currently no editable storage rows need this but
  // the gate is wired generically so a future registry change doesn't
  // silently skip the confirmation.
  const [manualConfirmPending, setManualConfirmPending] = useState(null);

  useEffect(() => {
    if (storeState?.effective != null) setStorePath(storeState.effective);
    if (liveModels) setAutoScan(liveModels.auto_scan_on_start !== false);
  }, [storeState, liveModels]);

  const storeDirty = !!storeState && storePath.trim() !== storeState.effective;
  const autoScanDirty = !!liveModels && autoScan !== (liveModels.auto_scan_on_start !== false);

  const submitStore = async (path, { migrate = false } = {}) => {
    try {
      const resp = await storeSet.mutateAsync({ path, migrate });
      if (resp.status === "needs_migration") {
        setPendingPlan({ ...resp.plan, path });
        return;
      }
      const moved = resp.migration?.moved?.length || 0;
      window.__hal0Toast && window.__hal0Toast(
        `Storage set → ${path}${moved ? ` · moved ${moved} model(s)` : ""}`,
        "ok",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const onSave = () => submitStore(storePath.trim(), { migrate: false });

  // Check whether a settings key requires a manual-restart confirm
  // before saving. If so, defer via setManualConfirmPending.
  const needsManualConfirm = (dotKey) => {
    const entry = registry[dotKey];
    return entry?.apply_class === "manual-restart";
  };

  const onAutoScanSave = async () => {
    // manual-restart gate (latent — auto_scan_on_start is immediate,
    // but the pattern is wired so a registry change auto-enforces it).
    if (needsManualConfirm("models.auto_scan_on_start")) {
      setManualConfirmPending(() => async () => {
        await update.mutateAsync({ models: { auto_scan_on_start: autoScan } });
      });
      return;
    }
    try {
      await update.mutateAsync({ models: { auto_scan_on_start: autoScan } });
      window.__hal0Toast && window.__hal0Toast("Auto-scan setting saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const onConfirmMigrate = async () => {
    if (!pendingPlan) return;
    const path = pendingPlan.path;
    setPendingPlan(null);
    try {
      const resp = await storeMigrate.mutateAsync({ path });
      const moved = resp.status === "ok" ? (resp.migration?.moved?.length || 0) : 0;
      window.__hal0Toast && window.__hal0Toast(
        `Moved ${moved} model(s) → ${path}`,
        "ok",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Move failed — ${e?.message || "see logs"}`, "err");
    }
  };

  return (
    <div className="s-section">
      <h2>Storage</h2>
      <p className="desc">
        Where hal0 reads and writes model files. One path drives <span className="mono" style={{color: "var(--fg)"}}>hal0</span> — pick once, applies everywhere.
      </p>

      {storeQuery.isPending && <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading storage state…</div>}
      {storeQuery.isError && (
        <div className="err">{storeQuery.error?.message || "Failed to load storage state"}</div>
      )}

      {storeState && (
        <>
          {storeState.fallback_active && (
            <div className="s-panel" style={{marginBottom: 12, padding: 12, fontFamily: "var(--jbm)", fontSize: 11.5, color: "var(--fg-3)", borderLeft: "2px solid var(--accent)"}}>
              <b style={{color: "var(--accent)"}}>One field now drives storage.</b> We simplified storage settings — your current path is <span className="mono" style={{color: "var(--fg)"}}>{storeState.effective}</span>. Click Save to make it the new single source of truth.
            </div>
          )}

          <div className="s-panel">
            <SRow
              k="Storage location"
              sub="Absolute directory · the pull engine points here"
              mono
              v={
                <input
                  className="input mono"
                  value={storePath}
                  onChange={e => setStorePath(e.target.value)}
                  placeholder="/mnt/ai-models"
                  style={{minWidth: 320, width: "100%"}}
                />
              }
            />
            <SRow
              k="Current state"
              sub="Probe of the effective storage path"
              mono
              v={
                storeState.current_state.exists
                  ? <>
                      <b style={{color: "var(--ok)"}}>exists</b>
                      <span style={{color: "var(--fg-4)"}}> · {storeState.current_state.files_count} files · {_fmtBytes(storeState.current_state.size_bytes)} used · {_fmtBytes(storeState.current_state.free_bytes)} free</span>
                      {!storeState.current_state.writable && <span style={{color: "var(--warn)", marginLeft: 6}}>· read-only</span>}
                    </>
                  : <span style={{color: "var(--warn)"}}>missing · create it before saving</span>
              }
            />
            <SRow
              k="Suggested locations"
              sub="Click to fill — labels show current state"
              v={
                <div style={{display: "flex", gap: 6, flexWrap: "wrap"}}>
                  {storeState.suggestions.map(s => (
                    <button
                      key={s.path}
                      className={"chip" + (s.is_current ? " amber" : "")}
                      style={{cursor: "pointer", fontFamily: "var(--jbm)"}}
                      onClick={() => setStorePath(s.path)}
                      title={s.exists ? `${s.files_count} files · ${_fmtBytes(s.size_bytes)} used · ${_fmtBytes(s.free_bytes)} free` : "does not exist yet"}
                    >
                      {s.path}
                      <span style={{marginLeft: 6, color: "var(--fg-4)", fontSize: 10}}>
                        {s.exists
                          ? (s.files_count > 0 ? `${s.files_count} files` : "empty")
                          : "missing"}
                      </span>
                    </button>
                  ))}
                </div>
              }
            />
            <SRow
              k="Auto-scan on start"
              sub="Walk the storage path when hal0-api starts; new files get registered automatically"
              v={
                <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
                  <input
                    type="checkbox"
                    checked={autoScan}
                    onChange={e => setAutoScan(e.target.checked)}
                    style={{accentColor: "var(--accent)"}}
                  />
                  <span>{autoScan ? "enabled" : "disabled"}</span>
                </label>
              }
              actions={
                <div style={{display: "inline-flex", alignItems: "center", gap: 6}}>
                  <ApplyBadge settingsKey="models.auto_scan_on_start" registry={registry} />
                  {autoScanDirty && (
                    <button className="btn ghost sm" disabled={update.isPending} onClick={onAutoScanSave}>
                      {update.isPending ? "Saving…" : "Save"}
                    </button>
                  )}
                </div>
              }
            />
            <SRow
              k="File extensions"
              sub="Read-only · edit via hal0 config edit"
              mono
              v={(liveModels?.file_extensions || []).join(" · ") || "—"}
              actions={<ApplyBadge settingsKey="models.file_extensions" registry={registry} />}
            />
          </div>

          <div style={{marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
            <span className="mono" style={{fontSize: 11, color: "var(--fg-4)", display: "inline-flex", alignItems: "center", gap: 8}}>
              Stored at <span style={{color: "var(--fg-3)"}}>/etc/hal0/hal0.toml</span>
              <button
                className="btn ghost sm"
                title="Re-read hal0.toml from disk — use after editing it with hal0 config edit"
                disabled={reload.isPending}
                onClick={() => reload.mutate(undefined, {
                  onSuccess: () => window.__hal0Toast && window.__hal0Toast("Config reloaded from disk", "ok"),
                  onError: (err) => window.__hal0Toast && window.__hal0Toast(`Reload failed — ${err?.message || "see logs"}`, "err"),
                })}
              >{reload.isPending ? "Reloading…" : "Reload from disk"}</button>
              {storeDirty && <span style={{color: "var(--warn)"}}>· unsaved changes</span>}
            </span>
            <div style={{display: "inline-flex", alignItems: "center", gap: 8}}>
              <ApplyBadge settingsKey="models.store" registry={registry} />
              <button
                className="btn ghost sm"
                disabled={!storeDirty || storeSet.isPending}
                onClick={() => storeState && setStorePath(storeState.effective)}
              >Reset</button>
              <button
                className="btn"
                disabled={!storeDirty || !storePath.trim() || storeSet.isPending}
                onClick={onSave}
              >{storeSet.isPending ? "Saving…" : "Save"}</button>
            </div>
          </div>
          {storeSet.isError && (
            <div className="err" style={{marginTop: 10}}>
              {storeSet.error?.message || "Save failed"}
            </div>
          )}

          <ConfirmDialog
            open={!!pendingPlan}
            onCancel={() => setPendingPlan(null)}
            onConfirm={onConfirmMigrate}
            title="Move existing models?"
            message={
              pendingPlan ? (
                <span>
                  Hal0 will move <b className="mono">{pendingPlan.files_count} file(s)</b> ({_fmtBytes(pendingPlan.size_bytes)}) from <span className="mono" style={{color: "var(--fg)"}}>{pendingPlan.source}</span> to <span className="mono" style={{color: "var(--accent)"}}>{pendingPlan.target}</span>.
                  {pendingPlan.same_filesystem
                    ? <> Same filesystem — should be instant.</>
                    : <> Cross-filesystem copy — may take a while.</>}
                  {" "}A failure leaves both paths intact, you can retry safely.
                </span>
              ) : null
            }
            confirmLabel={storeMigrate.isPending ? "Moving…" : "Move + apply"}
          />
          <ConfirmDialog
            open={!!manualConfirmPending}
            onCancel={() => setManualConfirmPending(null)}
            onConfirm={async () => {
              const fn = manualConfirmPending;
              setManualConfirmPending(null);
              try {
                await fn();
                window.__hal0Toast && window.__hal0Toast("Setting saved — manual restart required to take effect", "warn");
              } catch (e) {
                window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
              }
            }}
            title="Manual restart required"
            message={
              <span>
                This setting requires a <b>manual operator restart</b> to take effect.
                The new value will be persisted now — restart the service to apply it.{" "}
                <span className="chip" style={{color: "var(--err)", borderColor: "var(--err)", fontSize: 10, padding: "1px 6px"}}>⚠ manual restart</span>
              </span>
            }
            confirmLabel="Save anyway"
          />
        </>
      )}
    </div>
  );
}
