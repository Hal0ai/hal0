"""Model discovery — scan filesystem roots and auto-register found models.

The scanner walks each configured root (see :class:`hal0.config.schema.ModelsConfig`)
and looks for files matching ``ModelsConfig.file_extensions``.  Each
candidate is normalised, fingerprinted against the curated catalogue by
filename, and registered with the :class:`hal0.registry.store.ModelRegistry`
unless an entry already points at the same path.

This is the manual ``POST /api/models/scan`` path AND the startup
auto-scan in :func:`hal0.api.lifespan` — both share
:func:`scan_and_register`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hal0.config.schema import ModelsConfig
from hal0.model_meta import capability_from_filename, derive_model_provider
from hal0.registry import tombstones
from hal0.registry.curated import CURATED_MODELS, CuratedModel
from hal0.registry.fileset import SHARD_RE
from hal0.registry.model import Model
from hal0.registry.store import ModelAlreadyExists, ModelRegistry

log = logging.getLogger(__name__)

#: Serialises a scan with a model delete (ADR-0026). The delete removes the
#: row, cleans up files and writes tombstones while holding it; without it a
#: scan landing between the row removal and the tombstone re-registers the
#: file, and the cleanup then treats the new row as a sharing owner.
SCAN_DELETE_LOCK = threading.Lock()

# Soft budget — beyond this the scan returns what it has and logs a warning.
_SCAN_BUDGET_SECONDS = 30.0

# Directory names whose contents are always skipped (vision projectors,
# tokenizer assets, training checkpoints — none of those are standalone
# models the dispatcher can route to). Also skip:
#   - HuggingFace cache internals (`blobs` holds hash-named binary blobs;
#     `snapshots/<rev>/<file>` symlinks into them are followed instead).
#   - ComfyUI accessory model directories — the .safetensors there are
#     auxiliary components (VAEs, text encoders, control nets) used by
#     a full image-gen workflow, not standalone models the dispatcher
#     can route to. Once we add a proper image-gen surface these will
#     come back through that channel.
_SKIP_DIR_NAMES = frozenset(
    {
        "mmproj",
        ".git",
        "__pycache__",
        "blobs",
        # HF cache "this file doesn't exist on remote" sentinel — 0-byte
        # markers that satisfy the existence check but aren't real files.
        ".no_exist",
        # ComfyUI accessory components — pulled in via an image-gen
        # workflow, not standalone loadable models. Bring back through a
        # dedicated image-gen surface once one exists.
        "vae",
        "vae_approx",
        "clip",
        "clip_vision",
        "controlnet",
        "embeddings",
        "loras",
        "text_encoders",
        "upscale_models",
        "diffusion_models",
        "comfyui-nodes",
        # ComfyUI custom-node checkouts bundle their own detector / helper
        # weights; those are node assets, never routable chat models.
        "custom_nodes",
        "comfyui-user",
        # vibevoice / moonshine model assets live under voices/; those
        # are managed by their respective slot providers, not by the
        # generic dispatcher.
        "voices",
    }
)

# A filename whose stem is a long pure-hex string is almost always an
# HF cache blob (and the symlink that references it lives under
# snapshots/ with a real name). We deduplicate via symlink resolution
# upstream of this filter; this is a belt-and-suspenders fallback.
_HEX_BLOB_RE = re.compile(r"^[0-9a-f]{32,}$")

# HF Transformers multi-file shard pattern (e.g. model-00001-of-00003.safetensors).
#
# ML-2 (plan §7.1c a4 / seam S11): this used to be a LOCAL, stem-only pattern
# (``^.+-\d{5}-of-\d{5}$``) whose only use was dropping every shard on sight
# — a sharded model was invisible to auto-scan. ``SHARD_RE`` is now the
# SINGLE shared definition (:mod:`hal0.registry.fileset`), matched against
# the full filename (incl. extension). ``find_candidates`` below groups a
# complete shard set into ONE candidate (shard-1 as the entry point) instead
# of dropping every part; ``_is_skippable`` still recognises individual shard
# filenames (for callers that want "is this independently interesting",
# e.g. the scan-preview per-file listing) but no longer means every shard is
# silently discarded from registration — that grouping happens upstream of
# this check, in ``find_candidates``.


# ── Candidate dataclass ───────────────────────────────────────────────────


@dataclass
class CandidateModel:
    """One discovered file ready for registry registration."""

    path: Path
    size_bytes: int
    suggested_id: str
    curated_match: CuratedModel | None
    capability_guess: str
    # Resolved path to a multimodal projector (mmproj) GGUF sidecar that sits
    # in the same directory, or None. Associated post-walk by find_candidates.
    mmproj: Path | None = None
    # Ordered list of sibling shard paths (INCLUDING `path` itself as
    # shard-1/entry point) when this candidate is a multi-shard model
    # (plan §7.1c a4 — discover groups instead of dropping). None for an
    # ordinary single-file candidate.
    shards: list[Path] | None = None


# ── helpers ───────────────────────────────────────────────────────────────


def _normalise_id(stem: str) -> str:
    """Turn a basename stem into a registry-friendly id."""
    lowered = stem.lower()
    replaced = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    collapsed = re.sub(r"-+", "-", replaced)
    return collapsed or "model"


def _guess_capability(filename: str) -> str:
    """Best-effort capability inference from the filename.

    Delegates to the single shared token table in
    :func:`hal0.model_meta.capability_from_filename` (MR-3) so the auto-scan,
    single-file detect, and ``classify`` heuristics can no longer drift.
    Reranker filenames now yield ``rerank`` instead of the old ``chat``
    default. ``chat`` remains the default for unrecognised filenames — the
    #940 backstop contract ``SlotManager._fallback_local_model`` relies on.
    """
    return capability_from_filename(filename) or "chat"


def _match_curated(filename: str) -> CuratedModel | None:
    """Return the curated entry whose ``hf_file`` equals ``filename``."""
    base = Path(filename).name
    for entry in CURATED_MODELS:
        if entry.hf_file == base:
            return entry
    return None


def _is_mmproj_sidecar(p: Path) -> bool:
    """True for a multimodal-projector (mmproj) sidecar file.

    Matched by filename rather than suffix: the real artifact is named
    ``mmproj-F32.mmproj`` and ``.mmproj`` is not one of the configured model
    ``file_extensions``, so an extension check would miss it.
    """
    return "mmproj" in p.name.lower()


def _shard_key(p: Path) -> tuple[str, str, str] | None:
    """Return the ``(dir, stem, total)`` grouping key for a shard file, else ``None``.

    Matched against ``p.name`` (the shared :data:`SHARD_RE` includes the
    extension) — the positive twin of the skip check below: this decides
    which files belong to the SAME shard set so ``find_candidates`` can
    group them into one candidate instead of dropping them.
    """
    m = SHARD_RE.match(p.name)
    if not m:
        return None
    return (str(p.parent), m.group("stem"), m.group("tot"))


def _shard_index(p: Path) -> int | None:
    """Return the 1-based shard index encoded in ``p``'s filename, or ``None``."""
    m = SHARD_RE.match(p.name)
    return int(m.group("idx")) if m else None


def _is_skippable(p: Path) -> bool:
    """Skip dotfiles, .tmp partials, hash-only blob names, shards, accessory dirs.

    A shard file is still flagged here (for callers wanting "is this
    independently interesting", e.g. the scan-preview per-file listing) —
    but ``find_candidates`` intercepts complete shard SETS earlier and
    surfaces them as one grouped candidate (plan §7.1c a4); this check no
    longer means "silently discarded forever" the way it used to.
    """
    name = p.name
    if name.startswith("."):
        return True
    if name.endswith(".tmp"):
        return True
    if "mmproj" in name.lower():
        return True
    if _HEX_BLOB_RE.match(p.stem):
        return True
    if SHARD_RE.match(name):
        return True
    return any(part in _SKIP_DIR_NAMES for part in p.parts)


# ── public API ────────────────────────────────────────────────────────────


def find_candidates(
    roots: list[str | Path],
    extensions: list[str],
    known_paths: set[str],
    ignored_paths: set[str] | None = None,
    known_inodes: set[tuple[int, int]] | None = None,
) -> list[CandidateModel]:
    """Walk each root and return :class:`CandidateModel`s not already registered.

    Files whose absolute path is in ``known_paths`` are skipped silently
    so a re-scan after a manual registry add doesn't fight itself. The
    walk is case-insensitive on extension to handle ``.GGUF`` vs ``.gguf``.

    ``ignored_paths`` are tombstoned resolved paths
    (:mod:`hal0.registry.tombstones`): a file at or below one is never a
    candidate, so a model deleted with its files kept does not reappear.
    Any directory BELOW a root whose name starts with ``.`` is skipped
    (``.Trash-1000/``, ``.cache/``…); the root itself may live under a dot
    directory.

    ``known_inodes`` are ``(st_dev, st_ino)`` pairs of already-registered
    files: a hardlinked copy of a registered model (the same bytes under a
    second name, e.g. an HF-cache blob beside a store copy) is not a new
    candidate.
    """
    ignored = ignored_paths or set()
    inodes = known_inodes or set()
    exts = {e.lower() for e in extensions}
    seen: set[Path] = set()
    out: list[CandidateModel] = []
    # Resolved directory → resolved mmproj sidecar path. Collected during the
    # walk and associated with sibling candidates once the walk completes, so
    # ordering (sidecar before or after its model) doesn't matter.
    mmproj_by_dir: dict[Path, Path] = {}
    # Shard grouping key -> {shard_index: resolved path}. Populated during
    # the walk (plan §7.1c a4); closed out into ONE CandidateModel per
    # complete group (shard-1 present) after the walk, instead of the old
    # "drop every shard" behaviour. An incomplete group (no shard-1 seen)
    # is silently dropped, same as before.
    shard_groups: dict[tuple[str, str, str], dict[int, Path]] = {}
    shard_names: dict[tuple[str, str, str], Path] = {}
    started = time.monotonic()
    for root in roots:
        root_path = Path(root).expanduser()
        if not root_path.exists() or not root_path.is_dir():
            continue
        for candidate in _walk_files(root_path, ignored):
            if time.monotonic() - started > _SCAN_BUDGET_SECONDS:
                log.warning(
                    "discover.budget_exceeded after=%.1fs roots=%s — returning partial results",
                    time.monotonic() - started,
                    [str(r) for r in roots],
                )
                return out
            try:
                if not candidate.is_file():
                    continue
            except OSError:
                continue
            # Record mmproj sidecars for association, then skip them so they
            # never become standalone routable candidates. Done before the
            # generic skip rule (which also drops mmproj) and before the
            # extension check (the real sidecar's .mmproj suffix isn't listed).
            if _is_mmproj_sidecar(candidate):
                try:
                    mmproj_abs = candidate.resolve()
                except OSError:
                    mmproj_abs = candidate
                # A tombstoned sidecar stays ignored: attaching it to a sibling
                # would hand the file back to a model (and to its later delete).
                if not tombstones.is_tombstoned(mmproj_abs, ignored):
                    mmproj_by_dir.setdefault(mmproj_abs.parent, mmproj_abs)
                continue
            # Shard grouping (plan §7.1c a4): bucket by (dir, stem, total)
            # instead of falling into the generic skip rule below, which
            # would drop it unconditionally. The group is closed out (or
            # dropped, if incomplete) after the walk finishes.
            shard_key = _shard_key(candidate)
            if shard_key is not None:
                if candidate.suffix.lower() not in exts:
                    continue
                # Honour the skip dirs here too (#2443): a complete shard
                # set bundled in e.g. ``custom_nodes/`` must not group into
                # a chat candidate. Check the UN-resolved path — HF snapshot
                # shards resolve into ``blobs/``, which is itself skip-listed —
                # and only the dirs BELOW the scan root, so a root such as
                # ``/data/embeddings/models`` still registers its shard sets.
                if _under_skip_dir(candidate, root_path):
                    continue
                try:
                    shard_abs = candidate.resolve()
                except OSError:
                    shard_abs = candidate
                idx = _shard_index(candidate)
                if idx is not None:
                    shard_groups.setdefault(shard_key, {})[idx] = shard_abs
                    if idx == 1:
                        # Name from the walked path, not the resolved one:
                        # an HF snapshot shard resolves to a hash-named blob.
                        shard_names[shard_key] = candidate
                continue
            if _is_skippable(candidate):
                continue
            if candidate.suffix.lower() not in exts:
                continue
            abs_path = candidate.resolve()
            # Re-check on the resolved path so HF snapshot symlinks that
            # point into a `/blobs/<sha>` cache get skipped — their suffix
            # check passes (the symlink name has the right extension) but
            # the resolved target has no extension and a hex-blob stem.
            if _is_skippable(abs_path):
                continue
            if str(abs_path) in known_paths:
                continue
            if tombstones.is_tombstoned(abs_path, ignored):
                continue
            if abs_path in seen:
                continue
            seen.add(abs_path)
            try:
                st = abs_path.stat()
            except OSError:
                size = 0
            else:
                if (st.st_dev, st.st_ino) in inodes:
                    continue
                size = st.st_size
            # Prefer the symlink filename for id derivation + curated
            # match: the resolved blob is hash-named, but the snapshot
            # symlink carries the human-meaningful name.
            naming_source = candidate
            out.append(
                CandidateModel(
                    path=abs_path,
                    size_bytes=size,
                    suggested_id=_normalise_id(naming_source.stem),
                    curated_match=_match_curated(naming_source.name),
                    capability_guess=_guess_capability(naming_source.name),
                )
            )
    # Close out shard groups (plan §7.1c a4): a COMPLETE group (shard-1
    # present) becomes one candidate keyed off shard-1, aggregate size, and
    # the full ordered sibling list. An incomplete group (shard-1 missing —
    # e.g. only shards 2-3 of a 3-part set landed) is dropped entirely, same
    # as the old behaviour, since llama-server can't load a set missing its
    # entry point. Already-registered groups are filtered by shard-1's path,
    # mirroring the single-file `known_paths` check above.
    for key, idx_map in shard_groups.items():
        if 1 not in idx_map:
            continue
        ordered = [idx_map[i] for i in sorted(idx_map)]
        entry_path = ordered[0]
        if str(entry_path) in known_paths or entry_path in seen:
            continue
        if any(tombstones.is_tombstoned(sp, ignored) for sp in ordered):
            continue
        if _inode(entry_path) in inodes:
            continue
        seen.add(entry_path)
        total_size = 0
        for shard_path in ordered:
            try:
                total_size += shard_path.stat().st_size
            except OSError:
                continue
        naming_source = shard_names.get(key, entry_path)
        out.append(
            CandidateModel(
                path=entry_path,
                size_bytes=total_size,
                suggested_id=_normalise_id(naming_source.stem),
                curated_match=_match_curated(naming_source.name),
                capability_guess=_guess_capability(naming_source.name),
                shards=ordered,
            )
        )
    # Associate each sidecar with sibling main models in the same directory.
    for cand in out:
        sidecar = mmproj_by_dir.get(cand.path.parent)
        if sidecar is not None:
            cand.mmproj = sidecar
    return out


def register_candidate(registry: ModelRegistry, candidate: CandidateModel) -> Model:
    """Build a :class:`Model` from ``candidate`` and add it to ``registry``."""
    curated = candidate.curated_match
    # A checkpoint discovered under the ComfyUI models tree is an image-gen
    # model, not a chat model — tag it capability "image" / backend "comfyui"
    # so it lands on the dashboard's image-gen surface instead of the llm
    # bucket (and out of the chat fallback pool). ``checkpoints/`` is the one
    # ComfyUI subdir the scan walks; the accessory dirs are skip-listed above.
    is_comfyui = "/comfyui/models/" in str(candidate.path)
    comfyui_backends = ["comfyui"] if is_comfyui else []
    if curated is not None:
        model = Model(
            id=curated.id,
            name=curated.display_name,
            path=str(candidate.path),
            size_bytes=candidate.size_bytes,
            license=curated.license,
            capabilities=[curated.capability]
            if curated.capability
            else (["image"] if is_comfyui else ["chat"]),
            backends=comfyui_backends,
            # Task 3: stamp provider alongside the tag-era backends tag on a
            # NEW row — backends stays a vestigial lane hint (non-comfyui
            # rows carry none), provider is the write-path source of truth.
            provider=derive_model_provider(comfyui_backends),
            hf_repo=curated.hf_repo,
            hf_filename=curated.hf_file,
            tags=list(curated.tags),
            metadata={"discovered": True, "source": "auto-scan"},
            # Curated arch id (§7.1d) — feeds the model↔runner arch
            # fit-check (hal0#2118). The auto-scan path deliberately reads
            # no GGUF header (it may walk thousands of files), so the
            # uncurated branch below leaves ``architecture`` unset; those
            # rows pick it up if re-registered via scan-commit/add-from-path.
            architecture=curated.architecture,
        )
    else:
        model = Model(
            id=candidate.suggested_id,
            name=candidate.path.stem,
            path=str(candidate.path),
            size_bytes=candidate.size_bytes,
            capabilities=["image"] if is_comfyui else [candidate.capability_guess],
            backends=comfyui_backends,
            provider=derive_model_provider(comfyui_backends),
            metadata={"discovered": True, "source": "auto-scan"},
        )
    # Carry a discovered mmproj sidecar onto the model so the llama-server
    # provider can surface it as --mmproj. None when no sidecar was found.
    if candidate.mmproj is not None:
        model.mmproj = str(candidate.mmproj)
    # Multi-shard group (plan §7.1c a4): the shard list rides in metadata as
    # the lossless fallback for any registry backend, plus a best-effort
    # `model_file` row per shard/mmproj on the SQLite backend (see
    # `_maybe_register_shard_files`).
    if candidate.shards:
        model.metadata = {**model.metadata, "shards": [str(p) for p in candidate.shards]}
    try:
        registry.add(model)
    except ModelAlreadyExists:
        # A concurrent scan or manual add already claimed this id — return
        # the existing entry without raising so the caller's "added" count
        # stays meaningful.
        return registry.get(model.id)
    _maybe_register_shard_files(registry, model, candidate)
    return model


def _maybe_register_shard_files(
    registry: ModelRegistry, model: Model, candidate: CandidateModel
) -> None:
    """Best-effort ``model_file`` rows for a discovered shard group.

    Only applies when ``registry`` is SQLite-backed (duck-typed via
    ``db_path`` — the public ``ModelRegistry`` name is bound to
    :class:`hal0.registry.sqlite_store.SqliteModelRegistry`). The historic
    TOML store has no such table; ``model.metadata["shards"]`` above is the
    lossless fallback for that path. Never raises — a `model_file` write
    failure must not undo the registry row that just committed.
    """
    db_path = getattr(registry, "db_path", None)
    if db_path is None or not candidate.shards:
        return
    from hal0.db import repository
    from hal0.db.connection import connect, tx

    total = len(candidate.shards)
    try:
        with connect(db_path) as conn, tx(conn):
            for idx, shard_path in enumerate(candidate.shards, start=1):
                try:
                    size = shard_path.stat().st_size
                except OSError:
                    size = None
                repository.insert_model_file(
                    conn,
                    model_id=model.id,
                    rel=shard_path.name,
                    dest=str(shard_path),
                    size_bytes=size,
                    role="shard" if total > 1 else "model",
                    shard_index=idx if total > 1 else None,
                )
            if candidate.mmproj is not None:
                try:
                    mm_size = candidate.mmproj.stat().st_size
                except OSError:
                    mm_size = None
                repository.insert_model_file(
                    conn,
                    model_id=model.id,
                    rel=candidate.mmproj.name,
                    dest=str(candidate.mmproj),
                    size_bytes=mm_size,
                    role="mmproj",
                    shard_index=None,
                )
    except Exception:
        log.warning("discover.shard_model_file_write_failed model_id=%s", model.id, exc_info=True)


def backfill_coordless(registry: ModelRegistry) -> list[str]:
    """Repair existing registry rows that have empty HF coordinates.

    A row auto-registered before its curated coords landed carries empty
    ``hf_repo``/``hf_filename`` (so it classifies "unresolvable" on stack
    import and can't be pulled by id). For each such row, match it against the
    curated catalogue by the on-disk filename and fill in
    ``hf_repo``/``hf_filename`` — plus ``name``/``tags`` when those are empty —
    from the curated entry. The model id is never changed.

    Returns the list of ids that were backfilled. Idempotent: a row that
    already carries both coordinates is left untouched, so a second call is a
    no-op.
    """
    repaired: list[str] = []
    for row in registry.list():
        if row.hf_repo and row.hf_filename:
            continue
        curated = _match_curated(Path(row.path).name)
        if curated is None:
            continue
        registry.update(
            row.id,
            {
                "hf_repo": row.hf_repo or curated.hf_repo,
                "hf_filename": row.hf_filename or curated.hf_file,
                "name": row.name or curated.display_name,
                "tags": row.tags or list(curated.tags),
            },
        )
        repaired.append(row.id)
    return repaired


#: Origin marker :func:`register_candidate` stamps on every row it writes.
#: Only rows carrying it are eligible for :func:`reconcile_skipped_auto_scan`;
#: scan-commit (``"scan"``), add-from-path, pulls and hand-added rows carry a
#: different source (or none) and are never touched.
_AUTO_SCAN_SOURCE = "auto-scan"

#: Skip dirs that mark a stored registry path as "would no longer be
#: discovered". ``blobs`` is excluded: an auto-scanned HF shard set stores
#: its RESOLVED shard-1 path, which legitimately lives under ``blobs/``.
_RECONCILE_DIR_NAMES = _SKIP_DIR_NAMES - {"blobs"}


def _under_skip_dir(path: Path, root: Path) -> bool:
    """True when a directory BETWEEN ``root`` and ``path`` is a skip dir.

    Skip-dir names only count below the scan root: a root such as
    ``/data/embeddings/models`` is not itself "under a skipped dir".
    ``path`` must be inside ``root``.
    """
    return any(part in _SKIP_DIR_NAMES for part in path.parent.relative_to(root).parts)


def _inode(path: Path) -> tuple[int, int] | None:
    """``(st_dev, st_ino)`` of ``path``, or ``None`` if it cannot be stat'd."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _walk_files(root: Path, ignored: set[str] | None = None) -> Iterator[Path]:
    """Yield every file below ``root`` without descending into dot directories.

    ``os.walk`` lets the walk prune ``.Trash-1000/``, ``.cache/`` and similar
    trees in place, so they cost neither stats nor the scan budget. Only
    directories BELOW ``root`` are pruned: a root that itself sits under a
    dot directory (``~/.cache/models``) still scans. Symlinked directories
    are not followed, matching the old ``Path.rglob`` walk; symlinked files
    (HF ``snapshots/``) are yielded. The file name itself is covered by
    :func:`_is_skippable`. A directory at or below a tombstoned path
    (``ignored``) is pruned too, so a kept FLM/ONNX model tree costs nothing.
    """

    def _onerror(exc: OSError) -> None:
        log.warning("discover.walk_failed root=%s err=%s", root, exc)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=_onerror):
        here = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".")
            and not (ignored and tombstones.is_tombstoned(os.path.realpath(here / d), ignored))
        ]
        for name in filenames:
            yield here / name


def _containing_roots(path: Path, roots: list[Path]) -> list[Path]:
    """The roots (as given, and resolved) that contain ``path``, deduped."""
    out: list[Path] = []
    for root in roots:
        forms = [root]
        with contextlib.suppress(OSError):
            forms.append(root.resolve())
        for form in forms:
            if form not in out and path.is_relative_to(form):
                out.append(form)
    return out


def reconcile_skipped_auto_scan(
    registry: ModelRegistry,
    *,
    roots: list[str | Path],
    protected_ids: set[str],
) -> dict:
    """Drop auto-scan rows whose stored path now sits under a skip dir.

    An older scan may have registered files the skip rules now exclude (e.g.
    a ComfyUI ``custom_nodes/`` asset registered as a chat LLM, #2443). The
    add pass never revisits them (their paths are in ``known_paths``) and
    :func:`prune_missing` only drops rows whose file is gone, so without
    this the bogus row survives every upgrade.

    A row is removed only if ALL hold: ``metadata["source"] == "auto-scan"``;
    its stored path is under at least one of ``roots``; relative to EVERY
    root containing it, a directory below that root is a skip dir (``blobs``
    excepted); and its id is not in ``protected_ids``. Rows outside every
    root are left alone. Protected matches are reported under ``referenced``
    instead, mirroring ``prune_missing``'s ``missing_referenced``. Returns
    ``{"removed": [...], "referenced": [...]}``.
    """
    root_paths = [Path(r).expanduser() for r in roots]
    removed: list[str] = []
    referenced: list[str] = []
    for m in registry.list():
        if not _is_reconcilable(m, root_paths):
            continue
        if m.id in protected_ids:
            log.warning(
                "discover.reconcile_kept_referenced id=%s path=%s — under a skipped dir "
                "but referenced by a slot/stack",
                m.id,
                m.path,
            )
            referenced.append(m.id)
            continue
        try:
            registry.remove(m.id)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("discover.reconcile_remove_failed id=%s err=%s", m.id, exc)
            continue
        log.info("discover.reconcile_removed id=%s path=%s — under a skipped dir", m.id, m.path)
        removed.append(m.id)
    return {"removed": removed, "referenced": referenced}


def _is_reconcilable(m: Model, roots: list[Path]) -> bool:
    """True for an auto-scan row that every scan root containing it would now
    skip (a skip dir below that root, ``blobs`` excepted)."""
    if (m.metadata or {}).get("source") != _AUTO_SCAN_SOURCE:
        return False
    path = Path(m.path)
    containing = _containing_roots(path, roots)
    if not containing:
        return False
    return all(
        any(part in _RECONCILE_DIR_NAMES for part in path.parent.relative_to(root).parts)
        for root in containing
    )


def is_runtime_managed(m: Model) -> bool:
    """True for a row whose bytes a runtime manages, not hal0 (FLM).

    FLM rows (``metadata.runtime == "flm"`` or ``provider == "flm"``) record
    the FLM model directory as ``path``; FLM pulls and removes those files
    itself. Prune never drops them and model delete never removes their
    directory.
    """
    if (m.metadata or {}).get("runtime") == "flm":
        return True
    return getattr(m, "provider", None) == "flm"


def _root_is_live(root: Path) -> bool:
    """True when a missing file under ``root`` can be trusted as deleted.

    * non-empty → live.
    * empty and a mount point (``os.path.ismount``) → live: the share IS
      mounted, it just holds nothing now.
    * empty and not a mount point, or absent → not live. That is what an
      unmounted NFS share looks like (its mount point is an empty local
      directory), so every row under it is "unknown", not "gone".

    Trade-off: an ordinary local root whose last model file was removed by
    hand is also empty and not a mount point, so its ghost row is kept as
    ``missing_unverified`` until the root holds something again or the row
    is deleted explicitly (ADR-0026).
    """
    try:
        with os.scandir(root) as it:
            if any(True for _ in it):
                return True
    except OSError:
        return False
    try:
        return os.path.ismount(root)
    except OSError:
        return False


def _missing_is_trustworthy(path: Path, roots: list[Path], live: dict[Path, bool]) -> bool:
    """Whether a missing ``path`` really means "deleted", not "not mounted".

    Under a scan root, the root must be live (:func:`_root_is_live`). Outside
    every root, the file's parent directory must still exist.
    """
    containing = [r for r in roots if path.is_relative_to(r)]
    if containing:
        for root in containing:
            if root not in live:
                live[root] = _root_is_live(root)
        return all(live[r] for r in containing)
    try:
        return path.parent.is_dir()
    except OSError:
        return False


def _prune_row(registry: ModelRegistry, model_id: str) -> None:
    """Remove one pruned row and release its ``store_blob`` references.

    The row's ``model_file`` rows cascade away with it; without the release
    the blob would stay positively referenced forever and
    :func:`hal0.registry.gc.prune_orphans` could never reclaim it. Same
    release an explicit delete does (ADR-0026). Best-effort.
    """
    from hal0.registry import gc

    db_path = getattr(registry, "db_path", None)
    refs: list[tuple[str, str]] = []
    if db_path is not None:
        try:
            refs = gc.model_blob_refs(db_path, model_id)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("discover.prune_refs_read_failed id=%s err=%s", model_id, exc)
    try:
        registry.remove(model_id)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("discover.prune_remove_failed id=%s err=%s", model_id, exc)
        return
    if db_path is not None and refs:
        try:
            gc.release_blob_refs(db_path, refs, model_id=model_id)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("discover.prune_release_failed id=%s err=%s", model_id, exc)


def prune_missing(
    registry: ModelRegistry,
    *,
    protected_ids: set[str],
    dry_run: bool = False,
    roots: list[str | Path] | None = None,
) -> dict:
    """Remove registry rows whose backing path no longer exists on disk.

    A row is pruned only if BOTH: its resolved path is missing AND its id is
    not in ``protected_ids`` (slot/stack-referenced ids). Protected-but-missing
    rows are returned under ``missing_referenced`` so callers can surface them
    as drift needing repair rather than silently deleting a referenced model.
    Rows of a runtime that manages its own files (FLM — see
    :func:`is_runtime_managed`) are never pruned: their ``path`` is the
    runtime's model directory, not a file hal0 owns, and they count as kept.

    When ``roots`` is given, a missing row is pruned only if the miss is
    trustworthy (:func:`_missing_is_trustworthy`): its scan root exists and
    is non-empty, or — outside every root — its parent directory exists. A
    row under an unmounted share is reported under ``missing_unverified``
    and kept.
    Returns {"pruned": [...], "missing_referenced": [...],
    "missing_unverified": [...], "kept": int}.
    """
    pruned: list[str] = []
    missing_referenced: list[str] = []
    missing_unverified: list[str] = []
    kept = 0
    root_paths: list[Path] = []
    for r in roots or []:
        rp = Path(r).expanduser()
        with contextlib.suppress(OSError):
            rp = rp.resolve()
        root_paths.append(rp)
    live: dict[Path, bool] = {}
    for m in registry.list():
        if is_runtime_managed(m):
            kept += 1
            continue
        # ``p.exists()`` is authoritative: do NOT special-case size_bytes==0
        # (that's stale metadata — the weights may well still be on disk).
        try:
            present = Path(m.path).exists()
        except OSError:
            present = False
        if present:
            kept += 1
            continue
        # Path is missing on disk.
        if m.id in protected_ids:
            # Referenced by a live slot or a stack — this is functional drift
            # that needs a repair, not a deletion. Report, never remove.
            missing_referenced.append(m.id)
            continue
        if roots is not None:
            p = Path(m.path).expanduser()
            with contextlib.suppress(OSError):
                p = p.resolve()
            if not _missing_is_trustworthy(p, root_paths, live):
                missing_unverified.append(m.id)
                continue
        pruned.append(m.id)
        if not dry_run:
            _prune_row(registry, m.id)
    return {
        "pruned": pruned,
        "missing_referenced": missing_referenced,
        "missing_unverified": missing_unverified,
        "kept": kept,
    }


def referenced_model_ids() -> set[str]:
    """Model ids referenced by any configured slot, stack, or hal0.toml field.

    Best-effort wrapper around :func:`_collect_referenced_model_ids` — each
    per-slot / per-stack / per-config load is wrapped so a single malformed
    config can't blow up the whole scan, and whatever loaded is returned.
    Callers that DELETE rows with this as protection on every boot should use
    the collector directly and act only when it reports ``complete``.
    """
    return _collect_referenced_model_ids()[0]


def _collect_referenced_model_ids() -> tuple[set[str], bool]:
    """``(ids, complete)`` — ``complete`` is False if any source failed to load.

    Union of: each slot's default model, capability child selections, every
    model id referenced by a stack (slot primaries + capability rows), and the
    hal0.toml fields that name a model id (``[brain_chat] model`` /
    ``tool_model``, ``[realtime] default_model`` / ``stt_model`` /
    ``tts_model``). Missing config files are not failures; a load that raises
    is.
    """
    ids: set[str] = set()
    complete = True

    # ── Slots (+ live capability child selections) ────────────────────────
    try:
        from hal0.config.loader import list_slots, load_slot_config

        for slot_name in list_slots():
            try:
                slot = load_slot_config(slot_name)
            except Exception as exc:  # pragma: no cover — defensive
                log.warning("discover.ref_slot_load_failed slot=%s err=%s", slot_name, exc)
                complete = False
                continue
            model_id = getattr(getattr(slot, "model", None), "default", "") or ""
            if model_id:
                ids.add(model_id)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("discover.ref_slots_failed err=%s", exc)
        complete = False

    # hal0.toml fields that name a model id directly.
    try:
        from hal0.config.loader import load_hal0_config

        hal0_cfg = load_hal0_config()
        brain = hal0_cfg.brain_chat
        rt = hal0_cfg.realtime
        for model_id in (
            brain.model,
            brain.tool_model,
            rt.default_model,
            rt.stt_model,
            rt.tts_model,
        ):
            if model_id:
                ids.add(model_id)
    except Exception as exc:
        log.warning("discover.ref_hal0_config_failed err=%s", exc)
        complete = False

    # Capability child selections (live [selections.<slot>.<child>] rows).
    try:
        from hal0.capabilities.config import load_capabilities_config

        cap_cfg = load_capabilities_config()
        for children in getattr(cap_cfg, "selections", {}).values():
            for sel in children.values():
                child_model = getattr(sel, "model", "") or ""
                if child_model:
                    ids.add(child_model)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("discover.ref_capabilities_failed err=%s", exc)
        complete = False

    # ── Stacks (slot primaries + capability rows) ─────────────────────────
    try:
        from hal0.stacks import StacksCatalog
        from hal0.stacks.portable import _referenced_model_ids

        for stack in StacksCatalog().list():
            try:
                # ResolvedStack duck-types StackConfig for _referenced_model_ids
                # (both expose ``.slots`` of StackSlotEntry).
                ids |= _referenced_model_ids(stack)
            except Exception as exc:  # pragma: no cover — defensive
                log.warning(
                    "discover.ref_stack_failed stack=%s err=%s",
                    getattr(stack, "slug", "?"),
                    exc,
                )
                complete = False
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("discover.ref_stacks_failed err=%s", exc)
        complete = False

    return ids, complete


def scan_and_register(
    registry: ModelRegistry,
    cfg: ModelsConfig,
    *,
    prune: bool = False,
    protected_ids: set[str] | None = None,
) -> dict:
    """Discover candidates under ``cfg.roots`` and register the new ones.

    Returns a result dict shaped for both the API surface and the
    startup log line.

    When ``prune`` is True, also run :func:`prune_missing` BEFORE the add pass
    (so a file moved within a root re-registers under its id in the same
    scan) to drop registry rows whose backing file no longer exists — except
    rows in ``protected_ids`` (slot/stack-referenced), which are reported
    under ``missing_referenced`` for repair, and rows whose miss can't be
    trusted (empty/absent root), reported under ``missing_unverified``. The
    ``pruned`` / ``missing_referenced`` / ``missing_unverified`` keys are
    always present (``[]`` when ``prune`` is False) so the return shape is
    stable. The whole scan holds :data:`SCAN_DELETE_LOCK`.

    Every call also runs :func:`reconcile_skipped_auto_scan` first, so rows an
    older scan registered under a now-skipped dir are dropped (reported under
    ``reconciled``; protected matches under ``reconcile_referenced``). Its
    protection is ``protected_ids`` unioned with every slot/stack/hal0.toml
    reference, collected only if a row actually matches. If any reference
    source fails to load, reconcile is skipped for that scan rather than
    deleting with a partial protected set.
    """
    with SCAN_DELETE_LOCK:
        return _scan_and_register_locked(registry, cfg, prune=prune, protected_ids=protected_ids)


def _scan_and_register_locked(
    registry: ModelRegistry,
    cfg: ModelsConfig,
    *,
    prune: bool,
    protected_ids: set[str] | None,
) -> dict[str, Any]:
    # scan_roots() folds the effective store/pull_root into the declared roots
    # so a headless install (where --models-dir wrote pull_root but not roots)
    # still scans where the models actually are.
    roots = cfg.scan_roots()

    reconciled: list[str] = []
    reconcile_referenced: list[str] = []
    root_paths = [Path(r).expanduser() for r in roots]
    if any(_is_reconcilable(m, root_paths) for m in registry.list()):
        ref_ids, complete = _collect_referenced_model_ids()
        if not complete:
            log.warning(
                "discover.reconcile_skipped — a slot/stack/config reference failed to "
                "load; not removing rows under skipped dirs this scan"
            )
        else:
            rec = reconcile_skipped_auto_scan(
                registry,
                roots=list(roots),
                protected_ids=ref_ids | (protected_ids or set()),
            )
            reconciled = rec["removed"]
            reconcile_referenced = rec["referenced"]

    # Prune BEFORE the add pass: a file moved within a root keeps its derived
    # id, so the stale row must go first or the new path is skipped as an
    # id_collision and the model is missing until the next scan.
    pruned: list[str] = []
    missing_referenced: list[str] = []
    missing_unverified: list[str] = []
    if prune:
        prune_result = prune_missing(
            registry,
            protected_ids=protected_ids or set(),
            dry_run=False,
            roots=list(roots),
        )
        pruned = prune_result["pruned"]
        missing_referenced = prune_result["missing_referenced"]
        missing_unverified = prune_result["missing_unverified"]

    known_paths: set[str] = set()
    known_inodes: set[tuple[int, int]] = set()
    for existing in registry.list():
        try:
            known_paths.add(str(Path(existing.path).resolve()))
        except OSError:
            known_paths.add(existing.path)
        known_paths.add(existing.path)
        ino = _inode(Path(existing.path))
        if ino is not None:
            known_inodes.add(ino)

    candidates = find_candidates(
        roots=list(roots),
        extensions=list(cfg.file_extensions),
        known_paths=known_paths,
        ignored_paths=tombstones.load(),
        known_inodes=known_inodes,
    )

    added: list[str] = []
    skipped: list[dict] = []
    backfilled: list[str] = []

    # Backfill pass — repair EXISTING coord-less registry rows from the curated
    # catalogue. find_candidates() skips files already registered by path (they
    # are in known_paths), so a row auto-registered before the curated coords
    # landed never re-surfaces as a candidate. Match each coord-less row against
    # curated by its on-disk filename and fill hf_repo/hf_filename/name/tags.
    # Idempotent (a row with coords is left alone) and never changes the id.
    backfilled.extend(backfill_coordless(registry))

    for cand in candidates:
        existing_id = cand.curated_match.id if cand.curated_match else cand.suggested_id
        if registry.has(existing_id):
            existing = registry.get(existing_id)
            if existing.path == str(cand.path):
                skipped.append({"path": str(cand.path), "reason": "already_registered"})
                continue
            # Same id, different path — skip to avoid clobbering the
            # operator's hand-pinned location.
            skipped.append({"path": str(cand.path), "reason": f"id_collision:{existing_id}"})
            continue
        try:
            model = register_candidate(registry, cand)
            added.append(model.id)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("discover.register_failed path=%s err=%s", cand.path, exc)
            skipped.append({"path": str(cand.path), "reason": f"register_failed:{exc}"})

    return {
        "added": added,
        "backfilled": backfilled,
        "skipped": skipped,
        "scanned_roots": [str(r) for r in roots],
        "pruned": pruned,
        "missing_referenced": missing_referenced,
        "missing_unverified": missing_unverified,
        "reconciled": reconciled,
        "reconcile_referenced": reconcile_referenced,
    }


def is_skippable(p: Path) -> bool:
    """Public wrapper around the internal skip rules (shards, mmproj, hex blobs,
    HF/ComfyUI accessory dirs). Used by ``scan/preview`` so the preview list
    obeys the same filters as auto-discovery."""
    return _is_skippable(p)


__all__ = [
    "CandidateModel",
    "backfill_coordless",
    "find_candidates",
    "is_runtime_managed",
    "is_skippable",
    "prune_missing",
    "reconcile_skipped_auto_scan",
    "referenced_model_ids",
    "register_candidate",
    "scan_and_register",
]
