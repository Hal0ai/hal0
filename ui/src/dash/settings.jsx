// hal0 dashboard — Settings view (secrets, storage, updates, voice, image-gen, general, about)
//
// Phase B2: every section reads from live hooks. Storage drives
// [models].store; General is the cosmetic placeholders block (theme
// locked to dark, density picker, accent chip).
//
// OmniRouter routing table, Agent-policy, and Memory (Cognee) sections
// were removed in #544 — those surfaces live on the MCP view and the
// agent view, respectively. The settings rail is for knobs only.
//
// #554: Voice (STT model, TTS model, TTS default voice) + Image-gen
// (enable toggle, engine/model) sections persist via:
//   - POST /api/capabilities/{slot}/{child}  — model/provider/enabled
//   - PUT  /api/slots/{name}/config          — default_voice extra field
// Extras that have no slot-config path (image size, steps, workflow per-request
// params read from the body at inference time) are deferred (#554 follow-up).

import { useSecrets, useSecretSet, useSecretDelete } from '@/api/hooks/useSecrets'
import { SECRET_PRESETS } from './extra-modals.jsx'
import { useUpdateState, useUpdateCheck, useUpdateApply, useUpdateJob, useSetUpdateChannel, useUpdateRollback } from '@/api/hooks/useUpdates'
import { useCapabilities, useCapabilityApply } from '@/api/hooks/useCapabilities'
import { useSlots, useSlotEdit, useSlotConfig, useSlotVoices } from '@/api/hooks/useSlots'
import {
  useSettings,
  useSettingsUpdate,
  useSettingsReload,
  useSettingsSchema,
  useModelStore,
  useModelStoreSet,
  useModelStoreMigrate,
  useApplyPlan,
} from '@/api/hooks/useSettings'
import { useServiceRepair } from '@/api/hooks/useServicesHealth'
import { useMemoryGraphStatus, useUpdateMemoryGraph } from '@/api/hooks/useMemory'
import { useNpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import { useQueryClient } from '@tanstack/react-query'

const { useState: useStateSet, useEffect: useEffectSet, useRef: useRefSet } = React;

function SettingsView({ param }) {
  const VALID_IDS = ["general", "slots", "npu", "memory", "voice", "imagegen", "storage", "secrets", "updates", "advanced", "about"];
  const initialSection = param && VALID_IDS.includes(param) ? param : "general";
  const [section, setSection] = useStateSet(initialSection);
  const sections = [
    { id: "general",   label: "General" },
    { id: "slots",     label: "Slots" },
    { id: "npu",       label: "NPU" },
    { id: "memory",    label: "Memory" },
    { id: "voice",     label: "Voice" },
    { id: "imagegen",  label: "Image-gen" },
    { id: "storage",   label: "Storage" },
    { id: "secrets",   label: "Secrets" },
    { id: "updates",   label: "Updates" },
    { id: "advanced",  label: "Advanced" },
    { id: "about",     label: "About" },
  ];

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Configure</span>
        <h1>Settings</h1>
        <span className="vh-spacer" />
      </div>

      <div className="settings-layout">
        <div className="settings-nav">
          {sections.map(s => (
            <div
              key={s.id}
              className={"nav-item" + (section === s.id ? " active" : "")}
              onClick={() => setSection(s.id)}
            >
              {s.label}
            </div>
          ))}
        </div>

        <div className="settings-content">
          {section === "general" && <GeneralSection />}
          {section === "slots" && <SlotsSection />}
          {section === "npu" && <NpuSection />}
          {section === "memory" && <MemorySection />}
          {section === "voice" && <VoiceSection />}
          {section === "imagegen" && <ImageGenSection />}
          {section === "storage" && <StorageSection />}
          {section === "secrets" && <SecretsSection />}
          {section === "updates" && <UpdatesSection />}
          {section === "advanced" && <AdvancedSection />}
          {section === "about" && <AboutSection />}
        </div>
      </div>
    </div>
  );
}

// ─── shared row helper ───
const SRow = ({ k, sub, v, mono, children, actions }) => (
  <div className="s-row">
    <div className="k">
      <span>{k}</span>
      {sub && <span className="sub">{sub}</span>}
    </div>
    <div className={"v" + (mono ? " mono" : "")}>{children || v}</div>
    {actions && <div className="ac">{actions}</div>}
  </div>
);

// ─── per-key apply badge (issue #552) ────────────────────────────────────────
//
// Shared apply-class chip style for settings rows.
// The registry is fetched once via useApplyPlan(); the component is
// purely presentational — it looks up the key, picks a colour, and
// renders the chip. If the registry hasn't loaded yet or the key is
// unknown, renders nothing so the row layout stays clean.
//
// Badge legend:
//   immediate     → green "live"
//   service-restart → amber "⟳ restart <service>"
//   manual-restart  → red "⚠ manual restart"
function ApplyBadge({ settingsKey, registry }) {
  const entry = registry && registry[settingsKey];
  if (!entry) return null;
  const cls = entry.apply_class;
  const isImmediate = cls === "immediate";
  const isServiceRestart = cls === "service-restart";
  const isManualRestart = cls === "manual-restart";
  const svc = isServiceRestart && entry.services && entry.services[0] ? entry.services[0] : null;
  return (
    <span
      className="chip"
      style={{
        fontFamily: "var(--jbm)",
        fontSize: 10,
        padding: "2px 8px",
        whiteSpace: "nowrap",
        color: isImmediate ? "var(--ok)" : isServiceRestart ? "var(--warn)" : "var(--err)",
        borderColor: isImmediate ? "var(--ok)" : isServiceRestart ? "var(--warn)" : "var(--err)",
        background: isImmediate
          ? "rgba(46,204,113,0.08)"
          : isServiceRestart
            ? "rgba(255,176,0,0.08)"
            : "rgba(231,76,60,0.08)",
      }}
      title={
        isImmediate
          ? "Applied immediately on save — no restart needed"
          : isServiceRestart
            ? `Requires restarting ${svc || "service"} to take effect`
            : "Requires a manual operator restart to take effect"
      }
    >
      {isImmediate && "live"}
      {isServiceRestart && (svc ? `⟳ restart ${svc}` : "⟳ restart")}
      {isManualRestart && "⚠ manual restart"}
    </span>
  );
}

// ─── Models (v0.3 single-source-of-truth `[models].store`) ───────────
//
// Replaces the two-field roots + pull_root surface from PR #313 with
// ONE Storage location field, with a confirmation modal when the prior
// path has data ("Move N models from A to B?").
//
// The remaining toggles (auto_scan_on_start, file_extensions) keep
// writing through the generic PUT /api/settings since they don't need
// the propagation / migration plumbing.
function _fmtBytes(n) {
  if (!n || n < 0) return "—";
  if (n < 1024) return n + " B";
  if (n < 1024 ** 2) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 ** 3) return (n / 1024 ** 2).toFixed(1) + " MB";
  return (n / 1024 ** 3).toFixed(2) + " GB";
}

function StorageSection() {
  const settings = useSettings();
  const update = useSettingsUpdate();
  const reload = useSettingsReload();
  const storeQuery = useModelStore();
  const storeSet = useModelStoreSet();
  const storeMigrate = useModelStoreMigrate();
  const applyPlanQuery = useApplyPlan();
  const registry = applyPlanQuery.data?.registry || {};
  const liveModels = settings.data?.models;
  const storeState = storeQuery.data;

  // Single edit buffer for the storage path. Auto-scan is a separate
  // PATCH so a Save on storage doesn't accidentally toggle it.
  const [storePath, setStorePath] = useStateSet("");
  const [autoScan, setAutoScan] = useStateSet(true);
  // Migration confirmation dialog state. ``pendingPlan`` holds the
  // dry-run response so the modal can render N files / M bytes without
  // a second round-trip.
  const [pendingPlan, setPendingPlan] = useStateSet(null);
  // Manual-restart confirm gate — for any future key classified
  // manual-restart; currently no editable storage rows need this but
  // the gate is wired generically so a future registry change doesn't
  // silently skip the confirmation.
  const [manualConfirmPending, setManualConfirmPending] = useStateSet(null);

  useEffectSet(() => {
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

// Dedicated, discoverable HuggingFace-token field (P4). Wraps the existing
// /api/secrets store under the fixed name HF_TOKEN — set/delete update os.environ
// live (no restart), so the next gated/large pull authenticates immediately.
function HfTokenField() {
  const secretsQuery = useSecrets();
  const setSecret = useSecretSet();
  const delSecret = useSecretDelete();
  const [val, setVal] = useStateSet("");
  const isSet = (secretsQuery.data ?? []).some(s => s.name === "HF_TOKEN" && s.set);
  return (
    <div className="s-panel" style={{padding: 16, marginBottom: 16}}>
      <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 6}}>HuggingFace token</div>
      <p className="desc" style={{margin: "0 0 10px"}}>
        Needed for gated / large model pulls. Stored encrypted; applied live (no restart).
        Status: {isSet ? <span style={{color: "var(--ok)"}}>set ✓</span> : <span style={{color: "var(--fg-4)"}}>not set</span>}
      </p>
      <div style={{display: "flex", gap: 8}}>
        <input
          type="password"
          className="input mono"
          aria-label="HuggingFace token"
          value={val}
          onChange={e => setVal(e.target.value)}
          placeholder={isSet ? "•••••••• (set) — enter to replace" : "hf_…"}
          style={{flex: 1, padding: "8px 10px", fontSize: 13}}
        />
        <button
          className="btn"
          disabled={!val.trim() || setSecret.isPending}
          onClick={() => setSecret.mutate({ name: "HF_TOKEN", value: val.trim() }, { onSuccess: () => setVal("") })}
        >
          {setSecret.isPending ? "Saving…" : "Save"}
        </button>
        {isSet && (
          <button className="btn ghost" disabled={delSecret.isPending} onClick={() => delSecret.mutate("HF_TOKEN")}>
            {delSecret.isPending ? "Clearing…" : "Clear"}
          </button>
        )}
      </div>
    </div>
  );
}

// Per-key descriptions shared with the Add-Secret modal. Anything not in
// the preset table is a user-defined key — say so instead of mislabelling
// it as a fallback provider.
const SECRET_DESCRIPTIONS = Object.fromEntries(SECRET_PRESETS.map(p => [p.id, p.desc]));
const secretDescription = (name) =>
  SECRET_DESCRIPTIONS[name] || "Custom key · exported to hal0 services and slot containers as an env var";

function SecretsSection() {
  const [addOpen, setAddOpen] = useStateSet(false);
  const [addTarget, setAddTarget] = useStateSet(null);
  const secretsQuery = useSecrets();
  const delSecret = useSecretDelete();
  const rows = secretsQuery.data ?? [];
  const openAdd = (name) => { setAddTarget(name || null); setAddOpen(true); };
  return (
    <div className="s-section">
      <h2>Secrets</h2>
      <p className="desc">Stored encrypted at rest, never shown again after saving. Each key is exported to hal0 services and slot containers as an environment variable — model-pull auth, fallback providers, or your own custom keys.</p>
      <HfTokenField />
      {secretsQuery.isLoading && (
        <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading…</div>
      )}
      {secretsQuery.isError && (
        <div className="err">{secretsQuery.error?.message || "Could not load secrets"}</div>
      )}
      <div className="s-panel">
        {rows.length === 0 && !secretsQuery.isLoading && !secretsQuery.isError && (
          <div className="s-row" style={{padding: "18px 16px"}}>
            <span className="mono" style={{fontSize: 12, color: "var(--fg-4)"}}>no secrets configured · add one</span>
          </div>
        )}
        {rows.map(s => (
          <SRow
            key={s.name}
            k={s.name}
            sub={secretDescription(s.name)}
            mono
            v={s.set
              ? <span style={{color: "var(--ok)"}}>{s.masked || '••• · set'}</span>
              : <span style={{color: "var(--fg-4)"}}>not set</span>}
            actions={s.set
              ? (<>
                  <button className="btn ghost sm" onClick={() => openAdd(s.name)}>Update</button>
                  <button
                    className="btn danger sm"
                    disabled={delSecret.isPending && delSecret.variables === s.name}
                    onClick={() => {
                      delSecret.mutate(s.name, {
                        onSuccess: () => window.__hal0Toast && window.__hal0Toast(`${s.name} removed`, "warn"),
                        onError: (err) => window.__hal0Toast && window.__hal0Toast(
                          `Remove failed — ${err?.message || "see logs"}`,
                          "err",
                        ),
                      });
                    }}
                  >{delSecret.isPending && delSecret.variables === s.name ? "Removing…" : "Remove"}</button>
                </>)
              : <button className="btn ghost sm" onClick={() => openAdd(s.name)}>Add</button>}
          />
        ))}
      </div>
      <div style={{marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
        <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
          {rows.length > 0 ? `${rows.length} key${rows.length === 1 ? "" : "s"} stored` : "add keys for model pulls, fallback providers, or custom env vars"}
        </span>
        <button className="btn" onClick={() => openAdd(null)}>{Icons.plus} Add secret</button>
      </div>
      <AddSecretModal open={addOpen} initialName={addTarget} onClose={() => setAddOpen(false)} />
    </div>
  );
}

function UpdatesSection() {
  // Phase B1: live state + check + apply mutations. While the query is
  // in flight or 5xx'd we render an empty envelope and let the SRow
  // fallbacks show '—' rather than fabricated versions.
  // Issue #546: channel switch (stable | nightly) is wired to
  // useSetUpdateChannel → PUT /api/updates/channel; reads the current
  // value from useUpdateState().hal0.channel on load.
  const stateQuery = useUpdateState();
  const checkM = useUpdateCheck();
  const applyM = useUpdateApply();
  const setChannelM = useSetUpdateChannel();
  const rollbackM = useUpdateRollback();
  const [rollbackConfirm, setRollbackConfirm] = useStateSet(false);
  // Optional version pin — parity with `hal0 update --target`. Empty
  // installs the channel's latest.
  const [pinVersion, setPinVersion] = useStateSet("");
  const u = stateQuery.data || { hal0: {}, flm: {} };

  // The current channel lives on each per-component envelope (both
  // populated from telemetry.channel in hal0.toml); hal0.channel is
  // authoritative for the switch's initial value.
  const currentChannel = u.hal0?.channel || 'stable';

  // Track the most recent apply job so the user sees the backend's
  // verdict, not just the 202 ack. Toasts fire once on terminal state.
  const [jobId, setJobId] = useStateSet(null);
  const lastTerminalJob = useRefSet(null);
  const { job, terminal } = useUpdateJob(jobId);
  useEffectSet(() => {
    if (!terminal || !job || lastTerminalJob.current === job.id) return;
    lastTerminalJob.current = job.id;
    if (job.state === 'applied') {
      window.__hal0Toast && window.__hal0Toast(`Updated to ${job.version || 'latest'} — services restarted`, "ok");
    } else {
      const detail = job.error || job.error_code || 'unknown';
      window.__hal0Toast && window.__hal0Toast(`Update failed: ${detail}`, "err");
    }
  }, [terminal, job]);

  const jobBusy = job && (job.state === 'queued' || job.state === 'running');
  const jobLabel = jobBusy
    ? (job.state === 'queued' ? 'queued…' : 'installing…')
    : null;

  return (
    <div className="s-section">
      <h2>Updates</h2>
      <p className="desc">Signed self-update. hal0 verifies a Sigstore signature before swapping binaries. Per-channel pins.</p>
      <div className="s-panel">
        <SRow
          k="hal0"
          sub="Dashboard + API + CLI"
          mono
          v={<>
            {u.hal0?.available
              ? <><span style={{color: "var(--accent)"}}>{u.hal0.available} available</span> <span style={{color: "var(--fg-4)"}}>· current {u.hal0.current}</span></>
              : <span>current {u.hal0?.current}</span>}
            {jobLabel && <span style={{marginLeft: 8, color: "var(--warn)", fontFamily: "var(--jbm)", fontSize: 11}}>· {jobLabel}</span>}
          </>}
          actions={<>
            <button
              className="btn sm"
              disabled={(!u.hal0?.available && !pinVersion.trim()) || applyM.isPending || !!jobBusy}
              onClick={() => {
                applyM.mutate(pinVersion.trim() || undefined, {
                  onSuccess: (snap) => {
                    setJobId(snap?.id || null);
                    window.__hal0Toast && window.__hal0Toast("Update started — brief outage during restart", "warn");
                  },
                  onError: (err) => {
                    const msg = (err && err.message) || "could not start update";
                    window.__hal0Toast && window.__hal0Toast(`Update failed: ${msg}`, "err");
                  },
                });
              }}
            >{applyM.isPending ? "Starting…" : (jobBusy ? "Installing…" : "Install update")}</button>
            <button
              className="btn ghost sm"
              disabled={checkM.isPending}
              onClick={() => checkM.mutate(undefined, {
                onError: (err) => {
                  const msg = (err && err.message) || "check failed";
                  window.__hal0Toast && window.__hal0Toast(`Check failed: ${msg}`, "err");
                },
              })}
            >{checkM.isPending ? "Checking…" : "Check"}</button>
            <button
              className="btn ghost sm"
              disabled={rollbackM.isPending || !!jobBusy}
              onClick={() => setRollbackConfirm(true)}
            >{rollbackM.isPending ? "Rolling back…" : "Roll back"}</button>
            <a className="btn ghost sm" href="https://hal0.dev/changelog" target="_blank" rel="noreferrer">Changelog →</a>
          </>}
        />
        {u.hal0?.revoked && (
          <SRow
            k="Release notice"
            sub="The latest release on this channel was withdrawn"
            v={<span style={{color: "var(--warn)"}}>
              {u.hal0.revoked_version ? `${u.hal0.revoked_version} was revoked` : "latest release revoked"}
              {u.hal0.revoked_reason ? ` — ${u.hal0.revoked_reason}` : ""}
            </span>}
          />
        )}
        <SRow
          k="flm"
          sub="Manual deb · vendor-supplied"
          mono
          v={u.flm?.current || '—'}
        />
        <SRow
          k="Pin version"
          sub="Install a specific release instead of the channel's latest · CLI parity: hal0 update --target"
          mono
          v={
            <input
              value={pinVersion}
              onChange={e => setPinVersion(e.target.value)}
              placeholder="empty = latest"
              className="mono"
              style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", width: 160}}
            />
          }
        />
        <SRow
          k="Auto-check"
          sub="Background update checks by the daemon"
          mono
          v={stateQuery.data ? (u.autoCheck ? <span style={{color: "var(--ok)"}}>enabled</span> : <span style={{color: "var(--fg-4)"}}>disabled</span>) : "—"}
        />
        <SRow
          k="Channel"
          sub="Release track · persisted to hal0.toml"
          v={
            <select
              className="input mono"
              value={currentChannel}
              disabled={setChannelM.isPending}
              onChange={(e) => {
                const next = e.target.value === 'nightly' ? 'nightly' : 'stable';
                if (next === currentChannel) return;
                setChannelM.mutate(next, {
                  onSuccess: () => {
                    window.__hal0Toast && window.__hal0Toast(`Channel set to ${next}`, "ok");
                  },
                  onError: (err) => {
                    const msg = (err && err.message) || "could not set channel";
                    window.__hal0Toast && window.__hal0Toast(`Channel change failed: ${msg}`, "err");
                  },
                });
              }}
              style={{maxWidth: 160}}
            >
              <option value="stable">stable</option>
              <option value="nightly">nightly</option>
            </select>
          }
        />
      </div>
      <ConfirmDialog
        open={rollbackConfirm}
        onCancel={() => setRollbackConfirm(false)}
        onConfirm={() => {
          setRollbackConfirm(false);
          rollbackM.mutate(undefined, {
            // The backend swaps the version symlink but does NOT restart
            // services — the running hal0-api keeps serving the current
            // build until the operator bounces it.
            onSuccess: () => window.__hal0Toast && window.__hal0Toast("Rolled back — restart hal0-api (Settings → Advanced) to run the previous version", "warn"),
            onError: (err) => window.__hal0Toast && window.__hal0Toast(`Rollback failed: ${err?.message || "no previous version retained"}`, "err"),
          });
        }}
        title="Roll back hal0?"
        message={
          <span>
            Reverts the installed tree to the previous retained version{u.hal0?.current ? <> (currently on <b className="mono">{u.hal0.current}</b>)</> : null}.
            The running service keeps serving until you restart hal0-api (Settings → Advanced).
            If no previous version is retained, nothing changes.
          </span>
        }
        confirmLabel="Roll back"
      />
    </div>
  );
}

// ─── Kokoro TTS voice list ──────────────────────────────────────────────────
// Remsky Kokoro-FastAPI af_bella default. Full list from kokoro-v1 pack.
// No backend API exposes the voice list — hardcoded against the upstream.
// See: https://github.com/remsky/Kokoro-FastAPI#voices
const KOKORO_VOICES = [
  { id: "af_bella",   label: "Bella (af) — American female, warm" },
  { id: "af_sarah",   label: "Sarah (af) — American female, clear" },
  { id: "af_nicole",  label: "Nicole (af) — American female" },
  { id: "am_adam",    label: "Adam (am) — American male" },
  { id: "am_michael", label: "Michael (am) — American male" },
  { id: "bf_emma",    label: "Emma (bf) — British female" },
  { id: "bf_isabella",label: "Isabella (bf) — British female" },
  { id: "bm_george",  label: "George (bm) — British male" },
  { id: "bm_lewis",   label: "Lewis (bm) — British male" },
];

// ─── MemorySection ───────────────────────────────────────────────────────────

function MemorySection() {
  const applyPlanQuery = useApplyPlan();
  const registry = applyPlanQuery.data?.registry || {};

  return (
    <div className="s-section">
      <h2>Memory</h2>
      <p className="desc">
        Memory engine, graph extraction, and second-pass reranking. Changes to the engine
        require a hal0-api restart; graph and reranker knobs apply live.
      </p>
      <MemoryEnginePanel registry={registry} />
      <MemoryGraphPanel />
      <MemoryRerankerPanel registry={registry} />
    </div>
  );
}

function MemoryEnginePanel({ registry }) {
  const settings = useSettings();
  const update = useSettingsUpdate();
  const schemaQuery = useSettingsSchema();
  const schema = schemaQuery.data || null;
  const live = settings.data || null;

  const engineField = _schemaField(schema, "memory.engine");
  const currentEngine = live?.memory?.engine || "hindsight";
  const [engine, setEngine] = useStateSet(currentEngine);
  useEffectSet(() => {
    if (live?.memory?.engine) setEngine(live.memory.engine);
  }, [live?.memory?.engine]);

  const dirty = engine !== currentEngine;

  const doSave = async () => {
    try {
      await update.mutateAsync({ memory: { engine } });
      setEngine(engine);
      window.__hal0Toast && window.__hal0Toast("Memory engine saved — restart hal0-api to apply", "warn");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const engineDesc = ADV_DESC_OVERRIDE["memory.engine"] || engineField?.description || "";
  const options = ["hindsight", "pgvector"];

  return (
    <div className="s-panel" style={{marginBottom: 12}}>
      <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
        <div className="k"><span>Engine</span><span className="sub">hal0.toml [memory] · requires restart to switch</span></div>
      </div>
      <SRow
        k="Engine"
        sub={<span title={engineDesc}>{engineDesc.length > 150 ? engineDesc.slice(0, 147) + "…" : engineDesc}</span>}
        v={
          <select value={engine} onChange={e => setEngine(e.target.value)} style={_advInputStyle}>
            {options.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        }
        actions={<ApplyBadge settingsKey="memory.engine" registry={registry} />}
      />
      <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
        {dirty && (
          <button className="btn ghost sm" onClick={() => setEngine(currentEngine)}>Reset</button>
        )}
        <button className="btn sm" disabled={!dirty || update.isPending} onClick={doSave}>
          {update.isPending ? "Saving…" : "Save engine"}
        </button>
      </div>
    </div>
  );
}

function MemoryRerankerPanel({ registry }) {
  const settings = useSettings();
  const update = useSettingsUpdate();
  const schemaQuery = useSettingsSchema();
  const schema = schemaQuery.data || null;
  const live = settings.data || null;

  const RERANK_KEYS = [
    "memory.embedding.rerank_gateway_url",
    "memory.embedding.rerank_model",
    "memory.embedding.rerank_connect_timeout_s",
    "memory.embedding.rerank_read_timeout_s",
  ];
  const fields = {};
  for (const k of RERANK_KEYS) fields[k] = _schemaField(schema, k);

  const [buf, setBuf] = useStateSet({});
  const onChange = (dotKey, value) => setBuf(b => ({ ...b, [dotKey]: value }));

  const dirtyKeys = Object.keys(buf).filter(k => {
    const { ok, value } = _advCoerce(fields[k], buf[k]);
    if (!ok) return true;
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
    try {
      await update.mutateAsync(patch);
      setBuf({});
      window.__hal0Toast && window.__hal0Toast("Reranker settings saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  return (
    <div className="s-panel" style={{marginBottom: 12}}>
      <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
        <div className="k"><span>Reranker</span><span className="sub">hal0.toml [memory.embedding] · second-pass ranking after recall</span></div>
      </div>
      {RERANK_KEYS.map(k => (
        <AdvRow
          key={k}
          dotKey={k}
          field={fields[k]}
          live={_getIn(live, k)}
          buf={buf[k]}
          onChange={onChange}
          registry={registry}
        />
      ))}
      <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
        {dirtyKeys.length > 0 && (
          <button className="btn ghost sm" onClick={() => setBuf({})}>Reset</button>
        )}
        <button className="btn sm" disabled={!canSave} onClick={doSave}>
          {update.isPending ? "Saving…" : "Save reranker"}
        </button>
      </div>
    </div>
  );
}

// ─── VoiceSection ───────────────────────────────────────────────────────────
//
// STT: pick model from capabilities.catalogs.voice.stt — persisted via
//   POST /api/capabilities/voice/stt {model, provider, enabled}.
// TTS: model/enabled via capabilities POST; request defaults
//   (default_voice / default_speed / default_response_format) via
//   PUT /api/slots/tts/config. /v1/audio/speech injects them at request
//   time when the body omits the param, so saves apply immediately.
//   The voice picker prefers the live list from GET /api/slots/tts/voices
//   (engine /v1/audio/voices proxy) and falls back to the Kokoro seed pack.
//
// Not offered on purpose: STT language hints (moonshine is English-only —
// the request param is ignored), STT silence thresholds (no such endpoint
// param exists), TTS sample rate (fixed 24 kHz container constant).
function VoiceSection() {
  const capsQuery = useCapabilities();
  const applyCapability = useCapabilityApply();
  const ttsSlotCfgQuery = useSlotConfig("tts");
  const ttsVoicesQuery = useSlotVoices("tts");
  const editSlot = useSlotEdit();

  const caps = capsQuery.data;
  const voiceCatalogs = caps?.catalogs?.voice || {};
  const voiceSelections = caps?.selections?.voice || {};

  const sttSelection = voiceSelections.stt || {};
  const ttsSelection = voiceSelections.tts || {};
  const ttsCfg = ttsSlotCfgQuery.data || {};

  // STT local edit state
  const [sttModel, setSttModel] = useStateSet("");
  const [sttEnabled, setSttEnabled] = useStateSet(false);
  // TTS local edit state
  const [ttsModel, setTtsModel] = useStateSet("");
  const [ttsEnabled, setTtsEnabled] = useStateSet(false);
  const [ttsVoice, setTtsVoice] = useStateSet("");
  const [ttsSpeed, setTtsSpeed] = useStateSet("");
  const [ttsFormat, setTtsFormat] = useStateSet("");

  // Populate from live data
  useEffectSet(() => {
    if (sttSelection.model != null) setSttModel(sttSelection.model || "");
    if (sttSelection.enabled != null) setSttEnabled(!!sttSelection.enabled);
  }, [sttSelection.model, sttSelection.enabled]);

  useEffectSet(() => {
    if (ttsSelection.model != null) setTtsModel(ttsSelection.model || "");
    if (ttsSelection.enabled != null) setTtsEnabled(!!ttsSelection.enabled);
  }, [ttsSelection.model, ttsSelection.enabled]);

  useEffectSet(() => {
    const v = ttsCfg.default_voice;
    if (v != null) setTtsVoice(String(v));
    if (ttsCfg.default_speed != null) setTtsSpeed(String(ttsCfg.default_speed));
    if (ttsCfg.default_response_format != null) setTtsFormat(String(ttsCfg.default_response_format));
  }, [ttsCfg.default_voice, ttsCfg.default_speed, ttsCfg.default_response_format]);

  const origVoice = ttsCfg.default_voice ? String(ttsCfg.default_voice) : "";
  const origSpeed = ttsCfg.default_speed != null ? String(ttsCfg.default_speed) : "";
  const origFormat = ttsCfg.default_response_format ? String(ttsCfg.default_response_format) : "";
  const speedNum = parseFloat(ttsSpeed);
  const speedValid = ttsSpeed.trim() === "" || (!isNaN(speedNum) && speedNum >= 0.25 && speedNum <= 4);
  const sttDirty = sttModel !== (sttSelection.model || "") || sttEnabled !== !!sttSelection.enabled;
  const ttsDirty = ttsModel !== (ttsSelection.model || "") || ttsEnabled !== !!ttsSelection.enabled
    || ttsVoice !== origVoice || ttsSpeed !== origSpeed || ttsFormat !== origFormat;

  const sttCatalogItems = voiceCatalogs.stt?.items || voiceCatalogs.stt?.models || [];
  const ttsCatalogItems = voiceCatalogs.tts?.items || voiceCatalogs.tts?.models || [];

  const doSaveStt = async () => {
    try {
      await applyCapability.mutateAsync({ slot: "voice", child: "stt", body: { model: sttModel, enabled: sttEnabled } });
      window.__hal0Toast && window.__hal0Toast("STT settings saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`STT save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const doSaveTts = async () => {
    try {
      // Persist model + enabled via capability apply
      await applyCapability.mutateAsync({ slot: "voice", child: "tts", body: { model: ttsModel, enabled: ttsEnabled } });
      // Persist request defaults via slot config — only the changed fields.
      // Empty string intentionally clears a default back to the engine's own
      // (null on the wire; /v1/audio/speech skips null/empty on injection).
      const patch = {};
      if (ttsVoice !== origVoice) patch.default_voice = ttsVoice || null;
      if (ttsSpeed !== origSpeed) patch.default_speed = ttsSpeed.trim() === "" ? null : speedNum;
      if (ttsFormat !== origFormat) patch.default_response_format = ttsFormat || null;
      if (Object.keys(patch).length > 0) {
        await editSlot.mutateAsync({ name: "tts", body: patch });
      }
      window.__hal0Toast && window.__hal0Toast("TTS settings saved — applies to the next /v1/audio/speech request", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`TTS save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const loading = capsQuery.isLoading;
  const sttStatus = sttSelection.status || "offline";
  const ttsStatus = ttsSelection.status || "offline";

  const statusChip = (st) => {
    const color = st === "ready" || st === "serving" ? "var(--ok)" : st === "starting" || st === "warming" ? "var(--warn)" : "var(--fg-4)";
    return <span className="chip mono" style={{borderColor: color, color, fontSize: 10, padding: "1px 6px"}}>{st}</span>;
  };

  return (
    <div className="s-section">
      <h2>Voice</h2>
      <p className="desc">STT (speech-to-text) and TTS (text-to-speech) slot configuration. Changes persist to the voice.stt and voice.tts capability slots.</p>

      {/* ── STT ── */}
      <div className="s-panel" style={{marginBottom: 12}}>
        <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
          <div className="k"><span>STT</span><span className="sub">speech-to-text · voice.stt slot</span></div>
          <div className="v">{statusChip(sttStatus)}</div>
        </div>
        <SRow k="Enabled" v={
          <input type="checkbox" checked={sttEnabled} onChange={e => setSttEnabled(e.target.checked)} style={{accentColor: "var(--accent)"}} />
        } />
        <SRow k="Model" v={
          sttCatalogItems.length > 0 ? (
            <select value={sttModel} onChange={e => setSttModel(e.target.value)}
              style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
              <option value="">— unset —</option>
              {sttCatalogItems.map(m => (
                <option key={m.id || m.model_id || m} value={m.id || m.model_id || m}>{m.id || m.model_id || m}</option>
              ))}
            </select>
          ) : (
            <input value={sttModel} onChange={e => setSttModel(e.target.value)} placeholder="model id (e.g. moonshine-base)"
              className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 260}} />
          )
        } sub={sttCatalogItems.length === 0 ? "no installed STT models — install one in the Models view" : undefined} />
        <SRow k="Language" sub="moonshine is English-only; the /v1/audio/transcriptions language param is accepted but ignored" mono v={<span style={{color: "var(--fg-4)"}}>English</span>} />
        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {sttDirty && (
            <button className="btn ghost sm" onClick={() => { setSttModel(sttSelection.model || ""); setSttEnabled(!!sttSelection.enabled); }}>Reset</button>
          )}
          <button className="btn sm" disabled={!sttDirty || loading || applyCapability.isPending} onClick={doSaveStt}>Save STT</button>
        </div>
      </div>

      {/* ── TTS ── */}
      <div className="s-panel">
        <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
          <div className="k"><span>TTS</span><span className="sub">text-to-speech · voice.tts slot</span></div>
          <div className="v">{statusChip(ttsStatus)}</div>
        </div>
        <SRow k="Enabled" v={
          <input type="checkbox" checked={ttsEnabled} onChange={e => setTtsEnabled(e.target.checked)} style={{accentColor: "var(--accent)"}} />
        } />
        <SRow k="Model" v={
          ttsCatalogItems.length > 0 ? (
            <select value={ttsModel} onChange={e => setTtsModel(e.target.value)}
              style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
              <option value="">— unset —</option>
              {ttsCatalogItems.map(m => (
                <option key={m.id || m.model_id || m} value={m.id || m.model_id || m}>{m.id || m.model_id || m}</option>
              ))}
            </select>
          ) : (
            <input value={ttsModel} onChange={e => setTtsModel(e.target.value)} placeholder="model id (e.g. kokoro-v1)"
              className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 260}} />
          )
        } sub={ttsCatalogItems.length === 0 ? "no installed TTS models — install one in the Models view" : undefined} />
        {/* Voice options come from the live slot when it answers
            (GET /api/slots/tts/voices proxies the engine's /v1/audio/voices);
            the hardcoded Kokoro pack is only the cold-slot fallback for the
            bundled default engine. An explicitly non-Kokoro model with no
            live list gets a free-form voice id input. */}
        {(() => {
          const liveVoices = ttsVoicesQuery.data?.source === "live" ? (ttsVoicesQuery.data.voices || []) : [];
          const kokoroish = !ttsModel || ttsModel.toLowerCase().includes("kokoro");
          const options = liveVoices.length > 0
            ? liveVoices.map(v => {
                const seed = KOKORO_VOICES.find(k => k.id === v);
                return { id: v, label: seed ? seed.label : v };
              })
            : (kokoroish ? KOKORO_VOICES : null);
          const srcNote = liveVoices.length > 0
            ? "voices reported live by the tts slot"
            : (kokoroish ? "bundled voices (Kokoro v1) · slot offline — list is the seed pack" : "model-specific voice id");
          return (
            <SRow k="Default voice" sub={`applied when /v1/audio/speech omits the voice param · ${srcNote}`} v={
              options ? (
                <select value={ttsVoice} onChange={e => setTtsVoice(e.target.value)}
                  style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
                  <option value="">— use engine default (af_bella) —</option>
                  {/* keep a saved voice selectable even if the live list lost it */}
                  {ttsVoice && !options.some(o => o.id === ttsVoice) && (
                    <option value={ttsVoice}>{ttsVoice} (saved)</option>
                  )}
                  {options.map(v => (
                    <option key={v.id} value={v.id}>{v.label}</option>
                  ))}
                </select>
              ) : (
                <input value={ttsVoice} onChange={e => setTtsVoice(e.target.value)}
                  placeholder="empty = engine default"
                  className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 220}} />
              )
            } />
          );
        })()}
        <SRow k="Default speed" sub="applied when the request omits speed · Kokoro clamps to 0.5–2.0 · empty = engine default (1.0)" v={
          <input type="number" min={0.25} max={4} step={0.05} value={ttsSpeed}
            onChange={e => setTtsSpeed(e.target.value)} placeholder="1.0"
            className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: `1px solid ${speedValid ? "var(--line)" : "var(--err)"}`, borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 100}} />
        } />
        <SRow k="Default format" sub="applied when the request omits response_format · empty = engine default (mp3)" v={
          <select value={ttsFormat} onChange={e => setTtsFormat(e.target.value)}
            style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
            <option value="">— engine default (mp3) —</option>
            {["mp3", "wav", "opus", "flac", "pcm"].map(f => <option key={f} value={f}>{f}</option>)}
          </select>
        } />
        <SRow k="Sample rate" sub="fixed by the Kokoro engine — not configurable" mono v={<span style={{color: "var(--fg-4)"}}>24 kHz</span>} />
        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {ttsDirty && (
            <button className="btn ghost sm" onClick={() => {
              setTtsModel(ttsSelection.model || "");
              setTtsEnabled(!!ttsSelection.enabled);
              setTtsVoice(origVoice);
              setTtsSpeed(origSpeed);
              setTtsFormat(origFormat);
            }}>Reset</button>
          )}
          <button className="btn sm" disabled={!ttsDirty || !speedValid || loading || applyCapability.isPending || editSlot.isPending} onClick={doSaveTts}>Save TTS</button>
        </div>
      </div>
    </div>
  );
}

// ─── ImageGenSection ─────────────────────────────────────────────────────────
//
// Image-gen exposes enable/engine(provider)/model picks for the img.img slot.
// Persisted via POST /api/capabilities/img/img {model, provider, enabled}.
//
// Generation defaults (#599 ImageGenConfig — [image] table on the img slot
// TOML): default_size / default_steps / idle_restore_minutes. These persist
// via PUT /api/slots/{name}/config { image: {...} } through useSlotEdit,
// mirroring how the Voice section wires TTS default_voice. The img slot name
// is discovered from useSlots (type "image" / name "img"); when no img slot
// exists the controls degrade to disabled with a hint.
function ImageGenSection() {
  const capsQuery = useCapabilities();
  const applyCapability = useCapabilityApply();
  const slotsQuery = useSlots();
  const editSlot = useSlotEdit();

  const caps = capsQuery.data;
  const imgCatalogs = caps?.catalogs?.img || {};
  const imgSelections = caps?.selections?.img || {};
  const imgSelection = imgSelections.img || {};

  // Discover the img slot name so the [image] config read/write targets a real
  // slot. Prefer an explicit "img" name, else the first image-type slot.
  const imgSlotName =
    (slotsQuery.data || []).find(s => s.name === "img" || s.type === "image" || s.group === "img")?.name || null;
  const imgCfgQuery = useSlotConfig(imgSlotName);
  const imgCfgImage = (imgCfgQuery.data?.image) || {};

  // Schema defaults (ImageGenConfig): an all-defaults [image] table is elided
  // from the dumped config, so fall back to the same defaults the backend uses.
  const DEF_SIZE = "1024x1024";
  const DEF_STEPS = "0";
  const DEF_IDLE = "60";
  const origSize = imgCfgImage.default_size != null ? String(imgCfgImage.default_size) : DEF_SIZE;
  const origSteps = imgCfgImage.default_steps != null ? String(imgCfgImage.default_steps) : DEF_STEPS;
  const origIdle = imgCfgImage.idle_restore_minutes != null ? String(imgCfgImage.idle_restore_minutes) : DEF_IDLE;

  const [imgModel, setImgModel] = useStateSet("");
  const [imgEnabled, setImgEnabled] = useStateSet(false);
  const [imgProvider, setImgProvider] = useStateSet("");
  const [defaultSize, setDefaultSize] = useStateSet(DEF_SIZE);
  const [defaultSteps, setDefaultSteps] = useStateSet(DEF_STEPS);
  const [idleRestore, setIdleRestore] = useStateSet(DEF_IDLE);

  useEffectSet(() => {
    if (imgSelection.model != null) setImgModel(imgSelection.model || "");
    if (imgSelection.enabled != null) setImgEnabled(!!imgSelection.enabled);
    if (imgSelection.provider != null) setImgProvider(imgSelection.provider || "");
  }, [imgSelection.model, imgSelection.enabled, imgSelection.provider]);

  useEffectSet(() => {
    const img = imgCfgQuery.data?.image || {};
    setDefaultSize(img.default_size != null ? String(img.default_size) : DEF_SIZE);
    setDefaultSteps(img.default_steps != null ? String(img.default_steps) : DEF_STEPS);
    setIdleRestore(img.idle_restore_minutes != null ? String(img.idle_restore_minutes) : DEF_IDLE);
  }, [imgCfgQuery.data]);

  const imgDirty = imgModel !== (imgSelection.model || "") || imgEnabled !== !!imgSelection.enabled || imgProvider !== (imgSelection.provider || "");
  const defaultsDirty = !!imgSlotName && (defaultSize !== origSize || defaultSteps !== origSteps || idleRestore !== origIdle);
  const imgCatalogItems = imgCatalogs.img?.items || imgCatalogs.img?.models || [];
  const imgStatus = imgSelection.status || "offline";

  const doSave = async () => {
    try {
      const body = { model: imgModel, enabled: imgEnabled };
      if (imgProvider) body.provider = imgProvider;
      await applyCapability.mutateAsync({ slot: "img", child: "img", body });
      window.__hal0Toast && window.__hal0Toast("Image-gen settings saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Image-gen save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const doSaveDefaults = async () => {
    if (!imgSlotName) return;
    // Coerce to the ImageGenConfig field types before writing. Steps / idle are
    // non-negative ints (schema ge=0); size is a freeform "WxH" string.
    const image = {
      default_size: defaultSize.trim() || DEF_SIZE,
      default_steps: Math.max(0, parseInt(defaultSteps, 10) || 0),
      idle_restore_minutes: Math.max(0, parseInt(idleRestore, 10) || 0),
    };
    try {
      await editSlot.mutateAsync({ name: imgSlotName, body: { image } });
      window.__hal0Toast && window.__hal0Toast("Image-gen defaults saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const resetDefaults = () => {
    setDefaultSize(origSize);
    setDefaultSteps(origSteps);
    setIdleRestore(origIdle);
  };

  const statusChip = (st) => {
    const color = st === "ready" || st === "serving" ? "var(--ok)" : st === "starting" || st === "warming" ? "var(--warn)" : "var(--fg-4)";
    return <span className="chip mono" style={{borderColor: color, color, fontSize: 10, padding: "1px 6px"}}>{st}</span>;
  };

  const loading = capsQuery.isLoading;

  return (
    <div className="s-section">
      <h2>Image-gen</h2>
      <p className="desc">ComfyUI / stable-diffusion image generation slot configuration. Changes persist to the img.img capability slot.</p>

      <div className="s-panel">
        <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
          <div className="k"><span>Image-gen</span><span className="sub">img.img slot · ComfyUI engine</span></div>
          <div className="v">{statusChip(imgStatus)}</div>
        </div>
        <SRow k="Enabled" v={
          <input type="checkbox" checked={imgEnabled} onChange={e => setImgEnabled(e.target.checked)} style={{accentColor: "var(--accent)"}} />
        } />
        <SRow k="Engine" sub="provider for the img slot" v={
          <select value={imgProvider} onChange={e => setImgProvider(e.target.value)}
            style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
            <option value="">— auto —</option>
            <option value="comfyui">comfyui</option>
          </select>
        } />
        <SRow k="Model" v={
          imgCatalogItems.length > 0 ? (
            <select value={imgModel} onChange={e => setImgModel(e.target.value)}
              style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
              <option value="">— unset —</option>
              {imgCatalogItems.map(m => (
                <option key={m.id || m.model_id || m} value={m.id || m.model_id || m}>{m.id || m.model_id || m}</option>
              ))}
            </select>
          ) : (
            <input value={imgModel} onChange={e => setImgModel(e.target.value)} placeholder="model id (e.g. sdxl-turbo-fp16)"
              className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 260}} />
          )
        } sub={imgCatalogItems.length === 0 ? "no installed image models — install one in the Models view" : undefined} />

        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {imgDirty && (
            <button className="btn ghost sm" onClick={() => {
              setImgModel(imgSelection.model || "");
              setImgEnabled(!!imgSelection.enabled);
              setImgProvider(imgSelection.provider || "");
            }}>Reset</button>
          )}
          <button className="btn sm" disabled={!imgDirty || loading || applyCapability.isPending} onClick={doSave}>Save Image-gen</button>
        </div>
      </div>

      {/* ── Generation defaults (#599 ImageGenConfig — [image] on the img slot) ── */}
      <div className="s-panel" style={{marginTop: 12}}>
        <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
          <div className="k">
            <span>Generation defaults</span>
            <span className="sub">img slot config · applied when a /v1/images request omits the param</span>
          </div>
          <div className="v">
            {imgSlotName
              ? <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-3)"}}>{imgSlotName}</span>
              : <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-4)"}}>no img slot</span>}
          </div>
        </div>
        <SRow k="Default size" sub="Output resolution as WxH (e.g. 1024x1024)" v={
          <input value={defaultSize} onChange={e => setDefaultSize(e.target.value)} placeholder={DEF_SIZE}
            disabled={!imgSlotName}
            className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 140}} />
        } />
        <SRow k="Default steps" sub="Sampler steps · 0 = use the model-class default" v={
          <input type="number" min={0} value={defaultSteps} onChange={e => setDefaultSteps(e.target.value)} placeholder={DEF_STEPS}
            disabled={!imgSlotName}
            className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 100}} />
        } />
        <SRow k="Idle restore" sub="Minutes of img inactivity before the GPU arbiter restores LLM slots · 0 = manual only" v={
          <input type="number" min={0} value={idleRestore} onChange={e => setIdleRestore(e.target.value)} placeholder={DEF_IDLE}
            disabled={!imgSlotName}
            className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 100}} />
        } />
        {!imgSlotName && (
          <div className="s-row" style={{padding: "6px 12px"}}>
            <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>No img slot configured — create one in the Slots view to edit generation defaults.</span>
          </div>
        )}
        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {defaultsDirty && (
            <button className="btn ghost sm" onClick={resetDefaults}>Reset</button>
          )}
          <button className="btn sm" disabled={!defaultsDirty || editSlot.isPending} onClick={doSaveDefaults}>
            {editSlot.isPending ? "Saving…" : "Save defaults"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── NpuSection ──────────────────────────────────────────────────────────────
//
// FastFlowLM (FLM) runs on the AMD XDNA2 NPU as a single process that can
// multiplex chat + embed + ASR. The three operator-relevant knobs already
// persist in the npu slot TOML and are consumed by providers/flm.py:
//   - [model].context_size → HAL0_FLM_CTX → --ctx-len
//   - [npu].embed          → HAL0_FLM_LOAD_EMBED → --embed 1
//   - [npu].asr            → HAL0_FLM_LOAD_ASR   → --asr 1
// All three take effect when the slot's container next (re)starts, so they're
// ─── SlotsSection ────────────────────────────────────────────────────────────

function SlotsSection() {
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
  const settings = useSettings();
  const update = useSettingsUpdate();
  const schemaQuery = useSettingsSchema();
  const applyPlanQuery = useApplyPlan();
  const registry = applyPlanQuery.data?.registry || {};
  const schema = schemaQuery.data || null;
  const live = settings.data || null;

  const RUNTIME_KEYS = [
    "slots.max_slots", "slots.port_range_start", "slots.port_range_end",
    "slots.idle_timeout_s", "slots.evict_pressure_mb", "slots.publish_host",
  ];
  const runtimeFields = {};
  for (const k of RUNTIME_KEYS) runtimeFields[k] = _schemaField(schema, k);

  const [buf, setBuf] = useStateSet({});
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
      <h2>Slots</h2>
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
            <div className="k"><span>Default slots</span><span className="sub">For each modality with multiple slots, pick the one that serves type-routed requests</span></div>
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
          <div className="k"><span>Runtime</span><span className="sub">hal0.toml [slots]</span></div>
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

// service-restart. Persisted via PUT /api/slots/{name}/config, mirroring how
// ImageGenSection writes the [image] table. A read-only occupancy strip below
// reflects the live AIE-column allocation (single-tenant: one FLM = 8 cols).
function NpuSection() {
  const slotsQuery = useSlots();
  const editSlot = useSlotEdit();
  const occQuery = useNpuOccupancy();

  const npuSlots = (slotsQuery.data || []).filter(s => s.device === "npu");
  const npuName = npuSlots.length > 0 ? npuSlots[0].name : null;
  const cfgQuery = useSlotConfig(npuName);
  const cfg = cfgQuery.data || {};
  const liveCtx = cfg.model?.context_size;
  const liveNpu = cfg.npu || {};

  const DEF_CTX = "16384";
  const origCtx = liveCtx != null ? String(liveCtx) : DEF_CTX;
  const origAsr = !!liveNpu.asr;
  const origEmbed = !!liveNpu.embed;

  const [ctx, setCtx] = useStateSet(DEF_CTX);
  const [asr, setAsr] = useStateSet(false);
  const [embed, setEmbed] = useStateSet(false);
  useEffectSet(() => {
    setCtx(liveCtx != null ? String(liveCtx) : DEF_CTX);
    setAsr(!!liveNpu.asr);
    setEmbed(!!liveNpu.embed);
  }, [cfgQuery.data]);

  const ctxNum = parseInt(ctx, 10);
  const ctxValid = /^\d+$/.test(ctx.trim()) && ctxNum >= 512;
  const dirty = !!npuName && (ctx !== origCtx || asr !== origAsr || embed !== origEmbed);

  const doSave = async () => {
    if (!npuName || !ctxValid) return;
    const body = { model: { context_size: ctxNum }, npu: { asr, embed } };
    try {
      await editSlot.mutateAsync({ name: npuName, body });
      window.__hal0Toast && window.__hal0Toast("NPU settings saved — restart the slot to apply", "warn");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const reset = () => { setCtx(origCtx); setAsr(origAsr); setEmbed(origEmbed); };

  const occ = occQuery.data;
  const inputStyle = {fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"};

  return (
    <div className="s-section">
      <h2>NPU</h2>
      <p className="desc">
        FastFlowLM on the AMD XDNA2 NPU. One FLM process serves chat and, when enabled,
        embeddings + speech-to-text on the same 8-column AIE array. These knobs write the
        npu slot TOML and take effect on the slot's next restart.
      </p>

      {slotsQuery.isPending && (
        <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading slots…</div>
      )}
      {!slotsQuery.isPending && npuSlots.length === 0 && (
        <p className="hint" style={{fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)"}}>
          No NPU slot configured. Create a slot with device <span className="mono">npu</span> in the Slots view (or run <span className="mono">hal0 setup</span> with NPU opt-in) to tune FLM here.
        </p>
      )}

      {npuSlots.length > 0 && (
        <div className="s-panel">
          <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
            <div className="k"><span>FLM slot</span><span className="sub">{npuName} · device=npu · profile=flm</span></div>
            <div className="v">
              <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-3)"}}>{npuName}</span>
            </div>
          </div>
          <SRow
            k="Context size"
            sub="FLM --ctx-len (tokens) · larger = more KV cache on the NPU"
            v={
              <input type="number" min={512} step={512} value={ctx} disabled={!npuName}
                onChange={e => setCtx(e.target.value)} placeholder={DEF_CTX}
                className="mono" style={{...inputStyle, width: 120, borderColor: ctxValid || !ctx ? "var(--line)" : "var(--err)"}} />
            }
            actions={<span className="chip mono" style={{fontSize: 10, padding: "2px 8px", color: "var(--warn)", borderColor: "var(--warn)", whiteSpace: "nowrap"}}>⟳ restart {npuName}</span>}
          />
          <SRow
            k="Load embeddings"
            sub="Serve /v1/embeddings from the FLM trio (--embed 1)"
            v={
              <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
                <input type="checkbox" checked={embed} disabled={!npuName} onChange={e => setEmbed(e.target.checked)} style={{accentColor: "var(--accent)"}} />
                <span>{embed ? "enabled" : "disabled"}</span>
              </label>
            }
          />
          <SRow
            k="Load ASR"
            sub="Serve /v1/audio/transcriptions from the FLM trio (--asr 1)"
            v={
              <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
                <input type="checkbox" checked={asr} disabled={!npuName} onChange={e => setAsr(e.target.checked)} style={{accentColor: "var(--accent)"}} />
                <span>{asr ? "enabled" : "disabled"}</span>
              </label>
            }
          />
          <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
            {dirty && <button className="btn ghost sm" onClick={reset}>Reset</button>}
            <button className="btn sm" disabled={!dirty || !ctxValid || editSlot.isPending} onClick={doSave}>
              {editSlot.isPending ? "Saving…" : "Save NPU settings"}
            </button>
          </div>
        </div>
      )}

      {/* ── Live occupancy (read-only) ── */}
      {occ?.present && (
        <div className="s-panel" style={{marginTop: 12}}>
          <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
            <div className="k"><span>Occupancy</span><span className="sub">live AIE column allocation · single-tenant</span></div>
            <div className="v">
              <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: occ.cols_used > 0 ? "var(--ok)" : "var(--fg-4)", borderColor: occ.cols_used > 0 ? "var(--ok)" : "var(--line)"}}>
                {occ.cols_used}/{occ.cols_total} cols
              </span>
            </div>
          </div>
          <SRow k="Peak" mono v={`${occ.tops_peak} TOPS · ${occ.tiles} tiles (${occ.rows}×${occ.cols})`} />
          {(occ.slots || []).map(s => (
            <SRow key={s.name} k={s.name} sub={s.model || "—"} mono
              v={<>
                <span style={{color: s.state === "serving" || s.state === "ready" ? "var(--ok)" : "var(--fg-4)"}}>{s.state}</span>
                <span style={{color: "var(--fg-4)"}}> · {s.cols?.length || 0} cols{s.gb != null ? ` · ${s.gb} GB` : ""}</span>
              </>} />
          ))}
        </div>
      )}
      {occQuery.data && !occ?.present && (
        <div style={{marginTop: 12, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>
          No NPU detected on this host — occupancy unavailable.
        </div>
      )}
    </div>
  );
}

function GeneralSection() {
  const settings = useSettings();
  const update = useSettingsUpdate();
  const applyPlanQuery = useApplyPlan();
  const registry = applyPlanQuery.data?.registry || {};
  const liveTelemetry = settings.data?.telemetry;

  const [telemetry, setTelemetry] = useStateSet(false);
  useEffectSet(() => {
    if (settings.data) setTelemetry(settings.data.telemetry?.enabled === true);
  }, [settings.data]);

  const telemetryDirty = !!settings.data && telemetry !== (liveTelemetry?.enabled === true);

  const onSaveTelemetry = async () => {
    try {
      await update.mutateAsync({ telemetry: { enabled: telemetry } });
      window.__hal0Toast && window.__hal0Toast(`Telemetry ${telemetry ? "enabled" : "disabled"}`, "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const meta = settings.data?.meta || {};
  const version = meta.hal0_version || "—";
  const schemaVer = meta.schema_version != null ? String(meta.schema_version) : "—";

  return (
    <div className="s-section">
      <h2>General</h2>
      <p className="desc">Platform identity and privacy.</p>
      <div className="s-panel">
        <SRow k="hal0 version" sub="Running API version" mono v={<span style={{color: "var(--fg-2)"}}>{version}</span>} />
        <SRow k="Schema version" sub="hal0.toml schema version" mono v={<span style={{color: "var(--fg-3)"}}>{schemaVer}</span>} />
        <SRow
          k="Anonymous telemetry"
          sub="Opt-in · off by default. Sends anonymized, aggregate usage counts to help prioritize work — no prompts, model I/O, or file paths leave the machine."
          v={
            <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
              <input
                type="checkbox"
                checked={telemetry}
                disabled={!settings.data}
                onChange={e => setTelemetry(e.target.checked)}
                style={{accentColor: "var(--accent)"}}
              />
              <span>{telemetry ? "enabled" : "disabled"}</span>
            </label>
          }
          actions={
            <div style={{display: "inline-flex", alignItems: "center", gap: 6}}>
              <ApplyBadge settingsKey="telemetry.enabled" registry={registry} />
              {telemetryDirty && (
                <button className="btn ghost sm" disabled={update.isPending} onClick={onSaveTelemetry}>
                  {update.isPending ? "Saving…" : "Save"}
                </button>
              )}
            </div>
          }
        />
      </div>
    </div>
  );
}

// ─── AdvancedSection ─────────────────────────────────────────────────────────
//
// Closes the hal0.toml ↔ UI parity gap: every [slots] / [dispatcher] /
// [memory] / [activity] key that previously required `hal0 config edit`.
// Controls are rendered FROM THE SERVER SCHEMA (GET /api/settings/schema —
// pydantic field types, bounds, and descriptions), so copy can't drift and
// new constraints apply without frontend edits. Saves go through the same
// deep-merging PUT /api/settings as the rest of the page; per-key effect
// chips come from the apply-plan registry, and any dirty manual-restart key
// routes through a confirm gate before the write.
//
// memory.engine is a plain string in the schema (validator-enforced), so
// its options are pinned here to the backend's accepted set.
// Every key here is verified consumed by the backend: max_slots gates
const ADV_GROUPS = [
  { title: "Dispatcher", sub: "hal0.toml [dispatcher] · upstream routing tunables", keys: [
    "dispatcher.prefetch_timeout_s", "dispatcher.prefetch_parallel_cap",
  ]},
  { title: "Activity log", sub: "hal0.toml [activity] · durable audit trail", keys: [
    "activity.enabled", "activity.retention_days", "activity.max_rows",
  ]},
];
// memory.engine's validator also accepts "cognee" and "mem0", but the
// factory silently maps both to hindsight — offering them would lie.
const ADV_OPTIONS = {
  "memory.engine": ["hindsight", "pgvector"],
};
// Overrides replace the schema description where it's stale or missing.
const ADV_DESC_OVERRIDE = {
  "slots.publish_host":
    "Host address slot ports publish on. 127.0.0.1 = loopback-only (default, safe): slots are reachable only via hal0-api/Traefik. " +
    "0.0.0.0 exposes every slot's raw port directly on your LAN (e.g. http://<host>.local:<port>), bypassing the reverse-proxy front door — " +
    "only widen this on a trusted network. A specific interface IP binds just that address. Applies on the next slot restart.",
  "memory.engine":
    "Active memory engine, applied on the next hal0-api restart. hindsight is the durable default. " +
    "pgvector is an in-memory, NON-DURABLE fallback — existing memories are not migrated and won't be visible while selected.",
  "activity.enabled": "Record config changes and state transitions to the durable activity log.",
  "activity.retention_days": "Days of activity history to keep before pruning. The HAL0_ACTIVITY_RETENTION_DAYS env var, if set, overrides this value.",
  "activity.max_rows": "Hard cap on stored activity rows (minimum 100).",
};

// Resolve $ref / single-allOf indirection in a pydantic JSON schema node.
function _schemaResolve(schema, node) {
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
function _schemaField(schema, dotKey) {
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

const _getIn = (obj, dotKey) =>
  dotKey.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

const _deepMergePatch = (a, b) => {
  const out = { ...a };
  for (const k of Object.keys(b)) {
    const both = a && typeof a[k] === "object" && a[k] && !Array.isArray(a[k])
      && typeof b[k] === "object" && b[k] && !Array.isArray(b[k]);
    out[k] = both ? _deepMergePatch(a[k], b[k]) : b[k];
  }
  return out;
};

// Buffer string → typed value per the field schema. Returns {ok, value}.
function _advCoerce(f, raw) {
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

const _advInputStyle = {
  fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)",
  border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px",
};

function AdvRow({ dotKey, field, live, buf, onChange, registry }) {
  const label = dotKey.split(".").slice(1).join(".");
  const desc = ADV_DESC_OVERRIDE[dotKey] || field?.description || "";
  const shortDesc = desc.length > 150 ? desc.slice(0, 147) + "…" : desc;
  const options = ADV_OPTIONS[dotKey] || field?.enum || null;
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

function AdvancedSection() {
  const settings = useSettings();
  const update = useSettingsUpdate();
  const schemaQuery = useSettingsSchema();
  const applyPlanQuery = useApplyPlan();
  const registry = applyPlanQuery.data?.registry || {};
  const schema = schemaQuery.data || null;
  const live = settings.data || null;

  // Edit buffer — dotKey → raw control value; only touched keys present.
  const [buf, setBuf] = useStateSet({});
  const [confirmKeys, setConfirmKeys] = useStateSet(null);
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
        Low-level dispatcher and activity log tuning. Slot runtime moved to Slots; memory
        moved to Memory. Effect chips show whether a change applies live or needs a restart.
      </p>

      {loading && <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading config schema…</div>}
      {(settings.isError || schemaQuery.isError) && (
        <div className="err">{settings.error?.message || schemaQuery.error?.message || "Failed to load settings"}</div>
      )}

      {!loading && !settings.isError && !schemaQuery.isError && (
        <>
          {ADV_GROUPS.map(g => (
            <React.Fragment key={g.title}>
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
                  />
                ))}
              </div>
            </React.Fragment>
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

// ─── Memory graph extraction panel ──────────────────────────────────────────
//
// [memory.graph] gets a dedicated panel (not schema-driven rows) because the
// dedicated PUT /api/memory/graph endpoint does what the generic settings PUT
// can't: it validates the extraction slot against live enabled llm slots
// (available_slots / slot_resolves) and propagates the change into the
// hindsight-api drop-in + restart, reporting a propagation error if that
// restart fails (ADR-0023 §3).
function MemoryGraphPanel() {
  const statusQuery = useMemoryGraphStatus();
  const updateGraph = useUpdateMemoryGraph();
  const st = statusQuery.data;

  const [enabled, setEnabled] = useStateSet(false);
  const [slot, setSlot] = useStateSet("");
  const [timeoutS, setTimeoutS] = useStateSet("300");
  useEffectSet(() => {
    if (!st) return;
    setEnabled(!!st.enabled);
    setSlot(st.extraction_slot || "");
    if (st.llm_timeout_s != null) setTimeoutS(String(st.llm_timeout_s));
  }, [st?.enabled, st?.extraction_slot, st?.llm_timeout_s]);

  const timeoutNum = parseInt(timeoutS, 10);
  const timeoutValid = /^\d+$/.test(timeoutS.trim()) && timeoutNum >= 30 && timeoutNum <= 3600;
  const dirty = !!st && (
    enabled !== !!st.enabled
    || slot !== (st.extraction_slot || "")
    || (st.llm_timeout_s != null && timeoutS !== String(st.llm_timeout_s))
  );
  const slots = st?.available_slots || [];
  // Keep the currently-configured slot pickable even when it no longer
  // resolves, so the operator can see (and move off) a stale value.
  const slotOptions = slot && !slots.includes(slot) ? [slot, ...slots] : slots;

  const doSave = async () => {
    try {
      const body = { enabled };
      if (slot) body.extraction_slot = slot;
      if (timeoutValid) body.llm_timeout_s = timeoutNum;
      const resp = await updateGraph.mutateAsync(body);
      const perr = resp?.propagation?.error;
      window.__hal0Toast && window.__hal0Toast(
        perr
          ? `Saved, but hindsight-api restart failed — ${perr}`
          : "Memory graph settings saved",
        perr ? "err" : "ok",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const stateChip = !st
    ? <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-4)"}}>—</span>
    : st.enabled
      ? (st.in_flight > 0
          ? <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--warn)", borderColor: "var(--warn)"}}>extracting · {st.in_flight}</span>
          : <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--ok)", borderColor: "var(--ok)"}}>on</span>)
      : <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-4)"}}>off</span>;

  return (
    <div className="s-panel" style={{marginBottom: 12}}>
      <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
        <div className="k">
          <span>Memory graph extraction</span>
          <span className="sub">hal0.toml [memory.graph] · builds a knowledge graph from stored memories via a local LLM slot</span>
        </div>
        <div className="v">{stateChip}</div>
      </div>
      {statusQuery.isError && (
        <div className="s-row" style={{padding: "8px 12px"}}>
          <span className="mono" style={{fontSize: 11, color: "var(--err)"}}>
            Could not load graph status — {statusQuery.error?.message || "is memory enabled on this install?"}
          </span>
        </div>
      )}
      <SRow k="Enabled" sub="Extract entities/relations from new memories in the background" v={
        <input type="checkbox" checked={enabled} disabled={!st} onChange={e => setEnabled(e.target.checked)} style={{accentColor: "var(--accent)"}} />
      } />
      <SRow
        k="Extraction slot"
        sub={st && !st.slot_resolves
          ? "⚠ configured slot doesn't match an enabled LLM slot — extraction is stalled until this points at a live slot"
          : "Local LLM slot that runs the extraction prompts"}
        v={
          slotOptions.length > 0 ? (
            <select value={slot} disabled={!st} onChange={e => setSlot(e.target.value)} style={_advInputStyle}>
              {slotOptions.map(s => (
                <option key={s} value={s}>{s}{slots.includes(s) ? "" : " (not running)"}</option>
              ))}
            </select>
          ) : (
            <input value={slot} disabled={!st} onChange={e => setSlot(e.target.value)} placeholder="slot name (e.g. utility)"
              className="mono" style={{..._advInputStyle, width: 200}} />
          )
        }
      />
      <SRow
        k="LLM timeout"
        sub="Seconds the Hindsight daemon waits on extraction / consolidation / reflect calls (30–3600) · covers cold slot starts"
        v={
          <input
            type="number" min={30} max={3600} value={timeoutS} disabled={!st}
            onChange={e => setTimeoutS(e.target.value)}
            placeholder="300"
            className="mono"
            style={{..._advInputStyle, width: 100, borderColor: timeoutValid || !timeoutS ? "var(--line)" : "var(--err)"}}
          />
        }
      />
      {st && (
        <SRow
          k="Extraction health"
          sub="Lifetime counters for graph builds on this install"
          mono
          v={<>
            <span style={{color: "var(--ok)"}}>{st.builds_ok} built</span>
            <span style={{color: st.errors > 0 ? "var(--err)" : "var(--fg-4)"}}> · {st.errors} error{st.errors === 1 ? "" : "s"}</span>
            {st.last_built_at && <span style={{color: "var(--fg-4)"}}> · last {new Date(st.last_built_at).toLocaleString()}</span>}
            {st.last_error && <span style={{color: "var(--err)"}} title={st.last_error}> · {st.last_error.slice(0, 80)}{st.last_error.length > 80 ? "…" : ""}</span>}
          </>}
        />
      )}
      <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
        {dirty && (
          <button className="btn ghost sm" onClick={() => {
            setEnabled(!!st?.enabled);
            setSlot(st?.extraction_slot || "");
            setTimeoutS(st?.llm_timeout_s != null ? String(st.llm_timeout_s) : "300");
          }}>Reset</button>
        )}
        <button className="btn sm" disabled={!dirty || !timeoutValid || updateGraph.isPending} onClick={doSave}>
          {updateGraph.isPending ? "Saving…" : "Save graph settings"}
        </button>
      </div>
    </div>
  );
}

// ─── Restart hal0-api panel ──────────────────────────────────────────────────
//
// Uses the whitelisted one-click repair endpoint (hal0-api.service is in
// _REPAIRABLE_UNITS). The POST's connection is EXPECTED to drop — the API
// restarts underneath the request — so both success and network error enter
// a health-poll loop against /api/health until the service answers again.
function RestartApiPanel() {
  const repair = useServiceRepair();
  const qc = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useStateSet(false);
  const [waiting, setWaiting] = useStateSet(false);
  const pollRef = useRefSet(null);
  // Unmount guard: the tick may be awaiting fetch() when the component
  // unmounts — clearing the timer alone wouldn't stop it from rescheduling
  // or firing toasts/invalidation after navigation.
  const cancelledRef = useRefSet(false);

  useEffectSet(() => () => {
    cancelledRef.current = true;
    if (pollRef.current) clearTimeout(pollRef.current);
  }, []);

  const pollHealth = (deadline) => {
    pollRef.current = setTimeout(async () => {
      try {
        const res = await fetch("/api/health", { headers: { Accept: "application/json" } });
        if (cancelledRef.current) return;
        if (res.ok) {
          setWaiting(false);
          window.__hal0Toast && window.__hal0Toast("hal0-api is back online", "ok");
          qc.invalidateQueries();
          return;
        }
      } catch { /* still restarting */ }
      if (cancelledRef.current) return;
      if (Date.now() < deadline) pollHealth(deadline);
      else {
        setWaiting(false);
        window.__hal0Toast && window.__hal0Toast("hal0-api didn't come back within 90s — check journalctl -u hal0-api", "err");
      }
    }, 1500);
  };

  const doRestart = () => {
    setWaiting(true);
    window.__hal0Toast && window.__hal0Toast("Restarting hal0-api — brief outage expected", "warn");
    // The request racing the restart means BOTH outcomes are normal here.
    repair.mutate("hal0-api.service", {
      onSettled: () => pollHealth(Date.now() + 90_000),
    });
  };

  return (
    <div className="s-panel">
      <SRow
        k="hal0-api service"
        sub="Control plane · dashboard, slot manager, /v1 proxy. Restart to apply settings marked ⟳ restart hal0-api or ⚠ manual restart."
        v={waiting
          ? <span className="mono" style={{color: "var(--warn)", fontSize: 11}}>restarting — waiting for /api/health…</span>
          : <span className="mono" style={{color: "var(--fg-3)", fontSize: 11}}>running</span>}
        actions={
          <button className="btn danger sm" disabled={waiting || repair.isPending} onClick={() => setConfirmOpen(true)}>
            {waiting ? "Restarting…" : "Restart hal0-api"}
          </button>
        }
      />
      <ConfirmDialog
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => { setConfirmOpen(false); doRestart(); }}
        title="Restart hal0-api?"
        message={
          <span>
            In-flight requests (including chat completions) will drop while the service restarts —
            typically a few seconds. Slot containers keep running; the dashboard reconnects automatically.
          </span>
        }
        confirmLabel="Restart"
      />
    </div>
  );
}

function AboutSection() {
  // #543: read hal0 version live from /api/updates/state instead of a
  // hardcoded literal that drifts from the running build. Empty until the
  // first response lands so the layout doesn't shift around a stale value.
  const stateQuery = useUpdateState();
  const liveVersion = stateQuery.data?.hal0?.current || "";
  return (
    <div className="s-section">
      <h2>About</h2>
      <div className="s-panel">
        <SRow k="hal0" mono v={liveVersion ? `${liveVersion} — container slots` : "—"} />
        <SRow k="License" v="Apache-2.0" />
        <SRow k="Repository" mono v="github.com/Hal0ai/hal0" actions={<a className="btn ghost sm" href="https://github.com/Hal0ai/hal0" target="_blank" rel="noreferrer">{Icons.ext} Open</a>} />
        <SRow k="Docs" v="hal0.dev/docs" actions={<a className="btn ghost sm" href="https://hal0.dev/docs/" target="_blank" rel="noreferrer">{Icons.ext} Open</a>} />
        <SRow k="Discord" v="discord.gg/hal0" actions={<a className="btn ghost sm" href="https://discord.gg/hal0" target="_blank" rel="noreferrer">{Icons.ext} Join</a>} />
      </div>
      <div style={{marginTop: 14, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>
        Built on FLM (XDNA2), llama.cpp, whisper.cpp, sd.cpp, Kokoro, Cognee.
      </div>
    </div>
  );
}

Object.assign(window, { SettingsView });
