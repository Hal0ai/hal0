// hal0 dashboard — Model interactive surface
// Add by HF coords modal · Delete model confirm · Downloads error/paused states ·
// "Used by" slot panel · "On disk" panel for the detail pane

const { useState: useStateMM, useEffect: useEffectMM } = React;

// ─── Add model by HF coords ─────────────────────────────────────
function AddByHfModal({ open, onClose }) {
  const [repo, setRepo] = useStateMM("");
  const [inspected, setInspected] = useStateMM(false);
  const [inspecting, setInspecting] = useStateMM(false);
  const [variant, setVariant] = useStateMM(null);
  const [name, setName] = useStateMM("");
  const [labels, setLabels] = useStateMM({ chat: true });
  const [mmproj, setMmproj] = useStateMM("");

  useEffectMM(() => {
    if (open) {
      setRepo(""); setInspected(false); setInspecting(false);
      setVariant(null); setName(""); setLabels({ chat: true }); setMmproj("");
    }
  }, [open]);

  const variants = [
    { id: "Q4_K_M",     size: "4.9 GB", info: "single file" },
    { id: "UD-Q4_K_XL", size: "5.1 GB", info: "single file · unsloth dynamic" },
    { id: "Q8_0",       size: "8.5 GB", info: "sharded · 2 files" },
    { id: "Q4_0",       size: "4.7 GB", info: "single file · legacy" },
    { id: "F16",        size: "16.2 GB", info: "single file · full precision" },
  ];

  const inspect = () => {
    if (!repo) return;
    setInspecting(true);
    setTimeout(() => {
      setInspecting(false);
      setInspected(true);
      setName("user." + (repo.split("/")[1] || "model").replace(/-GGUF$/, ""));
    }, 600);
  };

  const sel = variants.find(v => v.id === variant);
  const canPull = inspected && variant && name;

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Catalog · add model"
      title="Add model from Hugging Face"
      width={680}
      foot={
        <>
          <span>Files land under <span className="mono">/var/lib/hal0/models/user.*</span></span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" disabled={!canPull} onClick={() => { onClose(); window.__hal0Toast && window.__hal0Toast(`Pulling ${name} · ${sel ? sel.size : ""}`, "info"); }}>
              {Icons.download} Pull{sel ? ` (${sel.size})` : ""}
            </button>
          </span>
        </>
      }
    >
      <div className="form-row">
        <div className="form-lbl">
          <span>Repo <span className="req">*</span></span>
          <span className="sub">org / repo · GGUF preferred</span>
        </div>
        <div className="form-ctl" style={{display: "flex", gap: 8}}>
          <input
            className="input mono"
            value={repo}
            onChange={e => { setRepo(e.target.value); setInspected(false); }}
            placeholder="unsloth/Qwen3-8B-GGUF"
            style={{flex: 1}}
            autoFocus
          />
          <button className="btn ghost sm" disabled={!repo || inspecting} onClick={inspect}>
            {inspecting ? "Inspecting…" : "Inspect"}
          </button>
        </div>
      </div>

      {inspected && (
        <>
          <div className="form-row">
            <div className="form-lbl">
              <span>Variants <span className="req">*</span></span>
              <span className="sub">{variants.length} available · pick a quant</span>
            </div>
            <div className="form-ctl" style={{display: "flex", flexDirection: "column", gap: 6}}>
              {variants.map(v => (
                <div
                  key={v.id}
                  className={"variant-row" + (variant === v.id ? " sel" : "")}
                  onClick={() => setVariant(v.id)}
                >
                  <span className="rad" />
                  <span className="nm">
                    {v.id}
                    <span className="sub">{v.info}</span>
                  </span>
                  <span className="sz num">{v.size}</span>
                  <span className="info">{HAL0_DATA.host.ram.free > parseSizeGB(v.size) ? "✓ fits" : "tight on RAM"}</span>
                </div>
              ))}
              <div className="variant-row" onClick={() => setVariant("other")} style={{borderStyle: "dashed"}}>
                <span className="rad" />
                <span className="nm">Other…<span className="sub">free-text quant tag</span></span>
                <span></span>
                <span></span>
              </div>
            </div>
          </div>

          <div className="form-row">
            <div className="form-lbl">
              <span>Model name (in hal0)</span>
              <span className="sub">prefixed with <span className="mono">user.</span> by convention</span>
            </div>
            <div className="form-ctl">
              <input className="input mono" value={name} onChange={e => setName(e.target.value)} />
            </div>
          </div>

          <div className="form-row">
            <div className="form-lbl">
              <span>Labels</span>
              <span className="sub">drives OmniRouter eligibility</span>
            </div>
            <div className="form-ctl" style={{display: "flex", flexWrap: "wrap", gap: 8}}>
              {["chat", "tool-calling", "vision", "embeddings", "reranking", "transcription", "tts", "image", "edit"].map(l => (
                <label key={l} className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={!!labels[l]}
                    onChange={e => setLabels({ ...labels, [l]: e.target.checked })}
                  />
                  <span className="mono">{l}</span>
                </label>
              ))}
              {labels.vision && !mmproj && (
                <div className="err" style={{flexBasis: "100%"}}>vision label requires an mmproj file — pick one below</div>
              )}
            </div>
          </div>

          {labels.vision && (
            <div className="form-row">
              <div className="form-lbl">
                <span>mmproj file</span>
                <span className="warn">required for vision-labeled models</span>
              </div>
              <div className="form-ctl">
                <select className="input mono" value={mmproj} onChange={e => setMmproj(e.target.value)}>
                  <option value="">— pick from repo files…</option>
                  <option>mmproj-Q8_0.gguf</option>
                  <option>mmproj-F16.gguf</option>
                </select>
              </div>
            </div>
          )}

          <div className="form-section">Pre-flight</div>
          <div style={{padding: 12, background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11.5, lineHeight: 1.7}}>
            <div>repo · <span style={{color: "var(--fg)"}}>{repo}</span></div>
            <div>variant · <span style={{color: "var(--fg)"}}>{variant || "—"}</span></div>
            <div>size · <span style={{color: "var(--fg)"}}>{sel ? sel.size : "—"}</span></div>
            <div>disk · <span style={{color: "var(--ok)"}}>412 GB free on /var ✓</span></div>
            <div>auth · <span style={{color: "var(--ok)"}}>HF_TOKEN set ✓</span></div>
          </div>
        </>
      )}
    </Modal>
  );
}

// ─── Delete model confirm ───────────────────────────────────────
function DeleteModelDialog({ open, onClose, model }) {
  if (!model) return null;
  // Structured match by model_id (preferred); falls back to id for legacy entries.
  const slotsUsing = HAL0_DATA.slots.filter(s => s.model_id === model.id);
  const hasUsers = slotsUsing.length > 0;
  return (
    <ConfirmDialog
      open={open}
      onCancel={onClose}
      onConfirm={() => { onClose(); window.__hal0Toast && window.__hal0Toast(`Deleted ${model.longName}`, "ok"); }}
      title={`Delete ${model.longName}?`}
      message={
        <span>
          This removes <span className="mono" style={{color: "var(--fg)"}}>{model.size}</span> from <span className="mono">/var/lib/hal0/models</span>.{" "}
          {hasUsers && (
            <span style={{display: "block", marginTop: 10, padding: "10px 12px", background: "var(--warn-soft)", border: "1px solid var(--warn-line)", borderRadius: "var(--rad-sm)", color: "var(--warn)", fontFamily: "var(--jbm)", fontSize: 12}}>
              ⚠ {slotsUsing.length} slot{slotsUsing.length > 1 ? "s" : ""} reference this model: <b>{slotsUsing.map(s => s.name).join(", ")}</b>. They'll move to <span className="mono">empty</span> state. Re-configure with a different model first if you need them live.
            </span>
          )}
        </span>
      }
      confirmLabel="Delete model"
      destructive
      typeToConfirm={hasUsers ? model.id : null}
    />
  );
}

// ─── Used-by panel (Model detail) ───────────────────────────────
function UsedByPanel({ model }) {
  if (!model) return null;
  const using = HAL0_DATA.slots.filter(s => s.model_id === model.id);
  return (
    <div className="mdl-detail-recipe">
      <div className="lbl">Used by</div>
      {using.length === 0 ? (
        <div className="mono" style={{fontSize: 12, color: "var(--fg-4)", fontStyle: "italic"}}>
          No slot references this model.
        </div>
      ) : (
        using.map(s => (
          <div key={s.name} className="ro-row" style={{cursor: "pointer", padding: "7px 0"}}
               onClick={() => { window.location.hash = "#slots/" + s.name; }}>
            <span className="k" style={{display: "flex", alignItems: "center", gap: 6}}>
              <span className={"dot " + s.state} />
              {s.name}
            </span>
            <span className="v" style={{display: "flex", alignItems: "center", gap: 6}}>
              <span style={{color: "var(--fg-3)"}}>{s.type} · {s.device}</span>
              {s.isDefault && <span className="chip outlined amber">default</span>}
              <span style={{marginLeft: "auto", color: "var(--accent)"}}>→</span>
            </span>
          </div>
        ))
      )}
    </div>
  );
}

// ─── On-disk panel (Model detail) ───────────────────────────────
function OnDiskPanel({ model }) {
  if (!model || !model.installed) return null;
  return (
    <div className="mdl-detail-recipe">
      <div className="lbl">On disk</div>
      <div className="ro-row">
        <span className="k">path</span>
        <span className="v" style={{wordBreak: "break-all", fontSize: 11}}>/var/lib/hal0/models/{model.id}.gguf</span>
      </div>
      <div className="ro-row">
        <span className="k">sha256</span>
        <span className="v" style={{fontSize: 11, color: "var(--fg-3)"}}>a3f4…b87c</span>
      </div>
      <div className="ro-row">
        <span className="k">verified</span>
        <span className="v"><span style={{color: "var(--ok)"}}>✓</span> 41 days ago</span>
      </div>
      <div style={{display: "flex", gap: 6, marginTop: 8}}>
        <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast("Path copied to clipboard", "ok")}>Copy path</button>
        <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast("Verifying sha256…", "info")}>Re-verify</button>
      </div>
    </div>
  );
}

// ─── Download row with full state vocabulary ────────────────────
function DownloadRow({ dl, onPause, onResume, onCancel, onRetry, onRemove }) {
  const state = dl.state;
  return (
    <div style={{padding: "12px 16px", borderBottom: "1px solid var(--line-soft)", position: "relative"}}>
      <div style={{display: "flex", justifyContent: "space-between", fontFamily: "var(--jbm)", fontSize: 11.5, marginBottom: 6, alignItems: "center", gap: 8}}>
        <span style={{
          color: state === "done" ? "var(--ok)" : state === "cancelled" ? "var(--fg-4)" : state === "error" ? "var(--err)" : "var(--fg)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          flex: 1,
          textDecoration: state === "cancelled" ? "line-through" : "none",
        }}>{dl.name}</span>
        <span style={{
          color: state === "done" ? "var(--ok)" : state === "queued" ? "var(--fg-4)" : state === "paused" ? "var(--warn)" : state === "error" ? "var(--err)" : "var(--fg)",
          fontSize: 11,
        }}>
          {state === "done"      && "✓ done"}
          {state === "queued"    && "queued"}
          {state === "pulling"   && `${dl.pct}%`}
          {state === "paused"    && `${dl.pct}% paused`}
          {state === "verifying" && "verifying…"}
          {state === "cancelled" && "cancelled"}
          {state === "error"     && "failed"}
        </span>
      </div>
      <div className="dl-bar" style={{height: 4}}>
        <i style={{
          width: `${dl.pct || 0}%`,
          background: state === "done" ? "var(--ok)" : state === "error" ? "var(--err)" : state === "paused" || state === "cancelled" ? "var(--fg-4)" : "var(--accent)",
        }} />
      </div>
      {state === "pulling" && (
        <div style={{display: "flex", justifyContent: "space-between", fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", marginTop: 4}}>
          <span>{dl.done} / {dl.size}</span>
          <span>{dl.rate} · {dl.eta}</span>
        </div>
      )}
      {state === "error" && (
        <div style={{marginTop: 6, padding: "8px 10px", background: "var(--err-soft)", border: "1px solid var(--err-line)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11, color: "var(--err)"}}>
          corrupted shard 2/2 · sha256 mismatch
        </div>
      )}
      {/* Per-row actions */}
      <div style={{display: "flex", gap: 4, marginTop: 6}}>
        {state === "pulling" && (
          <>
            <button className="btn ghost sm" onClick={() => onPause && onPause(dl)}>Pause</button>
            <button className="btn ghost sm" onClick={() => onCancel && onCancel(dl)}>Cancel</button>
          </>
        )}
        {state === "paused" && (
          <>
            <button className="btn ghost sm" onClick={() => onResume && onResume(dl)}>Resume</button>
            <button className="btn ghost sm" onClick={() => onCancel && onCancel(dl)}>Cancel</button>
          </>
        )}
        {state === "queued" && (
          <button className="btn ghost sm" onClick={() => onCancel && onCancel(dl)}>Cancel</button>
        )}
        {state === "error" && (
          <>
            <button className="btn ghost sm" onClick={() => onRetry && onRetry(dl)}>{Icons.restart} Retry</button>
            <button className="btn ghost sm" onClick={() => onRemove && onRemove(dl)}>Remove</button>
          </>
        )}
        {state === "cancelled" && (
          <button className="btn ghost sm" onClick={() => onRemove && onRemove(dl)}>Remove</button>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { AddByHfModal, DeleteModelDialog, UsedByPanel, OnDiskPanel, DownloadRow });
