// hal0 dashboard — Settings view (secrets, updates, lemond admin, omnirouter, memory, agent policy)
//
// Phase B1: Secrets, Updates, and the Lemonade admin version readouts
// pull from live hooks; AgentPolicy + Memory (Cognee) stay scripted
// (their backends live behind the Agent surface, deferred to B2).
// Capabilities hook feeds the Lemonade admin section's per-cap fields.

import { useSecrets, useSecretSet, useSecretDelete } from '@/api/hooks/useSecrets'
import { useUpdateState, useUpdateCheck, useUpdateApply } from '@/api/hooks/useUpdates'
import { useCapabilities, useCapabilityPatch } from '@/api/hooks/useCapabilities'
import { useLemondRollup, useLemonadeStats } from '@/api/hooks/useLemonade'
import { useSettings, useSettingsUpdate } from '@/api/hooks/useSettings'

const { useState: useStateSet, useEffect: useEffectSet } = React;

function SettingsView() {
  const [section, setSection] = useStateSet("secrets");
  const sections = [
    { id: "secrets",   label: "Secrets" },
    { id: "models",    label: "Models" },
    { id: "updates",   label: "Updates" },
    { id: "lemonade",  label: "Lemonade admin" },
    { id: "omni",      label: "OmniRouter" },
    { id: "agent",     label: "Agent policy" },
    { id: "memory",    label: "Memory (Cognee)" },
    { id: "appearance",label: "Appearance" },
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
          {section === "models" && <ModelsSection />}
          {section === "updates" && <UpdatesSection />}
          {section === "lemonade" && <LemonadeSection />}
          {section === "omni" && <OmniRouterSection />}
          {section === "agent" && <AgentPolicySection />}
          {section === "memory" && <MemorySection />}
          {section === "appearance" && <AppearanceSection />}
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

// ─── Models (PR feat/models-scan-and-add-by-path) ───────────────
//
// Edits [models].roots + [models].pull_root in hal0.toml so the
// Scan-directory + Add-by-path flows in Models view default to the
// operator's preferred location (e.g. /mnt/ai-models). Changes hit
// /api/settings (deep-merge PUT) so the rest of the config rounds
// through untouched.
function ModelsSection() {
  const settings = useSettings();
  const update = useSettingsUpdate();
  const liveModels = settings.data?.models;
  // Local edit buffers — we only PATCH when the operator clicks Save so
  // they can revise multiple roots without each keystroke triggering an
  // atomic write on disk.
  const [rootsText, setRootsText] = useStateSet("");
  const [pullRoot, setPullRoot] = useStateSet("");
  const [autoScan, setAutoScan] = useStateSet(true);
  // Re-seed buffers each time the upstream config changes (after Save
  // the response payload supersedes our buffer).
  useEffectSet(() => {
    if (!liveModels) return;
    setRootsText((liveModels.roots || []).join("\n"));
    setPullRoot(liveModels.pull_root || "");
    setAutoScan(liveModels.auto_scan_on_start !== false);
  }, [liveModels]);

  const dirty = !!liveModels && (
    rootsText !== (liveModels.roots || []).join("\n") ||
    pullRoot !== (liveModels.pull_root || "") ||
    autoScan !== (liveModels.auto_scan_on_start !== false)
  );

  const onSave = async () => {
    const roots = rootsText
      .split("\n")
      .map(s => s.trim())
      .filter(Boolean);
    try {
      await update.mutateAsync({
        models: {
          roots,
          pull_root: pullRoot.trim(),
          auto_scan_on_start: autoScan,
          // Preserve file_extensions verbatim — the Settings UI doesn't
          // surface it yet, but we don't want a Save to drop the field.
          file_extensions: liveModels?.file_extensions || [".gguf", ".safetensors"],
        },
      });
      window.__hal0Toast && window.__hal0Toast("Models settings saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Save failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };

  return (
    <div className="s-section">
      <h2>Models</h2>
      <p className="desc">
        Where hal0 looks for already-downloaded model files (Scan) and where new HuggingFace pulls land. Each entry must be an absolute path readable by <span className="mono" style={{color: "var(--fg)"}}>hal0-api</span>.
      </p>
      {settings.isPending && <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading settings…</div>}
      {settings.isError && (
        <div className="err">{settings.error?.message || "Failed to load settings"}</div>
      )}
      {liveModels && (
        <>
          <div className="s-panel">
            <SRow
              k="Model directories (scan roots)"
              sub="One absolute path per line · used by Scan + auto-scan"
              v={
                <textarea
                  className="input mono"
                  value={rootsText}
                  onChange={e => setRootsText(e.target.value)}
                  rows={Math.max(3, rootsText.split("\n").length)}
                  placeholder={"/mnt/ai-models\n/var/lib/hal0/models"}
                  style={{width: "100%", minWidth: 320, resize: "vertical"}}
                />
              }
            />
            <SRow
              k="Pull root"
              sub="Destination for HuggingFace downloads · finished files land at <pull_root>/<model_id>/"
              mono
              v={
                <input
                  className="input mono"
                  value={pullRoot}
                  onChange={e => setPullRoot(e.target.value)}
                  placeholder="/mnt/ai-models"
                  style={{minWidth: 320, width: "100%"}}
                />
              }
            />
            <SRow
              k="Auto-scan on start"
              sub="Walk the scan roots when hal0-api starts; new files get registered automatically"
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
            />
            <SRow
              k="File extensions"
              sub="Read-only · edit via hal0 config edit"
              mono
              v={(liveModels.file_extensions || []).join(" · ") || "—"}
            />
          </div>
          <div style={{marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
            <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
              Stored at <span style={{color: "var(--fg-3)"}}>/etc/hal0/hal0.toml</span>
              {dirty && <span style={{marginLeft: 8, color: "var(--warn)"}}>· unsaved changes</span>}
            </span>
            <div style={{display: "inline-flex", gap: 8}}>
              <button
                className="btn ghost sm"
                disabled={!dirty || update.isPending}
                onClick={() => {
                  if (!liveModels) return;
                  setRootsText((liveModels.roots || []).join("\n"));
                  setPullRoot(liveModels.pull_root || "");
                  setAutoScan(liveModels.auto_scan_on_start !== false);
                }}
              >Reset</button>
              <button
                className="btn"
                disabled={!dirty || update.isPending}
                onClick={onSave}
              >{update.isPending ? "Saving…" : "Save"}</button>
            </div>
          </div>
          {update.isError && (
            <div className="err" style={{marginTop: 10}}>
              {update.error?.message || "Save failed"}
            </div>
          )}
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
  // Phase B1: live state + check + apply mutations. Fallback shows the
  // design's v0.2.2-available story when no backend.
  const stateQuery = useUpdateState();
  const checkM = useUpdateCheck();
  const applyM = useUpdateApply();
  const u = stateQuery.data || {
    hal0: { current: 'v0.2.1', available: 'v0.2.2', channel: 'stable' },
    lemonade: { current: 'v10.6.0', pinned: true, channel: 'stable' },
    flm: { current: 'v0.9.42', source: 'manual-deb' },
    autoCheck: true,
  };
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
          </>}
          actions={<>
            <button className="btn sm" disabled={!u.hal0?.available} onClick={() => {
              applyM.mutate('hal0', {
                onSuccess: () => window.__hal0Toast && window.__hal0Toast("Update started — brief outage during restart", "warn"),
              });
            }}>Install update</button>
            <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast("Opening hal0.dev/changelog", "info")}>Changelog →</button>
          </>}
        />
        <SRow
          k="lemonade"
          sub="Pinned. SHA-256 verified."
          mono
          v={`${u.lemonade?.current} · channel: ${u.lemonade?.channel || 'stable'}`}
          actions={<button className="btn ghost sm" onClick={() => checkM.mutate('lemonade')}>Check</button>}
        />
        <SRow
          k="flm"
          sub="Manual deb · vendor-supplied"
          mono
          v={u.flm?.current || '—'}
          actions={<button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast("Opening FLM install guide", "info")}>Re-install</button>}
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

function LemonadeSection() {
  const [argsEdit, setArgsEdit] = useStateSet(false);
  const [restartOpen, setRestartOpen] = useStateSet(false);
  // Phase B1: live Lemonade readouts + capabilities preview at the top
  // of the admin panel so operators see version + loaded budget
  // alongside the static config form (which keeps local edits until
  // PATCH wiring in B2).
  const lemond = useLemondRollup();
  const stats = useLemonadeStats();
  const caps = useCapabilities();
  return (
    <div className="s-section">
      <h2>Lemonade admin</h2>
      <p className="desc">Direct edit of <span className="mono" style={{color: "var(--fg)"}}>/internal/config</span>. Changes write to capabilities.toml and may require <span className="mono" style={{color: "var(--warn)"}}>⟳ restart</span>.</p>
      <div className="s-panel" style={{marginBottom: 12}}>
        <SRow k="runtime" mono v={<>{lemond.version} · {lemond.status} · <b>{lemond.loaded}</b>/{lemond.budget} loaded</>} />
        <SRow k="throughput" mono v={lemond.throughput != null ? `${lemond.throughput} MB/s` : '—'} />
        <SRow k="last TTFT" mono v={lemond.lastTtft != null ? `${(lemond.lastTtft * 1000).toFixed(0)} ms` : '—'} />
        <SRow k="last decode" mono v={lemond.lastTokPerSec != null ? `${lemond.lastTokPerSec.toFixed(1)} tok/s` : '—'} />
        {caps.data?.capabilities && Object.entries(caps.data.capabilities).map(([k, v]) => (
          <SRow key={k} k={`capability · ${k}`} mono v={<><b>{v.provider}</b>{v.model ? <> · {v.model}</> : null}</>} />
        ))}
      </div>
      <div className="s-panel">
        <SRow
          k="max_loaded_models"
          sub="Per-type LRU budget"
          mono
          v={<input className="input mono" defaultValue="4" style={{maxWidth: 80}} />}
          actions={<span style={{color: "var(--warn)", fontFamily: "var(--jbm)", fontSize: 11}}>⟳ requires restart</span>}
        />
        <SRow
          k="ctx_size"
          sub="Default per /v1/load — overridable per slot"
          mono
          v={<input className="input mono" defaultValue="4096" style={{maxWidth: 100}} />}
          actions={<span style={{color: "var(--warn)", fontFamily: "var(--jbm)", fontSize: 11}}>⟳ per-slot</span>}
        />
        <SRow
          k="llamacpp.args"
          sub="Mandatory baseline · ADR-0008 · read-only by default"
          mono
          v={argsEdit
            ? <input className="input mono" defaultValue="--parallel 1 --threads 8 --flash-attn on" />
            : <span className="mono" style={{padding: "6px 10px", background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", display: "inline-block", color: "var(--fg-2)"}}>--parallel 1 --threads 8 --flash-attn on</span>}
          actions={argsEdit
            ? <>
                <span style={{color: "var(--err)", fontFamily: "var(--jbm)", fontSize: 10, lineHeight: 1.4, maxWidth: 180}}>
                  ⚠ Removing <span style={{color: "var(--fg)"}}>--parallel 1</span> or <span style={{color: "var(--fg)"}}>--threads N</span> can deadlock the GPU
                </span>
                <button className="btn ghost sm" onClick={() => setArgsEdit(false)}>Done</button>
              </>
            : <button className="btn ghost sm" onClick={() => setArgsEdit(true)}>{Icons.edit} Edit</button>}
        />
        <SRow
          k="flm.args"
          sub="FLM trio config — drives the NPU coresident packing"
          mono
          v={<input className="input mono" defaultValue="--asr 1 --embed 1" />}
        />
        <SRow
          k="kokoro.cpu_bin"
          sub="Linux-only · GPU support is upstream-pending"
          mono
          v="builtin"
        />
        <SRow
          k="whispercpp.backend"
          mono
          v={<select className="input mono" defaultValue="vulkan" style={{maxWidth: 160}}><option>vulkan</option><option>cpu</option><option>cublas</option></select>}
        />
        <SRow
          k="sdcpp"
          sub="rocm · steps 20 · cfg 7.0 · 512×512"
          mono
          v={<input className="input mono" defaultValue="--steps 20 --cfg 7.0 --w 512 --h 512" />}
        />
      </div>
      <div style={{marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
        <div className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
          <span style={{color: "var(--warn)"}}>⟳</span> 2 fields will require a lemond restart to apply.
        </div>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={() => window.__hal0Toast && window.__hal0Toast("Opening /etc/lemonade/config.yaml", "info")}>{Icons.ext} View config file</button>
          <button className="btn" onClick={() => setRestartOpen(true)}>{Icons.restart} Save + restart lemond</button>
        </div>
      </div>

      <ConfirmDialog
        open={restartOpen}
        onCancel={() => setRestartOpen(false)}
        onConfirm={() => { setRestartOpen(false); window.__hal0Toast && window.__hal0Toast("Restarting lemond — brief outage", "warn"); }}
        title="Save changes and restart lemond?"
        message={<span>The runtime will be unavailable for <span className="mono" style={{color: "var(--warn)"}}>~8–12 seconds</span> while it reloads. In-flight inference requests will fail; the dashboard will reconnect automatically. Loaded models reload from disk — no re-pull required.</span>}
        confirmLabel="Save + restart"
      />
    </div>
  );
}

function OmniRouterSection() {
  return (
    <div className="s-section">
      <h2>OmniRouter</h2>
      <p className="desc">Client-side tool-calling loop owned by hal0. Eight tools — five upstream, three hal0-custom. Active set filters per-request based on enabled slots.</p>
      <div className="s-panel">
        <div style={{padding: "10px 18px", borderBottom: "1px solid var(--line-soft)", background: "var(--bg)", fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em", display: "grid", gridTemplateColumns: "200px 100px 1fr auto", gap: 16}}>
          <span>tool</span>
          <span>status</span>
          <span>target</span>
          <span>actions</span>
        </div>
        {HAL0_DATA.omnirouter.map(t => (
          <div key={t.name} className="s-tool-row">
            <span className="nm">{t.name}</span>
            <span className="st">{t.active ? <span className="chip ok">active</span> : <span className="chip">inactive</span>}</span>
            <span className="tg">
              {t.active ? <>target: <b>{t.target}</b></> : t.target}
            </span>
            <button className="btn ghost sm">{Icons.edit}</button>
          </div>
        ))}
      </div>
      <div style={{marginTop: 14, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
        <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, color: "var(--fg-2)", fontSize: 12, cursor: "pointer"}}>
          <input type="checkbox" defaultChecked style={{accentColor: "var(--accent)"}} />
          <span>Persist persona swaps as default</span>
        </label>
        <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>session-only by default</span>
      </div>
    </div>
  );
}

function AgentPolicySection() {
  return (
    <div className="s-section">
      <h2>Agent policy</h2>
      <p className="desc">Per-capability approval policy for bundled agents. <span className="mono">always</span> requires approval each call · <span className="mono">remember</span> auto-approves after first OK · <span className="mono">deny</span> blocks.</p>
      <div className="s-panel">
        {[
          { cap: "registry-write", desc: "model_pull, model_delete", policy: "always" },
          { cap: "fs-read",        desc: "read_file, list_dir",       policy: "remember" },
          { cap: "fs-write",       desc: "write_file, edit_file",     policy: "always" },
          { cap: "shell-exec",     desc: "run shell commands",         policy: "always" },
          { cap: "net-fetch",      desc: "http_get, fetch_url",        policy: "remember" },
          { cap: "slot-control",   desc: "restart_slot, unload_slot",  policy: "always" },
        ].map(p => (
          <SRow
            key={p.cap}
            k={<span style={{fontFamily: "var(--jbm)"}}>{p.cap}</span>}
            sub={p.desc}
            v={
              <div className="mono" style={{display: "inline-flex", border: "1px solid var(--line)", borderRadius: 4, overflow: "hidden"}}>
                {["always", "remember", "deny"].map(o => (
                  <span
                    key={o}
                    style={{
                      padding: "4px 10px",
                      fontSize: 11,
                      cursor: "pointer",
                      background: p.policy === o ? "var(--accent-soft)" : "transparent",
                      color: p.policy === o ? "var(--accent)" : "var(--fg-3)",
                      borderRight: o !== "deny" ? "1px solid var(--line)" : "none",
                    }}
                  >{o}</span>
                ))}
              </div>
            }
          />
        ))}
      </div>
    </div>
  );
}

function MemorySection() {
  return (
    <div className="s-section">
      <h2>Memory (Cognee)</h2>
      <p className="desc">Cognee namespace + store inspection. The dashboard exposes only what operators need; agents own the rest of the surface via MCP in Phase 8.</p>
      <div className="s-panel">
        <SRow k="Namespace" mono v="shared (default)" actions={<button className="btn ghost sm">{Icons.edit} Change</button>} />
        <SRow k="Store" mono v="SQLite + LanceDB + Kuzu" />
        <SRow k="Records" mono v={<span className="num">2,847</span>} />
        <SRow k="Disk usage" mono v="184 MB" />
        <SRow k="Last write" mono v="3 min ago · pi-coder" />
      </div>
      <div style={{marginTop: 14}}>
        <button className="btn danger">{Icons.warn} Reset namespace</button>
      </div>
    </div>
  );
}

function AppearanceSection() {
  return (
    <div className="s-section">
      <h2>Appearance</h2>
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
  return (
    <div className="s-section">
      <h2>About</h2>
      <div className="s-panel">
        <SRow k="hal0" mono v="v0.2.1 — Lemonade-embedded slots" />
        <SRow k="License" v="Apache-2.0" />
        <SRow k="Repository" mono v="github.com/Hal0ai/hal0" actions={<button className="btn ghost sm">{Icons.ext} Open</button>} />
        <SRow k="Docs" v="hal0.dev/docs/v0.2-upgrade" actions={<button className="btn ghost sm">{Icons.ext} Open</button>} />
        <SRow k="Discord" v="discord.gg/hal0" actions={<button className="btn ghost sm">{Icons.ext} Join</button>} />
      </div>
      <div style={{marginTop: 14, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>
        Built on AMD Lemonade, FLM (XDNA2), llama.cpp, whisper.cpp, sd.cpp, Kokoro, Cognee.
      </div>
    </div>
  );
}

Object.assign(window, { SettingsView });
