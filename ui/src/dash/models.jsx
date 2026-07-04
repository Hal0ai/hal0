// hal0 dashboard — Models view (catalog + detail + downloads)
//
// Phase B2 wireup (#220 brief): the catalog is driven entirely by
// useModels(); the HAL0_DATA fallback is gone now the backend always
// emits ``ns`` on every row. The detail pane's Recipe section reads
// each model's persisted ``defaults`` and writes them back via PUT
// /api/models/{id}, and the Downloads pane is a thin shell around
// per-row usePullJob() instances tracked by model_id.

import { useModels, usePullJob, useHfSearch, fmtBytes } from '@/api/hooks/useModels'
import { useSlots, useSlotSwap } from '@/api/hooks/useSlots'
import { useMetaEnums } from '@/api/hooks/useMeta'
import { isUpstreamModel } from '@/lib/normalizeApiModel'

const { useState: useStateM, useMemo: useMemoM, useEffect: useEffectM } = React;

function ModelsView() {
  const [selId, setSelId] = useStateM(null);
  const [filters, setFilters] = useStateM({ type: null, device: null });
  const [q, setQ] = useStateM("");
  const [addOpen, setAddOpen] = useStateM(false);
  const [addByPathOpen, setAddByPathOpen] = useStateM(false);
  const [scanOpen, setScanOpen] = useStateM(false);
  const [recipeOpen, setRecipeOpen] = useStateM(false);
  const [delModel, setDelModel] = useStateM(null);
  // Image-gen / ComfyUI models live on their own segment of this view — the
  // dispatcher-routable models (llm/embed/rerank/stt/tts) and the image-gen
  // tree (checkpoints/loras/vae/…) are different worlds and were bleeding into
  // one list (a mis-tagged checkpoint showing under "llm", the "image" filter
  // surfacing nothing). ``tab`` toggles between them.
  const [tab, setTab] = useStateM("models");
  // Issue #311: "Search HF" panel state. ``searchOpen`` toggles the
  // panel; ``searchQ`` is the input; ``searchPick`` is the coord the
  // user clicked "Add" on, which the panel hands off to AddByHfModal
  // via the ``initialRepo`` prop.
  const [searchOpen, setSearchOpen] = useStateM(false);
  const [searchQ, setSearchQ] = useStateM("");
  const [searchPick, setSearchPick] = useStateM("");
  // Track which model_ids the user has launched a pull for this
  // session — the Downloads pane renders one DownloadRow per entry
  // and each row owns its own usePullJob() instance (which reattaches
  // to an in-flight pull on mount).
  const [activePulls, setActivePulls] = useStateM([]);

  const modelsQuery = useModels();
  const modelList = modelsQuery.data ?? [];
  const enums = useMetaEnums();

  // Toolbar chip vocabularies — meta-driven (GET /api/meta/enums, with the
  // static fallback when the endpoint is absent). Type chips are the slot
  // types minus `image` (image models live on the Image/ComfyUI tab); device
  // chips are the legacy backend tokens the model normalizer emits (rocm /
  // vulkan from the GPU devices' legacy_backend, plus npu/cpu device ids).
  const typeChips = useMemoM(
    () => enums.slot_types.filter(t => t !== "image"),
    [enums],
  );
  const deviceChips = useMemoM(
    () => [...new Set(
      enums.devices
        .filter(d => d.device_class !== "img")
        .map(d => d.legacy_backend || d.id),
    )],
    [enums],
  );

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
    if (filters.device) {
      // Match against the full backend set (a model that resolves to device
      // "rocm" may still also run on vulkan/cpu), not just the single
      // best-device the normalizer picked.
      const bes = Array.isArray(m.backends) ? m.backends : [];
      if (!bes.includes(filters.device) && m.device !== filters.device) return false;
    }
    if (q.trim()) {
      const needle = q.trim().toLowerCase();
      const hay = `${m.longName || ""} ${m.name || ""} ${m.id || ""} ${m.repo || ""}`.toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  };
  // A model belongs to the ComfyUI/image surface when the backend flags it
  // (owned_by/backends "comfyui", or a comfyui_category derived from its path)
  // or it classifies as image. Path-derived flags self-heal rows an older pull
  // mis-tagged, so this catches them even when capabilities still say "chat".
  const isComfy = m =>
    m.owned_by === "comfyui" ||
    (Array.isArray(m.backends) && m.backends.includes("comfyui")) ||
    !!m.comfyui_category ||
    m.type === "image";

  // Dispatcher-routable models — the ComfyUI ones are pulled out to their tab.
  // Upstream-advertised rows (aggregated from a provider's /v1/models, never
  // on this host's disk) get their own section instead of masquerading as
  // local not-yet-pulled entries in user.*.
  const installed = modelList.filter(m => m.installed && !isComfy(m) && fil(m));
  const blessed = modelList.filter(m => !m.installed && m.ns === "blessed" && !isComfy(m) && fil(m));
  const userNs = modelList.filter(m => m.ns === "pulled" && !m.installed && !isUpstreamModel(m) && !isComfy(m) && fil(m));
  const upstreamAdv = modelList.filter(m => isUpstreamModel(m) && !isComfy(m) && fil(m));
  const upstreamTotal = modelList.filter(m => isUpstreamModel(m)).length;

  // ComfyUI/image surface — INSTALLED only (we never advertise un-pulled image
  // models, same rule as FLM). Text search applies; the type/device chips do
  // not (they're dispatcher concepts). Grouped by models-tree subdir.
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
  const comfyTotal = modelList.filter(m => m.installed && isComfy(m)).length;

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
        <button className="btn ghost" onClick={() => setSearchOpen(v => !v)}>{Icons.search} Search HF</button>
        <button className="btn ghost" onClick={() => setScanOpen(true)}>{Icons.search} Scan directory</button>
        <button className="btn ghost" onClick={() => setAddByPathOpen(true)}>{Icons.plus} Add by path</button>
        <button className="btn" onClick={() => setAddOpen(true)}>{Icons.plus} Add by HF coords</button>
      </div>

      <div className="models-layout">
        {/* ── List (toolbar + rows) ── */}
        <div className="mdl-list">
          <div className="mdl-toolbar">
            <div className="mdl-toolbar-grp">
              <button
                className={"mdl-chip" + (tab === "models" ? " on" : "")}
                onClick={() => setTab("models")}
              >Models</button>
              <button
                className={"mdl-chip" + (tab === "image" ? " on" : "")}
                onClick={() => setTab("image")}
              >Image / ComfyUI{comfyTotal ? ` · ${comfyTotal}` : ""}</button>
            </div>
            <input
              className="input mono mdl-search"
              placeholder="search name, repo, id…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            {tab === "models" && (
              <>
                <div className="mdl-toolbar-grp">
                  <span className="lbl">type</span>
                  {/* image models moved to the Image/ComfyUI tab — no "image"
                      chip here (it used to match nothing). */}
                  {typeChips.map(t => (
                    <button key={t} className={"mdl-chip" + (filters.type === t ? " on" : "")} onClick={() => toggle("type", t)}>{t}</button>
                  ))}
                </div>
                <div className="mdl-toolbar-grp">
                  <span className="lbl">device</span>
                  {deviceChips.map(d => (
                    <button key={d} className={"mdl-chip" + (filters.device === d ? " on" : "")} onClick={() => toggle("device", d)}>{d}</button>
                  ))}
                </div>
                {(filters.type || filters.device || q.trim()) && (
                  <button className="mdl-chip mdl-clear" onClick={() => { setFilters({ type: null, device: null }); setQ(""); }}>clear ✕</button>
                )}
              </>
            )}
          </div>
          <div className="mdl-list-h">
            <span>{tab === "models" ? "Catalog" : "Image / ComfyUI"}</span>
            <span className="ct">· {tab === "models" ? (installed.length + blessed.length + userNs.length + upstreamAdv.length) : comfyModels.length} shown</span>
            <span className="right mono">{modelList.length} total · {modelList.filter(m => m.installed).length} on disk · {upstreamTotal} upstream · {comfyTotal} image</span>
          </div>

          {modelsQuery.isPending && (
            <div style={{padding: 16, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>Loading models…</div>
          )}
          {modelsQuery.isError && (
            <div style={{padding: 16, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--err)"}}>
              {modelsQuery.error?.message || "Failed to load models"}
            </div>
          )}

          {tab === "models" ? (
            <>
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

              {upstreamAdv.length > 0 && <div className="mdl-section-label">Upstream · remote · {upstreamAdv.length}</div>}
              {upstreamAdv.map(m => (
                <ModelRow key={m.id} model={m} selected={selId === m.id} onSelect={() => setSelId(m.id)} />
              ))}

              {!modelsQuery.isPending && !modelsQuery.isError && (installed.length + blessed.length + userNs.length + upstreamAdv.length) === 0 && (
                <div style={{padding: 24, textAlign: "center", fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)"}}>
                  No models match — {(q.trim() || filters.type || filters.device) ? "adjust the search or filters." : "the catalog is empty."}
                </div>
              )}
            </>
          ) : (
            <>
              {comfyCats.map(cat => (
                <React.Fragment key={cat}>
                  <div className="mdl-section-label">{cat} · {comfyByCat[cat].length}</div>
                  {comfyByCat[cat].map(m => (
                    <ModelRow key={m.id} model={m} selected={selId === m.id} onSelect={() => setSelId(m.id)} />
                  ))}
                </React.Fragment>
              ))}

              {!modelsQuery.isPending && !modelsQuery.isError && comfyModels.length === 0 && (
                <div style={{padding: 24, textAlign: "center", fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)"}}>
                  {q.trim()
                    ? "No image models match the search."
                    : "No image-gen models installed yet — pull one from the ComfyUI catalog."}
                </div>
              )}
            </>
          )}
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

      <AddByHfModal open={addOpen} onClose={() => setAddOpen(false)} initialRepo={searchPick} />
      <AddByPathModal open={addByPathOpen} onClose={() => setAddByPathOpen(false)} />
      <ScanDirectoryModal open={scanOpen} onClose={() => setScanOpen(false)} />
      <RecipeEditorModal open={recipeOpen} onClose={() => setRecipeOpen(false)} model={selected} />
      <DeleteModelDialog open={!!delModel} onClose={() => setDelModel(null)} model={delModel} />

      {/* Issue #311: free-text HF Hub model search panel. Toggled by
          the header "Search HF" button; debounced query against
          /api/hf/search; each row exposes an "Add" affordance that
          opens AddByHfModal with the chosen coord pre-filled. */}
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

// Issue #311: HF search panel — input + result rows. Lives in
// ModelsView's closure so it can read its state. The row click
// pipeline mirrors ModelRow (dot + name + tags + size) and adds an
// "Add" button that closes the panel and hands the coord to
// AddByHfModal via the searchPick state above.
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

function ModelRow({ model, selected, onSelect }) {
  // Backend tags use the unified device palette (.chip.dev-rocm / dev-vulkan /
  // dev-cpu / dev-npu) so a tag here is the same hue as everywhere else on the
  // dash. `type` is the dispatcher vocab derived in normalizeApiModel.
  const backends = Array.isArray(model.backends) ? model.backends : [];
  return (
    <div className={"mdl-row" + (selected ? " sel" : "")} onClick={onSelect}>
      <span className={"dot " + (model.installed ? "ready" : "empty")} />
      <span className="nm">
        {model.longName || model.name || model.id}
        <span className="sub">{model.repo || ""}</span>
      </span>
      <span className="mdl-row-tags">
        {model.type && <span className="chip">{model.type}</span>}
        {backends.map(b => (
          <span key={b} className={"chip dev-" + b}>{b}</span>
        ))}
      </span>
      <span className="sz num">{model.size || (model.size_bytes ? fmtBytes(model.size_bytes) : "")}</span>
      <span className="tg">
        {model.installed
          ? <span className="chip ok">installed</span>
          : isUpstreamModel(model)
            ? <span className="chip info" title={`Advertised by the "${model.upstream}" upstream — not stored on this host`}>upstream</span>
            : <span className="chip" style={{color: model.ns === "blessed" ? "var(--accent)" : "var(--fg-3)", borderColor: model.ns === "blessed" ? "var(--accent-line)" : "var(--line)", background: model.ns === "blessed" ? "var(--accent-soft)" : "transparent"}}>{model.ns}</span>}
      </span>
    </div>
  );
}

function ModelDetail({ model, onDelete, onEdit, onPullStarted }) {
  const pull = usePullJob();
  const slotsQuery = useSlots();
  const swap = useSlotSwap();
  const [cancelling, setCancelling] = useStateM(false);
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

  // Cancel an in-flight pull started from this pane. The hook hits
  // POST /api/models/{id}/pull/cancel and invalidates ['models']; we
  // surface the same error-toast pattern as the other mutations.
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
          <div className={"dot " + (model.installed ? "ready" : "empty")} />
          <div className="nm mono">{model.longName || model.name || model.id}</div>
          <span style={{marginLeft: "auto"}}>
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
        <div><div className="k">type</div><div className="v">{model.type || (model.capabilities?.[0]) || "—"}</div></div>
        <div><div className="k">device</div><div className="v">{model.device || (model.backends?.[0]) || "—"}</div></div>
        <div><div className="k">runtime</div><div className="v">{model.runtime || "—"}</div></div>
        <div><div className="k">namespace</div><div className="v">{model.ns || "—"}</div></div>
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
                // Prefer a slot that already runs this model (re-load),
                // otherwise the first compatible slot. Multiple compatible
                // slots without a current owner: user picks via slot card.
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
          // Upstream-advertised row: nothing to pull — the dispatcher proxies
          // requests to the remote provider. Manage it on the Connections view.
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

Object.assign(window, { ModelsView, ModelRow, ModelDetail, DownloadsPane, HfSearchPanel });
