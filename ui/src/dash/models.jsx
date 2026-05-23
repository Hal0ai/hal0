// hal0 dashboard — Models view (catalog + detail + downloads)
//
// Phase B1: catalog drives off `useModels()`. Mock fallback retains the
// HAL0_DATA.models shape so dev + mock builds render. Downloads pane
// keeps its local optimistic-state UI; per-row SSE wiring lands when
// the prototype's DownloadRow swaps to `usePullJob(id)` (Phase B2).

import { useModels } from '@/api/hooks/useModels'

const { useState: useStateM } = React;

function ModelsView() {
  const [selId, setSelId] = useStateM("qwen3.6-27b-mtp");
  const [filters, setFilters] = useStateM({ type: null, device: null, ns: null });
  const [addOpen, setAddOpen] = useStateM(false);
  const [delModel, setDelModel] = useStateM(null);

  const modelsQuery = useModels();
  const modelList = (modelsQuery.data && modelsQuery.data.length > 0)
    ? modelsQuery.data
    : HAL0_DATA.models;

  const selected = modelList.find(m => m.id === selId) || modelList[0];

  const fil = m => {
    if (filters.type && m.type !== filters.type) return false;
    if (filters.device && m.device !== filters.device) return false;
    if (filters.ns && m.ns !== filters.ns) return false;
    return true;
  };
  const installed = modelList.filter(m => m.installed && fil(m));
  const blessed = modelList.filter(m => !m.installed && m.ns === "blessed" && fil(m));
  const userNs = modelList.filter(m => m.ns === "pulled" && fil(m));

  const toggle = (k, v) => setFilters(f => ({ ...f, [k]: f[k] === v ? null : v }));

  const recipe = HAL0_DATA.recipe[selId] || HAL0_DATA.recipe["qwen3.6-27b-mtp"];

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Catalog</span>
        <h1>Models</h1>
        <span className="vh-spacer" />
        <button className="btn ghost" onClick={() => window.__hal0Toast && window.__hal0Toast("HF search — stubbed", "info")}>{Icons.search} Search HF</button>
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
          <ModelDetail model={selected} recipe={recipe} onDelete={() => setDelModel(selected)} />
          <DownloadsPane />
        </div>
      </div>

      <AddByHfModal open={addOpen} onClose={() => setAddOpen(false)} />
      <DeleteModelDialog open={!!delModel} onClose={() => setDelModel(null)} model={delModel} />
    </div>
  );
}

function ModelRow({ model, selected, onSelect }) {
  return (
    <div className={"mdl-row" + (selected ? " sel" : "")} onClick={onSelect}>
      <span className={"dot " + (model.installed ? "ready" : "empty")} />
      <span className="nm">
        {model.longName}
        <span className="sub">{model.repo}</span>
      </span>
      <span className="sz num">{model.params}</span>
      <span className="sz num">{model.size}</span>
      <span className="tg">
        {model.installed
          ? <span className="chip ok">installed</span>
          : <span className="chip" style={{color: model.ns === "blessed" ? "var(--accent)" : "var(--fg-3)", borderColor: model.ns === "blessed" ? "var(--accent-line)" : "var(--line)", background: model.ns === "blessed" ? "var(--accent-soft)" : "transparent"}}>{model.ns}</span>}
      </span>
    </div>
  );
}

function ModelDetail({ model, recipe, onDelete }) {
  return (
    <div className="mdl-detail">
      <div className="mdl-detail-h">
        <div style={{display: "flex", alignItems: "center", gap: 10, marginBottom: 6}}>
          <div className={"dot " + (model.installed ? "ready" : "empty")} />
          <div className="nm mono">{model.longName}</div>
          <span style={{marginLeft: "auto"}}>
            {model.installed
              ? <span className="chip ok">installed</span>
              : <span className="chip amber">available</span>}
          </span>
        </div>
        <div className="repo">{model.repo}</div>
      </div>
      <div className="mdl-detail-meta">
        <div><div className="k">params</div><div className="v">{model.params}</div></div>
        <div><div className="k">size</div><div className="v">{model.size}</div></div>
        <div><div className="k">type</div><div className="v">{model.type}</div></div>
        <div><div className="k">device</div><div className="v">{model.device}</div></div>
        <div><div className="k">runtime</div><div className="v">{model.runtime}</div></div>
        <div><div className="k">namespace</div><div className="v">{model.ns}</div></div>
      </div>
      <div className="mdl-detail-labels">
        {model.labels.map(l => <span key={l} className="chip">{l}</span>)}
      </div>
      <div className="mdl-detail-recipe">
        <div className="lbl">recipe options</div>
        {Object.entries(recipe).map(([k, v]) => (
          <div key={k} className="ro-row">
            <span className="k">{k}</span>
            <span className="v">{String(v)}</span>
          </div>
        ))}
        <div style={{marginTop: 10, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)", display: "flex", gap: 6, alignItems: "center"}}>
          <span style={{color: "var(--warn)"}}>⟳</span>
          <span>ctx_size + llamacpp_backend require slot restart to apply.</span>
        </div>
      </div>
      <UsedByPanel model={model} />
      <OnDiskPanel model={model} />
      <div className="mdl-detail-actions">
        {model.installed ? (
          <>
            <button className="btn" onClick={() => window.__hal0Toast && window.__hal0Toast(`Loading ${model.longName}…`, "info")}>Load now</button>
            <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast("Recipe editor — coming next batch", "info")}>{Icons.edit} Edit options</button>
            <button className="btn danger sm" onClick={onDelete}>{Icons.unload} Delete</button>
          </>
        ) : (
          <>
            <button className="btn" onClick={() => window.__hal0Toast && window.__hal0Toast(`Pulling ${model.longName} · ${model.size}`, "info")}>{Icons.download} Pull ({model.size})</button>
            <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Opening huggingface.co/${model.repo}`, "info")}>View on HF →</button>
          </>
        )}
      </div>
    </div>
  );
}

function DownloadsPane() {
  const [downloads, setDownloads] = useStateM(HAL0_DATA.downloads);
  const active = downloads.filter(d => d.state === "pulling" || d.state === "queued" || d.state === "paused" || d.state === "error" || d.state === "cancelled");
  const update = (dl, patch) => setDownloads(ds => ds.map(d => d === dl ? { ...d, ...patch } : d));
  const remove = (dl) => setDownloads(ds => ds.filter(d => d !== dl));
  return (
    <div className="mdl-dl">
      <div className="mdl-dl-h">
        <span>Downloads</span>
        <span className="ct mono">{active.length}</span>
        {active.length > 0 && (
          <span style={{marginLeft: "auto", color: "var(--fg-4)", textTransform: "none", letterSpacing: 0, fontFamily: "var(--jbm)", fontSize: 11}}>~12.4 MB/s</span>
        )}
      </div>
      {active.length === 0 ? (
        <div style={{padding: "32px 16px", textAlign: "center", color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>
          <div style={{marginBottom: 6}}>No active downloads.</div>
          <div style={{fontSize: 11, color: "var(--fg-5)"}}>Add a model from the catalog or via "Add by HF coords".</div>
        </div>
      ) : (
        active.slice(0, 5).map((d, i) => (
          <DownloadRow
            key={i}
            dl={d}
            onPause={(dl) => update(dl, { state: "paused" })}
            onResume={(dl) => update(dl, { state: "pulling" })}
            onCancel={(dl) => update(dl, { state: "cancelled" })}
            onRetry={(dl) => update(dl, { state: "pulling", pct: 0 })}
            onRemove={remove}
          />
        ))
      )}
      <div style={{padding: "10px 16px", display: "flex", gap: 6, borderTop: active.length ? "1px solid var(--line-soft)" : "none"}}>
        <button className="btn ghost sm" style={{flex: 1, justifyContent: "center"}} disabled={!active.length} onClick={() => setDownloads(ds => ds.map(d => d.state === "pulling" ? { ...d, state: "paused" } : d))}>Pause all</button>
        <button className="btn ghost sm" style={{flex: 1, justifyContent: "center"}} onClick={() => window.__hal0Toast && window.__hal0Toast("Full downloads view — coming next batch", "info")}>View all</button>
      </div>
    </div>
  );
}

Object.assign(window, { ModelsView, ModelRow, ModelDetail, DownloadsPane });
