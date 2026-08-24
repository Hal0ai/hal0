// hal0 dashboard — Runner Images (Slots ▸ Runner Images, runner-catalogue-v2)
//
// Mirrors models.jsx's ModelsView/ModelRow/ModelDetail/DownloadRow/DownloadsPane
// shapes: RunnerImagesView / RunnerImageRow / RunnerCard / RunnerDownloadRow /
// RunnerDownloadsPane. Backed by useRunnerImages.ts (list/sync/pull-job/pulls-list),
// mirroring useModels.ts's usePullJob() SSE pattern — progress here is
// layers_done/layers_total (a whole OCI image pull via podman) instead of bytes.
//
// runner-catalogue-v2 additions: a Defaults strip (per-family effective image
// ref + release/override badge + clear), a per-row tag picker over the
// contract's `available_tags`, a "newer tag" chip when the headline lags the
// registry, and Set-as-family-default (PUT /api/settings override map) with a
// confirm dialog naming the `in_use_by` slots that will drift.

import {
  useRunnerImages,
  useRunnerImageSync,
  useRunnerImagePullJob,
  useRunnerImagePullsList,
  useSetDefaultImage,
} from '@/api/hooks/useRunnerImages'
// Explicit import rather than the legacy window-global (primitives.jsx also
// publishes ConfirmDialog on window) — the settings pages' idiom. Keeps this
// module renderable without chrome wiring, e.g. under vitest
// (__tests__/runner-images-confirm-flow.test.tsx renders the confirm flow).
import { ConfirmDialog } from '@/dash/primitives.jsx'

const { useState: useStateRI, useEffect: useEffectRI } = React;

function fmtBytesRI(b) {
  if (!b || b < 0) return '—';
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`;
  return `${(b / 1024 ** 3).toFixed(2)} GB`;
}

// ── Pure helpers (unit-tested in __tests__/runner-images-view.test.tsx) ──

// Defaults-strip rows from the enriched /api/runner-images list: one
// {family, ref, source} per family whose default resolves to a catalogued
// row (`is_default` carries the family + whether it's the baked release
// default or a [slots].default_images override). First row per family wins;
// sorted by family for a stable strip order. Defensive against rows from a
// pre-contract backend (no is_default field) — they're simply skipped.
export function defaultsStripRows(images) {
  const rows = [];
  const seen = new Set();
  for (const img of images || []) {
    const d = img && img.is_default;
    if (!d || !d.family || seen.has(d.family)) continue;
    seen.add(d.family);
    rows.push({ family: d.family, ref: `${img.image}:${img.tag}`, source: d.source });
  }
  rows.sort((a, b) => (a.family < b.family ? -1 : a.family > b.family ? 1 : 0));
  return rows;
}

// True when the registry knows a newer tag than the row's headline `tag` —
// `available_tags` is newest-first per the sync contract, so this is just a
// head comparison. False on probe failure (empty list) or missing fields.
export function newerTagAvailable(image) {
  if (!image || !image.tag) return false;
  const tags = image.available_tags;
  if (!Array.isArray(tags) || tags.length === 0) return false;
  return tags[0] !== image.tag;
}

// Tag choices for the card's tag <select>: the contract's newest-first
// `available_tags` with the headline tag guaranteed present (prepended when
// the probe failed or predates the contract).
function tagChoices(image) {
  const tags = Array.isArray(image?.available_tags) ? image.available_tags : [];
  if (image?.tag && !tags.includes(image.tag)) return [image.tag, ...tags];
  return tags.length ? tags : (image?.tag ? [image.tag] : []);
}

// ── RunnerImagesSyncButton ──────────────────────────────────────────────
// Rendered inside RunnerImagesView's toolbar — the Slots page (which hosts
// this view as its Runner Images tab) keeps its own "Lifecycle / Slots"
// header, so the sync CTA lives with the catalogue surface it refreshes.
export function RunnerImagesSyncButton() {
  const sync = useRunnerImageSync();

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
    <button className="btn" data-testid="ri-sync" disabled={sync.isPending} onClick={onSync}>
      {Icons.restart} {sync.isPending ? "Syncing…" : "Sync now"}
    </button>
  );
}

// ── RunnerImagesView ────────────────────────────────────────────────────
export function RunnerImagesView() {
  const [selId, setSelId] = useStateRI(null);
  const [q, setQ] = useStateRI("");

  const imagesQuery = useRunnerImages();
  const images = imagesQuery.data ?? [];

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

  return (
    <div className="models-layout" style={{marginTop: 18}}>
        <div className="mdl-list">
          <DefaultsStrip images={images} />
          <div className="mdl-toolbar">
            <input
              className="input mono mdl-search"
              placeholder="search image, tag, notes…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <div className="mdl-toolbar-grp" style={{marginLeft: "auto"}}>
              <RunnerImagesSyncButton />
            </div>
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
  );
}

// ── DefaultsStrip ───────────────────────────────────────────────────────
// Per-family effective default images (from the rows' server-computed
// `is_default` enrichment): family → effective ref + a release-default /
// override badge. An override gets a clear button — clearing writes
// `{family: null}` through the settings override map and falls back to the
// baked release default, so the confirm names the slots that will drift.
function DefaultsStrip({ images }) {
  const rows = defaultsStripRows(images);
  const setDefault = useSetDefaultImage();
  const [clearFamily, setClearFamily] = useStateRI(null);
  if (rows.length === 0) return null;

  const clearRow = rows.find(r => r.family === clearFamily) || null;
  // Slots pinned to the override'd default — they fall back on clear.
  const clearImg = clearRow
    ? (images || []).find(i => i.is_default && i.is_default.family === clearRow.family)
    : null;
  const drifters = (clearImg?.in_use_by || []);

  const onClear = () => {
    const fam = clearFamily;
    setClearFamily(null);
    setDefault.mutate({ family: fam, ref: null }, {
      onSuccess: () => window.__hal0Toast && window.__hal0Toast(`${fam}: override cleared — release default applies`, "info"),
      onError: (e) => window.__hal0Toast && window.__hal0Toast(`Clear failed — ${e?.message || "see logs"}`, "err"),
    });
  };

  return (
    <div className="ri-defaults" data-testid="ri-defaults" style={{padding: "10px 16px", borderBottom: "1px solid var(--line-soft)"}}>
      <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 6}}>
        Family defaults
      </div>
      {rows.map(r => (
        <div key={r.family} data-testid={`ri-default-${r.family}`} style={{display: "flex", alignItems: "center", gap: 8, padding: "3px 0", fontSize: 12}}>
          <span className="mono" style={{color: "var(--fg-2)", minWidth: 88}}>{r.family}</span>
          <span className="mono" style={{color: "var(--fg-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{r.ref}</span>
          {r.source === "override"
            ? <span className="chip" style={{color: "var(--accent)", borderColor: "var(--accent-line)"}}>override</span>
            : <span className="chip">release default</span>}
          {r.source === "override" && (
            <button
              className="btn ghost sm"
              data-testid={`ri-clear-default-${r.family}`}
              disabled={setDefault.isPending}
              onClick={() => setClearFamily(r.family)}
              title="Remove the operator override — the release default applies again"
            >clear</button>
          )}
        </div>
      ))}
      <ConfirmDialog
        open={!!clearRow}
        onCancel={() => setClearFamily(null)}
        onConfirm={onClear}
        title={`Clear ${clearRow?.family || ""} override`}
        message={
          clearRow
            ? `Remove the ${clearRow.family} override (${clearRow.ref}) and fall back to the release default.` +
              (drifters.length
                ? ` Slots using it now: ${drifters.join(", ")} — they pick up the release image on their next restart.`
                : " No slot currently references it.")
            : ""
        }
        confirmLabel="Clear override"
        footerNote="Applies on the next slot restart."
      />
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
        {image.is_default && (
          <span className="chip" style={{color: "var(--ok)", borderColor: "var(--ok)"}}>
            {image.is_default.family} default{image.is_default.source === "override" ? " · override" : ""}
          </span>
        )}
        {newerTagAvailable(image) && (
          <span className="chip" data-testid="ri-newer-tag" style={{color: "var(--accent)", borderColor: "var(--accent-line)"}}>
            newer: {image.available_tags[0]}
          </span>
        )}
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
  const setDefault = useSetDefaultImage();
  // Tag picker over the contract's available_tags (headline preselected).
  // null = "follow the headline"; reset whenever the selected row changes.
  const [pickedTag, setPickedTag] = useStateRI(null);
  const imageId = image?.id;
  useEffectRI(() => { setPickedTag(null); }, [imageId]);
  const [confirmDefault, setConfirmDefault] = useStateRI(false);

  if (!image) {
    return (
      <div className="mdl-detail" style={{padding: 24, textAlign: "center", color: "var(--fg-4)"}}>
        Select a runner image.
      </div>
    );
  }

  const tags = tagChoices(image);
  const selTag = pickedTag ?? image.tag;
  // The pull job is id-keyed: POST /{id}/pull resolves the catalogued row's
  // headline tag server-side (registry/runner_pull_jobs.enqueue) and takes no
  // tag parameter. Pulling a non-headline tag therefore isn't wired yet — the
  // button gates honestly instead of pretending, and setting a tag as the
  // family default rolls the headline on the next sync, after which it IS the
  // pullable tag. (Called out in the PR body; per-tag pull needs a backend
  // pull-route change.)
  const pullTagMismatch = selTag !== image.tag;
  const inFlight = pull.imageId === image.id && pull.inFlight;
  const onPull = async () => {
    try {
      await pull.start(image.id);
      window.__hal0Toast && window.__hal0Toast(`Pulling ${image.id}…`, "info");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Pull failed to start — ${e?.message || "see logs"}`, "err");
    }
  };

  // Set-as-family-default: only offered when the backend already associates
  // this row's image with a family (`is_default.family`) — for any other row
  // there is no family key to write the override under.
  const family = image.is_default?.family || null;
  const defaultRef = `${image.image}:${selTag}`;
  const alreadyDefault = !!(image.is_default && selTag === image.tag && image.is_default.source);
  const inUseBy = Array.isArray(image.in_use_by) ? image.in_use_by : [];
  const onSetDefault = () => {
    setConfirmDefault(false);
    setDefault.mutate({ family, ref: defaultRef }, {
      onSuccess: () => window.__hal0Toast && window.__hal0Toast(`${family} default → ${defaultRef}`, "info"),
      onError: (e) => window.__hal0Toast && window.__hal0Toast(`Set default failed — ${e?.message || "see logs"}`, "err"),
    });
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
        <div><div className="k">in use by</div><div className="v mono">{inUseBy.length ? inUseBy.join(", ") : "—"}</div></div>
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
        {tags.length > 1 && (
          <div style={{display: "flex", alignItems: "center", gap: 8, marginBottom: 10}}>
            <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>tag</span>
            <select
              className="input mono"
              data-testid="ri-tag-pick"
              value={selTag}
              onChange={(e) => setPickedTag(e.target.value)}
              style={{fontSize: 11, padding: "3px 6px"}}
            >
              {tags.map(t => (
                <option key={t} value={t}>{t}{t === image.tag ? " · headline" : ""}</option>
              ))}
            </select>
            {newerTagAvailable(image) && (
              <span className="chip" style={{color: "var(--accent)", borderColor: "var(--accent-line)"}}>
                newer: {image.available_tags[0]}
              </span>
            )}
          </div>
        )}

        {inFlight ? (
          <div>
            <div className="mono" style={{fontSize: 11, color: "var(--fg-3)"}}>
              {pull.state} — {pull.layersDone}/{pull.layersTotal || "?"} layers
              {pull.line ? ` · ${pull.line}` : ""}
            </div>
            <button className="btn ghost sm" onClick={pull.cancel} style={{marginTop: 6}}>Cancel</button>
          </div>
        ) : (
          <div style={{display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap"}}>
            <button
              className="btn"
              data-testid="ri-pull"
              disabled={pullTagMismatch}
              title={pullTagMismatch
                ? `Pull is keyed to the headline tag (${image.tag}) — set ${selTag} as the ${family || "family"} default first; the catalogue rolls to it on sync`
                : undefined}
              onClick={onPull}
            >
              {Icons.download} {image.local_path ? "Re-pull" : "Pull"} <span className="mono" style={{fontSize: 10, opacity: .7}}>:{image.tag}</span>
            </button>
            {family && (
              <button
                className="btn ghost"
                data-testid="ri-set-default"
                disabled={setDefault.isPending || alreadyDefault}
                title={alreadyDefault
                  ? `${defaultRef} already is the ${family} default`
                  : `Pin ${defaultRef} as the ${family} family default via a settings override`}
                onClick={() => setConfirmDefault(true)}
              >Set as {family} default</button>
            )}
          </div>
        )}
        {pull.imageId === image.id && pull.error && (
          <div className="mono" style={{fontSize: 11, color: "var(--err)", marginTop: 6}}>{pull.error.message}</div>
        )}
      </div>

      <ConfirmDialog
        open={confirmDefault}
        onCancel={() => setConfirmDefault(false)}
        onConfirm={onSetDefault}
        title={`Set ${family || ""} default`}
        message={
          `Pin ${defaultRef} as the ${family || ""} family default ([slots].default_images override).` +
          (inUseBy.length
            ? ` Slots on this family's image now: ${inUseBy.join(", ")} — they move to the new tag on their next restart.`
            : " No slot currently references this image.")
        }
        confirmLabel="Set default"
        footerNote="Applies on the next slot restart."
      />
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
  // An error envelope (or proxy 5xx body) must not crash the whole tab.
  const jobs = Array.isArray(pullsList.data) ? pullsList.data : [];
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
