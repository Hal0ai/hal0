// hal0 dashboard — Models view (catalog + detail + downloads)
//
// Phase B2 wireup (#220 brief): the catalog is driven entirely by
// useModels(); the HAL0_DATA fallback is gone now the backend always
// emits ``ns`` on every row. The detail pane's Recipe section reads
// each model's persisted ``defaults`` and writes them back via PUT
// /api/models/{id}, and the Downloads pane is a thin shell around
// per-row usePullJob() instances tracked by model_id.

import { useModels, usePullJob, fmtBytes } from '@/api/hooks/useModels'

const { useState: useStateM, useMemo: useMemoM, useEffect: useEffectM } = React;

function ModelsView() {
  const [selId, setSelId] = useStateM(null);
  const [filters, setFilters] = useStateM({ type: null, device: null, ns: null });
  const [addOpen, setAddOpen] = useStateM(false);
  const [addByPathOpen, setAddByPathOpen] = useStateM(false);
  const [scanOpen, setScanOpen] = useStateM(false);
  const [recipeOpen, setRecipeOpen] = useStateM(false);
  const [delModel, setDelModel] = useStateM(null);
  // Track which model_ids the user has launched a pull for this
  // session — the Downloads pane renders one DownloadRow per entry
  // and each row owns its own usePullJob() instance (which reattaches
  // to an in-flight pull on mount).
  const [activePulls, setActivePulls] = useStateM([]);

  const modelsQuery = useModels();
  const modelList = modelsQuery.data ?? [];

  // Auto-pick the first installed model on first render so the detail
  // pane never opens empty.
  useEffectM(() => {
    if (!selId && modelList.length) {
      const first = modelList.find(m => m.installed) || modelList[0];
      if (first) setSelId(first.id);
    }
  }, [modelList, selId]);

  const selected = modelList.find(m => m.id === selId) || modelList[0];

  const fil = m => {
    if (filters.type && m.type !== filters.type) return false;
    if (filters.device && m.device !== filters.device) return false;
    if (filters.ns && m.ns !== filters.ns) return false;
    return true;
  };
  const installed = modelList.filter(m => m.installed && fil(m));
  const blessed = modelList.filter(m => !m.installed && m.ns === "blessed" && fil(m));
  const userNs = modelList.filter(m => m.ns === "pulled" && !m.installed && fil(m));

  const toggle = (k, v) => setFilters(f => ({ ...f, [k]: f[k] === v ? null : v }));

  // Listen for any other surface (FirstRun, Add modal) that starts a
  // pull and surface it in our Downloads pane.
  useEffectM(() => {
    const handler = (e) => {
      const id = e?.detail?.modelId;
      if (id) setActivePulls(prev => prev.includes(id) ? prev : [...prev, id]);
    };
    window.addEventListener("hal0:pull-started", handler);
    return () => window.removeEventListener("hal0:pull-started", handler);
  }, []);

  const removeActive = (id) => setActivePulls(prev => prev.filter(x => x !== id));

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Catalog</span>
        <h1>Models</h1>
        <span className="vh-spacer" />
        <button className="btn ghost" onClick={() => window.__hal0Toast && window.__hal0Toast("HF search — stubbed", "info")}>{Icons.search} Search HF</button>
        <button className="btn ghost" onClick={() => setScanOpen(true)}>{Icons.search} Scan directory</button>
        <button className="btn ghost" onClick={() => setAddByPathOpen(true)}>{Icons.plus} Add by path</button>
        <button className="btn" onClick={() => setAddOpen(true)}>{Icons.plus} Add by HF coords</button>
      </div>

      <div className="models-layout">
        {/* ── Filters ── */}
        <div className="mdl-filters">
          <div className="mdl-filter-grp">
            <div className="lbl">type</div>
            <div className="mdl-filter-chips">
              {["llm", "embedding", "reranking", "transcription", "tts", "image"].map(t => (
                <button key={t} className={"mdl-chip" + (filters.type === t ? " on" : "")} onClick={() => toggle("type", t)}>{t}</button>
              ))}
            </div>
          </div>
          <div className="mdl-filter-grp">
            <div className="lbl">device</div>
            <div className="mdl-filter-chips">
              {["gpu-rocm", "gpu-vulkan", "cpu", "npu"].map(d => (
                <button key={d} className={"mdl-chip" + (filters.device === d ? " on" : "")} onClick={() => toggle("device", d)}>{d}</button>
              ))}
            </div>
          </div>
          <div className="mdl-filter-grp">
            <div className="lbl">namespace</div>
            <div className="mdl-filter-chips">
              {["blessed", "pulled"].map(n => (
                <button key={n} className={"mdl-chip" + (filters.ns === n ? " on" : "")} onClick={() => toggle("ns", n)}>{n}</button>
              ))}
            </div>
          </div>
          <div className="mdl-filter-grp">
            <div className="lbl">labels</div>
            <div className="mdl-filter-chips">
              {["chat", "tool-calling", "vision", "embeddings", "reranking", "transcription", "tts", "image", "edit"].map(l => (
                <button key={l} className="mdl-chip">{l}</button>
              ))}
            </div>
          </div>
          <div className="mdl-filter-grp">
            <div className="lbl">search</div>
            <input className="input mono" placeholder="qwen, embed, …" />
          </div>
          <div style={{borderTop: "1px solid var(--line-soft)", paddingTop: 10, fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", lineHeight: 1.7}}>
            <div>{modelList.length} total · {modelList.filter(m => m.installed).length} on disk</div>
            <div style={{color: "var(--fg-5)"}}>{modelList.filter(m => m.ns === "blessed").length} blessed · {modelList.filter(m => m.ns === "pulled").length} pulled</div>
          </div>
        </div>

        {/* ── List ── */}
        <div className="mdl-list">
          <div className="mdl-list-h">
            <span>Catalog</span>
            <span className="ct">· {installed.length + blessed.length + userNs.length} shown</span>
            <span className="right">sort: <span style={{color: "var(--fg-2)"}}>installed</span> ▾</span>
          </div>

          {modelsQuery.isPending && (
            <div style={{padding: 16, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>Loading models…</div>
          )}
          {modelsQuery.isError && (
            <div style={{padding: 16, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--err)"}}>
              {modelsQuery.error?.message || "Failed to load models"}
            </div>
          )}

          {installed.length > 0 && <div className="mdl-section-label">Installed · {installed.length}</div>}
          {installed.map(m => (
            <ModelRow key={m.id} model={m} selected={selId === m.id} onSelect={() => setSelId(m.id)} />
          ))}

          {blessed.length > 0 && <div className="mdl-section-label">Available · blessed · {blessed.length}</div>}
          {blessed.map(m => (
            <ModelRow key={m.id} model={m} selected={selId === m.id} onSelect={() => setSelId(m.id)} />
          ))}

          {userNs.length > 0 && <div className="mdl-section-label">user.* · {userNs.length}</div>}
          {userNs.map(m => (
            <ModelRow key={m.id} model={m} selected={selId === m.id} onSelect={() => setSelId(m.id)} />
          ))}
        </div>

        {/* ── Detail + Downloads ── */}
        <div style={{display: "flex", flexDirection: "column", gap: 14}}>
          <ModelDetail
            model={selected}
            onDelete={() => setDelModel(selected)}
            onEdit={() => setRecipeOpen(true)}
            onPullStarted={(id) => setActivePulls(prev => prev.includes(id) ? prev : [...prev, id])}
          />
          <DownloadsPane activeIds={activePulls} onRemove={removeActive} />
        </div>
      </div>

      <AddByHfModal open={addOpen} onClose={() => setAddOpen(false)} />
      <AddByPathModal open={addByPathOpen} onClose={() => setAddByPathOpen(false)} />
      <ScanDirectoryModal open={scanOpen} onClose={() => setScanOpen(false)} />
      <RecipeEditorModal open={recipeOpen} onClose={() => setRecipeOpen(false)} model={selected} />
      <DeleteModelDialog open={!!delModel} onClose={() => setDelModel(null)} model={delModel} />
    </div>
  );
}

function ModelRow({ model, selected, onSelect }) {
  return (
    <div className={"mdl-row" + (selected ? " sel" : "")} onClick={onSelect}>
      <span className={"dot " + (model.installed ? "ready" : "empty")} />
      <span className="nm">
        {model.longName || model.name || model.id}
        <span className="sub">{model.repo || model.hf_repo || ""}</span>
      </span>
      <span className="sz num">{model.params || ""}</span>
      <span className="sz num">{model.size || (model.size_bytes ? fmtBytes(model.size_bytes) : "")}</span>
      <span className="tg">
        {model.installed
          ? <span className="chip ok">installed</span>
          : <span className="chip" style={{color: model.ns === "blessed" ? "var(--accent)" : "var(--fg-3)", borderColor: model.ns === "blessed" ? "var(--accent-line)" : "var(--line)", background: model.ns === "blessed" ? "var(--accent-soft)" : "transparent"}}>{model.ns}</span>}
      </span>
    </div>
  );
}

function ModelDetail({ model, onDelete, onEdit, onPullStarted }) {
  const pull = usePullJob();
  if (!model) {
    return (
      <div className="mdl-detail">
        <div className="mdl-detail-h" style={{padding: 24, color: "var(--fg-4)"}}>No model selected.</div>
      </div>
    );
  }
  // Render the persisted defaults — pydantic ModelDefaults shape:
  // {context_size, n_gpu_layers, rope_freq_base, extra_args}.
  const defaults = model.defaults || {};
  const recipeRows = [
    ["context_size", defaults.context_size],
    ["n_gpu_layers", defaults.n_gpu_layers],
    ["rope_freq_base", defaults.rope_freq_base],
    ["extra_args", defaults.extra_args],
  ].filter(([, v]) => v !== null && v !== undefined && v !== "");

  const onPull = async () => {
    try {
      await pull.start(model.id);
      onPullStarted && onPullStarted(model.id);
      window.dispatchEvent(new CustomEvent("hal0:pull-started", { detail: { modelId: model.id } }));
      window.__hal0Toast && window.__hal0Toast(
        `Pulling ${model.longName || model.id} · ${model.size || fmtBytes(model.size_bytes || 0)}`,
        "info",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Pull failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };

  return (
    <div className="mdl-detail">
      <div className="mdl-detail-h">
        <div style={{display: "flex", alignItems: "center", gap: 10, marginBottom: 6}}>
          <div className={"dot " + (model.installed ? "ready" : "empty")} />
          <div className="nm mono">{model.longName || model.name || model.id}</div>
          <span style={{marginLeft: "auto"}}>
            {model.installed
              ? <span className="chip ok">installed</span>
              : <span className="chip amber">available</span>}
          </span>
        </div>
        <div className="repo">{model.repo || model.hf_repo || model.id}</div>
      </div>
      <div className="mdl-detail-meta">
        <div><div className="k">params</div><div className="v">{model.params || "—"}</div></div>
        <div><div className="k">size</div><div className="v">{model.size || (model.size_bytes ? fmtBytes(model.size_bytes) : "—")}</div></div>
        <div><div className="k">type</div><div className="v">{model.type || (model.capabilities?.[0]) || "—"}</div></div>
        <div><div className="k">device</div><div className="v">{model.device || (model.backends?.[0]) || "—"}</div></div>
        <div><div className="k">runtime</div><div className="v">{model.runtime || "—"}</div></div>
        <div><div className="k">namespace</div><div className="v">{model.ns || "—"}</div></div>
      </div>
      <div className="mdl-detail-labels">
        {(model.labels || model.capabilities || []).map(l => <span key={l} className="chip">{l}</span>)}
      </div>
      <div className="mdl-detail-recipe">
        <div className="lbl">recipe options</div>
        {recipeRows.length === 0 ? (
          <div className="mono" style={{fontSize: 12, color: "var(--fg-4)", fontStyle: "italic"}}>
            No defaults set — launcher will use its own.
          </div>
        ) : recipeRows.map(([k, v]) => (
          <div key={k} className="ro-row">
            <span className="k">{k}</span>
            <span className="v">{String(v)}</span>
          </div>
        ))}
        <div style={{marginTop: 10, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)", display: "flex", gap: 6, alignItems: "center"}}>
          <span style={{color: "var(--warn)"}}>⟳</span>
          <span>context_size + extra_args require slot restart to apply.</span>
        </div>
      </div>
      <UsedByPanel model={model} />
      <OnDiskPanel model={model} />
      <div className="mdl-detail-actions">
        {model.installed ? (
          <>
            <button className="btn" onClick={() => window.__hal0Toast && window.__hal0Toast(`Loading ${model.longName || model.id}…`, "info")}>Load now</button>
            <button className="btn ghost sm" onClick={onEdit}>{Icons.edit} Edit options</button>
            <button className="btn danger sm" onClick={onDelete}>{Icons.unload} Delete</button>
          </>
        ) : (
          <>
            <button className="btn" onClick={onPull} disabled={pull.inFlight}>
              {Icons.download} {pull.inFlight ? `Pulling ${pull.pct ?? 0}%` : `Pull (${model.size || (model.size_bytes ? fmtBytes(model.size_bytes) : "—")})`}
            </button>
            <button className="btn ghost sm" onClick={() => window.open(`https://huggingface.co/${model.repo || model.hf_repo || ""}`, "_blank")}>View on HF →</button>
          </>
        )}
      </div>
    </div>
  );
}

function DownloadsPane({ activeIds, onRemove }) {
  return (
    <div className="mdl-dl">
      <div className="mdl-dl-h">
        <span>Downloads</span>
        <span className="ct mono">{activeIds.length}</span>
      </div>
      {activeIds.length === 0 ? (
        <div style={{padding: "32px 16px", textAlign: "center", color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>
          <div style={{marginBottom: 6}}>No active downloads.</div>
          <div style={{fontSize: 11, color: "var(--fg-5)"}}>Add a model from the catalog or via "Add by HF coords".</div>
        </div>
      ) : (
        activeIds.slice(0, 8).map(id => (
          <DownloadRow key={id} modelId={id} onRemove={onRemove} />
        ))
      )}
    </div>
  );
}

Object.assign(window, { ModelsView, ModelRow, ModelDetail, DownloadsPane });
