// hal0 dashboard — Model interactive surface
//
// Add by HF coords modal · Recipe editor · Delete confirm ·
// "Used by" slot panel · "On disk" panel · DownloadRow (live SSE).
//
// Phase B2 wireup (#220 brief): every modal now drives off real
// hooks — `useModelInspect` populates the variant list, `usePullJob`
// owns the pull lifecycle, `useModelUpdate` saves recipe edits, and
// `useModelDelete` returns the cascade affected_slots straight from
// the backend. The old HAL0_DATA fallbacks live on only as cosmetic
// fixtures (host RAM, /var disk hint).

import { useModelInspect, useModelUpdate, useModelDelete, usePullJob, fmtBytes, fmtSpeed, fmtEta } from '@/api/hooks/useModels'
import { useSlots } from '@/api/hooks/useSlots'

const { useState: useStateMM, useEffect: useEffectMM, useMemo: useMemoMM } = React;

// ─── Add model by HF coords ─────────────────────────────────────
function AddByHfModal({ open, onClose }) {
  const [repo, setRepo] = useStateMM("");
  const [variant, setVariant] = useStateMM(null);
  const [name, setName] = useStateMM("");
  const [labels, setLabels] = useStateMM({ chat: true });
  const [mmproj, setMmproj] = useStateMM("");

  const inspect = useModelInspect();
  const pullJob = usePullJob();

  useEffectMM(() => {
    if (open) {
      setRepo(""); setVariant(null); setName(""); setLabels({ chat: true }); setMmproj("");
      inspect.reset();
      pullJob.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const variants = inspect.data?.variants ?? [];
  // mmproj affordance — HF repos often ship an mmproj-Q8.gguf next to
  // the main quants. Pick those out of the same variant list so the
  // dropdown reflects what's actually in the repo.
  const mmprojChoices = useMemoMM(
    () => variants.filter(v => /mmproj/i.test(v.id)).map(v => v.id),
    [variants],
  );

  const onInspect = async () => {
    if (!repo) return;
    try {
      const result = await inspect.mutateAsync({ hf_repo: repo });
      // Auto-suggest a user.* name from the repo's tail segment.
      const tail = (result?.repo || repo).split("/")[1] || "model";
      setName(prev => prev || ("user." + tail.replace(/-GGUF$/i, "")));
    } catch (e) {
      // The toast surface already renders the Hal0Error envelope;
      // surface a hint for offline mock scenarios so the operator
      // doesn't think the button is wedged.
      window.__hal0Toast && window.__hal0Toast(
        `Inspect failed — ${e?.message || "unreachable"}`,
        "err",
      );
    }
  };

  const inspected = inspect.isSuccess && !!inspect.data;
  const sel = variants.find(v => v.id === variant);
  const canPull = inspected && variant && name && !pullJob.inFlight;

  const onPull = async () => {
    if (!canPull) return;
    try {
      // The pull endpoint keys on the registry model id. We use the
      // operator-chosen ``name`` so the row lands under their preferred
      // namespace, and pass the HF variant + optional mmproj as the
      // pull job body — the backend resolves them against the registry
      // entry's hf_repo/hf_filename it writes during the curated
      // ``pick-default`` flow.
      const labelList = Object.entries(labels).filter(([, v]) => v).map(([k]) => k);
      await pullJob.start(name, {
        hf_repo: inspect.data?.repo ?? repo,
        hf_filename: variant,
        mmproj_filename: mmproj || undefined,
        labels: labelList,
      });
      window.__hal0Toast && window.__hal0Toast(
        `Pulling ${name} · ${sel?.size ?? ""}`, "info",
      );
      onClose();
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Pull failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Catalog · add model"
      title="Add model from Hugging Face"
      width={680}
      foot={
        <>
          <span>Files land under <span className="mono">/var/lib/hal0/models/&lt;id&gt;</span></span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" disabled={!canPull} onClick={onPull}>
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
            onChange={e => { setRepo(e.target.value); inspect.reset(); }}
            placeholder="unsloth/Qwen3-8B-GGUF"
            style={{flex: 1}}
            autoFocus
          />
          <button className="btn ghost sm" disabled={!repo || inspect.isPending} onClick={onInspect}>
            {inspect.isPending ? "Inspecting…" : "Inspect"}
          </button>
        </div>
      </div>

      {inspect.isError && (
        <div className="err" style={{marginBottom: 10}}>
          {inspect.error?.code === "hf.repo_not_found"
            ? `HF repo not found: ${repo}`
            : `Inspect failed: ${inspect.error?.message || "unreachable"}`}
        </div>
      )}

      {inspected && (
        <>
          <div className="form-row">
            <div className="form-lbl">
              <span>Variants <span className="req">*</span></span>
              <span className="sub">
                {variants.length} available · pick a quant
              </span>
            </div>
            <div className="form-ctl" style={{display: "flex", flexDirection: "column", gap: 6}}>
              {variants.length === 0 ? (
                <div className="mono" style={{fontSize: 12, color: "var(--fg-4)", fontStyle: "italic"}}>
                  No .gguf files found in this repo.
                </div>
              ) : variants.filter(v => !/mmproj/i.test(v.id)).map(v => (
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
                </div>
              ))}
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
                  <option value="">
                    {mmprojChoices.length === 0
                      ? "— no mmproj files in repo —"
                      : "— pick from repo files… —"}
                  </option>
                  {mmprojChoices.map(id => <option key={id} value={id}>{id}</option>)}
                </select>
              </div>
            </div>
          )}

          {inspect.data?.metadata?.license && (
            <div className="form-section">License · <span className="mono" style={{color: "var(--fg)"}}>{inspect.data.metadata.license}</span></div>
          )}

          <div className="form-section">Pre-flight</div>
          <div style={{padding: 12, background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11.5, lineHeight: 1.7}}>
            <div>repo · <span style={{color: "var(--fg)"}}>{inspect.data?.repo || repo}</span></div>
            <div>variant · <span style={{color: "var(--fg)"}}>{variant || "—"}</span></div>
            <div>size · <span style={{color: "var(--fg)"}}>{sel ? sel.size : "—"}</span></div>
            {pullJob.inFlight && (
              <div>pull · <span style={{color: "var(--accent)"}}>{pullJob.state} {pullJob.pct != null ? `${pullJob.pct}%` : ""}</span></div>
            )}
          </div>
        </>
      )}
    </Modal>
  );
}

// ─── Recipe editor (per-model defaults) ────────────────────────
function RecipeEditorModal({ open, onClose, model }) {
  const update = useModelUpdate();
  const init = model?.defaults || {};
  const [ctx, setCtx] = useStateMM("");
  const [ngl, setNgl] = useStateMM("");
  const [extra, setExtra] = useStateMM("");

  useEffectMM(() => {
    if (open && model) {
      setCtx(init.context_size != null ? String(init.context_size) : "");
      setNgl(init.n_gpu_layers != null ? String(init.n_gpu_layers) : "");
      setExtra(init.extra_args || "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, model?.id]);

  if (!model) return null;

  const onSave = async () => {
    const defaults = {};
    if (ctx.trim()) {
      const n = parseInt(ctx, 10);
      if (Number.isFinite(n)) defaults.context_size = n;
    }
    if (ngl.trim()) {
      const n = parseInt(ngl, 10);
      if (Number.isFinite(n)) defaults.n_gpu_layers = n;
    }
    if (extra.trim()) defaults.extra_args = extra;
    try {
      await update.mutateAsync({ id: model.id, body: { defaults } });
      window.__hal0Toast && window.__hal0Toast(`Updated ${model.longName || model.id}`, "ok");
      onClose();
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Save failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Recipe · edit defaults"
      title={`Edit options · ${model.longName || model.name || model.id}`}
      width={560}
      foot={
        <>
          <span style={{color: "var(--warn)"}}>⟳ ctx_size + extra_args require slot restart</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" onClick={onSave} disabled={update.isPending}>
              {update.isPending ? "Saving…" : "Save options"}
            </button>
          </span>
        </>
      }
    >
      <div className="form-row">
        <div className="form-lbl">
          <span>context_size</span>
          <span className="sub">tokens · empty = launcher default</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" inputMode="numeric" placeholder="e.g. 8192" value={ctx} onChange={e => setCtx(e.target.value)} />
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>n_gpu_layers</span>
          <span className="sub">-1 = all on GPU · 0 = CPU only</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" inputMode="numeric" placeholder="e.g. -1" value={ngl} onChange={e => setNgl(e.target.value)} />
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>extra_args</span>
          <span className="sub">freeform · appended after slot extra_args</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" placeholder="--rope-freq-base 10000" value={extra} onChange={e => setExtra(e.target.value)} />
        </div>
      </div>
      {update.isError && (
        <div className="err">{update.error?.message || "Save failed"}</div>
      )}
    </Modal>
  );
}

// ─── Delete model confirm ───────────────────────────────────────
function DeleteModelDialog({ open, onClose, model }) {
  const del = useModelDelete();
  // Live affected_slots — prefer the cascade response when the user
  // has already attempted a force_cascade=false dry-run (not yet
  // wired in B2); for the default flow we fall back to the live
  // slots query so the warning matches what the registry sees.
  const slotsQuery = useSlots();
  if (!model) return null;
  const slots = slotsQuery.data ?? [];
  const slotsUsing = slots.filter(s => (s.model_id || s.model?.default) === model.id);
  const hasUsers = slotsUsing.length > 0;

  const onConfirm = async () => {
    try {
      const res = await del.mutateAsync(model.id);
      const cascaded = res?.affected_slots?.length || 0;
      window.__hal0Toast && window.__hal0Toast(
        cascaded
          ? `Deleted ${model.longName || model.id} (cascaded ${cascaded} slot${cascaded > 1 ? "s" : ""})`
          : `Deleted ${model.longName || model.id}`,
        "ok",
      );
      onClose();
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Delete failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };

  return (
    <ConfirmDialog
      open={open}
      onCancel={onClose}
      onConfirm={onConfirm}
      title={`Delete ${model.longName || model.name || model.id}?`}
      message={
        <span>
          This removes <span className="mono" style={{color: "var(--fg)"}}>{model.size || fmtBytes(model.size_bytes || 0)}</span> from <span className="mono">/var/lib/hal0/models</span>.{" "}
          {hasUsers && (
            <span style={{display: "block", marginTop: 10, padding: "10px 12px", background: "var(--warn-soft)", border: "1px solid var(--warn-line)", borderRadius: "var(--rad-sm)", color: "var(--warn)", fontFamily: "var(--jbm)", fontSize: 12}}>
              ⚠ {slotsUsing.length} slot{slotsUsing.length > 1 ? "s" : ""} reference this model: <b>{slotsUsing.map(s => s.name).join(", ")}</b>. They'll move to <span className="mono">empty</span> state. Re-configure with a different model first if you need them live.
            </span>
          )}
        </span>
      }
      confirmLabel={del.isPending ? "Deleting…" : "Delete model"}
      destructive
      typeToConfirm={hasUsers ? model.id : null}
    />
  );
}

// ─── Used-by panel (Model detail) ───────────────────────────────
function UsedByPanel({ model }) {
  const slotsQuery = useSlots();
  if (!model) return null;
  const slots = slotsQuery.data ?? [];
  const using = slots.filter(s => (s.model_id || s.model?.default) === model.id);
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
              <span className={"dot " + (s.state || "empty")} />
              {s.name}
            </span>
            <span className="v" style={{display: "flex", alignItems: "center", gap: 6}}>
              <span style={{color: "var(--fg-3)"}}>{s.type || ""} · {s.device || s.backend || ""}</span>
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
  const path = model.path || `/var/lib/hal0/models/${model.id}/`;
  return (
    <div className="mdl-detail-recipe">
      <div className="lbl">On disk</div>
      <div className="ro-row">
        <span className="k">path</span>
        <span className="v" style={{wordBreak: "break-all", fontSize: 11}}>{path}</span>
      </div>
      <div className="ro-row">
        <span className="k">size</span>
        <span className="v">{model.size || fmtBytes(model.size_bytes || 0)}</span>
      </div>
      <div className="ro-row">
        <span className="k">ns</span>
        <span className="v">{model.ns || "—"}</span>
      </div>
      <div style={{display: "flex", gap: 6, marginTop: 8}}>
        <button className="btn ghost sm" onClick={() => {
          navigator.clipboard?.writeText(path);
          window.__hal0Toast && window.__hal0Toast("Path copied to clipboard", "ok");
        }}>Copy path</button>
      </div>
    </div>
  );
}

// ─── Download row backed by usePullJob ──────────────────────────
function DownloadRow({ modelId, onRemove }) {
  const job = usePullJob();
  // Reattach on mount so a refresh keeps showing in-flight pulls.
  useEffectMM(() => {
    if (modelId) job.reattach(modelId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelId]);

  const state = job.state;
  const pct = job.pct ?? 0;
  const onPause = () => {
    // Lemonade pull engine doesn't support pause — degrade gracefully
    // by cancelling the in-flight transfer; the row can be re-pulled
    // from the catalog.
    job.cancel();
  };
  const onResume = () => { /* see Pause comment */ };
  const onCancel = () => { job.cancel(); };
  const onRetry = () => { job.start(modelId); };

  return (
    <div style={{padding: "12px 16px", borderBottom: "1px solid var(--line-soft)", position: "relative"}}>
      <div style={{display: "flex", justifyContent: "space-between", fontFamily: "var(--jbm)", fontSize: 11.5, marginBottom: 6, alignItems: "center", gap: 8}}>
        <span style={{
          color: state === "completed" ? "var(--ok)" : state === "cancelled" ? "var(--fg-4)" : state === "failed" ? "var(--err)" : "var(--fg)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          flex: 1,
          textDecoration: state === "cancelled" ? "line-through" : "none",
        }}>{modelId}</span>
        <span style={{
          color: state === "completed" ? "var(--ok)" : state === "queued" ? "var(--fg-4)" : state === "failed" ? "var(--err)" : "var(--fg)",
          fontSize: 11,
        }}>
          {state === "completed" && "✓ done"}
          {state === "queued"    && "queued"}
          {state === "running"   && `${pct}%`}
          {state === "cancelled" && "cancelled"}
          {state === "failed"    && "failed"}
          {state === "idle"      && "—"}
        </span>
      </div>
      <div className="dl-bar" style={{height: 4}}>
        <i style={{
          width: `${pct}%`,
          background: state === "completed" ? "var(--ok)" : state === "failed" ? "var(--err)" : state === "cancelled" ? "var(--fg-4)" : "var(--accent)",
        }} />
      </div>
      {state === "running" && (
        <div style={{display: "flex", justifyContent: "space-between", fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", marginTop: 4}}>
          <span>{fmtBytes(job.downloaded)} / {fmtBytes(job.total)}</span>
          <span>{fmtSpeed(job.speedBps)} · {fmtEta(job.etaS)}</span>
        </div>
      )}
      {state === "failed" && job.error && (
        <div style={{marginTop: 6, padding: "8px 10px", background: "var(--err-soft)", border: "1px solid var(--err-line)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11, color: "var(--err)"}}>
          {job.error.message || "pull failed"}
        </div>
      )}
      <div style={{display: "flex", gap: 4, marginTop: 6}}>
        {state === "running" && (
          <>
            <button className="btn ghost sm" onClick={onPause}>Pause</button>
            <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
          </>
        )}
        {state === "queued" && (
          <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
        )}
        {state === "failed" && (
          <>
            <button className="btn ghost sm" onClick={onRetry}>{Icons.restart} Retry</button>
            <button className="btn ghost sm" onClick={() => onRemove && onRemove(modelId)}>Remove</button>
          </>
        )}
        {state === "cancelled" && (
          <button className="btn ghost sm" onClick={() => onRemove && onRemove(modelId)}>Remove</button>
        )}
        {state === "completed" && (
          <button className="btn ghost sm" onClick={() => onRemove && onRemove(modelId)}>Dismiss</button>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { AddByHfModal, RecipeEditorModal, DeleteModelDialog, UsedByPanel, OnDiskPanel, DownloadRow });
