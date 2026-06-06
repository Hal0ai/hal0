// hal0 dashboard — Settings view (secrets, storage, updates, runtime, general, about)
//
// Phase B2: every section reads from live hooks. Storage drives
// [models].store + propagates to Lemonade's extra_models_dir; Runtime
// edits /internal/config with immediate/deferred effect hints; General
// is the cosmetic placeholders block (theme locked to dark, density
// picker, accent chip).
//
// OmniRouter routing table, Agent-policy, and Memory (Cognee) sections
// were removed in #544 — those surfaces live on the MCP view and the
// agent view, respectively. The settings rail is for knobs only.

import { useSecrets, useSecretSet, useSecretDelete } from '@/api/hooks/useSecrets'
import { useUpdateState, useUpdateCheck, useUpdateApply, useUpdateJob, useSetUpdateChannel } from '@/api/hooks/useUpdates'
import { useCapabilities, useCapabilityPatch } from '@/api/hooks/useCapabilities'
import { useLemondRollup, useLemonadeStats } from '@/api/hooks/useLemonade'
import { useLemonadeConfig, useLemonadeConfigSet } from '@/api/hooks/useLemonadeConfig'
import { useSlots, useSlotEdit } from '@/api/hooks/useSlots'
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
    { id: "runtime",   label: "Runtime" },
    { id: "general",   label: "General" },
    { id: "about",     label: "About" },
  ];

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Configure</span>
        <h1>Settings</h1>
        <span className="vh-spacer" />
        <span className="hint mono">unsaved · 0</span>
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
          {section === "runtime" && <RuntimeSection />}
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
// Mirrors the chip style RuntimeSection uses for #545's Lemonade rows.
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
// ONE Storage location field. Hal0 propagates the chosen path to both
// the pull engine and Lemonade's extra_models_dir, with a confirmation
// modal when the prior path has data ("Move N models from A to B?").
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
      const lem = resp.lemonade?.restart;
      const lemMsg = lem === "ok"
        ? "Lemonade restarted"
        : lem === "failed"
          ? "Lemonade restart failed — run `systemctl restart hal0-lemonade.service` manually"
          : lem === "unavailable"
            ? "Lemonade not running here"
            : "Lemonade config unchanged";
      window.__hal0Toast && window.__hal0Toast(
        `Storage set → ${path}${moved ? ` · moved ${moved} model(s)` : ""} · ${lemMsg}`,
        lem === "failed" ? "warn" : "ok",
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
        Where hal0 reads and writes model files. One path drives both <span className="mono" style={{color: "var(--fg)"}}>hal0-api</span> and Lemonade — pick once, applies everywhere.
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
              sub="Absolute directory · pull engine + Lemonade both point here"
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
              Stored at <span style={{color: "var(--fg-3)"}}>/etc/hal0/hal0.toml</span> · propagates to Lemonade <span style={{color: "var(--fg-3)"}}>config.json</span>
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
  // Phase B1: live secrets list + delete mutation. The Add modal still
  // posts via the prototype's local form; useSecretSet wires the real
  // POST when modal upgrades land in B2.
  const secretsQuery = useSecrets();
  const delSecret = useSecretDelete();
  // Fall back to the design's three default rows when backend hasn't
  // shipped the endpoint.
  const fallbackRows = [
    { name: 'HF_TOKEN', set: true, masked: 'hf_•••••••••••••••••••••' },
    { name: 'OPENAI_API_KEY', set: false },
    { name: 'ANTHROPIC_API_KEY', set: false },
  ];
  const rows = (secretsQuery.data && secretsQuery.data.length > 0) ? secretsQuery.data : fallbackRows;
  return (
    <div className="s-section">
      <h2>Secrets</h2>
      <p className="desc">Encrypted at rest, scoped to lemond. Used for gated HF repos and provider auth.</p>
      <div className="s-panel">
        {rows.map(s => (
          <SRow
            key={s.name}
            k={s.name}
            sub={s.name === 'HF_TOKEN' ? 'Hugging Face — used by lemond for gated repos' : 'Optional · fallback provider'}
            mono
            v={s.set
              ? <span style={{color: "var(--ok)"}}>{s.masked || '••• · set'}</span>
              : <span style={{color: "var(--fg-4)"}}>not set</span>}
            actions={s.set
              ? (<>
                  <button className="btn ghost sm" onClick={() => setAddOpen(true)}>Update</button>
                  <button className="btn danger sm" onClick={() => {
                    delSecret.mutate(s.name, {
                      onSuccess: () => window.__hal0Toast && window.__hal0Toast(`${s.name} removed`, "warn"),
                    });
                  }}>Remove</button>
                </>)
              : <button className="btn ghost sm" onClick={() => setAddOpen(true)}>Add</button>}
          />
        ))}
      </div>
      <div style={{marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
        <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>{rows.length} known keys · add a custom key for any provider</span>
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
  const u = stateQuery.data || { hal0: {}, lemonade: {}, flm: {} };

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
            <a className="btn ghost sm" href="https://hal0.dev/changelog" target="_blank" rel="noreferrer">Changelog →</a>
          </>}
        />
        <SRow
          k="lemonade"
          sub="Pinned. SHA-256 verified."
          mono
          v={u.lemonade?.current ? `${u.lemonade.current} · channel: ${u.lemonade.channel || 'stable'}` : '—'}
          actions={<button
            className="btn ghost sm"
            disabled={checkM.isPending}
            onClick={() => checkM.mutate(undefined, {
              onError: (err) => {
                const msg = (err && err.message) || "check failed";
                window.__hal0Toast && window.__hal0Toast(`Check failed: ${msg}`, "err");
              },
            })}
          >{checkM.isPending ? "Checking…" : "Check"}</button>}
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

// ─── Lemonade config helpers ────────────────────────────────────────
//
// `--threads` is a typed number input in the UI but the wire-level key
// is `llamacpp_args` (a free-text flag string, DEFERRED). The regex
// matches the backend's `_THREADS_RE` in lemonade_admin.py so what the
// operator types here produces the same parsed value the server sees.
const THREADS_RE = /--threads\s+(\d+)/;

function extractThreads(llamacppArgs) {
  if (typeof llamacppArgs !== "string") return null;
  const m = llamacppArgs.match(THREADS_RE);
  return m ? m[1] : null;
}

function substituteThreads(llamacppArgs, next) {
  const base = typeof llamacppArgs === "string" ? llamacppArgs.trim() : "";
  if (THREADS_RE.test(base)) return base.replace(THREADS_RE, `--threads ${next}`).trim();
  return base ? `${base} --threads ${next}` : `--threads ${next}`;
}

// One-flag extractor (mirrors lemonade_admin._validate_flm_args shape).
// Used for the effective readouts chip strip.
function extractFlagValue(s, name) {
  if (typeof s !== "string") return null;
  const m = s.match(new RegExp(`--${name}\\s+(\\S+)`));
  return m ? m[1] : null;
}

// Runtime keys this form edits, in render order. Each row binds
// to one key in the live /internal/config snapshot; the effect badge
// (live / restart on next load) is derived from the backend's
// `_hal0.effects` partition rather than hard-coded here, so the two
// never drift. The `threads` row is a typed UI over `llamacpp_args` —
// the underlying key it writes on save is still llamacpp_args (deferred).
//
// `host` / `port` are read-only (systemd-gated / advanced).
// `extra_models_dir` + `kokoro.cpu_bin` are locked.
//
// `group` controls which disclosure tier a field appears in:
//   "common"   — always visible
//   "advanced" — collapsed behind a toggle (default closed)
//   (no group) — iterated but not rendered in these panels
const LEMONADE_FIELDS = [
  // ── Common: the knobs most operators need ──
  { key: "max_loaded_models", group: "common", sub: "Per-type LRU budget",                            kind: "number", width: 100, min: 1 },
  { key: "ctx_size",          group: "common", sub: "Default per /v1/load — overridable per slot",    kind: "number", width: 100, min: 256 },
  { key: "global_timeout",    group: "common", sub: "Default per-request timeout (sec)",              kind: "number", width: 100, min: 1 },
  { key: "log_level",         group: "common", sub: "Lemonade log verbosity",                         kind: "select", options: ["critical","error","warn","info","debug","trace"], width: 140 },
  { key: "threads",           group: "common", sub: "llama.cpp thread count (≥2 — typed; writes llamacpp_args)", kind: "threads", min: 2 },
  // ── Advanced: host/port, backend selects, per-backend args, sd.cpp ──
  { key: "llamacpp_backend",    group: "advanced", sub: "llama.cpp compute backend",     kind: "select", options: ["rocm","vulkan","cpu"], width: 140 },
  { key: "sdcpp_backend",       group: "advanced", sub: "sd.cpp compute backend",        kind: "select", options: ["rocm","vulkan","cpu"], width: 140 },
  { key: "whispercpp_backend",  group: "advanced", sub: "whisper.cpp compute backend",   kind: "select", options: ["vulkan","cpu","cublas"], width: 140 },
  { key: "steps",       group: "advanced", sub: "sd.cpp sampling steps",               kind: "number", width: 100, min: 1 },
  { key: "cfg_scale",   group: "advanced", sub: "sd.cpp classifier-free guidance",     kind: "number", width: 100, min: 0, step: 0.5 },
  { key: "width",       group: "advanced", sub: "sd.cpp output width (px)",            kind: "number", width: 100, min: 64 },
  { key: "height",      group: "advanced", sub: "sd.cpp output height (px)",           kind: "number", width: 100, min: 64 },
  {
    key: "flm_args", group: "advanced",
    sub: "FLM trio config — drives NPU coresident packing",
    kind: "text",
    warn: "--asr/--embed take 0 or 1; setting a modality to 0 requires disabling the corresponding NPU slot in dispatch.",
  },
  { key: "no_broadcast", group: "advanced", sub: "Skip UDP backend discovery", kind: "toggle" },
  { key: "host", group: "advanced", sub: "Listen host — change requires systemd unit edit", kind: "readonly" },
  { key: "port", group: "advanced", sub: "Listen port — change requires systemd unit edit", kind: "readonly" },
];

// ── shared field renderer (used by both Common + Advanced panels) ──
function LemonadeFieldRow({ f, edits, setField, fieldErrors, effects }) {
  const isDeferred = f.key === "threads" || effects.deferred.includes(f.key);
  const isImmediate = !isDeferred && effects.immediate.includes(f.key);
  const err = fieldErrors[f.key];

  const getOriginalStr = (key, cfg) => {
    if (!cfg) return "";
    if (key === "threads") {
      const t = extractThreads(cfg.llamacpp_args);
      return t == null ? "" : t;
    }
    const v = cfg[key];
    return v == null ? "" : String(v);
  };

  return (
    <SRow
      k={f.key}
      sub={f.sub}
      mono
      v={
        <div style={{display: "flex", flexDirection: "column", gap: 4}}>
          {f.kind === "select" ? (
            <select
              className="input mono"
              value={edits[f.key] ?? ""}
              onChange={(e) => setField(f.key, e.target.value)}
              style={{maxWidth: f.width}}
            >
              {(f.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : f.kind === "toggle" ? (
            <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
              <input
                type="checkbox"
                checked={edits[f.key] === "true"}
                onChange={(e) => setField(f.key, e.target.checked ? "true" : "false")}
                style={{accentColor: "var(--accent)"}}
              />
              <span>{edits[f.key] === "true" ? "enabled" : "disabled"}</span>
            </label>
          ) : f.kind === "readonly" ? (
            <span className="mono" style={{color: "var(--fg-3)"}}>
              {edits[f.key] || "—"}
            </span>
          ) : (
            <input
              className="input mono"
              type={f.kind === "threads" || f.kind === "number" ? "number" : "text"}
              min={f.min}
              step={f.step}
              value={edits[f.key] ?? ""}
              onChange={(e) => setField(f.key, e.target.value)}
              style={f.width ? {maxWidth: f.width} : {minWidth: 320, width: "100%"}}
            />
          )}
          {f.warn && (
            <span style={{color: "var(--err)", fontFamily: "var(--jbm)", fontSize: 10, lineHeight: 1.4}}>{f.warn}</span>
          )}
          {err && (
            <span className="err" style={{fontSize: 10, padding: 0, background: "none", border: "none"}}>{err}</span>
          )}
        </div>
      }
      actions={
        f.kind === "readonly" ? null : (
          <span
            className="chip"
            style={{
              fontFamily: "var(--jbm)",
              fontSize: 10,
              padding: "2px 8px",
              color: isDeferred ? "var(--warn)" : isImmediate ? "var(--ok)" : "var(--fg-4)",
              borderColor: isDeferred ? "var(--warn)" : isImmediate ? "var(--ok)" : "var(--line)",
              background: isDeferred ? "rgba(255,176,0,0.08)" : isImmediate ? "rgba(46,204,113,0.08)" : "transparent",
              whiteSpace: "nowrap",
            }}
            title={isDeferred ? "Persists to config now; takes effect on next /v1/load" : isImmediate ? "lemond applies this value immediately on POST" : ""}
          >
            {isDeferred ? "⟳ restart on next load" : isImmediate ? "live" : ""}
          </span>
        )
      }
    />
  );
}

// ── Idle-eviction sub-section ─────────────────────────────────────────
//
// Global idle_timeout_s → [slots].idle_timeout_s in hal0.toml, read/written
// via useSettings / useSettingsUpdate (same path as StorageSection's models.*).
// Per-slot idle_timeout_s → PUT /api/slots/{name}/config { idle_timeout_s: N }
// via useSlotEdit. A null per-slot value means "inherit global".
function IdleEvictionSection() {
  const settingsQuery = useSettings();
  const settingsUpdate = useSettingsUpdate();
  const slotsQuery = useSlots();
  const slotEdit = useSlotEdit();

  const globalVal = settingsQuery.data?.slots?.idle_timeout_s ?? 300;
  const [globalEdit, setGlobalEdit] = useStateSet("");
  const [slotEdits, setSlotEdits] = useStateSet({});

  // Populate global edit buffer when settings load or change.
  useEffectSet(() => {
    const v = settingsQuery.data?.slots?.idle_timeout_s;
    setGlobalEdit(v != null ? String(v) : "300");
  }, [settingsQuery.data]);

  // Populate per-slot edit buffers when slot list loads.
  useEffectSet(() => {
    if (!slotsQuery.data) return;
    const next = {};
    for (const s of slotsQuery.data) {
      next[s.name] = s.idle_timeout_s != null ? String(s.idle_timeout_s) : "";
    }
    setSlotEdits((prev) => {
      // Only reset keys that haven't been touched (avoid clobbering in-flight edits).
      const merged = { ...prev };
      for (const [k, v] of Object.entries(next)) {
        if (!(k in prev)) merged[k] = v;
      }
      return merged;
    });
  }, [slotsQuery.data]);

  const globalDirty = globalEdit.trim() !== String(globalVal);

  const onGlobalSave = async () => {
    const n = Number(globalEdit.trim());
    if (!Number.isFinite(n) || n < 0) {
      window.__hal0Toast && window.__hal0Toast("idle_timeout_s must be a non-negative integer", "err");
      return;
    }
    try {
      await settingsUpdate.mutateAsync({ slots: { idle_timeout_s: n } });
      window.__hal0Toast && window.__hal0Toast(
        `Global idle timeout set to ${n}s — restart hal0-api to apply`,
        "warn",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const onSlotSave = async (slotName) => {
    const raw = (slotEdits[slotName] ?? "").trim();
    const n = raw === "" ? null : Number(raw);
    if (raw !== "" && (!Number.isFinite(n) || n < 0)) {
      window.__hal0Toast && window.__hal0Toast("idle_timeout_s must be a non-negative integer or empty (inherit global)", "err");
      return;
    }
    try {
      await slotEdit.mutateAsync({ name: slotName, body: { idle_timeout_s: n } });
      window.__hal0Toast && window.__hal0Toast(
        `${slotName} idle timeout ${n == null ? "cleared (inherits global)" : `set to ${n}s`}`,
        "ok",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const slots = (slotsQuery.data || []).filter((s) => !s._synthetic);

  return (
    <div style={{marginTop: 20}}>
      <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", marginBottom: 8, textTransform: "uppercase", letterSpacing: 0.6, borderBottom: "1px solid var(--line)", paddingBottom: 6}}>
        Idle eviction
      </div>
      <p style={{fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-3)", marginBottom: 10}}>
        Models idle for longer than their TTL are unloaded by the Lemonade idle driver.
        Per-slot values override the global fallback; empty = inherit global.
      </p>
      <div className="s-panel">
        <SRow
          k="global idle_timeout_s"
          sub="Fleet default — [slots].idle_timeout_s in hal0.toml · applies on hal0-api restart"
          mono
          v={
            <input
              className="input mono"
              type="number"
              min={0}
              value={globalEdit}
              onChange={(e) => setGlobalEdit(e.target.value)}
              style={{maxWidth: 100}}
            />
          }
          actions={
            <div style={{display: "inline-flex", alignItems: "center", gap: 6}}>
              <span
                className="chip"
                style={{fontFamily: "var(--jbm)", fontSize: 10, padding: "2px 8px", color: "var(--warn)", borderColor: "var(--warn)", background: "rgba(255,176,0,0.08)", whiteSpace: "nowrap"}}
                title="Requires restarting hal0-api to take effect"
              >
                ⟳ restart hal0-api
              </span>
              {globalDirty && (
                <button
                  className="btn ghost sm"
                  disabled={settingsUpdate.isPending}
                  onClick={onGlobalSave}
                >
                  {settingsUpdate.isPending ? "Saving…" : "Save"}
                </button>
              )}
            </div>
          }
        />
        {slots.length === 0 && slotsQuery.isPending && (
          <div style={{padding: "8px 0", color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 11}}>Loading slots…</div>
        )}
        {slots.map((s) => {
          const original = s.idle_timeout_s != null ? String(s.idle_timeout_s) : "";
          const current = slotEdits[s.name] ?? original;
          const dirty = current !== original;
          return (
            <SRow
              key={s.name}
              k={s.name}
              sub={`per-slot idle_timeout_s · empty = inherit global (${globalVal}s)`}
              mono
              v={
                <input
                  className="input mono"
                  type="number"
                  min={0}
                  placeholder={`${globalVal} (global)`}
                  value={slotEdits[s.name] ?? original}
                  onChange={(e) => setSlotEdits((prev) => ({ ...prev, [s.name]: e.target.value }))}
                  style={{maxWidth: 100}}
                />
              }
              actions={
                dirty && (
                  <button
                    className="btn ghost sm"
                    disabled={slotEdit.isPending}
                    onClick={() => onSlotSave(s.name)}
                  >
                    {slotEdit.isPending ? "Saving…" : "Save"}
                  </button>
                )
              }
            />
          );
        })}
      </div>
    </div>
  );
}

function RuntimeSection() {
  // Issue #545 — typed Lemonade runtime knobs, restructured into tiers (#550).
  // Groups: Live read-outs (top) / Common (always visible) / Advanced (collapsed) /
  // Locked (muted read-only) / Idle eviction (sub-section at bottom).
  // Save/reset/validation logic is unchanged from #545 — all keys remain in
  // LEMONADE_FIELDS; the group tag only controls rendering, not the patch path.
  const lemond = useLemondRollup();
  const stats = useLemonadeStats();
  const caps = useCapabilities();
  const cfgQuery = useLemonadeConfig();
  const cfgSet = useLemonadeConfigSet();
  const cfg = cfgQuery.data;
  const effects = cfg?._hal0?.effects || { immediate: [], deferred: [] };
  const lockedDir = cfg?._hal0?.locked?.extra_models_dir;

  // Advanced disclosure toggle (default closed).
  const [advancedOpen, setAdvancedOpen] = useStateSet(false);

  // Edit buffer holds string values per key; populated from the live
  // snapshot once it loads. We keep everything as strings so the inputs
  // are controlled, and coerce numbers back on submit. The `threads`
  // field is special — its value lives inside `cfg.llamacpp_args`, not
  // at `cfg.threads`; the populate + buildPatch helpers translate.
  const [edits, setEdits] = useStateSet({});
  // Per-key validation errors echoed from the backend's
  // `lemonade.config_invalid` envelope details map; client-side
  // pre-checks (threads < 2) also land here before the round-trip.
  const [fieldErrors, setFieldErrors] = useStateSet({});
  // ConfirmDialog gate — saving a deferred-effect key warns it won't
  // take hold until the next /v1/load.
  const [confirmOpen, setConfirmOpen] = useStateSet(false);

  // Resolve the "original" value a row was loaded from. For `threads`
  // we extract from llamacpp_args; everything else is a verbatim read.
  const original = (key) => {
    if (!cfg) return "";
    if (key === "threads") {
      const t = extractThreads(cfg.llamacpp_args);
      return t == null ? "" : t;
    }
    const v = cfg[key];
    return v == null ? "" : String(v);
  };

  useEffectSet(() => {
    if (!cfg) return;
    const next = {};
    for (const f of LEMONADE_FIELDS) {
      next[f.key] = original(f.key);
    }
    setEdits(next);
  }, [cfg]);

  // Dirty keys are those whose edit-buffer value diverges from the
  // live snapshot. `threads` is a derived field, so its "original" is
  // the value parsed out of `llamacpp_args` (not a top-level key).
  // The `k in edits` gate prevents a one-frame "all dirty" flash on
  // first cfg load (edits starts as {} before the populate effect runs).
  const dirtyKeys = LEMONADE_FIELDS
    .map((f) => f.key)
    .filter((k) => cfg && k in edits && (edits[k] ?? "") !== original(k));
  const touchesDeferred = dirtyKeys.some((k) =>
    k === "threads" || effects.deferred.includes(k),
  );

  // If a dirty + errored key lives in the Advanced group, auto-expand it
  // so Save doesn't silently block with no visible cause.
  const hasAdvancedError = LEMONADE_FIELDS
    .filter((f) => f.group === "advanced")
    .some((f) => fieldErrors[f.key] && dirtyKeys.includes(f.key));
  useEffectSet(() => {
    if (hasAdvancedError) setAdvancedOpen(true);
  }, [hasAdvancedError]);

  // Client-side pre-validation. The backend re-checks these — but
  // surfacing the error inline before the round-trip keeps the form
  // feeling responsive and the typing flow tight. We do NOT block the
  // save; the backend may know better (e.g. it has the FLM trio
  // invariant); the `details` map from `lemonade.config_invalid`
  // overwrites this on response.
  useEffectSet(() => {
    const next = {};
    for (const f of LEMONADE_FIELDS) {
      if (f.kind === "threads") {
        const raw = (edits[f.key] ?? "").trim();
        if (raw !== "" && Number(raw) < 2) {
          next[f.key] = "must be ≥ 2 — below 2 trips the Vulkan dispatch deadlock";
        }
      } else if (typeof f.min === "number" && f.kind === "number") {
        const raw = (edits[f.key] ?? "").trim();
        if (raw !== "" && Number.isFinite(Number(raw)) && Number(raw) < f.min) {
          next[f.key] = `must be ≥ ${f.min}`;
        }
      }
    }
    setFieldErrors((prev) => ({ ...prev, ...next }));
  }, [edits]);

  const setField = (key, value) => {
    setEdits((e) => ({ ...e, [key]: value }));
    // Clear server-side errors on edit; client errors re-derive in the
    // useEffect above on the next render.
    if (fieldErrors[key]) setFieldErrors((fe) => ({ ...fe, [key]: undefined }));
  };

  // Coerce the dirty edits back to typed values for the POST body. We
  // only send keys the operator actually changed. The typed `threads`
  // field rewrites the underlying `llamacpp_args` string so the
  // backend validator (which inspects the raw args) still trips.
  const buildPatch = () => {
    const patch = {};
    for (const f of LEMONADE_FIELDS) {
      if (!dirtyKeys.includes(f.key)) continue;
      if (f.kind === "readonly") continue;
      const raw = (edits[f.key] ?? "").trim();
      if (f.kind === "threads") {
        const n = Number(raw);
        if (!Number.isFinite(n) || n < (f.min ?? 2)) continue;
        patch.llamacpp_args = substituteThreads(cfg?.llamacpp_args, n);
      } else if (f.kind === "toggle") {
        patch[f.key] = raw === "true";
      } else if (f.kind === "number") {
        const n = Number(raw);
        patch[f.key] = Number.isFinite(n) ? n : raw;
      } else {
        patch[f.key] = raw;
      }
    }
    return patch;
  };

  // A dirty key with an active error blocks the save (client validation
  // OR a server error echoed back on a prior attempt for that exact
  // key). Stale server errors on UNCHANGED fields don't block — the
  // user is intentionally re-saving a different key.
  const hasBlockingError = dirtyKeys.some((k) => fieldErrors[k]);

  const doSave = async () => {
    const patch = buildPatch();
    if (Object.keys(patch).length === 0) return;
    try {
      const resp = await cfgSet.mutateAsync(patch);
      const immediate = resp.effects?.immediate || [];
      const deferred = resp.effects?.deferred || [];
      const parts = [];
      if (immediate.length) parts.push(`${immediate.length} live: ${immediate.join(", ")}`);
      if (deferred.length) parts.push(`${deferred.length} restart on next load: ${deferred.join(", ")}`);
      // Drop any stale server errors from a prior attempt — the form
      // just refilled from the authoritative snapshot.
      setFieldErrors({});
      window.__hal0Toast && window.__hal0Toast(
        `Lemonade config saved${parts.length ? ` — ${parts.join(" · ")}` : ""}`,
        deferred.length ? "warn" : "ok",
      );
    } catch (e) {
      // `lemonade.config_invalid` carries a {key: reason} details map —
      // surface each reason inline beside its field.
      const details = e?.details;
      if (details && typeof details === "object") {
        setFieldErrors((prev) => ({ ...prev, ...details }));
      }
      window.__hal0Toast && window.__hal0Toast(
        `Save failed — ${e?.message || "see logs"}`,
        "err",
      );
    }
  };

  const onSaveClick = () => {
    if (touchesDeferred) setConfirmOpen(true);
    else doSave();
  };

  // ── effective readouts (rendered from cfg + useLemonadeStats) ──
  // The chips show the running value, not the form default. This is
  // the "do I have a stale page" sanity check the operator wants.
  const effectiveThreads = extractThreads(cfg?.llamacpp_args);
  const effectiveAsr = extractFlagValue(cfg?.flm_args, "asr");
  const effectiveEmbed = extractFlagValue(cfg?.flm_args, "embed");
  const liveStats = stats.data || {};
  const readouts = [
    { k: "threads",        v: effectiveThreads != null ? `–threads ${effectiveThreads}` : "—", emphasis: !!effectiveThreads },
    { k: "global_timeout", v: cfg?.global_timeout != null ? `${cfg.global_timeout}s` : "—", emphasis: cfg?.global_timeout != null },
    { k: "log_level",      v: cfg?.log_level || "—", emphasis: !!cfg?.log_level },
    { k: "–asr",           v: effectiveAsr != null ? String(effectiveAsr) : "—", emphasis: effectiveAsr != null },
    { k: "–embed",         v: effectiveEmbed != null ? String(effectiveEmbed) : "—", emphasis: effectiveEmbed != null },
  ];
  const lastStats = [
    { k: "TTFT",         v: liveStats.time_to_first_token != null ? `${(liveStats.time_to_first_token * 1000).toFixed(0)} ms` : "—" },
    { k: "decode",       v: liveStats.tokens_per_second != null ? `${liveStats.tokens_per_second.toFixed(1)} tok/s` : "—" },
    { k: "prompt tok",   v: liveStats.prompt_tokens != null ? String(liveStats.prompt_tokens) : "—" },
    { k: "output tok",   v: liveStats.output_tokens != null ? String(liveStats.output_tokens) : "—" },
  ];

  const commonFields = LEMONADE_FIELDS.filter((f) => f.group === "common");
  const advancedFields = LEMONADE_FIELDS.filter((f) => f.group === "advanced");

  return (
    <div className="s-section">
      <h2>Runtime</h2>
      <p className="desc">
        Direct edit of <span className="mono" style={{color: "var(--fg)"}}>/internal/config</span>.{" "}
        <span className="chip" style={{color: "var(--ok)", borderColor: "var(--ok)"}}>live</span>{" "}
        keys apply on save;{" "}
        <span className="chip" style={{color: "var(--warn)", borderColor: "var(--warn)"}}>⟳ restart on next load</span>{" "}
        keys take hold on the next <span className="mono">/v1/load</span>.
      </p>

      {/* ── Live read-outs (top, read-only) — version/status/budget/TTFT/tok·s ── */}
      <div className="s-panel" style={{marginBottom: 12}}>
        <SRow k="runtime" mono v={<>{lemond.version} · {lemond.status} · <b>{lemond.loaded}</b>/{lemond.budget} loaded</>} />
        <SRow k="throughput" mono v={lemond.throughput != null ? `${lemond.throughput} MB/s` : '—'} />
        {caps.data?.capabilities && Object.entries(caps.data.capabilities).map(([k, v]) => (
          <SRow key={k} k={`capability · ${k}`} mono v={<><b>{v.provider}</b>{v.model ? <> · {v.model}</> : null}</>} />
        ))}
      </div>

      {/* Effective readouts — what's actually running right now */}
      <div className="s-panel" style={{marginBottom: 12, padding: "10px 12px"}}>
        <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.6}}>effective</div>
        <div style={{display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10}}>
          {readouts.map((r) => (
            <span
              key={r.k}
              className="chip"
              style={{
                fontFamily: "var(--jbm)",
                color: r.emphasis ? "var(--fg)" : "var(--fg-4)",
                borderColor: r.emphasis ? "var(--accent)" : "var(--line)",
                background: r.emphasis ? "var(--accent-soft)" : "transparent",
              }}
              title={`effective ${r.k}`}
            >
              <span style={{color: "var(--fg-4)"}}>{r.k}</span>
              <span style={{marginLeft: 6, color: r.emphasis ? "var(--fg)" : "var(--fg-4)"}}>{r.v}</span>
            </span>
          ))}
        </div>
        <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.6}}>last request</div>
        <div style={{display: "flex", flexWrap: "wrap", gap: 6}}>
          {lastStats.map((r) => (
            <span
              key={r.k}
              className="chip"
              style={{fontFamily: "var(--jbm)", color: "var(--fg-3)", borderColor: "var(--line)"}}
            >
              <span style={{color: "var(--fg-4)"}}>{r.k}</span>
              <span style={{marginLeft: 6}}>{r.v}</span>
            </span>
          ))}
        </div>
      </div>

      {cfgQuery.isPending && (
        <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading Lemonade config…</div>
      )}
      {cfgQuery.isError && (
        <div className="err">{cfgQuery.error?.message || "Failed to load Lemonade config — is lemond running?"}</div>
      )}

      {cfg && (
        <>
          {/* ── Common: always-visible knobs ── */}
          <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.6}}>Common</div>
          <div className="s-panel" style={{marginBottom: 12}}>
            {commonFields.map((f) => (
              <LemonadeFieldRow key={f.key} f={f} edits={edits} setField={setField} fieldErrors={fieldErrors} effects={effects} />
            ))}
          </div>

          {/* ── Advanced: collapsed behind a toggle ── */}
          <div
            style={{display: "flex", alignItems: "center", gap: 8, marginBottom: 6, cursor: "pointer", userSelect: "none"}}
            onClick={() => setAdvancedOpen((o) => !o)}
          >
            <span className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: 0.6}}>Advanced</span>
            <span className="mono" style={{fontSize: 10, color: "var(--fg-4)"}}>
              {advancedOpen ? "▲" : "▼"}
            </span>
            <span style={{fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)"}}>
              {advancedOpen ? "hide" : `show — host/port, backends, sd.cpp${advancedFields.some((f) => dirtyKeys.includes(f.key)) ? " · unsaved" : ""}`}
            </span>
          </div>
          {advancedOpen && (
            <div className="s-panel" style={{marginBottom: 12}}>
              {advancedFields.map((f) => (
                <LemonadeFieldRow key={f.key} f={f} edits={edits} setField={setField} fieldErrors={fieldErrors} effects={effects} />
              ))}
            </div>
          )}

          {/* ── Locked: muted read-only ── */}
          <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.6}}>Locked</div>
          <div className="s-panel" style={{marginBottom: 12, opacity: 0.6}}>
            <SRow
              k="extra_models_dir"
              sub="Locked to the model store — change via Settings → Storage"
              mono
              v={<span style={{color: "var(--fg-3)"}}>{lockedDir || "—"}</span>}
            />
            <SRow
              k="kokoro.cpu_bin"
              sub="Linux-only · GPU support is upstream-pending"
              mono
              v="builtin"
            />
          </div>

          <div style={{marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
            <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
              {dirtyKeys.length === 0
                ? "No unsaved changes"
                : <>{dirtyKeys.length} unsaved {dirtyKeys.length === 1 ? "change" : "changes"}{touchesDeferred && <span style={{color: "var(--warn)"}}> · some restart on next load</span>}</>}
            </span>
            <div style={{display: "flex", gap: 8}}>
              <button
                className="btn ghost"
                disabled={dirtyKeys.length === 0 || cfgSet.isPending}
                onClick={() => {
                  const next = {};
                  for (const f of LEMONADE_FIELDS) next[f.key] = original(f.key);
                  setEdits(next);
                  setFieldErrors({});
                }}
              >Reset</button>
              <button
                className="btn"
                disabled={dirtyKeys.length === 0 || cfgSet.isPending || hasBlockingError}
                onClick={onSaveClick}
              >{cfgSet.isPending ? "Saving…" : "Save config"}</button>
            </div>
          </div>
        </>
      )}

      {/* ── Idle eviction sub-section ── */}
      <IdleEvictionSection />

      <ConfirmDialog
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => { setConfirmOpen(false); doSave(); }}
        title="Save runtime config changes?"
        message={<span>Some changed keys are <span className="chip" style={{color: "var(--warn)", borderColor: "var(--warn)", fontSize: 10, padding: "1px 6px"}}>⟳ restart on next load</span> — lemond persists them now but applies them only on the next <span className="mono">/v1/load</span>. Restart a slot to apply immediately. <span className="chip" style={{color: "var(--ok)", borderColor: "var(--ok)", fontSize: 10, padding: "1px 6px"}}>live</span> keys take effect right away.</span>}
        confirmLabel="Save"
      />
    </div>
  );
}

function GeneralSection() {
  return (
    <div className="s-section">
      <h2>General</h2>
      <p className="desc">Dark only for v0.2.1. Light mode lands when the website adds one.</p>
      <div className="s-panel">
        <SRow k="Theme" v={<span className="chip amber">dark</span>} />
        <SRow k="Density" sub="affects card padding + row heights" v={
          <div className="mono" style={{display: "inline-flex", border: "1px solid var(--line)", borderRadius: 4, overflow: "hidden"}}>
            {["compact", "comfortable", "spacious"].map(d => (
              <span key={d} style={{padding: "4px 10px", fontSize: 11, cursor: "pointer", background: d === "comfortable" ? "var(--accent-soft)" : "transparent", color: d === "comfortable" ? "var(--accent)" : "var(--fg-3)", borderRight: d !== "spacious" ? "1px solid var(--line)" : "none"}}>{d}</span>
            ))}
          </div>
        } />
        <SRow k="Accent" v={<span className="chip amber">sodium amber #FFB000</span>} sub="Brand-locked. Status colors are distinct." />
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
        <SRow k="hal0" mono v={liveVersion ? `${liveVersion} — Lemonade-embedded slots` : "—"} />
        <SRow k="License" v="Apache-2.0" />
        <SRow k="Repository" mono v="github.com/Hal0ai/hal0" actions={<a className="btn ghost sm" href="https://github.com/Hal0ai/hal0" target="_blank" rel="noreferrer">{Icons.ext} Open</a>} />
        <SRow k="Docs" v="hal0.dev/docs/v0.2-upgrade" actions={<a className="btn ghost sm" href="https://hal0.dev/docs/v0.2-upgrade" target="_blank" rel="noreferrer">{Icons.ext} Open</a>} />
        <SRow k="Discord" v="discord.gg/hal0" actions={<a className="btn ghost sm" href="https://discord.gg/hal0" target="_blank" rel="noreferrer">{Icons.ext} Join</a>} />
      </div>
      <div style={{marginTop: 14, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>
        Built on AMD Lemonade, FLM (XDNA2), llama.cpp, whisper.cpp, sd.cpp, Kokoro, Cognee.
      </div>
    </div>
  );
}

Object.assign(window, { SettingsView });
