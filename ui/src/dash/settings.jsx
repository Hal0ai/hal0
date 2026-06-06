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
import { useUpdateState, useUpdateCheck, useUpdateApply, useUpdateJob } from '@/api/hooks/useUpdates'
import { useCapabilities, useCapabilityPatch } from '@/api/hooks/useCapabilities'
import { useLemondRollup, useLemonadeStats } from '@/api/hooks/useLemonade'
import { useLemonadeConfig, useLemonadeConfigSet } from '@/api/hooks/useLemonadeConfig'
import {
  useSettings,
  useSettingsUpdate,
  useModelStore,
  useModelStoreSet,
  useModelStoreMigrate,
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

  const onAutoScanSave = async () => {
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
              actions={autoScanDirty ? <button className="btn ghost sm" disabled={update.isPending} onClick={onAutoScanSave}>{update.isPending ? "Saving…" : "Save"}</button> : null}
            />
            <SRow
              k="File extensions"
              sub="Read-only · edit via hal0 config edit"
              mono
              v={(liveModels?.file_extensions || []).join(" · ") || "—"}
            />
          </div>

          <div style={{marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
            <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
              Stored at <span style={{color: "var(--fg-3)"}}>/etc/hal0/hal0.toml</span> · propagates to Lemonade <span style={{color: "var(--fg-3)"}}>config.json</span>
              {storeDirty && <span style={{marginLeft: 8, color: "var(--warn)"}}>· unsaved changes</span>}
            </span>
            <div style={{display: "inline-flex", gap: 8}}>
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
  const stateQuery = useUpdateState();
  const checkM = useUpdateCheck();
  const applyM = useUpdateApply();
  const u = stateQuery.data || { hal0: {}, lemonade: {}, flm: {}, autoCheck: true };

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
          k="Auto-check"
          sub="Once per day · 09:00 local"
          v={<label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}><input type="checkbox" defaultChecked={!!u.autoCheck} style={{accentColor: "var(--accent)"}} /><span>enabled</span></label>}
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

// Runtime keys this form edits, in render order. Each row binds
// to one key in the live /internal/config snapshot; the effect label
// (Immediate / Deferred) is derived from the backend's `_hal0.effects`
// partition rather than hard-coded here, so the two never drift.
//
// `extra_models_dir` is intentionally NOT editable from this panel —
// the backend locks it to the [models].store single source of truth
// (see StorageSection + lemonade_admin._validate_extra_models_dir). We
// surface it read-only so the operator can see the locked value.
const LEMONADE_FIELDS = [
  { key: "max_loaded_models", sub: "Per-type LRU budget", kind: "number", width: 80 },
  { key: "ctx_size", sub: "Default per /v1/load — overridable per slot", kind: "number", width: 100 },
  {
    key: "llamacpp_args",
    sub: "Mandatory baseline · ADR-0008",
    kind: "text",
    warn: "⚠ Must keep --threads N (N ≥ 2) or lemond deadlocks under concurrent load",
  },
  {
    key: "flm_args",
    sub: "FLM trio config — drives the NPU coresident packing",
    kind: "text",
    warn: "⚠ Must keep --asr 1 --embed 1 or the NPU stt/embed slots lose their backend",
  },
  {
    key: "whispercpp_backend",
    sub: "whisper.cpp compute backend",
    kind: "select",
    options: ["vulkan", "cpu", "cublas"],
    width: 160,
  },
  {
    key: "sdcpp_backend",
    sub: "sd.cpp compute backend",
    kind: "select",
    options: ["rocm", "vulkan", "cpu"],
    width: 160,
  },
  { key: "steps", sub: "sd.cpp sampling steps", kind: "number", width: 80 },
  { key: "cfg_scale", sub: "sd.cpp classifier-free guidance", kind: "number", width: 80 },
  { key: "width", sub: "sd.cpp output width (px)", kind: "number", width: 80 },
  { key: "height", sub: "sd.cpp output height (px)", kind: "number", width: 80 },
];

function RuntimeSection() {
  // Phase B2 (issue #461): the admin config form now reads + writes the
  // live /api/lemonade/config surface. The runtime readouts at the top
  // stay on the polling rollup; capabilities preview stays as-is.
  const lemond = useLemondRollup();
  const stats = useLemonadeStats();
  const caps = useCapabilities();
  const cfgQuery = useLemonadeConfig();
  const cfgSet = useLemonadeConfigSet();
  const cfg = cfgQuery.data;
  const effects = cfg?._hal0?.effects || { immediate: [], deferred: [] };
  const lockedDir = cfg?._hal0?.locked?.extra_models_dir;

  // Edit buffer holds string values per key; populated from the live
  // snapshot once it loads. We keep everything as strings so the inputs
  // are controlled, and coerce numbers back on submit.
  const [edits, setEdits] = useStateSet({});
  // Per-key validation errors echoed from the backend's
  // `lemonade.config_invalid` envelope details map.
  const [fieldErrors, setFieldErrors] = useStateSet({});
  // ConfirmDialog gate — saving a deferred-effect key warns it won't
  // take hold until the next /v1/load.
  const [confirmOpen, setConfirmOpen] = useStateSet(false);

  useEffectSet(() => {
    if (!cfg) return;
    const next = {};
    for (const f of LEMONADE_FIELDS) {
      const v = cfg[f.key];
      next[f.key] = v == null ? "" : String(v);
    }
    setEdits(next);
  }, [cfg]);

  const original = (key) => {
    const v = cfg?.[key];
    return v == null ? "" : String(v);
  };
  const dirtyKeys = LEMONADE_FIELDS
    .map((f) => f.key)
    .filter((k) => cfg && (edits[k] ?? "") !== original(k));
  const touchesDeferred = dirtyKeys.some((k) => effects.deferred.includes(k));

  const setField = (key, value) => {
    setEdits((e) => ({ ...e, [key]: value }));
    if (fieldErrors[key]) setFieldErrors((fe) => ({ ...fe, [key]: undefined }));
  };

  // Coerce the dirty edits back to typed values for the POST body. We
  // only send keys the operator actually changed.
  const buildPatch = () => {
    const patch = {};
    for (const f of LEMONADE_FIELDS) {
      if (!dirtyKeys.includes(f.key)) continue;
      const raw = (edits[f.key] ?? "").trim();
      if (f.kind === "number") {
        const n = Number(raw);
        patch[f.key] = Number.isFinite(n) ? n : raw;
      } else {
        patch[f.key] = raw;
      }
    }
    return patch;
  };

  const doSave = async () => {
    const patch = buildPatch();
    if (Object.keys(patch).length === 0) return;
    setFieldErrors({});
    try {
      const resp = await cfgSet.mutateAsync(patch);
      const nImm = resp.effects?.immediate?.length || 0;
      const nDef = resp.effects?.deferred?.length || 0;
      const parts = [];
      if (nImm) parts.push(`${nImm} immediate`);
      if (nDef) parts.push(`${nDef} deferred until next load`);
      window.__hal0Toast && window.__hal0Toast(
        `Lemonade config saved${parts.length ? ` — ${parts.join(", ")}` : ""}`,
        nDef ? "warn" : "ok",
      );
    } catch (e) {
      // `lemonade.config_invalid` carries a {key: reason} details map —
      // surface each reason inline beside its field.
      const details = e?.details;
      if (details && typeof details === "object") {
        setFieldErrors(details);
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

  return (
    <div className="s-section">
      <h2>Runtime</h2>
      <p className="desc">Direct edit of <span className="mono" style={{color: "var(--fg)"}}>/internal/config</span>. <span style={{color: "var(--ok)"}}>Immediate</span> keys apply on save; <span style={{color: "var(--warn)"}}>deferred</span> keys take hold on the next <span className="mono">/v1/load</span>.</p>
      <div className="s-panel" style={{marginBottom: 12}}>
        <SRow k="runtime" mono v={<>{lemond.version} · {lemond.status} · <b>{lemond.loaded}</b>/{lemond.budget} loaded</>} />
        <SRow k="throughput" mono v={lemond.throughput != null ? `${lemond.throughput} MB/s` : '—'} />
        <SRow k="last TTFT" mono v={lemond.lastTtft != null ? `${(lemond.lastTtft * 1000).toFixed(0)} ms` : '—'} />
        <SRow k="last decode" mono v={lemond.lastTokPerSec != null ? `${lemond.lastTokPerSec.toFixed(1)} tok/s` : '—'} />
        {caps.data?.capabilities && Object.entries(caps.data.capabilities).map(([k, v]) => (
          <SRow key={k} k={`capability · ${k}`} mono v={<><b>{v.provider}</b>{v.model ? <> · {v.model}</> : null}</>} />
        ))}
      </div>

      {cfgQuery.isPending && (
        <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading Lemonade config…</div>
      )}
      {cfgQuery.isError && (
        <div className="err">{cfgQuery.error?.message || "Failed to load Lemonade config — is lemond running?"}</div>
      )}

      {cfg && (
        <>
          <div className="s-panel">
            {LEMONADE_FIELDS.map((f) => {
              const isDeferred = effects.deferred.includes(f.key);
              const isImmediate = effects.immediate.includes(f.key);
              const err = fieldErrors[f.key];
              return (
                <SRow
                  key={f.key}
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
                          {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      ) : (
                        <input
                          className="input mono"
                          type={f.kind === "number" ? "number" : "text"}
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
                    <span style={{fontFamily: "var(--jbm)", fontSize: 11, color: isDeferred ? "var(--warn)" : isImmediate ? "var(--ok)" : "var(--fg-4)"}}>
                      {isDeferred ? "⟳ deferred" : isImmediate ? "immediate" : ""}
                    </span>
                  }
                />
              );
            })}
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
                : <>{dirtyKeys.length} unsaved {dirtyKeys.length === 1 ? "change" : "changes"}{touchesDeferred && <span style={{color: "var(--warn)"}}> · some deferred until next load</span>}</>}
            </span>
            <div style={{display: "flex", gap: 8}}>
              <button
                className="btn ghost"
                disabled={dirtyKeys.length === 0 || cfgSet.isPending}
                onClick={() => { setEdits(Object.fromEntries(LEMONADE_FIELDS.map((f) => [f.key, original(f.key)]))); setFieldErrors({}); }}
              >Reset</button>
              <button
                className="btn"
                disabled={dirtyKeys.length === 0 || cfgSet.isPending}
                onClick={onSaveClick}
              >{cfgSet.isPending ? "Saving…" : "Save config"}</button>
            </div>
          </div>
        </>
      )}

      <ConfirmDialog
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => { setConfirmOpen(false); doSave(); }}
        title="Save deferred config changes?"
        message={<span>Some changed keys are <span className="mono" style={{color: "var(--warn)"}}>deferred</span> — lemond persists them now but applies them only on the next <span className="mono">/v1/load</span>. Restart a slot to apply immediately. Immediate keys take effect right away.</span>}
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
