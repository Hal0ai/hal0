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

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from hal0.config.schema import ModelsConfig
from hal0.model_meta import capability_from_filename
from hal0.registry.curated import CURATED_MODELS, CuratedModel
from hal0.registry.fileset import SHARD_RE
from hal0.registry.model import Model
from hal0.registry.store import ModelAlreadyExists, ModelRegistry

log = logging.getLogger(__name__)

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
) -> list[CandidateModel]:
    """Walk each root and return :class:`CandidateModel`s not already registered.

    Files whose absolute path is in ``known_paths`` are skipped silently
    so a re-scan after a manual registry add doesn't fight itself. The
    walk is case-insensitive on extension to handle ``.GGUF`` vs ``.gguf``.
    """
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
    started = time.monotonic()
    for root in roots:
        root_path = Path(root).expanduser()
        if not root_path.exists() or not root_path.is_dir():
            continue
        try:
            iterator = root_path.rglob("*")
        except OSError as exc:
            log.warning("discover.rglob_failed root=%s err=%s", root_path, exc)
            continue
        for candidate in iterator:
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
                try:
                    shard_abs = candidate.resolve()
                except OSError:
                    shard_abs = candidate
                idx = _shard_index(candidate)
                if idx is not None:
                    shard_groups.setdefault(shard_key, {})[idx] = shard_abs
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
            if abs_path in seen:
                continue
            seen.add(abs_path)
            try:
                size = abs_path.stat().st_size
            except OSError:
                size = 0
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
    for (_dirkey, _stem, _tot), idx_map in shard_groups.items():
        if 1 not in idx_map:
            continue
        ordered = [idx_map[i] for i in sorted(idx_map)]
        entry_path = ordered[0]
        if str(entry_path) in known_paths or entry_path in seen:
            continue
        seen.add(entry_path)
        total_size = 0
        for shard_path in ordered:
            try:
                total_size += shard_path.stat().st_size
            except OSError:
                continue
        naming_source = entry_path
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
            hf_repo=curated.hf_repo,
            hf_filename=curated.hf_file,
            tags=list(curated.tags),
            metadata={"discovered": True, "source": "auto-scan"},
        )
    else:
        model = Model(
            id=candidate.suggested_id,
            name=candidate.path.stem,
            path=str(candidate.path),
            size_bytes=candidate.size_bytes,
            capabilities=["image"] if is_comfyui else [candidate.capability_guess],
            backends=comfyui_backends,
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


def scan_and_register(registry: ModelRegistry, cfg: ModelsConfig) -> dict:
    """Discover candidates under ``cfg.roots`` and register the new ones.

    Returns a result dict shaped for both the API surface and the
    startup log line.
    """
    known_paths: set[str] = set()
    for existing in registry.list():
        try:
            known_paths.add(str(Path(existing.path).resolve()))
        except OSError:
            known_paths.add(existing.path)
        known_paths.add(existing.path)

    # scan_roots() folds the effective store/pull_root into the declared roots
    # so a headless install (where --models-dir wrote pull_root but not roots)
    # still scans where the models actually are.
    roots = cfg.scan_roots()
    candidates = find_candidates(
        roots=list(roots),
        extensions=list(cfg.file_extensions),
        known_paths=known_paths,
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
    "is_skippable",
    "register_candidate",
    "scan_and_register",
]
