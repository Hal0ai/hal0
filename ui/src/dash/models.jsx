// hal0 dashboard — Models view (catalog + detail + downloads)
//
// Phase B2 wireup (#220 brief): the catalog is driven entirely by
// useModels(); the HAL0_DATA fallback is gone now the backend always
// emits ``ns`` on every row. The detail pane's Recipe section reads
// each model's persisted ``defaults`` and writes them back via PUT
// /api/models/{id}, and the Downloads pane is a thin shell around
// per-row usePullJob() instances tracked by model_id.

import { useModels, usePullsList, useClearPullJob, usePullJob, useHfSearch, useModelUpdatesCheck, useModelUpdatesForceCheck, useModelUpdateApply, useModelUpdateAll, fmtBytes, fmtSpeed, fmtEta } from '@/api/hooks/useModels'
import { apiPost } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import { useSlots, useSlotSwap } from '@/api/hooks/useSlots'
import { useMetaEnums } from '@/api/hooks/useMeta'
import { isUpstreamModel } from '@/lib/normalizeApiModel'
import { MODEL_SORT_FIELDS, sortModels, fmtAdded } from '@/dash/model-sort.js'

const { useState: useStateM, useMemo: useMemoM, useEffect: useEffectM } = React;

// ── Simplified filter chips ────────────────────────────────────────────
// Each chip is a multi-select toggle with OR semantics (empty = show all).
// "DENSE" means neither MTP nor MOE; checked by absence of those tags.
const FILTER_CHIPS = [
  { id: "mtp",    label: "MTP",   check: m => (m.tags || []).some(t => String(t).toLowerCase() === "mtp") },
  { id: "moe",    label: "MOE",   check: m => (m.tags || []).some(t => String(t).toLowerCase() === "moe") },
  { id: "dense",  label: "DENSE", check: m => !(m.tags || []).some(t => String(t).toLowerCase() === "mtp" || String(t).toLowerCase() === "moe") },
  { id: "embed",  label: "Embed",  check: m => m.type === "embedding" },
  { id: "rerank", label: "Rerank", check: m => m.type === "reranking" },
  { id: "voice",  label: "Voice",  check: m => m.type === "tts" || m.type === "transcription" },
  { id: "vision", label: "Vision", check: m => (m.capabilities || m.labels || []).some(c => c === "vision") },
];

function modelMatchesFilters(m, filterSel) {
  if (filterSel.length === 0) return true;
  return filterSel.some(fid => {
    const def = FILTER_CHIPS.find(c => c.id === fid);
    return def ? def.check(m) : false;
  });
}

// ── Pagination ─────────────────────────────────────────────────────────
const PER_PAGE_OPTS = [10, 25, 50, "all"];

function usePageReset(deps) {
  // Refs so we don't cause extra renders — just track last deps value.
  const ref = React.useRef(null);
  const sig = JSON.stringify(deps);
  const changed = ref.current !== null && ref.current !== sig;
  if (changed) ref.current = sig;
  else if (ref.current === null) ref.current = sig;
  return changed;
}

// ── ModelsView ─────────────────────────────────────────────────────────
function ModelsView() {
  const [selId, setSelId] = useStateM(null);
  // Simplified multi-select OR filters
  const [filterSel, setFilterSel] = useStateM([]);
  // Sort
  const [sortField, setSortField] = useStateM("name");
  const [sortDir, setSortDir] = useStateM("asc");
  const [q, setQ] = useStateM("");
  // Tabs: "inference" (default), "image", "upstream"
  const [tab, setTab] = useStateM("inference");
  // Pagination
  const [page, setPage] = useStateM(1);
  const [perPage, setPerPage] = useStateM(25);
  // Modals
  const [addOpen, setAddOpen] = useStateM(false);
  const [addByPathOpen, setAddByPathOpen] = useStateM(false);
  const [scanOpen, setScanOpen] = useStateM(false);
  const [recipeOpen, setRecipeOpen] = useStateM(false);
  const [delModel, setDelModel] = useStateM(null);
  // HF search
  const [searchOpen, setSearchOpen] = useStateM(false);
  const [searchQ, setSearchQ] = useStateM("");
  const [searchPick, setSearchPick] = useStateM("");


  const modelsQuery = useModels();
  const modelList = modelsQuery.data ?? [];

  // HF update check — the hook triggers the (server-TTL-cached) probe on
  // mount; /api/models then carries per-row `update_available` flags.
  useModelUpdatesCheck();
  const updateAll = useModelUpdateAll();
  const forceCheck = useModelUpdatesForceCheck();
  const onCheckUpdates = async () => {
    try {
      const res = await forceCheck.mutateAsync();
      window.__hal0Toast && window.__hal0Toast(
        res.updates_available > 0
          ? `${res.updates_available} model update${res.updates_available === 1 ? "" : "s"} available`
          : `All ${res.checked} checked model${res.checked === 1 ? "" : "s"} up to date`,
        "info",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Update check failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };
  const updatable = modelList.filter(m => m.installed && m.update_available);
  const onUpdateAll = async () => {
    if (!updatable.length) return;
    try {
      const res = await updateAll.mutateAsync(updatable.map(m => m.id));
      if (res.started.length) {
        window.__hal0Toast && window.__hal0Toast(
          `Updating ${res.started.length} model${res.started.length === 1 ? "" : "s"} from Hugging Face`,
          "info",
        );
      }
      if (res.failed.length) {
        window.__hal0Toast && window.__hal0Toast(
          `${res.failed.length} update${res.failed.length === 1 ? "" : "s"} failed to start — ${res.failed[0].message}`,
          "err",
        );
      }
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Update all failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };

  // Auto-pick the first installed model on first render so the detail
  // pane never opens empty.
  useEffectM(() => {
    if (!selId && modelList.length) {
      const first = modelList.find(m => m.installed) || modelList[0];
      if (first) setSelId(first.id);
    }
  }, [modelList, selId]);

  const selected = modelList.find(m => m.id === selId) || modelList[0];

  // ── isComfy helper ──────────────────────────────────────────────────
  const isComfy = m =>
    m.owned_by === "comfyui" ||
    (Array.isArray(m.backends) && m.backends.includes("comfyui")) ||
    !!m.comfyui_category ||
    m.type === "image";

  // ── Combined filter (text + OR chips) ───────────────────────────────
  const fil = m => {
    if (!modelMatchesFilters(m, filterSel)) return false;
    if (q.trim()) {
      const needle = q.trim().toLowerCase();
      const hay = `${m.longName || ""} ${m.name || ""} ${m.id || ""} ${m.repo || ""}`.toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  };

  const bySort = rows => sortModels(rows, sortField, sortDir);

  // ── Tab datasets ────────────────────────────────────────────────────
  // Inference: installed + blessed + user.* (not upstream, not comfy)
  const installed = modelList.filter(m => m.installed && !isComfy(m) && !isUpstreamModel(m) && fil(m));
  const blessed = modelList.filter(m => !m.installed && m.ns === "blessed" && !isComfy(m) && !isUpstreamModel(m) && fil(m));
  const userNs = modelList.filter(m => m.ns === "pulled" && !m.installed && !isUpstreamModel(m) && !isComfy(m) && fil(m));
  const inferenceRows = [...bySort(installed), ...bySort(blessed), ...bySort(userNs)];

  // Upstream: only isUpstreamModel, fil already excludes comfy+non-upstream
  const upstreamAll = modelList.filter(m => isUpstreamModel(m) && !isComfy(m) && fil(m));
  const upstreamRows = bySort(upstreamAll);
  const upstreamTotal = modelList.filter(m => isUpstreamModel(m)).length;

  // Image: ComfyUI installed, text search only (no chip filters)
  const comfySearch = m => {
    if (!q.trim()) return true;
    const needle = q.trim().toLowerCase();
    const hay = `${m.longName || ""} ${m.name || ""} ${m.id || ""} ${m.repo || ""} ${m.comfyui_category || ""}`.toLowerCase();
    return hay.includes(needle);
  };
  const comfyModels = modelList.filter(m => m.installed && isComfy(m) && comfySearch(m));
  const comfyByCat = {};
  for (const m of comfyModels) {
    const cat = m.comfyui_category || "other";
    (comfyByCat[cat] = comfyByCat[cat] || []).push(m);
  }
  const comfyCats = Object.keys(comfyByCat).sort();
  // Flatten sorted for pagination
  const comfyRows = [];
  for (const cat of comfyCats) comfyRows.push(...bySort(comfyByCat[cat]));
  const comfyTotal = modelList.filter(m => m.installed && isComfy(m)).length;

  // ── Pagination logic ────────────────────────────────────────────────
  const activeRows = tab === "inference" ? inferenceRows
    : tab === "upstream" ? upstreamRows
    : comfyRows;

  const pageReset = usePageReset([tab, q, sortField, sortDir, filterSel]);
  useEffectM(() => { if (pageReset) setPage(1); }, [pageReset]);

  const totalPages = perPage === "all" ? 1 : Math.max(1, Math.ceil(activeRows.length / perPage));
  const safePage = Math.min(page, totalPages);
  const sliced = perPage === "all"
    ? activeRows
    : activeRows.slice((safePage - 1) * perPage, safePage * perPage);

  // Section labels for inference tab — re-derive from sliced set so
  // we only show labels for sections that have visible rows.
  const slicedInstalled = sliced.filter(m => m.installed);
  const slicedBlessed = sliced.filter(m => !m.installed && m.ns === "blessed");
  const slicedUserNs = sliced.filter(m => !m.installed && m.ns === "pulled");
  // Build sectioned rows preserving original slice order
  const sectionedInference = [];
  if (slicedInstalled.length) {
    sectionedInference.push({ type: "label", text: `Installed · ${installed.length}`, key: "lbl-installed" });
    for (const m of slicedInstalled) sectionedInference.push({ type: "row", model: m });
  }
  if (slicedBlessed.length) {
    sectionedInference.push({ type: "label", text: `Available · blessed · ${blessed.length}`, key: "lbl-blessed" });
    for (const m of slicedBlessed) sectionedInference.push({ type: "row", model: m });
  }
  if (slicedUserNs.length) {
    sectionedInference.push({ type: "label", text: `user.* · ${userNs.length}`, key: "lbl-userns" });
    for (const m of slicedUserNs) sectionedInference.push({ type: "row", model: m });
  }

  const pullsList = usePullsList();

  // ── Render ──────────────────────────────────────────────────────────
  const tabLabel = tab === "inference" ? `Inference Models${inferenceRows.length ? ` · ${inferenceRows.length}` : ""}`
    : tab === "upstream" ? `Upstream Models${upstreamTotal ? ` · ${upstreamTotal}` : ""}`
    : `Image / ComfyUI${comfyTotal ? ` · ${comfyTotal}` : ""}`;

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Catalog</span>
        <h1>Models</h1>
        <span className="vh-spacer" />
        {updatable.length > 0 ? (
          <button
            className="btn"
            data-testid="mdl-update-all"
            disabled={updateAll.isPending}
            title="Re-pull every installed model whose HuggingFace file has changed"
            onClick={onUpdateAll}
          >{Icons.download} {updateAll.isPending ? "Starting…" : `Update all (${updatable.length})`}</button>
        ) : (
          /* Everything current → the update surface would otherwise be
             invisible. Keep an explicit check affordance so the feature is
             discoverable and the TTL cache can be bypassed on demand. */
          <button
            className="btn ghost"
            data-testid="mdl-check-updates"
            disabled={forceCheck.isPending}
            title="Compare every installed model against its HuggingFace repo now"
            onClick={onCheckUpdates}
          >{Icons.download} {forceCheck.isPending ? "Checking…" : "Check updates"}</button>
        )}
        <button className="btn ghost" onClick={() => setSearchOpen(v => !v)}>{Icons.search} Search HF</button>
        <button className="btn ghost" onClick={() => setScanOpen(true)}>{Icons.search} Scan directory</button>
        <button className="btn ghost" onClick={() => setAddByPathOpen(true)}>{Icons.plus} Add by path</button>
        <button className="btn" onClick={() => setAddOpen(true)}>{Icons.plus} Add by HF coords</button>
      </div>

      {/* ── Tab bar (matches slot-tabs pattern) ── */}
      <div className="slot-tabs" role="tablist" style={{marginBottom: 0}}>
        <button
          role="tab"
          aria-selected={tab === "inference"}
          className={"slot-tab" + (tab === "inference" ? " on" : "")}
          onClick={() => setTab("inference")}
        >
          <span>Inference Models</span>
          <span className="slot-tab-ct num">{inferenceRows.length}</span>
        </button>
        <button
          role="tab"
          aria-selected={tab === "image"}
          className={"slot-tab comfy" + (tab === "image" ? " on" : "")}
          onClick={() => setTab("image")}
        >
          <span>Image / ComfyUI</span>
          <span className="slot-tab-ct num">{comfyTotal}</span>
        </button>
        <button
          role="tab"
          aria-selected={tab === "upstream"}
          className={"slot-tab" + (tab === "upstream" ? " on" : "")}
          onClick={() => setTab("upstream")}
        >
          <span>Upstream Models</span>
          <span className="slot-tab-ct num">{upstreamTotal}</span>
        </button>
      </div>

      <div className="models-layout" style={{marginTop: 18}}>
        {/* ── List (toolbar + rows) ── */}
        <div className="mdl-list">
          {/* Toolbar: search + simplified filter chips + sort + clear */}
          <div className="mdl-toolbar">
            <input
              className="input mono mdl-search"
              placeholder="search name, repo, id…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            {tab !== "image" && (
              <div className="mdl-toolbar-grp">
                <span className="lbl">filter</span>
                {FILTER_CHIPS.map(c => (
                  <button
                    key={c.id}
                    className={"mdl-chip" + (filterSel.includes(c.id) ? " on" : "")}
                    data-testid={`mdl-filter-${c.id}`}
                    onClick={() => setFilterSel(s => s.includes(c.id) ? s.filter(x => x !== c.id) : [...s, c.id])}
                  >{c.label}</button>
                ))}
                {(filterSel.length > 0 || q.trim()) && (
                  <button className="mdl-chip mdl-clear" onClick={() => { setFilterSel([]); setQ(""); }}>clear ✕</button>
                )}
              </div>
            )}
            <div className="mdl-toolbar-grp mdl-sort-grp">
              <select
                className="input mono mdl-sort"
                data-testid="mdl-sort-field"
                value={sortField}
                onChange={e => setSortField(e.target.value)}
              >
                {MODEL_SORT_FIELDS.map(f => <option key={f.id} value={f.id}>{f.label}</option>)}
              </select>
              <button
                className="mdl-chip mdl-sort-dir"
                data-testid="mdl-sort-dir"
                title={sortDir === "asc" ? "ascending" : "descending"}
                onClick={() => setSortDir(d => d === "asc" ? "desc" : "asc")}
              >{sortDir === "asc" ? "↑" : "↓"}</button>
            </div>
          </div>

          {/* Header row */}
          <div className="mdl-list-h">
            <span>{tabLabel}</span>
            <span className="ct">· {activeRows.length} shown</span>
            <span className="right mono">{modelList.length} total · {modelList.filter(m => m.installed).length} on disk · {upstreamTotal} upstream · {comfyTotal} image</span>
          </div>

          {/* Loading / error */}
          {modelsQuery.isPending && (
            <div style={{padding: 16, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>Loading models…</div>
          )}
          {modelsQuery.isError && (
            <div style={{padding: 16, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--err)"}}>
              {modelsQuery.error?.message || "Failed to load models"}
            </div>
          )}

          {/* Rows */}
          {!modelsQuery.isPending && !modelsQuery.isError && activeRows.length === 0 && (
            <div style={{padding: 24, textAlign: "center", fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)"}}>
              {q.trim() || filterSel.length
                ? "No models match — adjust the search or filters."
                : tab === "upstream" ? "No upstream models advertised." : "the catalog is empty."}
            </div>
          )}

          {tab === "inference" ? (
            sectionedInference.map(item =>
              item.type === "label"
                ? <div key={item.key} className="mdl-section-label">{item.text}</div>
                : <ModelRow key={item.model.id} model={item.model} selected={selId === item.model.id} onSelect={() => setSelId(item.model.id)} />
            )
          ) : tab === "image" ? (
            sliced.map(m => (
              <ModelRow key={m.id} model={m} selected={selId === m.id} onSelect={() => setSelId(m.id)} />
            ))
          ) : (
            /* upstream tab — flat paginated rows */
            sliced.map(m => (
              <ModelRow key={m.id} model={m} selected={selId === m.id} onSelect={() => setSelId(m.id)} />
            ))
          )}

          {/* Pagination footer */}
          <div className="mdl-pager">
            <div className="mdl-pager-pages">
              <button
                className="mdl-chip"
                disabled={safePage <= 1}
                onClick={() => setPage(p => Math.max(1, p - 1))}
              >← Prev</button>
              <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
                {perPage === "all" ? "1/1" : `${safePage}/${totalPages}`}
              </span>
              <button
                className="mdl-chip"
                disabled={safePage >= totalPages}
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              >Next →</button>
            </div>
            <div className="mdl-pager-size">
              <span className="lbl">per page</span>
              {PER_PAGE_OPTS.map(opt => (
                <button
                  key={String(opt)}
                  className={"mdl-chip" + (perPage === opt ? " on" : "")}
                  onClick={() => { setPerPage(opt); setPage(1); }}
                >{opt === "all" ? "All" : opt}</button>
              ))}
            </div>
          </div>
        </div>

        {/* ── Detail + Downloads ── */}
        <div className="models-sidebar">
          <ModelDetail
            model={selected}
            onDelete={() => setDelModel(selected)}
            onEdit={() => setRecipeOpen(true)}
          />
          <DownloadsPane />
        </div>
      </div>

      <AddByHfModal open={addOpen} onClose={() => setAddOpen(false)} initialRepo={searchPick} />
      <AddByPathModal open={addByPathOpen} onClose={() => setAddByPathOpen(false)} />
      <ScanDirectoryModal open={scanOpen} onClose={() => setScanOpen(false)} />
      <ModelDrawer open={recipeOpen} onClose={() => setRecipeOpen(false)} model={selected} />
      <DeleteModelDialog open={!!delModel} onClose={() => setDelModel(null)} model={delModel} />

      {searchOpen && (
        <HfSearchPanel
          q={searchQ}
          onQ={setSearchQ}
          onPick={(repo) => { setSearchPick(repo); setSearchOpen(false); setAddOpen(true); }}
          onClose={() => { setSearchOpen(false); setSearchQ(""); }}
        />
      )}
    </div>
  );
}

// ── HF Search Panel ───────────────────────────────────────────────────
function HfSearchPanel({ q, onQ, onPick, onClose }) {
  const search = useHfSearch(q);
  const rows = search.data?.results ?? [];
  return (
    <div className="hf-search-backdrop" onClick={onClose}>
      <div className="hf-search-panel" onClick={(e) => e.stopPropagation()}>
        <div className="hf-search-h">
          <span className="mono" style={{color: "var(--fg-3)", fontSize: 11}}>huggingface.co · search</span>
          <span className="vh-spacer" />
          <button className="mdl-chip" onClick={onClose}>close ✕</button>
        </div>
        <input
          className="input mono hf-search-input"
          autoFocus
          placeholder="search HuggingFace models — e.g. qwen3 8b gguf, bge embed, kokoro tts…"
          value={q}
          onChange={(e) => onQ(e.target.value)}
        />
        {q.trim() === "" && (
          <div className="hf-search-empty">Type a query to search the HF Hub.</div>
        )}
        {q.trim() !== "" && search.isPending && (
          <div className="hf-search-empty">Searching…</div>
        )}
        {q.trim() !== "" && search.isError && (
          <div className="hf-search-empty err">Search failed — {search.error?.message || "unreachable"}</div>
        )}
        {q.trim() !== "" && !search.isPending && !search.isError && rows.length === 0 && (
          <div className="hf-search-empty">No results.</div>
        )}
        {rows.length > 0 && (
          <div className="hf-search-list">
            {rows.map((r) => (
              <div key={r.id} className="hf-search-row">
                <span className={"dot " + (r.gated ? "empty" : "ready")} />
                <span className="nm">
                  {r.id}
                  <span className="sub">
                    {r.pipeline_tag || "—"}
                    {r.library ? ` · ${r.library}` : ""}
                    {" · "}
                    {Intl.NumberFormat().format(r.downloads || 0)} ↓
                    {" · "}
                    {r.likes || 0} ♥
                    {r.gated ? " · gated" : ""}
                  </span>
                </span>
                <span className="vh-spacer" />
                <button className="btn ghost sm" onClick={() => onPick(r.id)}>{Icons.plus} Add</button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── ModelRow ──────────────────────────────────────────────────────────
function ModelRow({ model, selected, onSelect }) {
  const backends = Array.isArray(model.backends) ? model.backends : [];
  return (
    <div className={"mdl-row" + (selected ? " sel" : "")} onClick={onSelect}>
      <span className="mdl-row-icon" data-testid={model.installed ? "mdl-row-installed" : "mdl-row-not-installed"}>
        {model.installed
          ? <span style={{color: "var(--green)", display: "inline-flex"}}>{Icons.download}</span>
          : <span style={{color: "var(--fg-5)", display: "inline-flex"}}>{Icons.download}</span>}
      </span>
      <span className="nm">
        {model.longName || model.name || model.id}
        <span className="sub">{model.repo || ""}</span>
      </span>
      <span className="mdl-row-tags">
        {model.type && <span className="chip">{model.type}</span>}
        {model.quant && <span className="chip quant" data-testid="mdl-row-quant">{model.quant}</span>}
        {backends.map(b => (
          <span key={b} className={"chip dev-" + b}>{b}</span>
        ))}
      </span>
      <span className="sz num">{model.size || (model.size_bytes ? fmtBytes(model.size_bytes) : "")}</span>
      <span className="tg">
        {isUpstreamModel(model)
          ? <span className="chip info" title={`Advertised by the "${model.upstream}" upstream — not stored on this host`}>upstream</span>
          : !model.installed
            ? <span className="chip" style={{color: model.ns === "blessed" ? "var(--accent)" : "var(--fg-3)", borderColor: model.ns === "blessed" ? "var(--accent-line)" : "var(--line)", background: model.ns === "blessed" ? "var(--accent-soft)" : "transparent"}}>{model.ns}</span>
            : model.update_available
              ? <span className="chip amber" data-testid="mdl-row-update" title="A newer version of this file is available on Hugging Face">update ↑</span>
              : null}
      </span>
    </div>
  );
}

// ── ModelDetail ───────────────────────────────────────────────────────
function ModelDetail({ model, onDelete, onEdit, onPullStarted }) {
  const pull = usePullJob();
  const slotsQuery = useSlots();
  const swap = useSlotSwap();
  const hfUpdate = useModelUpdateApply();
  const [cancelling, setCancelling] = useStateM(false);
  if (!model) {
    return (
      <div className="mdl-detail">
        <div className="mdl-detail-h" style={{padding: 24, color: "var(--fg-4)"}}>No model selected.</div>
      </div>
    );
  }
  const defaults = model.defaults || {};
  const recipeRows = [
    ["preferred_profile", defaults.profile],
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

  const onCancelPull = async () => {
    setCancelling(true);
    try {
      await pull.cancel();
      window.__hal0Toast && window.__hal0Toast(
        `Cancelled pull · ${model.longName || model.id}`, "info",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Cancel failed — ${e?.message || "see logs"}`, "err",
      );
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className="mdl-detail">
      <div className="mdl-detail-h">
        <div style={{display: "flex", alignItems: "center", gap: 10, marginBottom: 6}}>
          <span style={{display: "inline-flex"}}>
            {model.installed
              ? <span style={{color: "var(--green)"}}>{Icons.download}</span>
              : <span style={{color: "var(--fg-5)"}}>{Icons.download}</span>}
          </span>
          <div className="nm mono">{model.longName || model.name || model.id}</div>
          <span style={{marginLeft: "auto", display: "inline-flex", gap: 6}}>
            {model.installed && model.update_available && (
              <span className="chip amber" title="A newer version of this file is available on Hugging Face">update available</span>
            )}
            {model.installed
              ? <span className="chip ok">installed</span>
              : isUpstreamModel(model)
                ? <span className="chip info">upstream · {model.upstream}</span>
                : <span className="chip amber">available</span>}
          </span>
        </div>
        <div className="repo">{model.repo || model.hf_repo || model.id}</div>
      </div>
      <div className="mdl-detail-meta">
        <div><div className="k">params</div><div className="v">{model.params || "—"}</div></div>
        <div><div className="k">size</div><div className="v">{model.size || (model.size_bytes ? fmtBytes(model.size_bytes) : "—")}</div></div>
        <div><div className="k">quant</div><div className="v" data-testid="mdl-detail-quant">{model.quant || "—"}</div></div>
        <div><div className="k">type</div><div className="v">{model.type || (model.capabilities?.[0]) || "—"}</div></div>
        <div><div className="k">device</div><div className="v">{model.device || (model.backends?.[0]) || "—"}</div></div>
        <div><div className="k">runtime</div><div className="v">{model.runtime || "—"}</div></div>
        <div><div className="k">namespace</div><div className="v">{model.ns || "—"}</div></div>
        <div><div className="k">added</div><div className="v">{fmtAdded(model.created)}</div></div>
        <div><div className="k">origin</div><div className="v">{isUpstreamModel(model) ? `upstream · ${model.upstream}` : "local"}</div></div>
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
            {model.update_available && (
              <button
                className="btn"
                data-testid="mdl-detail-update"
                disabled={hfUpdate.isPending}
                title="Re-pull the newer file from Hugging Face over the installed one"
                onClick={async () => {
                  try {
                    await hfUpdate.mutateAsync(model.id);
                    window.__hal0Toast && window.__hal0Toast(
                      `Updating ${model.longName || model.id} from ${model.hf_repo || "Hugging Face"}`,
                      "info",
                    );
                  } catch (e) {
                    window.__hal0Toast && window.__hal0Toast(
                      `Update failed — ${e?.message || "see logs"}`, "err",
                    );
                  }
                }}
              >{Icons.download} {hfUpdate.isPending ? "Starting…" : "Update"}</button>
            )}
            <button
              className="btn"
              disabled={swap.isPending}
              onClick={async () => {
                const slots = slotsQuery.data ?? [];
                const compatible = slots.filter(s => s.type === model.type);
                if (compatible.length === 0) {
                  window.__hal0Toast && window.__hal0Toast(
                    `No slot accepts type=${model.type || "?"} — create one in Slots`,
                    "err",
                  );
                  return;
                }
                const owning = compatible.find(
                  s => (s.model_id || s.model?.default) === model.id,
                );
                if (!owning && compatible.length > 1) {
                  window.__hal0Toast && window.__hal0Toast(
                    `Multiple compatible slots — use the slot card swap dropdown to pick one`,
                    "warn",
                  );
                  return;
                }
                const target = owning || compatible[0];
                try {
                  await swap.mutateAsync({ name: target.name, model_id: model.id });
                  window.__hal0Toast && window.__hal0Toast(
                    `Loading ${model.longName || model.id} → slot ${target.name}`,
                    "info",
                  );
                } catch (e) {
                  window.__hal0Toast && window.__hal0Toast(
                    `Load failed — ${e?.message || "see logs"}`,
                    "err",
                  );
                }
              }}
            >{swap.isPending ? "Loading…" : "Load now"}</button>
            <button className="btn ghost sm" onClick={onEdit}>{Icons.edit} Edit options</button>
            <button className="btn danger sm" onClick={onDelete}>{Icons.unload} Delete</button>
          </>
        ) : isUpstreamModel(model) ? (
          <div className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
            Served remotely by the <span className="chip info">{model.upstream}</span> upstream — not stored on this host.
          </div>
        ) : (
          <>
            <button className="btn" onClick={onPull} disabled={pull.inFlight}>
              {Icons.download} {pull.inFlight ? `Pulling ${pull.pct ?? 0}%` : `Pull (${model.size || (model.size_bytes ? fmtBytes(model.size_bytes) : "—")})`}
            </button>
            {pull.inFlight && (
              <button className="btn ghost sm" onClick={onCancelPull} disabled={cancelling}>
                {cancelling ? "Cancelling…" : "Cancel"}
              </button>
            )}
            <button className="btn ghost sm" onClick={() => window.open(`https://huggingface.co/${model.hf_repo || model.repo || ""}`, "_blank")}>View on HF →</button>
          </>
        )}
      </div>
    </div>
  );
}

// ── DownloadRow ───────────────────────────────────────────────────────
// One row in the Downloads pane. Owns its own cancelling state via
// useStateM — must be its own component because it is rendered from a
// list (jobs.map). Calling a hook inside a .map() callback causes
// "Rendered more hooks than during the previous render" (React #310)
// the moment the job list changes length.
function DownloadRow({ job, clearJob }) {
  const j = job;
  const pct = j.bytes_total ? Math.round((j.bytes_downloaded / (j.bytes_total || 1)) * 100) : 0;
  const isRunning = j.state === 'running';
  const isQueued = j.state === 'queued';
  const isCompleted = j.state === 'completed';
  const isFailed = j.state === 'failed';
  const isCancelled = j.state === 'cancelled';
  const [cancelling, setCancelling] = useStateM(false);
  const doCancel = async () => {
    setCancelling(true);
    try {
      await apiPost(ENDPOINTS.modelPullCancel(j.model_id));
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Cancel failed — ${e?.message || "see logs"}`, "err",
      );
    } finally {
      setCancelling(false);
    }
  };
  return (
    <div style={{padding: "12px 16px", borderBottom: "1px solid var(--line-soft)"}}>
      <div style={{display: "flex", justifyContent: "space-between", fontFamily: "var(--jbm)", fontSize: 11.5, marginBottom: 6, alignItems: "center", gap: 8}}>
        <span style={{
          color: isCompleted ? "var(--ok)" : isCancelled ? "var(--fg-4)" : isFailed ? "var(--err)" : "var(--fg)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1,
        }}>
          {j.hf_repo || j.model_id}
          {j.dest_path && <span style={{color: "var(--fg-4)", marginLeft: 6, fontSize: 10}}>→ {j.dest_path}</span>}
        </span>
        <span style={{color: isCompleted ? "var(--ok)" : isFailed ? "var(--err)" : "var(--fg-3)", fontSize: 11}}>
          {isRunning && `${pct}%`}
          {isQueued && "queued"}
          {isCompleted && "✓ done"}
          {isFailed && "failed"}
          {isCancelled && "cancelled"}
        </span>
      </div>
      {!isCompleted && !isFailed && !isCancelled && (
        <div className="dl-bar" style={{height: 4, marginBottom: 4}}>
          <i style={{width: `${pct}%`, background: "var(--accent)"}} />
        </div>
      )}
      {isRunning && (
        <div style={{display: "flex", justifyContent: "space-between", fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", marginBottom: 4}}>
          <span>{fmtBytes(j.bytes_downloaded)} / {fmtBytes(j.bytes_total)}</span>
          <span>{fmtSpeed(j.speed_bps)} · {fmtEta(j.eta_s)}</span>
        </div>
      )}
      {isFailed && j.error && (
        <div style={{marginBottom: 6, padding: "6px 10px", background: "var(--err-soft)", border: "1px solid var(--err-line)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11, color: "var(--err)"}}>
          {j.error.message || "Download failed"}
        </div>
      )}
      <div style={{display: "flex", gap: 4}}>
        {(isRunning || isQueued) && (
          <button className="btn ghost sm" onClick={doCancel} disabled={cancelling}>
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        )}
        {isFailed && (
          <>
            <button className="btn ghost sm" onClick={() => apiPost(ENDPOINTS.modelPull(j.model_id))}>↻ Retry</button>
            <button className="btn ghost sm" onClick={() => clearJob.mutate(j.model_id)}>Clear</button>
          </>
        )}
        {isCompleted && (
          <button className="btn ghost sm" onClick={() => clearJob.mutate(j.model_id)}>Dismiss</button>
        )}
        {isCancelled && (
          <button className="btn ghost sm" onClick={() => clearJob.mutate(j.model_id)}>Clear</button>
        )}
      </div>
    </div>
  );
}

// ── DownloadsPane ─────────────────────────────────────────────────────
function DownloadsPane() {
  const { jobs } = usePullsList();
  const clearJob = useClearPullJob();
  return (
    <div className="mdl-dl">
      <div className="mdl-dl-h">
        <span>Downloads</span>
        <span className="ct mono">{jobs.length}</span>
      </div>
      {jobs.length === 0 ? (
        <div style={{padding: "32px 16px", textAlign: "center", color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>
          <div style={{marginBottom: 6}}>No active downloads.</div>
          <div style={{fontSize: 11, color: "var(--fg-5)"}}>Add a model from the catalog or via "Add by HF coords".</div>
        </div>
      ) : (
        jobs.map((j) => (
          <DownloadRow key={j.job_id || j.model_id} job={j} clearJob={clearJob} />
        ))
      )}
    </div>
  );
}

Object.assign(window, { ModelsView, ModelRow, ModelDetail, DownloadsPane, DownloadRow, HfSearchPanel });
