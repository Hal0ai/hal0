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
import { useUpdateState, useUpdateCheck, useUpdateApply, useUpdateJob, useSetUpdateChannel } from '@/api/hooks/useUpdates'
import { useCapabilities, useCapabilityPatch, useCapabilityApply } from '@/api/hooks/useCapabilities'
import { useSlotEdit, useSlotConfig } from '@/api/hooks/useSlots'
import {
  useSettings,
  useSettingsUpdate,
  useModelStore,
  useModelStoreSet,
  useModelStoreMigrate,
  useApplyPlan,
} from '@/api/hooks/useSettings'

const { useState: useStateSet, useEffect: useEffectSet, useRef: useRefSet } = React;

function SettingsView() {
  const [section, setSection] = useStateSet("secrets");
  const sections = [
    { id: "secrets",   label: "Secrets" },
    { id: "storage",   label: "Storage" },
    { id: "updates",   label: "Updates" },
    { id: "voice",     label: "Voice" },
    { id: "imagegen",  label: "Image-gen" },
    { id: "general",   label: "General" },
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
          {section === "secrets" && <SecretsSection />}
          {section === "storage" && <StorageSection />}
          {section === "updates" && <UpdatesSection />}
          {section === "voice" && <VoiceSection />}
          {section === "imagegen" && <ImageGenSection />}
          {section === "general" && <GeneralSection />}
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
            <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
              Stored at <span style={{color: "var(--fg-3)"}}>/etc/hal0/hal0.toml</span>
              {storeDirty && <span style={{marginLeft: 8, color: "var(--warn)"}}>· unsaved changes</span>}
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

function SecretsSection() {
  const [addOpen, setAddOpen] = useStateSet(false);
  const secretsQuery = useSecrets();
  const delSecret = useSecretDelete();
  const rows = secretsQuery.data ?? [];
  return (
    <div className="s-section">
      <h2>Secrets</h2>
      <p className="desc">Encrypted at rest. Used for gated HF repos and provider auth.</p>
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
            sub={s.name === 'HF_TOKEN' ? 'Hugging Face — used for gated repos' : 'Optional · fallback provider'}
            mono
            v={s.set
              ? <span style={{color: "var(--ok)"}}>{s.masked || '••• · set'}</span>
              : <span style={{color: "var(--fg-4)"}}>not set</span>}
            actions={s.set
              ? (<>
                  <button className="btn ghost sm" onClick={() => setAddOpen(true)}>Update</button>
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
              : <button className="btn ghost sm" onClick={() => setAddOpen(true)}>Add</button>}
          />
        ))}
      </div>
      <div style={{marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
        <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
          {rows.length > 0 ? `${rows.length} key${rows.length === 1 ? "" : "s"} stored` : "add keys for gated repos and provider auth"}
        </span>
        <button className="btn" onClick={() => setAddOpen(true)}>{Icons.plus} Add secret</button>
      </div>
      <AddSecretModal open={addOpen} onClose={() => setAddOpen(false)} />
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
              disabled={!u.hal0?.available || applyM.isPending || !!jobBusy}
              onClick={() => {
                applyM.mutate(undefined, {
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
            <a className="btn ghost sm" href="https://hal0.dev/changelog" target="_blank" rel="noreferrer">Changelog →</a>
          </>}
        />
        <SRow
          k="flm"
          sub="Manual deb · vendor-supplied"
          mono
          v={u.flm?.current || '—'}
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
        <SRow
          k="FirstRun"
          sub="Re-run the bundle picker without reinstalling"
          v="capabilities.toml will be overwritten on confirm"
          actions={<button className="btn ghost sm" onClick={() => window.location.hash = "#firstrun"}>{Icons.restart} Run picker again</button>}
        />
      </div>
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

// ─── VoiceSection ───────────────────────────────────────────────────────────
//
// STT: pick model from capabilities.catalogs.voice.stt — persisted via
//   POST /api/capabilities/voice/stt {model, provider, enabled}.
// TTS: pick model + default_voice — model/enabled via capabilities POST,
//   default_voice via PUT /api/slots/tts/config {default_voice}.
//
// Reflects current effective values from capabilities.selections.voice.{stt,tts}
// and from /api/slots/tts/config (for default_voice).
//
// Deferred (no per-slot-config path today):
//   - STT language hints, silence thresholds (per-request params, not slot config).
//   - TTS speed/sample-rate (per-request in /v1/audio/speech body, not persisted).
function VoiceSection() {
  const capsQuery = useCapabilities();
  const applyCapability = useCapabilityApply();
  const ttsSlotCfgQuery = useSlotConfig("tts");
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
  }, [ttsCfg.default_voice]);

  const sttDirty = sttModel !== (sttSelection.model || "") || sttEnabled !== !!sttSelection.enabled;
  const ttsDirty = ttsModel !== (ttsSelection.model || "") || ttsEnabled !== !!ttsSelection.enabled || ttsVoice !== (ttsCfg.default_voice ? String(ttsCfg.default_voice) : "");

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
      // Persist default_voice via slot config if changed
      const origVoice = ttsCfg.default_voice ? String(ttsCfg.default_voice) : "";
      if (ttsVoice !== origVoice) {
        // ttsVoice === "" intentionally clears default_voice back to the server default
        await editSlot.mutateAsync({ name: "tts", body: { default_voice: ttsVoice } });
      }
      window.__hal0Toast && window.__hal0Toast("TTS settings saved", "ok");
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
        <SRow k="Default voice" sub="applied when /v1/audio/speech omits the voice param · bundled voices (Kokoro v1)" v={
          <select value={ttsVoice} onChange={e => setTtsVoice(e.target.value)}
            style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
            <option value="">— use server default (af_bella) —</option>
            {KOKORO_VOICES.map(v => (
              <option key={v.id} value={v.id}>{v.label}</option>
            ))}
          </select>
        } />
        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {ttsDirty && (
            <button className="btn ghost sm" onClick={() => {
              setTtsModel(ttsSelection.model || "");
              setTtsEnabled(!!ttsSelection.enabled);
              setTtsVoice(ttsCfg.default_voice ? String(ttsCfg.default_voice) : "");
            }}>Reset</button>
          )}
          <button className="btn sm" disabled={!ttsDirty || loading || applyCapability.isPending || editSlot.isPending} onClick={doSaveTts}>Save TTS</button>
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
// Deferred (#554 follow-up — no clean slot-config path):
//   - Default size (width × height): read from /v1/images/generations body, not slot config.
//   - Steps: from extra_body.steps per-request; template defaults in workflows JSON.
//   - ComfyUI workflow selection: bound at inference time by model_class from the registry.
//   These require either per-request defaults in slot TOML (not yet modelled) or a
//   new /api/slots/{name}/defaults surface — tracked in the issue body.
function ImageGenSection() {
  const capsQuery = useCapabilities();
  const applyCapability = useCapabilityApply();

  const caps = capsQuery.data;
  const imgCatalogs = caps?.catalogs?.img || {};
  const imgSelections = caps?.selections?.img || {};
  const imgSelection = imgSelections.img || {};

  const [imgModel, setImgModel] = useStateSet("");
  const [imgEnabled, setImgEnabled] = useStateSet(false);
  const [imgProvider, setImgProvider] = useStateSet("");

  useEffectSet(() => {
    if (imgSelection.model != null) setImgModel(imgSelection.model || "");
    if (imgSelection.enabled != null) setImgEnabled(!!imgSelection.enabled);
    if (imgSelection.provider != null) setImgProvider(imgSelection.provider || "");
  }, [imgSelection.model, imgSelection.enabled, imgSelection.provider]);

  const imgDirty = imgModel !== (imgSelection.model || "") || imgEnabled !== !!imgSelection.enabled || imgProvider !== (imgSelection.provider || "");
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

        {/* Size / Steps / Workflow are per-request params (extra_body.*), not slot config — hidden until /api/slots/{name}/defaults lands */}

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
    </div>
  );
}

function GeneralSection() {
  return (
    <div className="s-section">
      <h2>General</h2>
      <p className="desc">
        The dashboard is dark-only by design. Theme / density / accent customization is not available in this release.
      </p>
      <div className="s-panel">
        <SRow k="Theme" v={<span className="chip mono" style={{color: "var(--fg-4)"}}>dark · locked</span>} />
      </div>
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
        <SRow k="Docs" v="hal0.dev/docs/v0.2-upgrade" actions={<a className="btn ghost sm" href="https://hal0.dev/docs/v0.2-upgrade" target="_blank" rel="noreferrer">{Icons.ext} Open</a>} />
        <SRow k="Discord" v="discord.gg/hal0" actions={<a className="btn ghost sm" href="https://discord.gg/hal0" target="_blank" rel="noreferrer">{Icons.ext} Join</a>} />
      </div>
      <div style={{marginTop: 14, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>
        Built on FLM (XDNA2), llama.cpp, whisper.cpp, sd.cpp, Kokoro, Cognee.
      </div>
    </div>
  );
}

Object.assign(window, { SettingsView });
