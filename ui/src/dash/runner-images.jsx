// hal0 dashboard — Runner Images (Slots ▸ Runner Images, runner-catalogue-v2)
//
// Mirrors models.jsx's ModelsView/ModelRow/ModelDetail/DownloadRow/DownloadsPane
// shapes: RunnerImagesView / RunnerImageRow / RunnerCard / RunnerDownloadRow /
// RunnerDownloadsPane. Backed by useRunnerImages.ts (list/sync/pull-job/pulls-list),
// mirroring useModels.ts's usePullJob() SSE pattern — progress here is
// layers_done/layers_total (a whole OCI image pull via podman) instead of bytes.
//
// runner-catalogue-v2 additions: a per-row tag picker over the contract's
// `available_tags`, a "newer tag" chip when the headline lags the registry,
// and Set-as-family-default (PUT /api/settings override map) with a confirm
// dialog naming the `in_use_by` slots that will drift.
//
// runner-catalogue-v3 (Task 10): the Defaults strip is now a launch-truth
// FamilyStrip driven straight by the server's `families` summary (effective
// ref, source tier, store state, newer-release marker), and the list groups
// into "Default families" / "Specialized" / "Other catalogued" sections via
// the pure groupRows() helper.

import {
  useRunnerImages,
  useRunnerImageSync,
  useRunnerImagePullJob,
  useRunnerImagePullsList,
  useSetDefaultImage,
  useRestartAffected,
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

// Row grouping for the catalogue list (runner-catalogue-v3, Task 10):
// default-family repos first, then specialty images (server-provided
// `specialties` from RUNNER_IMAGES supports / images.json — read
// defensively, since rows don't carry the field from every backend yet),
// then referenced/uncatalogued-family rows. A row that IS a family default
// lands in `defaults` even if it also carries specialties — the family strip
// above already surfaces it, so the list shouldn't also branch it into
// "Specialized".
export function groupRows(images) {
  const g = { defaults: [], specialized: [], other: [] };
  for (const img of images || []) {
    if (img.is_default) g.defaults.push(img);
    else if ((img.specialties || img.extra?.specialties || []).length) g.specialized.push(img);
    else g.other.push(img);
  }
  return g;
}

// Mutable/floating-pointer tag names — GHCR re-pushes these on every CI
// build (branch heads) or dev iteration (old floating dev tags), so their
// push time means "whenever CI/a dev last ran," not "a newer release than
// the headline." `available_tags` is already noise-free (cosign/CI-commit
// tag shapes are filtered server-side —
// hal0.registry.runner_image_sync.is_noise_tag), but these real, mutable
// tags can still sort ahead of the true headline on pure registry order.
// Live examples that motivated this: comfyui headlined `latest` but the
// chip read `newer: main` (main = CI branch tag re-pushed every build);
// rocmfpx showed `newer: server` (an old floating dev tag). `latest` is
// only "mutable" when it ISN'T itself the row's headline — a row
// headlined `latest` must still be able to see a genuinely newer tag.
export const MUTABLE_TAGS = new Set(["main", "master", "server", "edge", "nightly"]);

function isMutablePointerTag(tag, headlineTag) {
  if (tag === "latest") return headlineTag !== "latest";
  return MUTABLE_TAGS.has(tag);
}

// The newest tag fit to compare against (or display as) "newer than
// headline" — the first entry of `available_tags` that isn't a mutable
// pointer (see MUTABLE_TAGS). Digest-alias detection (a mutable tag that
// happens to point at the SAME manifest as the headline) isn't done here:
// available_tags carries only tag strings, no per-tag digest, so there's
// no data to compare against — name-based exclusion is what's available.
export function newestComparableTag(image) {
  if (!image) return undefined;
  const tags = Array.isArray(image.available_tags) ? image.available_tags : [];
  return tags.find(t => !isMutablePointerTag(t, image.tag));
}

// Release-shaped tag names (bare or `v`-prefixed dotted version numbers,
// e.g. "0824", "v1.2.3") — the "releases" lane in tagLanes() below.
const RELEASE_TAG_RE = /^(v?\d+(\.\d+)*)$/;

// Lane buckets over the v3 per-tag payload (Task 5: `image.tags[].{tag,
// digest,downloaded}` + `image.badges`) for the card's three-lane tag
// <select> — replaces the old flat tagChoices(). Falls back to bare
// `available_tags` strings (digestless: digest/downloaded/badge come back
// null) against a pre-v3 backend or a failed tag probe.
//
// releases: dotted/bare version-number tags. pins: the row's headline tag,
// guaranteed present even when the probe didn't carry it. other: everything
// else (branch heads, `latest`, floating dev tags — see MUTABLE_TAGS above).
// `aliasOf` names the first earlier tag in `image.tags`'/`available_tags`'
// original array order sharing the same digest, so e.g. a `latest` that
// just points at the newest release reads as "latest = 0826" instead of a
// mystery duplicate.
export function tagLanes(image) {
  const infos = Array.isArray(image?.tags) && image.tags.length
    ? image.tags
    : (image?.available_tags || []).map(t => ({ tag: t, digest: null, downloaded: null }));
  const seen = new Map(); // digest -> first tag carrying it
  const decorate = (t) => {
    const aliasOf = t.digest && seen.has(t.digest) ? seen.get(t.digest) : null;
    if (t.digest && !seen.has(t.digest)) seen.set(t.digest, t.tag);
    return { ...t, badge: image?.badges?.[t.tag] || null, aliasOf };
  };
  const lanes = { releases: [], pins: [], other: [] };
  for (const t of infos.map(decorate)) {
    if (RELEASE_TAG_RE.test(t.tag)) lanes.releases.push(t);
    else if (t.tag === image.tag) lanes.pins.push(t);   // headline pin
    else lanes.other.push(t);
  }
  if (!infos.some(t => t.tag === image.tag) && image?.tag) {
    lanes.pins.unshift({ tag: image.tag, digest: image.digest || null, downloaded: null, badge: image?.badges?.[image.tag] || null, aliasOf: null });
  }
  return lanes;
}

// The tag name behind newerTagAvailable's verdict — shared so the "newer:
// X" chip never names a tag the verdict didn't actually compare against
// (a per-tag digest-probe failure can drop a tag from `image.tags` without
// touching `available_tags`, so scanning the two lists independently could
// disagree). Digest path: the newest release-shaped tag other than the
// headline, from `image.tags`. Falls back to the pre-v3 name-based
// newestComparableTag() heuristic (mutable-pointer aware, see
// MUTABLE_TAGS) when the row predates the v3 per-tag payload. Returns
// `undefined` when there's no candidate either way.
export function newerTagCandidate(image) {
  if (!image || !image.tag) return undefined;
  const tags = Array.isArray(image.tags) ? image.tags : null;
  if (tags && tags.length) {
    // First release-shaped tag overall (not "first release-shaped tag other
    // than the headline") — excluding the headline from the scan meant that
    // when the headline WAS the newest release, an older release became the
    // "candidate" and the newer: chip showed permanently on up-to-date rows.
    // `tags` is already newest-first (sort_tags_newest_first), so the first
    // release-shaped entry is the newest release known; if that's the
    // headline itself (or there's no release-shaped tag at all), there's no
    // newer candidate.
    const cand = tags.find(t => RELEASE_TAG_RE.test(t.tag));
    if (!cand || cand.tag === image.tag) return undefined;
    return cand.tag;
  }
  return newestComparableTag(image);
}

// True when the registry knows a genuinely newer build than the row's
// headline `tag` — a digest fact when the row carries the v3 `tags[]`
// payload (the candidate's digest differs from the headline's; a probe
// that didn't resolve a digest for either side still counts as "newer" by
// name, same as the pre-v3 behavior). Falls back to the pre-v3 name-based
// heuristic when the row predates the v3 payload. Uses the same candidate
// as newerTagCandidate() above — see that comment for why that matters.
export function newerTagAvailable(image) {
  if (!image || !image.tag) return false;
  const cand = newerTagCandidate(image);
  if (!cand) return false;
  const tags = Array.isArray(image.tags) ? image.tags : null;
  if (tags && tags.length) {
    const head = tags.find(t => t.tag === image.tag);
    const candInfo = tags.find(t => t.tag === cand);
    if (head?.digest && candInfo?.digest) return candInfo.digest !== head.digest;
    return true;
  }
  return cand !== image.tag;               // pre-v3 fallback, unchanged
}

// Tag option label for the card's <select>: the tag name, its digest alias
// (`= <earlier tag>`) when it points at the same manifest as one already
// listed, a downloaded checkmark, and a trailing badge suffix.
function tagOptionLabel(t) {
  let label = t.tag;
  if (t.aliasOf) label += ` = ${t.aliasOf}`;
  if (t.downloaded) label += ' ✓';
  if (t.badge) label += ` · ${t.badge}`;
  return label;
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
  const images = imagesQuery.data?.images ?? [];
  const families = imagesQuery.data?.families ?? [];

  useEffectRI(() => {
    if (!selId && images.length) setSelId(images[0].id);
  }, [images, selId]);

  const filtered = images.filter(img => {
    if (!q.trim()) return true;
    const needle = q.trim().toLowerCase();
    const hay = `${img.id} ${img.image} ${img.tag} ${img.notes || ""}`.toLowerCase();
    return hay.includes(needle);
  });

  const grouped = groupRows(filtered);
  const selected = images.find(i => i.id === selId) || images[0];

  return (
    <div className="models-layout" style={{marginTop: 18}}>
        <div className="mdl-list">
          <FamilyStrip families={families} />
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

          {grouped.defaults.length > 0 && (
            <>
              <div className="mdl-list-h"><span>Default families</span></div>
              {grouped.defaults.map(img => (
                <RunnerImageRow key={img.id} image={img} selected={selId === img.id} onSelect={() => setSelId(img.id)} />
              ))}
            </>
          )}
          {grouped.specialized.length > 0 && (
            <>
              <div className="mdl-list-h"><span>Specialized</span></div>
              {grouped.specialized.map(img => (
                <RunnerImageRow key={img.id} image={img} selected={selId === img.id} onSelect={() => setSelId(img.id)} />
              ))}
            </>
          )}
          {grouped.other.length > 0 && (
            <>
              <div className="mdl-list-h"><span>Other catalogued</span></div>
              {grouped.other.map(img => (
                <RunnerImageRow key={img.id} image={img} selected={selId === img.id} onSelect={() => setSelId(img.id)} />
              ))}
            </>
          )}
        </div>

        <div className="models-sidebar">
          <RunnerCard image={selected} />
          <RunnerDownloadsPane />
        </div>
    </div>
  );
}

// Source chip for a family row's effective ref (see FamilyStrip below):
// override is the accent-colored chip with its clear button (unchanged from
// the old DefaultsStrip); env/manifest are plain chips naming the tier;
// release reads "release default" — the same label the strip has always
// shown for a non-override default.
function familySourceChip(source) {
  if (source === "override") {
    return <span className="chip" style={{color: "var(--accent)", borderColor: "var(--accent-line)"}}>override</span>;
  }
  if (source === "release") {
    return <span className="chip">release default</span>;
  }
  return <span className="chip">{source}</span>; // env | manifest
}

// ── FamilyStrip ─────────────────────────────────────────────────────────
// Launch-truth per-family strip (runner-catalogue-v3, Task 10 — replaces
// DefaultsStrip). Reads the server's `families` summary directly (Task 9's
// GET /api/runner-images `families` entry) instead of re-deriving it from
// image rows' `is_default` markers — the effective ref, its source tier,
// the store state of THAT ref, which slots launch it now vs. a different
// tag of the same repo, and whether the registry already has a newer
// release-shaped tag. The override clear-confirm flow moves in verbatim
// from DefaultsStrip: clearing writes `{family: null}` through the settings
// override map and falls back to the baked release default, so the confirm
// names the slots that will drift — now sourced from the family's own
// `slots` list rather than a row's `in_use_by`.
function FamilyStrip({ families }) {
  const rows = families || [];
  const setDefault = useSetDefaultImage();
  const [clearFamily, setClearFamily] = useStateRI(null);
  if (rows.length === 0) return null;

  const clearRow = rows.find(f => f.family === clearFamily) || null;
  const drifters = clearRow?.slots || [];

  const onClear = () => {
    const fam = clearFamily;
    setClearFamily(null);
    setDefault.mutate({ family: fam, ref: null }, {
      onSuccess: () => window.__hal0Toast && window.__hal0Toast(`${fam}: override cleared — release default applies`, "info"),
      onError: (e) => window.__hal0Toast && window.__hal0Toast(`Clear failed — ${e?.message || "see logs"}`, "err"),
    });
  };

  return (
    <div className="ri-defaults" data-testid="ri-family-strip" style={{padding: "10px 16px", borderBottom: "1px solid var(--line-soft)"}}>
      <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 6}}>
        Family defaults
      </div>
      {rows.map(f => (
        <div key={f.family} data-testid={`ri-family-${f.family}`} style={{padding: "3px 0"}}>
          <div style={{display: "flex", alignItems: "center", gap: 8, fontSize: 12}}>
            <span className="mono" style={{color: "var(--fg-2)", minWidth: 88}}>{f.family}</span>
            <span className="mono" style={{color: "var(--fg-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{f.effective_ref}</span>
            {familySourceChip(f.source)}
            <StoreStateChip image={{ store_state: f.store_state, downloaded: f.store_state === "present" }} />
            {f.update_available && f.newest_release && (
              <span className="chip" data-testid="ri-family-newer" style={{color: "var(--accent)", borderColor: "var(--accent-line)"}}>
                newer: {f.newest_release.tag}
              </span>
            )}
            {f.source === "override" && (
              <button
                className="btn ghost sm"
                data-testid={`ri-clear-default-${f.family}`}
                disabled={setDefault.isPending}
                onClick={() => setClearFamily(f.family)}
                title="Remove the operator override — the release default applies again"
              >clear</button>
            )}
          </div>
          {(f.slots?.length > 0 || f.pinned_slots?.length > 0) && (
            <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", marginTop: 2}}>
              {f.slots?.length > 0 && `via slots: ${f.slots.join(", ")}`}
              {f.slots?.length > 0 && f.pinned_slots?.length > 0 && " · "}
              {f.pinned_slots?.length > 0 && `pinned: ${f.pinned_slots.join(", ")}`}
            </div>
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
            ? `Remove the ${clearRow.family} override (${clearRow.effective_ref}) and fall back to the release default.` +
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

// Badge chip color for a tag's validated/candidate/deprecated badge
// (hal0.api.routes.runner_images._tag_badges).
const BADGE_COLOR = {
  validated: "var(--ok)",
  candidate: "var(--accent)",
  deprecated: "var(--err)",
};

function BadgeChip({ badge }) {
  if (!badge) return null;
  const color = BADGE_COLOR[badge] || "var(--fg-4)";
  return (
    <span className="chip" data-testid="ri-badge" style={{color, borderColor: color}}>{badge}</span>
  );
}

// Store-truth state chip: store_state "unknown" (the store couldn't be
// read this request) takes precedence over the downloaded verdict —
// showing "not downloaded" there would be a claim the backend can't back.
function StoreStateChip({ image }) {
  if (image.store_state === "unknown") {
    return <span className="chip" data-testid="ri-store-state" title="Could not read the image store">state unknown</span>;
  }
  return image.downloaded
    ? <span className="chip ok" data-testid="ri-store-state">✓ downloaded</span>
    : <span className="chip" data-testid="ri-store-state">not downloaded</span>;
}

// ── RunnerImageRow ──────────────────────────────────────────────────────
function RunnerImageRow({ image, selected, onSelect }) {
  return (
    <div className={"mdl-row" + (selected ? " sel" : "")} onClick={onSelect}>
      <span className="mdl-row-icon">
        {image.downloaded
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
            newer: {newerTagCandidate(image)}
          </span>
        )}
        <BadgeChip badge={image.badges?.[image.tag]} />
        {image.ownership && <span className="chip">{image.ownership}</span>}
        {image.publish && <span className="chip">{image.publish}</span>}
        {(image.extra?.features || []).map(f => (
          <span key={f} className="chip" style={{color: "var(--accent)", borderColor: "var(--accent-line)"}}>{f}</span>
        ))}
      </span>
      <span className="sz num">{fmtBytesRI(image.size_bytes)}</span>
      <span className="tg">
        <StoreStateChip image={image} />
      </span>
    </div>
  );
}

// ── RunnerCard (detail view) ────────────────────────────────────────────
function RunnerCard({ image }) {
  const pull = useRunnerImagePullJob();
  const setDefault = useSetDefaultImage();
  const restartAffected = useRestartAffected();
  const [confirmRestart, setConfirmRestart] = useStateRI(false);
  // Tag picker over the contract's available_tags (headline preselected).
  // null = "follow the headline"; reset whenever the selected row changes.
  const [pickedTag, setPickedTag] = useStateRI(null);
  const imageId = image?.id;
  useEffectRI(() => { setPickedTag(null); }, [imageId]);
  const [confirmDefault, setConfirmDefault] = useStateRI(false);
  // "other" lane (branch heads, floating dev tags) is collapsed behind this
  // toggle — resets alongside the tag pick whenever the selected row changes.
  const [showAllTags, setShowAllTags] = useStateRI(false);
  useEffectRI(() => { setShowAllTags(false); }, [imageId]);

  if (!image) {
    return (
      <div className="mdl-detail" style={{padding: 24, textAlign: "center", color: "var(--fg-4)"}}>
        Select a runner image.
      </div>
    );
  }

  const lanes = tagLanes(image);
  const tagCount = lanes.releases.length + lanes.pins.length + lanes.other.length;
  const selTag = pickedTag ?? image.tag;
  const inFlight = pull.imageId === image.id && pull.inFlight;
  const onPull = async () => {
    try {
      await pull.start(image.id, selTag !== image.tag ? selTag : undefined);
      window.__hal0Toast && window.__hal0Toast(`Pulling ${image.id}:${selTag}…`, "info");
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

  // Restart-affected (#2096 page-side workaround, Task 12): `in_use_by` is
  // matched server-side against this exact `image:tag` headline ref (see
  // enrich_row's `row_ref`), not the tag picker's `selTag` — restarting
  // targets what's actually launched right now, independent of whatever tag
  // the operator has picked in the dropdown above.
  const headlineRef = `${image.image}:${image.tag}`;
  const onRestartAffected = () => {
    setConfirmRestart(false);
    restartAffected.mutate({ ref: headlineRef }, {
      onSuccess: (res) => {
        const n = res?.restarted?.length ?? 0;
        window.__hal0Toast && window.__hal0Toast(`Restarted ${n} slot${n === 1 ? "" : "s"}`, "info");
      },
      onError: (e) => window.__hal0Toast && window.__hal0Toast(`Restart failed — ${e?.message || "see logs"}`, "err"),
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
            <StoreStateChip image={image} />
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
        {tagCount > 1 && (
          <div style={{display: "flex", alignItems: "center", gap: 8, marginBottom: 10, flexWrap: "wrap"}}>
            <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>tag</span>
            <select
              className="input mono"
              data-testid="ri-tag-pick"
              value={selTag}
              onChange={(e) => setPickedTag(e.target.value)}
              style={{fontSize: 11, padding: "3px 6px"}}
            >
              {lanes.releases.length > 0 && (
                <optgroup label="releases">
                  {lanes.releases.map(t => (
                    <option key={t.tag} value={t.tag}>{tagOptionLabel(t)}</option>
                  ))}
                </optgroup>
              )}
              {lanes.pins.length > 0 && (
                <optgroup label="pins">
                  {lanes.pins.map(t => (
                    <option key={t.tag} value={t.tag}>{tagOptionLabel(t)}</option>
                  ))}
                </optgroup>
              )}
              {/* Rendered whenever toggled open OR the current pick lives in
                  this lane — a controlled <select> whose value names an
                  unmounted <option> falls back to the first rendered one,
                  silently overriding React's own state (e.g. pick an
                  other-lane tag, then toggle "show all tags" back off). */}
              {(showAllTags || lanes.other.some(t => t.tag === selTag)) && lanes.other.length > 0 && (
                <optgroup label="other">
                  {lanes.other.map(t => (
                    <option key={t.tag} value={t.tag}>{tagOptionLabel(t)}</option>
                  ))}
                </optgroup>
              )}
            </select>
            {lanes.other.length > 0 && (
              <button
                type="button"
                className="btn ghost sm"
                data-testid="ri-show-all-tags"
                onClick={() => setShowAllTags(s => !s)}
              >{showAllTags ? "hide extra tags" : "show all tags"}</button>
            )}
            {newerTagAvailable(image) && (
              <span className="chip" style={{color: "var(--accent)", borderColor: "var(--accent-line)"}}>
                newer: {newerTagCandidate(image)}
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
              onClick={onPull}
            >
              {Icons.download} {image.local_path ? "Re-pull" : "Pull"} <span className="mono" style={{fontSize: 10, opacity: .7}}>:{selTag}</span>
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
            {inUseBy.length > 0 && (
              <button
                className="btn ghost"
                data-testid="ri-restart-affected"
                disabled={restartAffected.isPending}
                title={`Restart the ${inUseBy.length} slot${inUseBy.length === 1 ? "" : "s"} launching ${headlineRef}`}
                onClick={() => setConfirmRestart(true)}
              >Restart {inUseBy.length} affected slot{inUseBy.length === 1 ? "" : "s"}</button>
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

      <ConfirmDialog
        open={confirmRestart}
        onCancel={() => setConfirmRestart(false)}
        onConfirm={onRestartAffected}
        title="Restart affected slots"
        message={
          `Restart ${inUseBy.length} slot${inUseBy.length === 1 ? "" : "s"} launching ${headlineRef}: ${inUseBy.join(", ")}.`
        }
        confirmLabel="Restart"
        footerNote="A slot that fails to restart is skipped and logged — the rest still restart."
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
