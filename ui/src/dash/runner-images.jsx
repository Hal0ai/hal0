// hal0 dashboard — Runner Images (subpage of Models, feat/runner-image-catalogue)
//
// Mirrors models.jsx's ModelsView/ModelRow/ModelDetail/DownloadRow/DownloadsPane
// shapes: RunnerImagesView / RunnerImageRow / RunnerCard / RunnerDownloadRow /
// RunnerDownloadsPane. Backed by useRunnerImages.ts (list/sync/pull-job/pulls-list),
// mirroring useModels.ts's usePullJob() SSE pattern — progress here is
// layers_done/layers_total (a whole OCI image pull via podman) instead of bytes.

import {
  useRunnerImages,
  useRunnerImageSync,
  useRunnerImagePullJob,
  useRunnerImagePullsList,
} from '@/api/hooks/useRunnerImages'

const { useState: useStateRI, useEffect: useEffectRI } = React;

function fmtBytesRI(b) {
  if (!b || b < 0) return '—';
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`;
  return `${(b / 1024 ** 3).toFixed(2)} GB`;
}

// ── RunnerImagesView ────────────────────────────────────────────────────
export function RunnerImagesView() {
  const [selId, setSelId] = useStateRI(null);
  const [q, setQ] = useStateRI("");

  const imagesQuery = useRunnerImages();
  const images = imagesQuery.data ?? [];
  const sync = useRunnerImageSync();

  useEffectRI(() => {
    if (!selId && images.length) setSelId(images[0].id);
  }, [images, selId]);

  const filtered = images.filter(img => {
    if (!q.trim()) return true;
    const needle = q.trim().toLowerCase();
    const hay = `${img.id} ${img.image} ${img.tag} ${img.notes || ""}`.toLowerCase();
    return hay.includes(needle);
  });

  const selected = images.find(i => i.id === selId) || images[0];

  const onSync = async () => {
    try {
      const res = await sync.mutateAsync();
      const n = res.images?.length ?? 0;
      const warn = res.images_json_ok === false ? " (images.json unreachable — GHCR data only)" : "";
      window.__hal0Toast && window.__hal0Toast(`Runner image sync complete — ${n} image${n === 1 ? "" : "s"}${warn}`, res.images_json_ok === false ? "warn" : "info");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Sync failed — ${e?.message || "see logs"}`, "err");
    }
  };

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Catalog</span>
        <h1>Runner Images</h1>
        <span className="vh-spacer" />
        <button className="btn" data-testid="ri-sync" disabled={sync.isPending} onClick={onSync}>
          {Icons.restart} {sync.isPending ? "Syncing…" : "Sync now"}
        </button>
      </div>

      <div className="models-layout" style={{marginTop: 18}}>
        <div className="mdl-list">
          <div className="mdl-toolbar">
            <input
              className="input mono mdl-search"
              placeholder="search image, tag, notes…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>

          <div className="mdl-list-h">
            <span>Runner Images {filtered.length ? `· ${filtered.length}` : ""}</span>
            <span className="right mono">{images.length} total</span>
          </div>

          {imagesQuery.isPending && (
            <div style={{padding: 16, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>Loading runner images…</div>
          )}
          {imagesQuery.isError && (
            <div style={{padding: 16, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--err)"}}>
              {imagesQuery.error?.message || "Failed to load runner images"}
            </div>
          )}
          {!imagesQuery.isPending && !imagesQuery.isError && filtered.length === 0 && (
            <div style={{padding: 24, textAlign: "center", fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)"}}>
              {images.length === 0
                ? <>Nothing catalogued yet — click <b>Sync now</b> to discover published images.</>
                : "No runner images match — adjust the search."}
            </div>
          )}

          {filtered.map(img => (
            <RunnerImageRow key={img.id} image={img} selected={selId === img.id} onSelect={() => setSelId(img.id)} />
          ))}
        </div>

        <div className="models-sidebar">
          <RunnerCard image={selected} />
          <RunnerDownloadsPane />
        </div>
      </div>
    </div>
  );
}

// ── RunnerImageRow ──────────────────────────────────────────────────────
function RunnerImageRow({ image, selected, onSelect }) {
  return (
    <div className={"mdl-row" + (selected ? " sel" : "")} onClick={onSelect}>
      <span className="mdl-row-icon">
        {image.downloaded || image.local_path
          ? <span style={{color: "var(--green)", display: "inline-flex"}}>{Icons.download}</span>
          : <span style={{color: "var(--fg-5)", display: "inline-flex"}}>{Icons.download}</span>}
      </span>
      <span className="nm">
        {image.id}
        <span className="sub">{image.image}:{image.tag}</span>
      </span>
      <span className="mdl-row-tags">
        {image.ownership && <span className="chip">{image.ownership}</span>}
        {image.publish && <span className="chip">{image.publish}</span>}
        {(image.extra?.features || []).map(f => (
          <span key={f} className="chip" style={{color: "var(--accent)", borderColor: "var(--accent-line)"}}>{f}</span>
        ))}
      </span>
      <span className="sz num">{fmtBytesRI(image.size_bytes)}</span>
      <span className="tg">
        {image.local_path ? <span className="chip" style={{color: "var(--ok)", borderColor: "var(--ok)"}}>✓ downloaded</span> : null}
      </span>
    </div>
  );
}

// ── RunnerCard (detail view) ────────────────────────────────────────────
function RunnerCard({ image }) {
  const pull = useRunnerImagePullJob();

  if (!image) {
    return (
      <div className="mdl-detail" style={{padding: 24, textAlign: "center", color: "var(--fg-4)"}}>
        Select a runner image.
      </div>
    );
  }

  const inFlight = pull.imageId === image.id && pull.inFlight;
  const onPull = async () => {
    try {
      await pull.start(image.id);
      window.__hal0Toast && window.__hal0Toast(`Pulling ${image.id}…`, "info");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Pull failed to start — ${e?.message || "see logs"}`, "err");
    }
  };

  // Feature tags: the tag/pill component the Models page uses for capability
  // tags, reused here — images.json carries no `features` field today, so
  // this reads from `extra.features` (forward-compat: renders once a future
  // images.json revision adds it, degrades to nothing today).
  const features = Array.isArray(image.extra?.features) ? image.extra.features : [];

  return (
    <div className="mdl-detail">
      <div className="mdl-detail-h">
        <div style={{display: "flex", alignItems: "center", gap: 10, marginBottom: 6}}>
          <div className="nm mono">{image.id}</div>
          <span style={{marginLeft: "auto"}}>
            {image.downloaded_at
              ? <span className="chip ok">✓ downloaded</span>
              : <span className="chip">not downloaded</span>}
          </span>
        </div>
        <div className="repo">{image.image}:{image.tag}</div>
      </div>

      <div className="mdl-detail-meta">
        {image.digest && <div><div className="k">digest</div><div className="v mono">{image.digest.slice(0, 19)}…</div></div>}
        <div><div className="k">size</div><div className="v">{fmtBytesRI(image.size_bytes)}</div></div>
        <div><div className="k">ownership</div><div className="v">{image.ownership || "—"}</div></div>
        <div><div className="k">publish</div><div className="v">{image.publish || "—"}</div></div>
        <div><div className="k">manifest key</div><div className="v">{image.manifest_key || "—"}</div></div>
      </div>

      {image.notes && (
        <p style={{fontSize: 12, color: "var(--fg-3)", lineHeight: 1.5, padding: "0 16px"}}>{image.notes}</p>
      )}

      {features.length > 0 && (
        <div className="mdl-detail-labels">
          {features.map(f => <span key={f} className="chip">{f}</span>)}
        </div>
      )}

      {image.build && Object.keys(image.build).length > 0 && (
        <details style={{margin: "8px 16px"}}>
          <summary className="mono" style={{fontSize: 11, color: "var(--fg-4)", cursor: "pointer"}}>build</summary>
          <pre className="mono" style={{fontSize: 10, whiteSpace: "pre-wrap"}}>{JSON.stringify(image.build, null, 2)}</pre>
        </details>
      )}

      <div style={{padding: "0 16px 16px"}}>

        {inFlight ? (
          <div>
            <div className="mono" style={{fontSize: 11, color: "var(--fg-3)"}}>
              {pull.state} — {pull.layersDone}/{pull.layersTotal || "?"} layers
              {pull.line ? ` · ${pull.line}` : ""}
            </div>
            <button className="btn ghost sm" onClick={pull.cancel} style={{marginTop: 6}}>Cancel</button>
          </div>
        ) : (
          <button className="btn" data-testid="ri-pull" onClick={onPull}>
            {Icons.download} {image.local_path ? "Re-pull" : "Pull"}
          </button>
        )}
        {pull.imageId === image.id && pull.error && (
          <div className="mono" style={{fontSize: 11, color: "var(--err)", marginTop: 6}}>{pull.error.message}</div>
        )}
      </div>
    </div>
  );
}

// ── RunnerDownloadRow / RunnerDownloadsPane ─────────────────────────────
// Mirrors DownloadRow/DownloadsPane in models.jsx (bytes → layers).
function RunnerDownloadRow({ job }) {
  const pct = job.layers_total ? Math.round((job.layers_done / job.layers_total) * 100) : 0;
  const isRunning = job.state === "running";
  const isQueued = job.state === "queued";
  return (
    <div style={{padding: "12px 16px", borderBottom: "1px solid var(--line-soft)"}}>
      <div style={{display: "flex", justifyContent: "space-between", fontFamily: "var(--jbm)", fontSize: 11.5, marginBottom: 6, alignItems: "center", gap: 8}}>
        <span style={{overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1}}>
          {job.image_id}
        </span>
        <span style={{color: "var(--fg-3)", fontSize: 11}}>
          {isRunning && `${pct}% · ${job.layers_done}/${job.layers_total || "?"} layers`}
          {isQueued && "queued"}
        </span>
      </div>
      {(isRunning || isQueued) && (
        <div className="dl-bar" style={{height: 4}}>
          <i style={{width: `${pct}%`, background: "var(--accent)"}} />
        </div>
      )}
    </div>
  );
}

function RunnerDownloadsPane() {
  const pullsList = useRunnerImagePullsList();
  const jobs = pullsList.data ?? [];
  const active = jobs.filter(j => j.state === "queued" || j.state === "running");
  if (active.length === 0) return null;
  return (
    <div className="mdl-dl">
      <div className="mdl-dl-h">
        <span>Runner image downloads</span>
        <span className="ct mono">{active.length}</span>
      </div>
      {active.map(j => <RunnerDownloadRow key={j.id} job={j} />)}
    </div>
  );
}
