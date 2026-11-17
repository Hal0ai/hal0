// hal0 dashboard — Models view (catalog + detail + downloads)
//
// Phase B2 wireup (#220 brief): the catalog is driven entirely by
// useModels(); the HAL0_DATA fallback is gone now the backend always
// emits ``ns`` on every row. The detail pane's Recipe section reads
// each model's file facts (top-level fields) and writes them back via PUT
// /api/models/{id}, and the Downloads pane is a thin shell around
// per-row usePullJob() instances tracked by model_id.

import { useModels, usePullsList, usePullJob, useHfSearch, useModelUpdatesCheck, useModelUpdatesForceCheck, useModelUpdateApply, useModelUpdateAll, fmtBytes } from '@/api/hooks/useModels'
import { useSlots, useSlotSwap } from '@/api/hooks/useSlots'
import { useMetaEnums } from '@/api/hooks/useMeta'
import { isUpstreamModel } from '@/lib/normalizeApiModel'
import { useComfyuiFiles } from '@/api/hooks/useComfyui'
import { ImageFilesTab } from '@/dash/image/ImageFilesTab.jsx'
import { FlmModelsTab } from '@/dash/FlmModelsTab.jsx'
import { useFlmCatalog } from '@/api/hooks/useFlmCatalog'
import { flmCatalogRows } from '@/dash/npu/npu-models-pure'
import { imageFileCount } from '@/dash/image/image-files-pure'
import { draftVariantLabel, isDrafterModel } from '@/dash/spec-state.js'
import { MODEL_SORT_FIELDS, sortModels, fmtAdded, isMtpModel, isMoeModel } from '@/dash/model-sort.js'
import { DownloadRow, DownloadsPane } from '@/dash/models/DownloadsPane.jsx'
import { useXdnaFit } from '@/api/hooks/useXdnaFit'
import { useNpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import { npuTabVisible } from '@/dash/npu/npu-pure'
import {
  CatalogHeaderActions, CatalogList, CatalogRow, CatalogToolbar, DetailActions, DetailCard, DetailEmpty,
  DetailFacts, DetailHeader, DetailLine, DetailNone, DetailSection, StatusChip, toggleIn,
} from '@/dash/models/catalog-shell.jsx'
import {
  RUNS_ON_FILTERS, STATE_FILTERS, catalogStatus, matchesStateFilter, npuFitOf, pageSections, runsOn, slotsUsing,
} from '@/dash/models/catalog-pure'

const { useState: useStateM, useMemo: useMemoM, useEffect: useEffectM } = React;

// ── Simplified filter chips ────────────────────────────────────────────
// Each chip is a multi-select toggle with OR semantics (empty = show all).
// "DENSE" means neither MTP nor MOE; checked by absence of those tags.
// isMtpModel/isMoeModel live in model-sort.js (dependency-free, unit
// tested) — they mirror the backend's own MTP/MoE detection so the chips
// key on the same signal the server acts on, not on `Model.architecture`
// (never persisted on a registry row — #1649) or the retired `moe` tag.
const FILTER_CHIPS = [
  { id: "mtp",    label: "MTP",   check: isMtpModel },
  { id: "drafter", label: "Drafter", check: isDrafterModel },
  { id: "moe",    label: "MOE",   check: isMoeModel },
  { id: "dense",  label: "DENSE", check: m => !isMtpModel(m) && !isMoeModel(m) },
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
const MODEL_TABS = ["inference", "image", "upstream", "flm", "profiles"];
const toastM = (msg, kind) => window.__hal0Toast && window.__hal0Toast(msg, kind);
// `modelParam` mirrors SlotsView's slotParam: #models/profiles deep-links
// the Profiles tab (runner-catalogue-v2 — Profiles moved here from Slots).
function ModelsView({ modelParam = null }) {
  const [selId, setSelId] = useStateM(null);
  // Three chip groups, each multi-select OR; a row must pass every group.
  const [filterSel, setFilterSel] = useStateM([]);
  const [stateSel, setStateSel] = useStateM([]);
  const [runsSel, setRunsSel] = useStateM([]);
  // Sort
  const [sortField, setSortField] = useStateM("name");
  const [sortDir, setSortDir] = useStateM("asc");
  const [q, setQ] = useStateM("");
  // Tabs: "inference" (default), "image", "upstream", "flm", "profiles". Each is
  // hash-routed (#models/<tab>) so the sidebar sub-links track it; bare
  // #models or an unknown param shows Inference (same idiom as SlotsView).
  const tabFromParam = (p) => (MODEL_TABS.includes(p) ? p : "inference");
  const [tab, setTab] = useStateM(tabFromParam(modelParam));
  useEffectM(() => { setTab(tabFromParam(modelParam)); }, [modelParam]);
  // Pagination
  const [page, setPage] = useStateM(1);
  const [perPage, setPerPage] = useStateM(25);
  // Modals
  const [addOpen, setAddOpen] = useStateM(false);
  const [addByPathOpen, setAddByPathOpen] = useStateM(false);
  const [scanOpen, setScanOpen] = useStateM(false);
  // Model whose edit drawer is open — deliberately decoupled from the row
  // list's `selId`/`selected` so the row-menu kebab's "Edit model settings"
  // can target ANY row (selected or not) without first mutating catalog
  // selection, and so re-selecting a different row while the drawer is open
  // never silently swaps the drawer's target out from under an in-progress
  // edit (issue: the old `model={selected}` binding was reactive to selId).
  const [recipeModel, setRecipeModel] = useStateM(null);
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
      toastM(
        res.updates_available > 0
          ? `${res.updates_available} model update${res.updates_available === 1 ? "" : "s"} available`
          : `All ${res.checked} checked model${res.checked === 1 ? "" : "s"} up to date`,
        "info",
      );
    } catch (e) {
      toastM(`Update check failed — ${e?.message || "see logs"}`, "err");
    }
  };
  const updatable = modelList.filter(m => m.installed && m.update_available);
  const onUpdateAll = async () => {
    if (!updatable.length) return;
    try {
      const res = await updateAll.mutateAsync(updatable.map(m => m.id));
      if (res.started.length) {
        toastM(`Updating ${res.started.length} model${res.started.length === 1 ? "" : "s"} from Hugging Face`, "info");
      }
      if (res.failed.length) {
        toastM(`${res.failed.length} update${res.failed.length === 1 ? "" : "s"} failed to start — ${res.failed[0].message}`, "err");
      }
    } catch (e) {
      toastM(`Update all failed — ${e?.message || "see logs"}`, "err");
    }
  };
  // Upstream rows have no file to compare: checking them is asking the upstreams
  // again which models they advertise.
  const [recheckingUpstream, setRecheckingUpstream] = useStateM(false);
  const onRecheckUpstream = async () => {
    setRecheckingUpstream(true);
    try {
      const res = await modelsQuery.refetch();
      if (res.isError) throw res.error;
      const n = (res.data ?? []).filter(isUpstreamModel).length;
      toastM(`${n} upstream model${n === 1 ? "" : "s"} advertised`, "info");
    } catch (e) {
      toastM(`Could not re-read the upstream models — ${e?.message || "see logs"}`, "err");
    } finally {
      setRecheckingUpstream(false);
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

  // ── What each row's status is derived from ──────────────────────────
  // Who uses a model (slots), whether a pull for it is running, and, on a box
  // with an NPU, whether ggml-xdna can run it there. The fit is only asked for
  // when the NPU tab would show (the same gate as Slots → NPU).
  const slotList = useSlots().data ?? [];
  const npuOcc = useNpuOccupancy({ poll: false }).data;
  const npuShown = npuTabVisible(npuOcc, slotList);
  const xdnaFit = useXdnaFit({ enabled: npuShown }).data;
  const fitMap = npuShown ? xdnaFit : null;
  const pullsList = usePullsList();
  const activePulls = useMemoM(() => {
    const out = new Map();
    for (const j of pullsList.jobs) {
      if (j.state !== "queued" && j.state !== "running") continue;
      out.set(j.model_id, { pct: j.bytes_total ? (j.bytes_downloaded / j.bytes_total) * 100 : null });
    }
    return out;
  }, [pullsList.jobs]);
  const stateOf = (m) => ({
    installed: !!m.installed,
    remote: isUpstreamModel(m),
    inUseBy: slotsUsing(slotList, m.id),
    update: !!(m.installed && m.update_available),
    download: activePulls.get(m.id) ?? null,
  });

  // ── Combined filter (text + chip groups) ────────────────────────────
  const fil = m => {
    if (!modelMatchesFilters(m, filterSel)) return false;
    if (!matchesStateFilter(stateSel, stateOf(m))) return false;
    if (runsSel.length && !runsSel.some(r => runsOn(m, fitMap).includes(r))) return false;
    if (q.trim()) {
      const needle = q.trim().toLowerCase();
      const hay = `${m.longName || ""} ${m.name || ""} ${m.id || ""} ${m.repo || ""}`.toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  };

  const bySort = rows => sortModels(rows, sortField, sortDir);
  const used = m => slotsUsing(slotList, m.id).length > 0;

  // ── Tab datasets ────────────────────────────────────────────────────
  // Inference: installed + blessed + user.* (not upstream). Installed models a
  // slot references lead the list under "In use".
  const local = modelList.filter(m => !isUpstreamModel(m) && fil(m));
  const installed = local.filter(m => m.installed);
  const inferenceSections = [
    { key: "inuse", label: "In use", rows: bySort(installed.filter(used)) },
    { key: "installed", label: "Installed", rows: bySort(installed.filter(m => !used(m))) },
    { key: "blessed", label: "Available · blessed", rows: bySort(local.filter(m => !m.installed && m.ns === "blessed")) },
    { key: "userns", label: "user.*", rows: bySort(local.filter(m => !m.installed && m.ns === "pulled")) },
  ];
  const inferenceCount = inferenceSections.reduce((n, s) => n + s.rows.length, 0);

  // Upstream: only isUpstreamModel
  const upstreamAll = modelList.filter(m => isUpstreamModel(m) && fil(m));
  const upstreamSections = [
    { key: "inuse", label: "In use", rows: bySort(upstreamAll.filter(used)) },
    { key: "advertised", label: "Advertised", rows: bySort(upstreamAll.filter(m => !used(m))) },
  ];
  const upstreamTotal = modelList.filter(m => isUpstreamModel(m)).length;

  // Image: ComfyUI weight files are not registry rows — the tab is the file
  // inventory (ImageFilesTab). react-query serves the same query from cache, so
  // the tab count below costs no second request.
  // The count is a label, not worth the page: a failed, still-pending or malformed
  // listing (a body with no `files`) reads as 0 here, and the tab shows the failure.
  const imageFiles = useComfyuiFiles();
  const imageTotal = imageFileCount(imageFiles.data);

  // ── Pagination logic ────────────────────────────────────────────────
  const pageReset = usePageReset([tab, q, sortField, sortDir, filterSel, stateSel, runsSel]);
  useEffectM(() => { if (pageReset) setPage(1); }, [pageReset]);
  const paged = pageSections(tab === "upstream" ? upstreamSections : inferenceSections, page, perPage);

  // FLM: the catalogue is not registry rows; react-query serves the tab's own query from cache.
  const flmCatalog = useFlmCatalog();
  const flmTotal = useMemoM(() => flmCatalogRows(flmCatalog.data?.models).length, [flmCatalog.data]);

  // ── Render ──────────────────────────────────────────────────────────
  const tabLabel = tab === "inference" ? `Inference Models${inferenceCount ? ` · ${inferenceCount}` : ""}`
    : `Upstream Models${upstreamTotal ? ` · ${upstreamTotal}` : ""}`;

  // Upstream rows are neither installed nor downloadable, and have no file to update.
  const stateChips = tab === "upstream" ? STATE_FILTERS.filter(c => c.id === "inuse") : STATE_FILTERS;
  const toolbarGroups = [
    { id: "state", label: "state", chips: stateChips, selected: stateSel, onToggle: id => setStateSel(s => toggleIn(s, id)) },
    { id: "filter", label: "filter", chips: FILTER_CHIPS, selected: filterSel, onToggle: id => setFilterSel(s => toggleIn(s, id)) },
    ...(tab === "inference"
      ? [{
          id: "runs",
          label: "runs on",
          // The NPU chip is only offered on a box that has one.
          chips: RUNS_ON_FILTERS.filter(c => c.id !== "npu" || npuShown),
          selected: runsSel,
          onToggle: id => setRunsSel(s => toggleIn(s, id)),
        }]
      : []),
  ];
  const filtered = q.trim() || filterSel.length || stateSel.length || runsSel.length;

  // FLM's catalogue is a closed list with no per-model update check: checking it
  // is re-reading the catalogue, and adding is downloading one of its models.
  const [flmAddSeq, setFlmAddSeq] = useStateM(0);
  const [recheckingFlm, setRecheckingFlm] = useStateM(false);
  const onRecheckFlm = async () => {
    setRecheckingFlm(true);
    try {
      const res = await flmCatalog.refetch();
      if (res.isError) throw res.error;
      const rows = flmCatalogRows(res.data?.models);
      toastM(`FLM catalogue re-read · ${rows.length} model${rows.length === 1 ? "" : "s"}, ${rows.filter(r => r.installed).length} installed`, "info");
    } catch (e) {
      toastM(`Could not re-read the FLM catalogue — ${e?.message || "see logs"}`, "err");
    } finally {
      setRecheckingFlm(false);
    }
  };
  const headerCheck = tab === "flm"
    ? {
        testid: "mdl-check-flm",
        title: "Re-read the FastFlowLM catalogue. FLM has no per-model update check.",
        pending: recheckingFlm,
        onClick: onRecheckFlm,
      }
    : tab === "upstream"
    ? {
        testid: "mdl-check-upstream",
        title: "Ask the upstreams again which models they advertise",
        pending: recheckingUpstream,
        onClick: onRecheckUpstream,
      }
    : {
        title: "Compare every installed model against its HuggingFace repo now",
        pending: forceCheck.isPending,
        onClick: onCheckUpdates,
      };
  const headerAdd = tab === "flm"
    ? [{ label: "Download FLM model", hint: "Show the catalogue models that are not installed yet", onClick: () => setFlmAddSeq(n => n + 1) }]
    : tab === "upstream"
    ? [{ label: "Add upstream", hint: "Add a provider under Slots → Endpoints; its models appear here", onClick: () => { window.location.hash = "#slots/endpoints"; } }]
    : [
        { label: "Add by HF coords", hint: "a repo and a file, pulled into the model store", testid: "mdl-add-hf", onClick: () => setAddOpen(true) },
        { label: "Search HF", hint: "find a repo on Hugging Face, then add it", testid: "mdl-add-search", onClick: () => setSearchOpen(true) },
        { label: "Add by path", hint: "register a model file already on disk", testid: "mdl-add-path", onClick: () => setAddByPathOpen(true) },
        { label: "Scan directory", hint: "register every model file in a folder", testid: "mdl-add-scan", onClick: () => setScanOpen(true) },
      ];

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Catalog</span>
        {/* One heading for the whole catalog — the active tab names it. The
            Profiles tab swaps the title in place (and drops the model CTAs —
            ProfilesView carries its own actions) rather than stacking a
            second vh below the tab bar. */}
        <h1>{tab === "profiles" ? "Profiles" : "Models"}</h1>
        <span className="vh-spacer" />
        {tab === "profiles" || tab === "image" ? (
          null
        ) : (
          <CatalogHeaderActions check={headerCheck} addItems={headerAdd}>
            {tab === "inference" && updatable.length > 0 && (
              <button
                className="btn"
                data-testid="mdl-update-all"
                disabled={updateAll.isPending}
                title="Re-pull every installed model whose HuggingFace file has changed"
                onClick={onUpdateAll}
              >{Icons.download} {updateAll.isPending ? "Starting…" : `Update all (${updatable.length})`}</button>
            )}
          </CatalogHeaderActions>
        )}
      </div>

      {/* ── Tab bar (matches slot-tabs pattern) ── */}
      <div className="slot-tabs" role="tablist" style={{marginBottom: 0}}>
        <button
          role="tab"
          aria-selected={tab === "inference"}
          className={"slot-tab" + (tab === "inference" ? " on" : "")}
          onClick={() => { window.location.hash = "#models/inference"; }}
        >
          <span>Inference Models</span>
          <span className="slot-tab-ct num">{inferenceCount}</span>
        </button>
        <button
          role="tab"
          aria-selected={tab === "image"}
          className={"slot-tab comfy" + (tab === "image" ? " on" : "")}
          onClick={() => { window.location.hash = "#models/image"; }}
        >
          <span>Image / ComfyUI</span>
          <span className="slot-tab-ct num">{imageTotal}</span>
        </button>
        <button
          role="tab"
          aria-selected={tab === "upstream"}
          className={"slot-tab" + (tab === "upstream" ? " on" : "")}
          onClick={() => { window.location.hash = "#models/upstream"; }}
        >
          <span>Upstream Models</span>
          <span className="slot-tab-ct num">{upstreamTotal}</span>
        </button>
        <button
          role="tab"
          aria-selected={tab === "flm"}
          className={"slot-tab" + (tab === "flm" ? " on" : "")}
          data-testid="models-tab-flm"
          onClick={() => { window.location.hash = "#models/flm"; }}
        >
          <span>FLM</span>
          <span className="slot-tab-ct num">{flmTotal}</span>
        </button>
        <button
          role="tab"
          aria-selected={tab === "profiles"}
          className={"slot-tab" + (tab === "profiles" ? " on" : "")}
          onClick={() => { window.location.hash = "#models/profiles"; }}
        >
          <span>Profiles</span>
        </button>
      </div>

      {tab === "profiles" ? (
        window.ProfilesView ? <window.ProfilesView /> : null
      ) : tab === "image" ? (
        <ImageFilesTab />
      ) : tab === "flm" ? (
        <FlmModelsTab addSeq={flmAddSeq} />
      ) : (
      <div className="models-layout" style={{marginTop: 18}}>
        {/* ── List (toolbar + rows) ── */}
        <CatalogList
          toolbar={
            <CatalogToolbar
              q={q}
              onQ={setQ}
              groups={toolbarGroups}
              sort={{
                fields: MODEL_SORT_FIELDS,
                field: sortField,
                dir: sortDir,
                onField: setSortField,
                onDir: () => setSortDir(d => d === "asc" ? "desc" : "asc"),
              }}
              onClear={() => { setFilterSel([]); setStateSel([]); setRunsSel([]); setQ(""); }}
            />
          }
          title={tabLabel}
          right={`${modelList.length} total · ${modelList.filter(m => m.installed).length} on disk · ${upstreamTotal} upstream`}
          paged={paged}
          loading={modelsQuery.isPending ? "Loading models…" : null}
          error={modelsQuery.isError ? (modelsQuery.error?.message || "Failed to load models") : null}
          empty={filtered
            ? "No models match — adjust the search or filters."
            : tab === "upstream" ? "No upstream models advertised." : "the catalog is empty."}
          pager={{ perPage, onPage: setPage, onPerPage: (opt) => { setPerPage(opt); setPage(1); } }}
          renderRow={m => (
            <ModelRow
              key={m.id}
              model={m}
              state={stateOf(m)}
              npuFit={npuFitOf(m.id, fitMap)}
              selected={selId === m.id}
              onSelect={() => setSelId(m.id)}
              onEditModel={setRecipeModel}
              onDeleteModel={setDelModel}
            />
          )}
        />

        {/* ── Detail + Downloads ── */}
        <div className="models-sidebar">
          <ModelDetail
            model={selected}
            state={selected ? stateOf(selected) : undefined}
            npuFit={selected ? npuFitOf(selected.id, fitMap) : null}
            onDelete={() => setDelModel(selected)}
            onEdit={() => setRecipeModel(selected)}
          />
          <DownloadsPane />
        </div>
      </div>
      )}

      <AddByHfModal open={addOpen} onClose={() => setAddOpen(false)} initialRepo={searchPick} />
      <AddByPathModal open={addByPathOpen} onClose={() => setAddByPathOpen(false)} />
      <ScanDirectoryModal open={scanOpen} onClose={() => setScanOpen(false)} />
      <ModelDrawer open={!!recipeModel} onClose={() => setRecipeModel(null)} model={recipeModel} />
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
// A registry row on the shared catalog row (catalog-shell.jsx). `state` is what
// its status chip is derived from (ModelsView's `stateOf`); `npuFit` is the
// ggml-xdna fit when this box's NPU can run the file.
// `onEditModel` (new — Stream E kebab menu) opens the model edit drawer
// directly for THIS row's model, independent of catalog selection.
// `onDeleteModel` opens the shared DeleteModelDialog (type-the-name confirm,
// refcounted blob release) for THIS row — only offered for installed local
// models, mirroring the detail pane's Delete button gate.
const registryState = (model) => ({
  installed: !!model.installed,
  remote: isUpstreamModel(model),
  update: !!(model.installed && model.update_available),
});
function ModelRow({ model, state = registryState(model), npuFit = null, selected, onSelect, onEditModel, onDeleteModel }) {
  const backends = Array.isArray(model.backends) ? model.backends : [];
  const [menuOpen, setMenuOpen] = useStateM(false);
  const label = model.longName || model.name || model.id;
  return (
    <CatalogRow
      state={state}
      selected={selected}
      onSelect={onSelect}
      name={label}
      sub={model.repo || ""}
      size={model.size || (model.size_bytes ? fmtBytes(model.size_bytes) : "")}
      dotTestid={model.installed ? "mdl-row-installed" : "mdl-row-not-installed"}
      statusTestid={catalogStatus(state).kind === "update" ? "mdl-row-update" : "mdl-row-status"}
      tags={
        <>
          {model.default && (
            <span
              className="chip ok"
              data-testid="mdl-row-default"
              title={`Default${model.type ? ` for ${model.type}` : ""} — the model this type falls back to`}
            >
              ✓ default
            </span>
          )}
          {model.type && <span className="chip">{model.type}</span>}
          {model.quant && <span className="chip quant" data-testid="mdl-row-quant">{model.quant}</span>}
          {isDrafterModel(model) && (
            <span className="chip info" data-testid="mdl-row-drafter" title={`Paired drafter · ${draftVariantLabel(model.draftVariant)}`}>
              ⚡ {draftVariantLabel(model.draftVariant)}
            </span>
          )}
          {npuFit && (
            <span className="chip dev-npu" data-testid="mdl-row-npu" title={`Runs on the NPU through ggml-xdna. ${npuFit.reason}`}>
              npu · {npuFit.xdna_path}
            </span>
          )}
          {backends.map(b => (
            <span key={b} className={"chip dev-" + b}>{b}</span>
          ))}
        </>
      }
      menu={
        <button
          type="button"
          className="btn ghost sm"
          data-testid="mdl-row-menu-btn"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label={`Actions for ${label}`}
          onClick={(e) => { e.stopPropagation(); setMenuOpen(o => !o); }}
        >{Icons.more}</button>
      }
    >
      {menuOpen && (
        <>
          {/* Full-viewport click-outside dismiss — same idiom as the
              board-selector/orchestration dropdowns in board-view.jsx. */}
          <div className="mdl-row-menu-backdrop" onClick={(e) => { e.stopPropagation(); setMenuOpen(false); }} />
          <Menu
            anchor="right"
            items={[
              {
                icon: Icons.edit,
                label: "Edit model settings",
                onClick: () => onEditModel && onEditModel(model),
              },
              // Delete — same gate as the detail pane's Delete button: only an
              // installed local model has bytes/registry row to remove (an
              // upstream row is served remotely; a not-installed catalog row
              // has nothing on disk). Routes through DeleteModelDialog, so the
              // type-the-name confirm + refcount contract stays the boundary.
              ...(model.installed && !isUpstreamModel(model)
                ? [
                    { divider: true },
                    {
                      icon: Icons.unload,
                      label: "Delete model…",
                      danger: true,
                      onClick: () => onDeleteModel && onDeleteModel(model),
                    },
                  ]
                : []),
            ]}
            onClose={() => setMenuOpen(false)}
          />
        </>
      )}
    </CatalogRow>
  );
}

// ── ModelDetail ───────────────────────────────────────────────────────
// How a per-op and a fused ggml-xdna model each sit beside the FLM lane on the
// one NPU (ADR-0059).
const NPU_COEXIST = {
  fused: "Can share the NPU with FLM: the two take turns.",
  "per-op": "Cannot hold the NPU together with FLM. Loading one asks to unload the other.",
};
function ModelDetail({ model, state, npuFit = null, onDelete, onEdit, onPullStarted }) {
  const pull = usePullJob();
  const slotsQuery = useSlots();
  const swap = useSlotSwap();
  const hfUpdate = useModelUpdateApply();
  const [cancelling, setCancelling] = useStateM(false);
  if (!model) {
    return <DetailEmpty>No model selected.</DetailEmpty>;
  }
  const rowState = state ?? registryState(model);
  const upstream = isUpstreamModel(model);
  // ADR-0029: a model carries FILE FACTS only — chat template, jinja, whether
  // the file has MTP heads — plus its native context window (read-only, from
  // metadata). The tune (flags, context, thinking, vision, speculation) lives
  // on a profile.
  const nativeCtx = Number(model.metadata?.context_length) || null;
  const RECIPE_FIELDS = [
    { key: "chat_template", label: "Chat template", hint: "pinned template · empty = the one embedded in the GGUF" },
    { key: "jinja", label: "Jinja", hint: "false = this file's chat template does not work with --jinja" },
    { key: "mtp", label: "MTP heads", hint: "whether this file carries MTP heads · empty = read the 'mtp' tag" },
  ];
  const recipeRows = [
    ...RECIPE_FIELDS.map(f => ({ ...f, value: model[f.key] })),
    { key: "native_context", label: "Native context", hint: "the window this file was trained for · a slot's profile sets the window it launches with", value: nativeCtx ? `${nativeCtx.toLocaleString("en-US")} tokens` : null },
  ].filter(f => f.value !== null && f.value !== undefined && f.value !== "");

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
    <DetailCard>
      <DetailHeader
        state={rowState}
        name={model.longName || model.name || model.id}
        source={model.repo || model.hf_repo || model.id}
        chips={
          <>
            {model.installed && model.update_available && (
              <span className="chip amber" data-testid="mdl-detail-update-badge" role="status" aria-label="Update available on Hugging Face" title="A newer version of this file is available on Hugging Face">update available</span>
            )}
            {/* The update has its own badge above, so the status here says the rest. */}
            {upstream
              ? <span className="chip info">upstream · {model.upstream}</span>
              : <StatusChip state={{ ...rowState, update: false }} testid="mdl-detail-status" />}
          </>
        }
      />
      <DetailFacts
        facts={[
          { k: "params", v: model.params },
          { k: "size", v: model.size || (model.size_bytes ? fmtBytes(model.size_bytes) : null) },
          { k: "quant", v: model.quant, testid: "mdl-detail-quant" },
          { k: "type", v: model.type || (model.capabilities?.[0]) },
          { k: "device", v: model.device || (model.backends?.[0]) },
          { k: "runtime", v: model.runtime },
          { k: "namespace", v: model.ns },
          { k: "added", v: fmtAdded(model.created) },
          { k: "origin", v: upstream ? `upstream · ${model.upstream}` : "local" },
        ]}
      />
      <div className="mdl-detail-labels">
        {(model.labels || model.capabilities || []).map(l => <span key={l} className="chip">{l}</span>)}
      </div>
      {npuFit && (
        <DetailSection title="NPU fit · ggml-xdna" testid="mdl-npu-fit">
          <DetailLine k="path">
            <span className={"chip " + (npuFit.xdna_path === "fused" ? "ok" : "warn")} data-testid="mdl-npu-fit-path">{npuFit.xdna_path}</span>
          </DetailLine>
          <div className="mdl-detail-note">{npuFit.reason}</div>
          <div className="mdl-detail-note">{NPU_COEXIST[npuFit.xdna_path]}</div>
        </DetailSection>
      )}
      <div className="mdl-detail-recipe">
        <div className="form-section">File facts</div>
        {recipeRows.length === 0 ? (
          <DetailNone>No file facts recorded — the launcher reads them from the GGUF.</DetailNone>
        ) : recipeRows.map(f => (
          <div key={f.key} className="form-row" data-testid={`mdl-recipe-row-${f.key}`}>
            <div className="form-lbl">
              <span>{f.label}</span>
              <FieldInfoIcon description={f.hint} />
            </div>
            <div className="form-ctl">
              <span>{String(f.value)}</span>
            </div>
          </div>
        ))}
      </div>
      <UsedByPanel model={model} />
      <OnDiskPanel model={model} />
      <DetailActions>
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
        ) : upstream ? (
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
      </DetailActions>
    </DetailCard>
  );
}

Object.assign(window, { ModelsView, ModelRow, ModelDetail, DownloadsPane, DownloadRow, HfSearchPanel });
