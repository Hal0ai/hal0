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
from hal0.registry.curated import CURATED_MODELS, CuratedModel
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
# These need the transformers library to stitch back together; hal0's
# llama-server / FLM providers expect a single-file GGUF or single
# .safetensors checkpoint, so a lone shard isn't loadable on its own.
_SHARD_RE = re.compile(r"^.+-\d{5}-of-\d{5}$")


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


# ── helpers ───────────────────────────────────────────────────────────────


def _normalise_id(stem: str) -> str:
    """Turn a basename stem into a registry-friendly id."""
    lowered = stem.lower()
    replaced = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    collapsed = re.sub(r"-+", "-", replaced)
    return collapsed or "model"


def _guess_capability(filename: str) -> str:
    """Best-effort capability inference from the filename."""
    lower = filename.lower()
    if any(tok in lower for tok in ("embed", "nomic")):
        return "embed"
    if any(tok in lower for tok in ("vl", "vision", "vit")):
        return "vision"
    if any(tok in lower for tok in ("tts", "voice", "kokoro", "vibevoice")):
        return "tts"
    if any(tok in lower for tok in ("whisper", "moonshine", "asr", "stt")):
        return "asr"
    return "chat"


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


def _is_skippable(p: Path) -> bool:
    """Skip dotfiles, .tmp partials, hash-only blob names, shards, accessory dirs."""
    name = p.name
    if name.startswith("."):
        return True
    if name.endswith(".tmp"):
        return True
    if "mmproj" in name.lower():
        return True
    if _HEX_BLOB_RE.match(p.stem):
        return True
    if _SHARD_RE.match(p.stem):
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
    # Associate each sidecar with sibling main models in the same directory.
    for cand in out:
        sidecar = mmproj_by_dir.get(cand.path.parent)
        if sidecar is not None:
            cand.mmproj = sidecar
    return out


def register_candidate(registry: ModelRegistry, candidate: CandidateModel) -> Model:
    """Build a :class:`Model` from ``candidate`` and add it to ``registry``."""
    curated = candidate.curated_match
    if curated is not None:
        model = Model(
            id=curated.id,
            name=curated.display_name,
            path=str(candidate.path),
            size_bytes=candidate.size_bytes,
            license=curated.license,
            capabilities=[curated.capability] if curated.capability else ["chat"],
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
            capabilities=[candidate.capability_guess],
            metadata={"discovered": True, "source": "auto-scan"},
        )
    # Carry a discovered mmproj sidecar onto the model so the llama-server
    # provider can surface it as --mmproj. None when no sidecar was found.
    if candidate.mmproj is not None:
        model.mmproj = str(candidate.mmproj)
    try:
        registry.add(model)
    except ModelAlreadyExists:
        # A concurrent scan or manual add already claimed this id — return
        # the existing entry without raising so the caller's "added" count
        # stays meaningful.
        return registry.get(model.id)
    return model


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

    candidates = find_candidates(
        roots=list(cfg.roots),
        extensions=list(cfg.file_extensions),
        known_paths=known_paths,
    )

    added: list[str] = []
    skipped: list[dict] = []
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
        "skipped": skipped,
        "scanned_roots": [str(r) for r in cfg.roots],
    }


def is_skippable(p: Path) -> bool:
    """Public wrapper around the internal skip rules (shards, mmproj, hex blobs,
    HF/ComfyUI accessory dirs). Used by ``scan/preview`` so the preview list
    obeys the same filters as auto-discovery."""
    return _is_skippable(p)


__all__ = [
    "CandidateModel",
    "find_candidates",
    "is_skippable",
    "register_candidate",
    "scan_and_register",
]
