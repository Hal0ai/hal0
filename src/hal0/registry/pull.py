"""Hugging Face model pull — streaming download + SHA-256 + atomic install.

The pull engine runs as a FastAPI BackgroundTask: queue → running →
{completed, failed, cancelled}. State lives on ``app.state.model_pull_jobs``
so SSE / status endpoints can observe progress without coupling to this
module.

The actual download streams from
``https://huggingface.co/<repo>/resolve/main/<file>`` to a tempfile
under ``/var/lib/hal0/models/.tmp/`` and ``os.replace()``s into the final
location on success. We compute SHA-256 incrementally while streaming so
the registry entry can record an integrity tag without a second pass.

# NOTE: HF's ``resolve/main`` URLs are content-addressed at the LFS layer
# — once we've downloaded a file we treat it as immutable. No revalidation,
# no 304 dance. Anyone wanting a fresh build deletes the file and re-pulls.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

import httpx

from hal0.config import paths, store
from hal0.db import repository
from hal0.db.connection import connect, tx
from hal0.errors import Hal0Error
from hal0.install.perms import ensure_shared_dir
from hal0.model_meta import derive_model_provider
from hal0.registry import tombstones
from hal0.registry.fileset import SHARD_RE, FileSetEntry, FileSetPlan
from hal0.registry.model import Model, ModelCapabilities
from hal0.registry.store import ModelNotFound, ModelRegistry

log = logging.getLogger(__name__)


def _fsync_dir(dir_path: Path) -> None:
    """Best-effort fsync of a directory so a just-renamed entry is durable.

    An atomic ``os.replace`` only guarantees the rename is visible to later
    ``open`` calls; the directory entry itself is not on stable storage until
    the parent directory is fsynced. Any OSError (e.g. a platform without
    ``O_DIRECTORY``) is suppressed so it can never break the write it is
    meant to harden.
    """
    with contextlib.suppress(OSError):
        dfd = os.open(dir_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


# ── Tunables ─────────────────────────────────────────────────────────────────

# Stream chunk size — 256 KiB is a good balance between throughput and
# progress-update granularity. SSE emits at most one event per chunk.
_CHUNK_BYTES: int = 256 * 1024

# Minimum interval between SSE progress emits (when chunk-rate is high).
_SSE_MIN_INTERVAL_S: float = 0.5

# Connect timeout (the body stream is intentionally unbounded — large
# GGUFs take minutes on slow links).
_CONNECT_TIMEOUT_S: float = 30.0
_READ_TIMEOUT_S: float | None = None  # None → unbounded body read

# Path-safety regex: model ids are user-controllable, so we strip anything
# that could escape the models directory.
_SANITISE_RE = re.compile(r"[^A-Za-z0-9._-]+")


# ── Typed errors ─────────────────────────────────────────────────────────────


class PullError(Hal0Error):
    """Base error for pull operations."""

    code = "model.pull_failed"
    status = 500


class PullInvalidSource(PullError):
    """The model entry doesn't carry enough info to know what to download."""

    code = "model.invalid_source"
    status = 422


class PullJobNotFound(PullError):
    """No pull job for this model id."""

    code = "model.pull_job_not_found"
    status = 404


class PullInsufficientDisk(PullError):
    """The staging filesystem lacks room for the advertised download size."""

    code = "model.insufficient_disk"
    status = 507  # Insufficient Storage


class PullChecksumMismatch(PullError):
    """Streamed bytes don't hash to the SHA-256 HuggingFace advertised.

    Raised only when HF exposed an expected hash (the LFS object sha256 in
    ``X-Linked-ETag`` on the resolve response/redirect). Non-LFS files carry
    no sha256 and keep the record-only behaviour. The completed ``.part``
    is preserved for diagnosis; its resume sidecar is dropped so a retry
    starts clean instead of "resuming" corrupt bytes.
    """

    code = "pull.checksum_mismatch"
    status = 502  # upstream handed us bytes that don't match its own manifest


class _PullCancelled(Exception):
    """Internal control-flow: user cancel observed mid-stream.

    Raised by :func:`_download_one` so :func:`run_pull` can transition the
    job to ``cancelled`` after per-file staging cleanup already happened.
    Never surfaces past ``run_pull``.
    """


# ── Job record ───────────────────────────────────────────────────────────────


@dataclass
class PullFile:
    """One file of a (possibly multi-file) pull job.

    A plain pull has exactly one entry (the main GGUF); a vision pull adds
    an ``mmproj`` sidecar entry downloaded after the main model. Top-level
    ``PullJob.bytes_downloaded`` / ``bytes_total`` stay the AGGREGATE across
    entries so the SSE/status wire shape is unchanged for consumers.
    """

    hf_filename: str
    kind: str = "model"  # "model" | "shard" | "mmproj" | "draft" | "sidecar"
    dest: str | None = None  # final installed path, once known
    bytes_total: int = 0
    bytes_done: int = 0
    sha256: str | None = None  # computed while streaming
    expected_sha256: str | None = None  # HF-advertised LFS hash, if any

    def as_dict(self) -> dict[str, Any]:
        return {
            "hf_filename": self.hf_filename,
            "kind": self.kind,
            "dest": self.dest,
            "bytes_total": self.bytes_total,
            "bytes_done": self.bytes_done,
            "sha256": self.sha256,
            "expected_sha256": self.expected_sha256,
        }


@dataclass
class PullJob:
    """One in-flight model pull, addressable by ``model_id``.

    Lives on ``app.state.model_pull_jobs[model_id]``. SSE / status routes
    snapshot ``as_dict()`` to surface progress without holding the
    dataclass across event-loop ticks.
    """

    job_id: str
    model_id: str
    state: str = "queued"  # queued → running → {completed,failed,cancelled}
    bytes_downloaded: int = 0
    bytes_total: int = 0
    started_at: float = 0.0
    finished_at: float | None = None
    error: str | None = None
    error_code: str | None = None
    sha256: str | None = None
    path: str | None = None
    cancel_requested: bool = False
    # Per-file manifest (multi-file pulls, e.g. main GGUF + mmproj). Empty
    # until ``run_pull`` seeds it; single-file jobs get one entry. The
    # top-level bytes_* fields above remain the aggregate across files.
    files: list[PullFile] = field(default_factory=list)
    # Async signalling — set every time the background task makes
    # progress. SSE waits on this rather than polling.
    progress_event: asyncio.Event = field(default_factory=asyncio.Event)

    def as_dict(self) -> dict[str, Any]:
        """Serialisable snapshot for /pull/status and SSE frames.

        The top-level keys are the stable wire shape the UI reads
        (``bytes_downloaded``/``bytes_total`` are aggregates across files).
        ``files`` is additive detail — consumers that don't know it ignore it,
        and old persisted snapshots without it load fine.
        """
        return {
            "id": self.job_id,
            "model_id": self.model_id,
            "state": self.state,
            "bytes_downloaded": self.bytes_downloaded,
            "bytes_total": self.bytes_total,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "error_code": self.error_code,
            "sha256": self.sha256,
            "path": self.path,
            "files": [f.as_dict() for f in self.files],
        }

    def _signal(self) -> None:
        """Pulse the progress event so any awaiting SSE generator wakes up."""
        self.progress_event.set()
        self.progress_event = asyncio.Event()


# ── Path helpers ─────────────────────────────────────────────────────────────


def _sanitise_id(model_id: str) -> str:
    """Strip path-unsafe characters from a model id.

    The id is used as a directory name under ``/var/lib/hal0/models/``;
    if it contains '/' or '..' it could escape the models tree. Mapping
    everything else to '-' keeps the directory navigable.
    """
    cleaned = _SANITISE_RE.sub("-", model_id).strip("-.") or "model"
    return cleaned


def _pull_root() -> Path:
    """Return the configured pull destination root.

    ML-3: thin delegator to :func:`hal0.config.store.store_root` — the ONE
    resolver now shared, identically, by every writer (this function) AND
    every reader (provider container mounts via
    ``hal0.config.paths.model_store_root``). This used to have its OWN
    precedence/fallback (``effective_store()`` else ``paths.models_dir()``)
    that could diverge from the reader's (``[models].store`` else
    ``/mnt/ai-models``) — the "🔴 dual-resolver store trap" (plan §7.1e
    defect #1). Kept as a function (not inlined at call sites) purely to
    minimise the diff across this module's many internal callers.
    """
    return store.store_root()


def _final_path(model_id: str, filename: str) -> Path:
    """Resolve the final on-disk path: <pull_root>/<id>/<file>."""
    return _pull_root() / _sanitise_id(model_id) / filename


def _comfyui_models_dir(subdir: str) -> Path:
    """ComfyUI checkpoints/loras/vae directory under the model store.

    ComfyUI models live under the configurable model-store root
    (default /mnt/ai-models) at /comfyui/models/<subdir>, aligned
    with the slot's bind-mount path into the container. This ensures
    ComfyUI's own CheckpointLoaderSimple / LoraLoader / etc. find
    models when --base-directory points at the root.

    The subdir name is sanitised the same way model ids are so a curated
    entry can't escape the comfyui tree by setting
    ``comfyui_subdir="../../etc/passwd"``.
    """
    cleaned = _SANITISE_RE.sub("-", subdir).strip("-.") or "checkpoints"
    return Path(paths.model_store_root()) / "comfyui" / "models" / cleaned


def _final_path_for_entry(
    model_id: str,
    filename: str,
    comfyui_subdir: str | None,
    capability: str | None = None,
) -> Path:
    """Pick the final on-disk path based on whether this is a ComfyUI asset.

    Image-gen entries (``comfyui_subdir`` set) land under
    ``/var/lib/hal0/comfyui/models/<subdir>/<filename>`` so ComfyUI's
    own model loaders pick them up without a per-id rename.

    When ``capability`` is set (the FirstRun v2 install engine, design D2),
    the model lands in a capability-grouped tree. A GGUF gets one canonical
    ``model.gguf`` filename: ``<pull_root>/<capability>/<id>/model.gguf``;
    any other format keeps its upstream basename (:func:`_canonical_name`).
    This keeps the store self-documenting by role and gives the slot config
    a stable path that never chases a HF-specific filename. A sidecar
    ``meta.json`` (written by :func:`write_model_meta`) preserves provenance.

    Everything else uses the default ``/var/lib/hal0/models/<id>/<filename>``
    layout (back-compat for the standalone ``/api/models/{id}/pull`` path).
    """
    if comfyui_subdir:
        return _comfyui_models_dir(comfyui_subdir) / filename
    if capability:
        # Same directory as :func:`hal0.config.store.capability_model_dir`,
        # which is where the kokoro provider launches the bundle from.
        return (
            _pull_root()
            / _sanitise_id(capability)
            / _sanitise_id(model_id)
            / _canonical_name(filename)
        )
    return _final_path(model_id, filename)


#: The canonical installed name of a capability-grouped GGUF pull.
_CANONICAL_GGUF_NAME = "model.gguf"


def _is_gguf(filename: str) -> bool:
    return filename.lower().endswith(".gguf")


def _canonical_name(filename: str) -> str:
    """Installed basename for a capability-grouped pull.

    A GGUF lands as the canonical ``model.gguf``. Any other format keeps its
    upstream basename: the engines that load non-GGUF bundles locate their
    files by name (kokoro globs ``kokoro-*.onnx`` + ``voices*.bin``), and an
    ONNX file named ``model.gguf`` is a mislabel nothing can load (#2739).
    """
    if _is_gguf(filename):
        return _CANONICAL_GGUF_NAME
    return Path(filename).name or _CANONICAL_GGUF_NAME


def _shard_set(hf_file: str) -> list[str]:
    """Every part of the split GGUF ``hf_file`` opens, in order, or ``[]``.

    llama.cpp's ``gguf-split`` names parts ``<stem>-00001-of-0000N.gguf`` in
    one directory, so the whole set follows from the first part's name — the
    Add-from-HF modal sends only that one. A later part is refused: llama.cpp
    can only open a split model from its first part.
    """
    path = PurePosixPath(hf_file)
    m = SHARD_RE.match(path.name)
    if m is None:
        return []
    if int(m.group("idx")) != 1:
        raise PullInvalidSource(
            f"{hf_file!r} is part {int(m.group('idx'))} of a split model — "
            "pull it by its first part (-00001-of-…)",
            details={"hf_file": hf_file},
        )
    tot = int(m.group("tot"))
    return [
        str(path.with_name(f"{m.group('stem')}-{i:05d}-of-{tot:05d}.{m.group('ext')}"))
        for i in range(1, tot + 1)
    ]


def _shard_dests(final: Path, total: int) -> list[Path]:
    """Installed paths for a ``total``-part split model whose first part is ``final``.

    A capability-grouped install names the first part ``model.gguf``; the
    parts become ``model-00001-of-0000N.gguf`` … instead, because llama.cpp
    finds parts 2..N by the first part's ``-00001-of-`` name.
    """
    m = SHARD_RE.match(final.name)
    stem, ext = (m.group("stem"), m.group("ext")) if m else (final.stem, final.suffix.lstrip("."))
    return [final.with_name(f"{stem}-{i:05d}-of-{total:05d}.{ext}") for i in range(1, total + 1)]


def _curated_extra_files(model_id: str, hf_repo: str) -> list[str]:
    """The curated row's ``extra_files`` for this pull, or ``[]``.

    Only when the pull's repo IS the curated row's repo: an operator who
    points the same id at a different repo gets exactly the file they named.
    """
    from hal0.registry.curated import get_curated

    curated = get_curated(model_id)
    if curated is None or curated.hf_repo != hf_repo:
        return []
    return [f for f in curated.extra_files if f]


def _remove_mislabeled_canonical(final: Path, hf_file: str) -> None:
    """Drop a stale ``model.gguf`` beside a non-GGUF pull (#2739).

    Before #2739 every capability-grouped pull was renamed ``model.gguf``,
    so a kokoro pull left its ONNX file under that name, where the runner
    never looks. Re-pulling now installs the file under its real name; the
    old copy in the same per-model directory is dead weight, so remove it.
    Only called for per-model directories (never the shared ComfyUI tree),
    and only when the file is provably that old copy: the directory's
    ``meta.json`` records this ``hf_file``, or the bytes are the same size
    as the file just installed. Anything else is left alone.
    """
    if _is_gguf(hf_file) or final.name == _CANONICAL_GGUF_NAME:
        return
    stale = final.parent / _CANONICAL_GGUF_NAME
    if not stale.is_file():
        return
    meta = read_model_meta(stale) or {}
    try:
        same_size = stale.stat().st_size == final.stat().st_size
    except OSError:
        same_size = False
    if meta.get("hf_file") != hf_file and not same_size:
        return
    stale.unlink()
    log.info("model.pull_removed_mislabeled", extra={"path": str(stale)})


def write_model_meta(
    dest: Path,
    *,
    curated_id: str,
    hf_repo: str,
    hf_file: str,
    sha256: str | None,
    size_bytes: int,
    quant: str | None,
    capability: str | None,
) -> None:
    """Write a ``meta.json`` sidecar next to a capability-grouped model file.

    Preserves the HuggingFace provenance (repo/file/sha) that the grouped
    ``model.gguf`` filename drops, so the store layout (design D2) stays both
    clean to browse and fully traceable back to the source artefact.
    """
    import json as _json

    meta = {
        "curated_id": curated_id,
        "hf_repo": hf_repo,
        "hf_file": hf_file,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "quant": quant,
        "capability": capability,
    }
    (dest.parent / "meta.json").write_text(_json.dumps(meta, indent=2) + "\n")


def read_model_meta(dest: Path) -> dict[str, Any] | None:
    """Read the ``meta.json`` sidecar next to *dest*, or ``None``.

    The symmetric counterpart of :func:`write_model_meta`, which shipped
    without a reader — so the provenance it records could be inspected by a
    human but not by code. :func:`hal0.install.brain_model.already_pulled` is
    the first consumer: it uses the recorded ``curated_id`` / ``hf_file`` /
    ``size_bytes`` to decide whether a file already on disk is genuinely THIS
    curated entry, complete, rather than re-downloading multiple gigabytes on
    every ``install.sh`` re-run.

    Best-effort: a missing, unreadable, malformed, or non-object sidecar all
    return ``None``, i.e. "can't prove it — treat it as absent". Never raises.
    """
    import json as _json

    try:
        raw = _json.loads((dest.parent / "meta.json").read_text())
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _tmp_dir() -> Path:
    """Return the tempfile staging directory for in-flight pulls.

    Lives under the configured pull_root so the os.replace() into the
    final path stays on the same filesystem (otherwise atomic rename
    degrades to a cross-FS copy, which we don't want for multi-GB pulls).
    """
    return _pull_root() / ".tmp"


_PARTIAL_MAX_AGE_S: float = 24 * 3600  # reap .part files idle > 24h


def sweep_orphaned_partials(max_age_s: float = _PARTIAL_MAX_AGE_S) -> int:
    """Delete stale ``*.part`` staging files left by SIGKILL/OOM mid-pull.

    Best-effort, fail-soft. Only removes ``*.part`` files (and their
    ``*.part.json`` resume sidecars) whose mtime is older than ``max_age_s``
    so a concurrently-downloading partial (whose mtime advances as it grows)
    is never reaped. Sidecars are swept too so a reaped-but-not-resumed
    partial doesn't leave its sidecar lingering. Returns count removed.
    """
    tmp_dir = _tmp_dir()
    removed = 0
    try:
        # Both the partial and its resume sidecar (MR-7); once a .part is stale
        # enough to reap, its resume coordinates are worthless too.
        entries = list(tmp_dir.glob("*.part")) + list(tmp_dir.glob("*.part.json"))
    except OSError:
        return 0
    now = time.time()
    for p in entries:
        try:
            if not p.is_file():
                continue
            if (now - p.stat().st_mtime) < max_age_s:
                continue
            p.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            log.warning("model.partial_sweep_failed path=%s error=%s", p, exc)
    # Split pulls stage finished parts in ``<id>--parts/`` (see
    # :func:`_split_staging_dir`); reap a set nobody has touched for as long.
    with contextlib.suppress(OSError):
        for stage in tmp_dir.glob("*--parts"):
            try:
                newest = max(
                    (p.stat().st_mtime for p in stage.iterdir()), default=stage.stat().st_mtime
                )
                if stage.is_dir() and (now - newest) >= max_age_s:
                    shutil.rmtree(stage)
                    removed += 1
            except OSError as exc:
                log.warning("model.partial_sweep_failed path=%s error=%s", stage, exc)
    if removed:
        log.info("model.partial_sweep removed=%d dir=%s", removed, tmp_dir)
    return removed


def hf_download_url(repo: str, filename: str, revision: str = "main") -> str:
    """Build the canonical HuggingFace download URL.

    ``resolve/main`` (not ``raw/main``) is required for GGUF — LFS files
    aren't served raw. HF returns a 302 to a signed CDN URL; httpx
    follows that for us when ``follow_redirects=True``.
    """
    repo = repo.strip("/")
    filename = filename.lstrip("/")
    return f"https://huggingface.co/{repo}/resolve/{revision}/{filename}"


# ── Job orchestration ────────────────────────────────────────────────────────


# ── model-delete markers (ADR-0026) ──────────────────────────────────────────
#
# A model delete marks the id as "deleting" for its whole cascade, so no new
# pull or update job can start for it, and records when its row went, so a
# job created before that moment never registers afterwards.

_DELETING: set[str] = set()
_DELETED_AT: dict[str, float] = {}


def mark_deleting(model_id: str) -> None:
    """Block new pull/update jobs for ``model_id`` until :func:`unmark_deleting`."""
    _DELETING.add(model_id)


def unmark_deleting(model_id: str) -> None:
    _DELETING.discard(model_id)


def mark_deleted(model_id: str) -> None:
    """Record that ``model_id``'s registry row was removed just now."""
    _DELETED_AT[model_id] = time.time()


def ensure_not_deleting(model_id: str) -> None:
    """Raise ``Conflict`` (``model.deleting``) while a delete of ``model_id`` runs.

    Call right before a new job is stored in the jobs map, with no ``await``
    in between, so the check and the insert are atomic on the event loop.
    """
    if model_id in _DELETING:
        from hal0.errors import Conflict

        raise Conflict(
            f"model {model_id!r} is being deleted; retry the pull once the delete finishes",
            code="model.deleting",
            details={"model_id": model_id},
        )


def _registration_abandoned(job: PullJob) -> bool:
    """True when a finished download must NOT be registered.

    Either the job was cancelled, or its model was deleted after the job was
    created (a delete that raced the job's start).
    """
    if job.cancel_requested:
        return True
    deleted_at = _DELETED_AT.get(job.model_id)
    return deleted_at is not None and deleted_at > job.started_at


def make_job(model_id: str) -> PullJob:
    """Create a fresh job record for ``model_id``."""
    return PullJob(
        job_id=secrets.token_hex(8),
        model_id=model_id,
        state="queued",
        started_at=time.time(),
    )


def get_job(jobs: dict[str, PullJob], model_id: str) -> PullJob | None:
    """Return the most recent job for ``model_id``, or None."""
    return jobs.get(model_id)


# ── durable pull-job store (#626 / #MR-1) ─────────────────────────────────────
#
# The snapshot lives here (not in routes/models) so ``run_pull`` itself writes
# the terminal state on EVERY path — the dashboard route AND the installer /
# bundle-tier pulls, which call ``run_pull`` directly and previously bypassed the
# routes/models wrappers, 404ing every status poll after an install-time restart.


def _pull_jobs_dir() -> Path:
    """Return ``<var_lib>/model-pull-jobs`` (HAL0_HOME-aware via config paths)."""
    return paths.var_lib() / "model-pull-jobs"


def pull_job_file(model_id: str) -> Path:
    """Path of the durable snapshot for ``model_id``'s pull job."""
    return _pull_jobs_dir() / f"{_sanitise_id(model_id)}.json"


def persist_pull_job(job: PullJob) -> None:
    """Atomically mirror a pull-job snapshot to disk (best-effort, fail-soft).

    A failure to persist must never break the pull flow — the in-memory job
    stays authoritative for the running process. Called from ``run_pull`` so a
    restart-surviving status poll resolves for any caller (#MR-1).
    """
    path = pull_job_file(job.model_id)
    try:
        # model-pull-jobs/ is declared 2775; a bare mkdir under the daemon's
        # UMask=0022 births 2755 and re-drifts the audit after every pull (#1896).
        ensure_shared_dir(path.parent)
        fd, tmp_str = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        tmp_path: Path | None = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(job.as_dict(), f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            _fsync_dir(path.parent)
            tmp_path = None
        finally:
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    tmp_path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("model.pull_job_persist_failed model_id=%s error=%s", job.model_id, exc)


def sweep_pull_jobs(max_age_days: int = 14) -> int:
    """Garbage-collect stale terminal pull-job snapshots (#MR-8).

    Reap on-disk snapshots whose ``state`` is terminal (``completed`` /
    ``failed`` / ``cancelled``) and whose file mtime is older than
    ``max_age_days``. Non-terminal snapshots (``queued`` / ``running``) are
    preserved regardless of age so an in-flight or restart-surviving pull is
    never dropped. Called best-effort from the API lifespan on startup.

    Every step is fail-soft: a single unreadable / malformed / undeletable
    file is skipped so one bad snapshot never aborts the whole sweep. A
    missing jobs directory is a no-op. Returns the number of files removed.
    """
    jobs_dir = _pull_jobs_dir()
    if not jobs_dir.is_dir():
        return 0

    terminal = {"completed", "failed", "cancelled"}
    cutoff = max_age_days * 86400
    now = time.time()
    removed = 0
    for path in jobs_dir.glob("*.json"):
        try:
            if now - path.stat().st_mtime <= cutoff:
                continue
            with path.open(encoding="utf-8") as f:
                state = json.load(f).get("state")
            if state not in terminal:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        except (OSError, ValueError):
            # ValueError covers json.JSONDecodeError; OSError covers stat /
            # read / unlink failures. Skip this file, keep sweeping.
            continue
    return removed


def list_persisted_jobs() -> list[dict[str, Any]]:
    """Read all ``.json`` files from the pull-jobs directory and return a list of dicts.

    Best-effort: malformed or unreadable files are silently skipped.
    An absent jobs directory returns an empty list.
    """
    jobs_dir = _pull_jobs_dir()
    if not jobs_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(jobs_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                results.append(data)
        except (OSError, ValueError):
            continue
    return results


# ── resume / partial-download support (MR-7) ──────────────────────────────────
#
# A prior interrupted pull leaves a deterministic ``<id>.part`` in the staging
# dir plus a ``<id>.part.json`` sidecar recording the resume coordinates. The
# next ``run_pull`` re-reads the sidecar, re-hashes the on-disk prefix (stdlib
# hashlib can't serialise/restore its state, so re-reading the local prefix is
# the correctness-preserving way to keep the final SHA-256 exact), and issues a
# ``Range`` request to fetch only the tail. Hash correctness is non-negotiable:
# any doubt about the prefix (size mismatch, changed object, server ignoring the
# Range) triggers a clean full restart, never a silent double-count.

_CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+)", re.IGNORECASE)


def _read_resume_sidecar(part: Path, sidecar: Path, url: str) -> dict[str, Any] | None:
    """Return the resume metadata iff an on-disk partial is trustworthy.

    Guards every assumption the resume relies on: both files present, valid
    JSON, a positive recorded byte count, the same source url, and the recorded
    byte count matching the actual ``.part`` size on disk. Any mismatch returns
    ``None`` so the caller discards the stale partial and starts fresh.
    """
    if not (part.exists() and sidecar.exists()):
        return None
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    have = meta.get("bytes")
    if not isinstance(have, int) or have <= 0:
        return None
    if meta.get("url") != url:
        return None
    try:
        if part.stat().st_size != have:
            return None
    except OSError:
        return None
    return meta


def _discard_partial(part: Path, sidecar: Path) -> None:
    """Remove the staging ``.part`` and its sidecar, ignoring absence/errors."""
    with contextlib.suppress(OSError):
        part.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        sidecar.unlink(missing_ok=True)


def _write_resume_sidecar(
    part: Path,
    sidecar: Path,
    *,
    url: str,
    etag: str | None,
    total: int,
) -> None:
    """Persist resume coordinates next to a preserved ``.part`` (best-effort).

    Records the *actual* on-disk byte count (not the in-memory counter) so the
    next :func:`_read_resume_sidecar` probe — which compares against
    ``part.stat().st_size`` — accepts it. A zero-length or missing partial is
    not worth resuming, so it (and any stale sidecar) is discarded instead.
    """
    try:
        have = part.stat().st_size
    except OSError:
        _discard_partial(part, sidecar)
        return
    if have <= 0:
        _discard_partial(part, sidecar)
        return
    payload = {"url": url, "etag": etag, "bytes": have, "total": total}
    with contextlib.suppress(OSError):
        sidecar.write_text(json.dumps(payload), encoding="utf-8")


def _parse_content_range_total(header: str | None, resume_from: int) -> int | None:
    """Parse ``Content-Range: bytes <start>-<last>/<total>`` → total.

    Returns ``None`` when the header is missing/unparseable or when the range
    start disagrees with ``resume_from`` (a server sending from a different
    offset than we requested would corrupt the hash — the caller must restart).
    """
    if not header:
        return None
    m = _CONTENT_RANGE_RE.match(header.strip())
    if not m:
        return None
    start = int(m.group(1))
    total = int(m.group(3))
    if start != resume_from:
        return None
    return total


def _rehash_prefix(part: Path, hasher: Any) -> int:
    """Feed the on-disk prefix into ``hasher``; return the byte count read."""
    total = 0
    with open(part, "rb") as f:
        while True:
            block = f.read(_CHUNK_BYTES)
            if not block:
                break
            hasher.update(block)
            total += len(block)
    return total


def _staging_paths(model_id: str, filename: str) -> tuple[Path, Path]:
    """Deterministic per-(model, file) staging paths under ``.tmp``.

    Keyed by BOTH the sanitised model id and the sanitised filename so each
    file of a multi-file pull (main GGUF + mmproj sidecar) resumes
    independently (MR-7). Pre-multi-file partials were named ``<id>.part``;
    those simply never match the new key, so they are ignored (never
    mis-stitched) and reaped by :func:`sweep_orphaned_partials`.
    """
    tmp_dir = _tmp_dir()
    stem = f"{_sanitise_id(model_id)}--{_sanitise_id(filename)}"
    return tmp_dir / f"{stem}.part", tmp_dir / f"{stem}.part.json"


# ── expected-hash capture (WS-12) ─────────────────────────────────────────────
#
# HF's ``resolve/<rev>`` endpoint advertises the LFS object's sha256 as
# ``X-Linked-ETag`` on the redirect hop to the CDN (and on direct responses
# for LFS-backed files). Non-LFS files carry only a git-blob etag (40-hex
# sha1 / weak etag) — no sha256 exists for those, so they keep the historic
# record-only behaviour.

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _expected_sha256_from_response(resp: httpx.Response) -> str | None:
    """Extract HF's advertised LFS sha256 from ``X-Linked-ETag``, if present.

    Checks the redirect chain (``resp.history``) first — that's where the
    huggingface.co hop lives once httpx has followed the 302 to the CDN —
    then the final response. Returns the lowercase 64-hex digest, or ``None``
    when no response in the chain carries a sha256-shaped linked etag.
    """
    for r in [*resp.history, resp]:
        raw = (r.headers.get("x-linked-etag") or "").strip()
        if raw[:2] in ("W/", "w/"):
            raw = raw[2:]
        cleaned = raw.strip().strip('"').lower()
        if _SHA256_HEX_RE.match(cleaned):
            return cleaned
    return None


async def _download_one(
    job: PullJob,
    rec: PullFile,
    *,
    client: httpx.AsyncClient,
    hf_repo: str,
    base_headers: dict[str, str],
    final: Path,
    base_done: int,
    base_total: int,
    revision: str = "main",
) -> str:
    """Stream ONE file of a pull to ``final``; return its hex SHA-256.

    Owns the per-file staging lifecycle: the deterministic ``.part`` +
    resume sidecar (keyed by model id AND filename — see
    :func:`_staging_paths`), the Range-resume dance (MR-7), the integrity
    check against HF's advertised LFS sha256 (WS-12), and the atomic
    install. Aggregate job progress is maintained as ``base_done + <this
    file's bytes>`` so ``job.bytes_downloaded`` is monotonic across the
    file boundary of a multi-file pull.

    Staging cleanup contract (mirrors the historic single-file behaviour):
      * user cancel        → discard partial + sidecar, raise ``_PullCancelled``
      * checksum mismatch  → KEEP the completed ``.part`` for diagnosis, drop
                             the sidecar, raise :class:`PullChecksumMismatch`
      * permanent Hal0Error→ discard partial + sidecar, re-raise
      * transient httpx err→ preserve ``.part`` + write resume sidecar, re-raise
      * task cancelled     → preserve ``.part`` + write resume sidecar, re-raise
                             (issue #1225: hal0-api's lifespan shutdown
                             cancels in-flight pull TASKS so a
                             ``systemctl restart`` doesn't hang — that must
                             behave like a transient interruption, not a
                             user-requested "stop and clean up", so the next
                             pull attempt (manual or startup auto-resume)
                             continues from the last complete chunk instead
                             of restarting from zero)
      * anything else      → discard (unknown state must not poison a resume)
    """
    url = hf_download_url(hf_repo, rec.hf_filename, revision)
    headers = dict(base_headers)

    tmp_dir = _tmp_dir()
    ensure_shared_dir(tmp_dir)  # 2775, umask-proof (#1896)
    # Deterministic staging name (MR-7) so a prior interrupted pull can be
    # found and resumed. The JSON sidecar next to it records the resume
    # coordinates (url, etag, bytes-on-disk, total). TRADEOFF: this is not
    # isolated across two concurrent pulls of the SAME (model_id, filename).
    # In-process pulls are deduped by model_pull_jobs (routes/models.pull_model),
    # so the only unguarded case is two SEPARATE processes pulling the identical
    # id at once (rare — not an expected flow); resumability is worth that edge.
    part, sidecar = _staging_paths(job.model_id, rec.hf_filename)

    hasher = hashlib.sha256()
    last_emit = time.monotonic()

    # ── Resume probe (MR-7) ───────────────────────────────────────────────
    # Only reuse an on-disk partial the sidecar vouches for; anything else is
    # discarded so a stale/mismatched prefix can never corrupt the final hash.
    resume_from = 0
    resume_meta = _read_resume_sidecar(part, sidecar, url)
    if resume_meta is not None:
        resume_from = int(resume_meta["bytes"])
        headers["Range"] = f"bytes={resume_from}-"
        etag = resume_meta.get("etag")
        if etag:
            # If-Range forces a full 200 body when the object has changed,
            # so we never stitch a fresh tail onto a stale prefix.
            headers["If-Range"] = etag
    else:
        _discard_partial(part, sidecar)

    captured_etag: str | None = None
    try:
        # Retry loop exists solely so a 416 (bad/complete range) can fall back
        # to a clean full download in the same invocation. The happy path runs
        # the body exactly once and breaks.
        while True:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 416 and resume_from > 0:
                    # Range not satisfiable (already complete or bad range) —
                    # discard the prefix and restart from zero.
                    resume_from = 0
                    headers.pop("Range", None)
                    headers.pop("If-Range", None)
                    _discard_partial(part, sidecar)
                    hasher = hashlib.sha256()
                    continue

                if resp.status_code == 401 or resp.status_code == 403:
                    raise PullError(
                        f"hugging face returned {resp.status_code} for "
                        f"{hf_repo}/{rec.hf_filename} (gated repo? set HF_TOKEN)",
                        details={
                            "status": resp.status_code,
                            "repo": hf_repo,
                            "file": rec.hf_filename,
                        },
                    )
                if resp.status_code == 404:
                    raise PullError(
                        f"hugging face has no file {rec.hf_filename!r} in {hf_repo!r} at main",
                        details={"repo": hf_repo, "file": rec.hf_filename},
                    )
                if resp.status_code >= 400:
                    raise PullError(
                        f"hugging face returned HTTP {resp.status_code} for {url}",
                        details={"status": resp.status_code, "url": url},
                    )

                captured_etag = resp.headers.get("etag")
                # WS-12: capture HF's advertised LFS sha256 (X-Linked-ETag on
                # the resolve redirect / response). None for non-LFS files —
                # those keep the record-only behaviour.
                if rec.expected_sha256 is None:
                    rec.expected_sha256 = _expected_sha256_from_response(resp)

                if resp.status_code == 206 and resume_from > 0:
                    # Server honoured the Range — continue the existing prefix.
                    total = _parse_content_range_total(
                        resp.headers.get("content-range"), resume_from
                    )
                    if total is None:
                        # Offset the server sent disagrees with what we asked
                        # for — refuse to stitch (would corrupt the hash).
                        raise PullError(
                            f"resume range mismatch for {job.model_id}: "
                            f"unexpected Content-Range {resp.headers.get('content-range')!r}",
                            details={
                                "resume_from": resume_from,
                                "content_range": resp.headers.get("content-range"),
                            },
                        )
                    rec.bytes_total = total
                    job.bytes_total = base_total + total
                    # Re-hash the on-disk prefix so the final SHA-256 is exact
                    # (stdlib hashlib can't restore a checkpoint).
                    hashed = _rehash_prefix(part, hasher)
                    if hashed != resume_from:
                        raise PullError(
                            f"resume prefix changed for {job.model_id}: expected "
                            f"{resume_from} bytes, re-read {hashed}",
                            details={"expected": resume_from, "hashed": hashed},
                        )
                    rec.bytes_done = resume_from
                    job.bytes_downloaded = base_done + resume_from
                    mode = "ab"
                else:
                    # 200 (fresh, or the server/CDN ignored our Range) — start
                    # over from a clean hasher so we never double-count a prefix.
                    if resume_from > 0:
                        hasher = hashlib.sha256()
                        resume_from = 0
                    content_length = resp.headers.get("content-length")
                    if content_length:
                        try:
                            rec.bytes_total = int(content_length)
                        except ValueError:
                            rec.bytes_total = 0
                        job.bytes_total = base_total + rec.bytes_total
                    rec.bytes_done = 0
                    job.bytes_downloaded = base_done
                    mode = "wb"
                job._signal()

                # Disk-space preflight (MR-4): bail before streaming multi-GB
                # if the staging FS clearly can't hold what's still to fetch.
                # Measured against tmp_dir — that's where bytes land before the
                # os.replace() into the final path. A probe failure must not
                # itself fail the pull: if we can't measure, fall through to the
                # existing stream-until-ENOSPC behavior (no regression). On a
                # resume we only need room for the remaining (total-have) bytes.
                # PullInsufficientDisk is not an OSError, so the raise inside the
                # suppress still propagates to run_pull's Hal0Error handler.
                if rec.bytes_total > 0:
                    with contextlib.suppress(OSError):
                        free = shutil.disk_usage(tmp_dir).free
                        needed = rec.bytes_total - resume_from
                        if free < needed:
                            raise PullInsufficientDisk(
                                f"insufficient disk for {job.model_id}: need "
                                f"{needed} bytes, {free} free at {tmp_dir}",
                                details={
                                    "required_bytes": needed,
                                    "free_bytes": free,
                                    "path": str(tmp_dir),
                                },
                            )

                with open(part, mode) as f:
                    async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_BYTES):
                        if job.cancel_requested:
                            # Explicit user cancel — surfaced to run_pull via
                            # _PullCancelled; the except below drops the
                            # partial + sidecar.
                            raise _PullCancelled()
                        if not chunk:
                            continue
                        f.write(chunk)
                        hasher.update(chunk)
                        rec.bytes_done += len(chunk)
                        job.bytes_downloaded = base_done + rec.bytes_done
                        now = time.monotonic()
                        if (now - last_emit) >= _SSE_MIN_INTERVAL_S:
                            last_emit = now
                            job._signal()
            break

        # Stream complete — verify against the HF-advertised hash (WS-12)
        # BEFORE the atomic install so a corrupt object never lands at the
        # final path.
        digest = hasher.hexdigest()
        if rec.expected_sha256 and digest != rec.expected_sha256:
            # Keep the .part for diagnosis; drop the sidecar so a retry
            # starts clean rather than "resuming" a complete corrupt file.
            with contextlib.suppress(OSError):
                sidecar.unlink(missing_ok=True)
            raise PullChecksumMismatch(
                f"checksum mismatch for {hf_repo}/{rec.hf_filename}: expected "
                f"{rec.expected_sha256}, got {digest} (partial kept at {part})",
                details={
                    "repo": hf_repo,
                    "file": rec.hf_filename,
                    "expected_sha256": rec.expected_sha256,
                    "actual_sha256": digest,
                    "part_path": str(part),
                },
            )

        # Atomic install.
        ensure_shared_dir(final.parent)  # 2775, umask-proof (#1896)
        os.replace(part, final)
        _discard_partial(part, sidecar)  # part is renamed away; drop the sidecar
        size_bytes = final.stat().st_size
        rec.sha256 = digest
        rec.dest = str(final)
        rec.bytes_done = size_bytes
        if rec.bytes_total <= 0:
            rec.bytes_total = size_bytes
        job.bytes_downloaded = base_done + size_bytes
        job.bytes_total = max(job.bytes_total, base_total + rec.bytes_total)
        job._signal()
        return digest
    except _PullCancelled:
        _discard_partial(part, sidecar)
        raise
    except PullChecksumMismatch:
        raise  # .part intentionally preserved for diagnosis (sidecar dropped)
    except asyncio.CancelledError:
        # The TASK was cancelled (issue #1225: hal0-api's lifespan shutdown
        # does this to every in-flight pull so a `systemctl restart` doesn't
        # have to wait out a multi-GB download). Unlike the cooperative
        # ``job.cancel_requested`` flag above — an explicit user "stop and
        # clean up" — this is an interruption outside the download's
        # control, so it gets the same treatment as a transient transport
        # error: preserve the ``.part`` and write a resume sidecar so the
        # next pull attempt (manual re-POST or startup auto-resume) picks
        # up from the last complete chunk rather than restarting from zero.
        _write_resume_sidecar(part, sidecar, url=url, etag=captured_etag, total=rec.bytes_total)
        raise
    except Hal0Error:
        # Permanent failures (4xx PullError, insufficient disk) are not
        # resumable — discard the partial + sidecar (MR-7).
        _discard_partial(part, sidecar)
        raise
    except Exception as exc:
        # A transient transport error (connection drop / read timeout) must
        # PRESERVE the .part and record a resume sidecar so the NEXT run_pull
        # continues where this one left off (MR-7). Truly unexpected errors
        # poison state, so those still discard.
        if isinstance(exc, httpx.HTTPError):
            _write_resume_sidecar(part, sidecar, url=url, etag=captured_etag, total=rec.bytes_total)
        else:
            _discard_partial(part, sidecar)
        raise


def _others_total(job: PullJob, rec: PullFile) -> int:
    """Known ``bytes_total`` of every file of ``job`` except ``rec``.

    :func:`_download_one` reports ``job.bytes_total`` as this plus ``rec``'s
    own total, so when a split pull knows every part's size up front the
    job's total stays the whole set instead of shrinking to "files so far".
    """
    return sum(f.bytes_total for f in job.files if f is not rec)


def _split_staging_dir(model_id: str) -> Path:
    """Where a split pull holds verified parts until the whole set is in."""
    return _tmp_dir() / f"{_sanitise_id(model_id)}--parts"


async def _plan_split_parts(
    job: PullJob,
    hf_repo: str,
    *,
    hf_token: str | None,
    client: httpx.AsyncClient,
    recs: list[PullFile | None],
) -> str:
    """Pin a split pull to one commit and fill in each file's size and hash.

    Resolves ``main`` once and lists the repo tree at that sha, so a push to
    the repo mid-download can never mix parts from two revisions. Returns the
    sha. A part the tree does not list fails the pull before any download.
    """
    from hal0.registry.fileset import enumerate_repo, resolve_revision

    revision = await resolve_revision(hf_repo, token=hf_token, client=client)
    tree = {
        e.path: e
        for e in await enumerate_repo(hf_repo, revision=revision, token=hf_token, client=client)
    }
    missing = [r.hf_filename for r in recs if r is not None and r.hf_filename not in tree]
    if missing:
        raise PullInvalidSource(
            f"{hf_repo}@{revision[:12]} has no {', '.join(missing)} — the split set is incomplete",
            details={"repo": hf_repo, "revision": revision, "missing": missing},
        )
    for rec in recs:
        if rec is None:
            continue
        entry = tree[rec.hf_filename]
        rec.bytes_total = entry.lfs_size if entry.lfs_size is not None else entry.size
        rec.expected_sha256 = entry.lfs_oid
    job.bytes_total = sum(f.bytes_total for f in job.files)
    job._signal()
    return revision


def _staged_part_ok(path: Path, rec: PullFile) -> bool:
    """True when ``path`` already holds ``rec``'s verified bytes."""
    try:
        if not path.is_file() or path.stat().st_size != rec.bytes_total:
            return False
    except OSError:
        return False
    if not rec.expected_sha256:
        return True
    hasher = hashlib.sha256()
    _rehash_prefix(path, hasher)
    return hasher.hexdigest() == rec.expected_sha256


async def _pull_split_parts(
    job: PullJob,
    recs: list[PullFile],
    finals: list[Path],
    *,
    client: httpx.AsyncClient,
    hf_repo: str,
    base_headers: dict[str, str],
    revision: str,
) -> None:
    """Download every part of a split GGUF into the staging directory.

    Nothing at the install path changes until :func:`_install_split_parts`
    runs, so a failure on part N leaves an already-installed set whole. A
    part a previous attempt finished (and that still verifies) is kept, so
    a retry resumes at the first missing part. A cancel discards the lot.
    """
    stage = _split_staging_dir(job.model_id)
    ensure_shared_dir(stage)
    try:
        for rec, final in zip(recs, finals, strict=True):
            staged = stage / final.name
            if await asyncio.to_thread(_staged_part_ok, staged, rec):
                rec.bytes_done = rec.bytes_total
                rec.sha256 = rec.expected_sha256 or await asyncio.to_thread(_sha256_of, staged)
                rec.dest = str(staged)
                job.bytes_downloaded = sum(f.bytes_done for f in job.files)
                job._signal()
                continue
            await _download_one(
                job,
                rec,
                client=client,
                hf_repo=hf_repo,
                base_headers=base_headers,
                final=staged,
                base_done=sum(f.bytes_done for f in job.files if f is not rec),
                base_total=_others_total(job, rec),
                revision=revision,
            )
    except _PullCancelled:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _install_split_parts(job: PullJob, recs: list[PullFile], finals: list[Path]) -> None:
    """Move a fully staged split set into place, then drop the staging dir."""
    stage = _split_staging_dir(job.model_id)
    for rec, final in zip(recs, finals, strict=True):
        ensure_shared_dir(final.parent)  # 2775, umask-proof (#1896)
        os.replace(stage / final.name, final)
        rec.dest = str(final)
    shutil.rmtree(stage, ignore_errors=True)


def _sha256_of(path: Path) -> str:
    hasher = hashlib.sha256()
    _rehash_prefix(path, hasher)
    return hasher.hexdigest()


async def run_pull(
    job: PullJob,
    *,
    hf_repo: str = "",
    hf_file: str = "",
    registry: ModelRegistry,
    hf_token: str | None = None,
    client: httpx.AsyncClient | None = None,
    comfyui_subdir: str | None = None,
    capability: str | None = None,
    mmproj_file: str | None = None,
    draft_file: str | None = None,
    draft_repo: str | None = None,
    dest_override: str | None = None,
    fileset: FileSetPlan | None = None,
    extra_files: list[str] | None = None,
) -> None:
    """Background-task body: stream the file(s), hash, install, register.

    ML-2/ML-3: when ``fileset`` is given, this delegates entirely to
    :func:`_run_pull_fileset` — the generalised N-file loop (arbitrary
    shard counts + tokenizer/config carry, revision-pinned, hardlink-dedup
    aware, ``by-id`` pointer flip). The single/mmproj-pair path below is
    UNCHANGED for every existing caller that doesn't pass ``fileset``.

    Mutates ``job`` in place and pulses ``job._signal()`` on every chunk
    boundary or 500ms tick (whichever is rarer) so SSE consumers see
    progress without polling.

    Multi-file (WS-11): when ``mmproj_file`` is set, the mmproj sidecar is
    downloaded AFTER the main model into the same directory and associated
    on the registry row directly (``model.mmproj``) — no directory scan
    needed. ``job.bytes_downloaded`` / ``bytes_total`` stay the aggregate
    across files, so the SSE/status wire shape is unchanged.

    Cancellation: callers set ``job.cancel_requested = True``. The next
    chunk read checks the flag, deletes the current partial, transitions
    to ``cancelled``, and returns.

    Args:
        comfyui_subdir: When set (e.g. ``"checkpoints"``), the file lands
            under ``/var/lib/hal0/comfyui/models/<subdir>/<filename>``
            instead of the default ``/var/lib/hal0/models/<id>/<filename>``.
            Curated image-gen entries set this so ComfyUI's own model
            loaders find the file at the path their workflow nodes expect.
        mmproj_file: Optional multimodal-projector filename within the same
            HF repo (the Add-by-HF modal's vision pick, or a curated
            entry's ``mmproj_file``). Downloaded after the main file.
        draft_file: Optional speculative-decoding drafter GGUF (ADR-0031 §4),
            downloaded after the projector into the model's directory and
            registered as the row's ``draft`` fact. ``None`` leaves an
            existing pairing alone.
        draft_repo: HF repo holding ``draft_file``; defaults to ``hf_repo``.
        dest_override: Absolute final path for the MAIN file. The update
            flow (``POST /api/models/{id}/update``) pins this to the
            registry row's existing ``path`` so a re-pull replaces the
            installed bytes in place — never relocating an older
            flat-layout model into the capability-grouped tree and
            orphaning the previous file. When set, ``capability`` /
            ``comfyui_subdir`` routing is bypassed for the main file.
        extra_files: Further files from the same HF repo that the engine
            needs beside the main file (kokoro's ``voices-v1.0.bin``),
            installed into the main file's directory under their upstream
            basenames. ``None`` (the default) reads the curated row's
            ``extra_files`` for this model id and repo, so every caller — the
            API, the installer, the update flow — lays down the same bundle
            (#2739). Pass ``[]`` to pull the main file alone.
    """
    if fileset is not None:
        await _run_pull_fileset(
            job, fileset=fileset, registry=registry, hf_token=hf_token, client=client
        )
        return
    if not hf_repo or not hf_file:
        raise PullInvalidSource(
            "run_pull requires either (hf_repo, hf_file) or fileset",
            details={"model_id": job.model_id},
        )

    # A split GGUF pulls every part; ``hf_file`` names the first.
    shard_names = _shard_set(hf_file)

    job.state = "running"
    job.started_at = time.time()
    # Per-file manifest — main model (every part of a split one) first, then
    # the optional mmproj sidecar, then engine sidecars.
    if extra_files is None:
        extra_files = _curated_extra_files(job.model_id, hf_repo)
    if shard_names:
        job.files = [PullFile(hf_filename=name, kind="shard") for name in shard_names]
    else:
        job.files = [PullFile(hf_filename=hf_file, kind="model")]
    model_recs = list(job.files)
    mm_rec: PullFile | None = None
    if mmproj_file:
        mm_rec = PullFile(hf_filename=mmproj_file, kind="mmproj")
        job.files.append(mm_rec)
    draft_rec: PullFile | None = None
    draft_src_repo = draft_repo or hf_repo
    if draft_file:
        draft_rec = PullFile(hf_filename=draft_file, kind="draft")
        job.files.append(draft_rec)
    sidecar_recs = [PullFile(hf_filename=name, kind="sidecar") for name in extra_files]
    job.files.extend(sidecar_recs)
    job._signal()

    base_headers: dict[str, str] = {"User-Agent": "hal0/installer"}
    if hf_token:
        base_headers["Authorization"] = f"Bearer {hf_token}"

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(_CONNECT_TIMEOUT_S, read=_READ_TIMEOUT_S),
            follow_redirects=True,
        )

    try:
        # ── Main model file (every part of a split one) ───────────────────
        if dest_override:
            final = Path(dest_override)
            # A pre-#2739 non-GGUF row records ``.../model.gguf``; re-pulling
            # in place must install the real name, not the mislabel again.
            if final.name == _CANONICAL_GGUF_NAME and not _is_gguf(hf_file):
                final = final.parent / _canonical_name(hf_file)
        else:
            final = _final_path_for_entry(job.model_id, hf_file, comfyui_subdir, capability)
        model_finals = _shard_dests(final, len(model_recs)) if shard_names else [final]
        final = model_finals[0]
        revision = "main"
        if shard_names:
            # Every part (and the projector) from ONE commit, with sizes and
            # LFS hashes known up front: progress has its real total from the
            # first byte, and a later part is verified like the first.
            revision = await _plan_split_parts(
                job,
                hf_repo,
                hf_token=hf_token,
                client=client,
                recs=[
                    *model_recs,
                    mm_rec,
                    # A drafter from another repo is not in this tree.
                    draft_rec if draft_src_repo == hf_repo else None,
                ],
            )
            await _pull_split_parts(
                job,
                model_recs,
                model_finals,
                client=client,
                hf_repo=hf_repo,
                base_headers=base_headers,
                revision=revision,
            )
        else:
            await _download_one(
                job,
                model_recs[0],
                client=client,
                hf_repo=hf_repo,
                base_headers=base_headers,
                final=final,
                base_done=0,
                base_total=_others_total(job, model_recs[0]),
            )
        digest = model_recs[0].sha256 or ""
        size_bytes = sum(rec.bytes_done for rec in model_recs)

        # ── Optional mmproj sidecar (WS-11) ────────────────────────────────
        # Same directory as the main model, so the association survives the
        # discover scan's same-directory heuristic AND is set directly below.
        mmproj_final: Path | None = None
        if mm_rec is not None:
            mmproj_final = final.parent / Path(mm_rec.hf_filename).name
            await _download_one(
                job,
                mm_rec,
                client=client,
                hf_repo=hf_repo,
                base_headers=base_headers,
                final=mmproj_final,
                base_done=sum(f.bytes_done for f in job.files if f is not mm_rec),
                base_total=_others_total(job, mm_rec),
                revision=revision,
            )

        # ── Optional speculative-decoding drafter (ADR-0031 §4) ────────────
        # Same directory, upstream basename, like the projector. A drafter
        # from another repo is read from that repo's ``main``.
        draft_final: Path | None = None
        draft_info: Any = None
        if draft_rec is not None:
            draft_final = final.parent / Path(draft_rec.hf_filename).name
            await _download_one(
                job,
                draft_rec,
                client=client,
                hf_repo=draft_src_repo,
                base_headers=base_headers,
                final=draft_final,
                base_done=sum(f.bytes_done for f in job.files if f is not draft_rec),
                base_total=_others_total(job, draft_rec),
                revision=revision if draft_src_repo == hf_repo else "main",
            )
            draft_info = _classify_downloaded_drafter(draft_final)

        if shard_names:
            _install_split_parts(job, model_recs, model_finals)

        # ── Engine sidecars (#2739) ────────────────────────────────────────
        # Same directory as the main file, upstream basename: the engine
        # finds them by name (kokoro globs voices*.bin next to the ONNX).
        sidecar_finals: list[Path] = []
        for sc_rec in sidecar_recs:
            done = sum(f.bytes_done for f in job.files)
            total = sum(f.bytes_total for f in job.files)
            sc_final = final.parent / Path(sc_rec.hf_filename).name
            await _download_one(
                job,
                sc_rec,
                client=client,
                hf_repo=hf_repo,
                base_headers=base_headers,
                final=sc_final,
                base_done=done,
                base_total=total,
            )
            sidecar_finals.append(sc_final)

        if not comfyui_subdir:
            _remove_mislabeled_canonical(final, hf_file)

        # A delete cancels the job and waits for it (ADR-0026): if the cancel
        # landed after the last chunk, or the model was deleted after this job
        # started, never re-register the deleted model.
        if _registration_abandoned(job):
            raise _PullCancelled()

        # Register / update the registry entry (mmproj set directly — the
        # row is vision-ready without waiting for a directory scan).
        _register_pulled(
            registry,
            model_id=job.model_id,
            path=str(final),
            # The drafter's bytes count toward the row, as capacity.py documents.
            size_bytes=size_bytes + (draft_rec.bytes_done if draft_rec is not None else 0),
            sha256=digest,
            hf_repo=hf_repo,
            hf_filename=hf_file,
            capability=capability,
            comfyui_subdir=comfyui_subdir,
            mmproj=str(mmproj_final) if mmproj_final is not None else None,
            shards=[str(p) for p in model_finals] if shard_names else None,
            draft=str(draft_final) if draft_final is not None else None,
            draft_info=draft_info,
            draft_size_bytes=draft_rec.bytes_done if draft_rec is not None else 0,
            draft_sha256=draft_rec.sha256 if draft_rec is not None else None,
        )

        # Blob/file accounting — the SAME bookkeeping the fileset branch does
        # (#1412). Without it `store_blob`/`model_file` stay empty on every box
        # that pulls through HF coords, which silently disables refcounting,
        # hardlink dedup, and the store GC.
        if shard_names:
            installed_rows = [
                InstalledFile(rec.hf_filename, p, rec.bytes_done, rec.sha256, "shard", idx)
                for idx, (rec, p) in enumerate(zip(model_recs, model_finals, strict=True), 1)
            ]
        else:
            installed_rows = [InstalledFile(hf_file, final, size_bytes, digest, "model")]
        if mm_rec is not None and mmproj_final is not None:
            installed_rows.append(
                InstalledFile(
                    mmproj_file or mmproj_final.name,
                    mmproj_final,
                    mm_rec.bytes_done,
                    mm_rec.sha256,
                    "mmproj",
                )
            )
        if draft_rec is not None and draft_final is not None:
            installed_rows.append(
                InstalledFile(
                    draft_rec.hf_filename,
                    draft_final,
                    draft_rec.bytes_done,
                    draft_rec.sha256,
                    "draft",
                )
            )
        for sc_rec, sc_final in zip(sidecar_recs, sidecar_finals, strict=True):
            installed_rows.append(
                InstalledFile(
                    sc_rec.hf_filename, sc_final, sc_rec.bytes_done, sc_rec.sha256, "sidecar"
                )
            )
        _register_installed_files(registry, model_id=job.model_id, installed=installed_rows)

        # Capability-grouped pulls (FirstRun v2, design D2) get a meta.json
        # sidecar preserving HF provenance the canonical model.gguf name drops.
        if capability:
            write_model_meta(
                final,
                curated_id=job.model_id,
                hf_repo=hf_repo,
                hf_file=hf_file,
                sha256=digest,
                size_bytes=size_bytes,
                quant=None,
                capability=capability,
            )

        job.path = str(final)
        job.sha256 = digest
        job.bytes_downloaded = sum(f.bytes_done for f in job.files)
        total = sum(f.bytes_total for f in job.files)
        job.bytes_total = total if total > 0 else job.bytes_downloaded
        job.state = "completed"
        job.finished_at = time.time()
        job._signal()
    except _PullCancelled:
        # Explicit user cancel — staging already cleaned by _download_one.
        job.state = "cancelled"
        job.finished_at = time.time()
        job._signal()
    except asyncio.CancelledError:
        # Task itself was cancelled by the event loop — staging cleanup
        # happened in _download_one; just record the terminal state.
        job.state = "cancelled"
        job.finished_at = time.time()
        job._signal()
        raise
    except Hal0Error as exc:
        job.state = "failed"
        job.error = exc.message
        job.error_code = exc.code
        job.finished_at = time.time()
        job._signal()
        log.warning("model.pull_failed", extra={"model_id": job.model_id, "error": exc.message})
    except Exception as exc:
        job.state = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.error_code = "model.pull_failed"
        job.finished_at = time.time()
        job._signal()
        log.exception("model.pull_unexpected_error", extra={"model_id": job.model_id})
    finally:
        # Persist FIRST — a durable snapshot of the terminal (completed/failed/
        # cancelled) state so a restart-surviving status poll resolves for ANY
        # caller, including installer/bundle-tier pulls that call run_pull
        # directly (#MR-1). Ordered before the client close so a theoretical
        # aclose() raise can't skip the persist.
        persist_pull_job(job)
        if owns_client:
            await client.aclose()


def _classify_downloaded_drafter(path: Path) -> Any:
    """Read a downloaded drafter's GGUF header and classify it (ADR-0031 §4).

    A file that is not a drafter fails the pull with
    ``model.draft_not_a_drafter`` before anything is registered.
    """
    from hal0.errors import UnprocessableEntity
    from hal0.registry.drafters import classify_drafter
    from hal0.registry.gguf_header import read_gguf_header

    header = read_gguf_header(path)
    info = classify_drafter(header) if header else None
    if info is None:
        arch = (header or {}).get("general.architecture")
        # Don't leave a non-drafter beside the model for a scan to pair.
        with contextlib.suppress(OSError):
            path.unlink()
        raise UnprocessableEntity(
            f"{path.name} is not a speculative-decoding drafter (architecture {arch!r})",
            code="model.draft_not_a_drafter",
            details={"filename": path.name},
        )
    return info


def _register_pulled(
    registry: ModelRegistry,
    *,
    model_id: str,
    path: str,
    size_bytes: int,
    sha256: str,
    hf_repo: str,
    hf_filename: str,
    capability: str | None = None,
    comfyui_subdir: str | None = None,
    mmproj: str | None = None,
    shards: list[str] | None = None,
    draft: str | None = None,
    draft_info: Any = None,
    draft_size_bytes: int = 0,
    draft_sha256: str | None = None,
) -> None:
    """Upsert the registry entry after a successful pull.

    ``capability``/``comfyui_subdir`` are the pull layer's resolved routing
    (see :func:`hal0.api.routes.models._resolve_pull_capability`). For a brand-
    new entry (the curated wizard does NOT pre-seed a row) they decide the
    initial tagging: an image-gen pull into the ComfyUI tree must land as
    ``capabilities=["image"]`` / ``backends=["comfyui"]`` — NOT the old
    hardcoded ``["chat"]``, which mis-filed every fresh image checkpoint under
    the dashboard's llm bucket and drew it into the chat fallback pool.

    ``mmproj`` (WS-11) is the absolute path of a co-downloaded multimodal
    projector sidecar. Set directly so vision works right after the pull —
    no directory scan needed. ``None`` leaves any existing association
    (e.g. one a prior scan discovered) untouched.

    ``shards`` lists every installed part of a split GGUF, first part
    first (``path`` is that first part), stamped as ``metadata["shards"]`` —
    the same record the discover scan writes, which the delete and store
    accounting read. A single-file pull clears a stale list.

    ``draft`` (ADR-0031 §4) is the absolute path of a co-downloaded
    speculative-decoding drafter and ``draft_info`` its header classification
    (:class:`hal0.registry.drafters.DrafterInfo`): they write ``draft``,
    ``draft_kind`` and ``metadata["draft"]`` together. ``None`` leaves any
    existing pairing untouched, as ``mmproj`` does.
    """
    # ``pulled_at`` provenance lets the dashboard show when the installed
    # bytes were fetched — and thereby how stale they are vs an available
    # HF update (registry/update_check.py compares the shas).
    # A pull is an explicit request for these bytes: drop any tombstone a
    # keep-files delete left on the path (ADR-0026).
    tombstones.clear(path)
    fresh_meta: dict[str, Any] = {"sha256": sha256, "pulled_at": int(time.time())}
    if shards:
        fresh_meta["shards"] = list(shards)
    # Curated template override: some artifacts ship an embedded chat template
    # the runner's jinja engine can't execute (hal0-brain-sft's ``|min``), so
    # their catalogue entry names a bundled replacement. Stamped as the model's
    # chat_template fact, never over an operator's own setting.
    from hal0.registry.curated import get_curated

    curated = get_curated(model_id)
    curated_template = curated.chat_template if curated is not None else ""
    curated_tool_calling = curated.tool_calling if curated is not None else None
    # Context window — the pull path is the ONLY registration these bytes get
    # on a fresh install (nothing re-runs detection on them), and a row with
    # no window sends the 1.0 launch resolver to the 8192 safe fallback. For
    # the brain slot that is fatal: the steward's system prompt alone is
    # ~7.3k tokens, so the second turn 400s with exceed_context. The
    # artifact's own GGUF header is authoritative; the curated catalogue
    # window backfills an unreadable header.
    from hal0.registry.detect import (
        detect,
        detected_architecture,
        quant_from_filename,
        quant_from_rocmfpx_filename,
    )

    ctx_len: int | None = None
    detected_quant: str | None = None
    detected_arch: str | None = None
    detected_backends: list[str] = []
    detected_pooling_type: int | None = None
    try:
        detection = detect(Path(path))
        ctx_len = detection.context_length
        detected_quant = detection.quant
        # GGUF general.architecture — persisted so the model↔runner arch
        # fit-check (hal0#2118) can warn at slot assignment. The header is
        # authoritative; the curated catalogue backfills an unreadable one
        # (same tiering as context_length above).
        detected_arch = detected_architecture(detection)
        # #2192: detect() already classifies the runtime family
        # (suggested_backends: ["moonshine"]/["kokoro"]/["flm"]/…) and, for
        # GGUF embedding/reranking models, the header's pooling_type
        # (raw_hints["pooling_type"]) — but neither ever reached the
        # registry row below, which is the ONLY registration a pulled
        # model's bytes ever get. Without a populated ``backends``/
        # ``metadata.pooling_type``, hal0.model_meta.modality.
        # derive_modalities_from_model_info() can't recognise the model, so
        # a bound tts/transcription/embedding slot's LoadedSlot.modalities
        # stayed empty and OmniRouter's tool eligibility (resolve_for_request)
        # never saw those slots on a real deployment — despite the routing
        # logic itself being correct and unit-tested against a hand-built
        # model_info that happened to already carry the right shape.
        detected_backends = list(detection.suggested_backends)
        detected_pooling_type = detection.raw_hints.get("pooling_type")
    except Exception:  # header parse must never fail a completed pull
        ctx_len = None
    # #1890: capability-grouped installs (``_final_path_for_entry``) rename the
    # artifact to a canonical ``model.gguf``, so on-disk detection has no
    # filename token to work with — and the ROCmFPX family's custom
    # ``general.file_type`` is deliberately unmapped, so the header yields
    # nothing either. That is exactly the installer-seeded FPX brain model's
    # shape. Fall back to the quant-bearing HF source filename (the same
    # signal ``lazy_quant`` and ``write_model_meta`` preserve).
    if not detected_quant:
        detected_quant = quant_from_rocmfpx_filename(hf_filename or "") or quant_from_filename(
            hf_filename or ""
        )
    if not ctx_len and curated is not None and curated.context_length:
        ctx_len = int(curated.context_length)
    if not detected_arch and curated is not None and curated.architecture:
        detected_arch = str(curated.architecture)
    if ctx_len:
        fresh_meta["context_length"] = int(ctx_len)
        # #1657: mark this value as auto-detected (GGUF header or curated
        # backfill) so a LATER re-pull can tell it apart from an operator's
        # explicit metadata edit — see the merge below.
        fresh_meta["context_length_detected"] = True
    if detected_pooling_type is not None:
        # Header-derived fact, same standing as context_length/quant above —
        # re-stamped fresh on every pull, never operator-editable.
        fresh_meta["pooling_type"] = detected_pooling_type
    # #1890: stamp the detected quant on the row itself — the pull path is the
    # ONLY registration a pull-installed model ever gets, and a null ``quant``
    # left the #1790 FPX-on-wrong-runner guard inert (it reads the raw
    # ``model_dump()``, never the API serializer's ``lazy_quant`` backfill).
    # Written UNCONDITIONALLY: a re-pull replaces path/hf_filename/bytes, so a
    # replacement artifact that detection cannot classify must clear the prior
    # value rather than leave a stale quant attached to new bytes (which would
    # falsely 422 a non-FPX replacement on a cpu/vulkan runner).
    updates: dict[str, Any] = {
        "path": path,
        "size_bytes": size_bytes,
        "hf_repo": hf_repo,
        "hf_filename": hf_filename,
        "metadata": fresh_meta,
        "quant": detected_quant,
    }
    # Unlike ``quant`` (written unconditionally, see above), the arch is
    # written only when this pull actually read/backfilled one — an
    # unreadable replacement header must not CLEAR an arch a prior read (or
    # an operator edit) already established, since a missing arch merely
    # disarms the fit-check rather than mis-arming it.
    if detected_arch:
        updates["architecture"] = detected_arch
    if mmproj is not None:
        updates["mmproj"] = mmproj
    draft_meta: dict[str, Any] | None = None
    if draft is not None and draft_info is not None:
        updates["draft"] = draft
        updates["draft_kind"] = draft_info.kind
        draft_meta = {
            **draft_info.as_metadata(),
            "size_bytes": draft_size_bytes,
            "sha256": draft_sha256,
        }
        fresh_meta["draft"] = draft_meta
    try:
        existing = registry.get(model_id)
    except ModelNotFound:
        is_comfyui = bool(comfyui_subdir)
        cap = (capability or "").strip()
        caps = [cap] if cap else (["image"] if is_comfyui else ["chat"])
        # #2192: comfyui already special-cased its own backend; every other
        # kind detect() classified (moonshine/kokoro/flm/llama's vulkan-rocm-
        # cuda-cpu quartet/…) was dropped on the floor before this fix.
        backends = ["comfyui"] if is_comfyui else detected_backends
        # #1773: default tokenizer_repo from hf_repo when the pull
        # path already knows the source HF repo (the ONLY linkage a chat
        # cell's GuideLLM tokenizer resolution can trust without new pull
        # plumbing) — operator-settable/overridable afterward via the
        # registry edit surface.
        registry.add(
            Model(
                id=model_id,
                name=model_id,
                path=path,
                size_bytes=size_bytes,
                hf_repo=hf_repo,
                hf_filename=hf_filename,
                quant=detected_quant,
                capabilities=caps,
                backends=backends,
                # Task 3: stamp provider alongside the tag-era backends tag on
                # a NEW row — backends stays a vestigial lane hint, provider
                # is the write-path source of truth.
                provider=derive_model_provider(backends),
                mmproj=mmproj,
                draft=updates.get("draft"),
                draft_kind=updates.get("draft_kind"),
                metadata=dict(fresh_meta),
                architecture=detected_arch,
                chat_template=curated_template or None,
                tokenizer_repo=hf_repo or None,
                capability_flags=ModelCapabilities(tool_calling=curated_tool_calling)
                if curated_tool_calling is not None
                else ModelCapabilities(),
            )
        )
        return
    # Preserve license / capabilities / tags from any pre-pull register
    # call (e.g. pick-default seeded the entry from the curated catalogue
    # before kicking off the pull).
    merged_meta = dict(existing.metadata)
    merged_meta.update(fresh_meta)
    if not shards:
        merged_meta.pop("shards", None)
    # An operator-provided window outranks this pull's read — but a value
    # THIS SAME PATH auto-stamped on a prior pull (context_length_detected)
    # must NOT outrank a fresh re-pull's own detection, or an in-place
    # model update (replaced bytes, different GGUF header) can never pick
    # up the new window: it stays stuck at whatever the first pull happened
    # to read (#1657). Only a value with no detected-marker — i.e. one an
    # operator set explicitly through the metadata edit surface — wins over
    # the fresh read.
    if (
        existing.metadata.get("context_length")
        and not existing.metadata.get("context_length_detected")
        and "context_length" in fresh_meta
    ):
        merged_meta["context_length"] = existing.metadata["context_length"]
        merged_meta.pop("context_length_detected", None)
    updates["metadata"] = merged_meta
    # #2192: same "fill only if unset" rule as tokenizer_repo below — backfill
    # an empty backends list (the pre-fix state every non-comfyui pulled row
    # has) with this pull's detection, but never clobber an operator's own
    # edit (registry PATCH surface) or an already-tagged row.
    if detected_backends and not existing.backends:
        updates["backends"] = detected_backends
    # #1773: same "fill only if unset" rule as chat_template — a re-pull
    # backfills tokenizer_repo from hf_repo for a row that never
    # got one (pre-#1773 registration, or a hand-added row), but never
    # clobbers an operator's own tokenizer_repo override.
    if curated_template and not existing.chat_template:
        updates["chat_template"] = curated_template
    if hf_repo and not existing.tokenizer_repo:
        updates["tokenizer_repo"] = hf_repo
    if curated_tool_calling is not None and (
        existing.capability_flags is None or existing.capability_flags.tool_calling is None
    ):
        updates["capability_flags"] = ModelCapabilities(tool_calling=curated_tool_calling)
    registry.update(model_id, updates)


class InstalledFile(NamedTuple):
    """One file a pull just installed, as the store accounting sees it.

    ``rel`` is the repo-relative name (the ``model_file`` primary key
    alongside ``model_id``), ``dest`` the absolute installed path, ``sha256``
    the verified content digest (``None`` for a non-LFS file — nothing to
    dedup), and ``role`` the ``model``/``mmproj`` classification.
    """

    rel: str
    dest: Path
    size_bytes: int
    sha256: str | None
    role: str
    shard_index: int | None = None


def _register_installed_files(
    registry: ModelRegistry,
    *,
    model_id: str,
    installed: list[InstalledFile],
) -> None:
    """Write the ``store_blob`` + ``model_file`` accounting for a single-file
    (or mmproj-pair) pull — #1412.

    The fileset branch has always done this in two steps
    (:func:`_register_blob_after_install` per file, then the ``model_file``
    rows in :func:`_register_pulled_fileset`). The single-file branch did
    neither, so on any box whose models all arrived via HF coords both tables
    were empty and every consumer of them — refcount GC, hardlink dedup — was
    a silent no-op.

    The invariant this maintains is *refcount == number of ``model_file`` rows
    referencing the blob*. That makes the write idempotent: re-pulling the same
    bytes for the same ``(model_id, rel)`` leaves the count alone instead of
    ratcheting it upward forever (an inflated count can never fall back to 0,
    so the bytes would become unreclaimable). A re-pull whose digest CHANGED
    drops the superseded blob's reference before taking one on the new digest.

    No-op for a non-SQLite registry (the TOML escape hatch) — there is no
    ``model_file`` table to write, exactly as in :func:`_register_pulled_fileset`.
    """
    db = getattr(registry, "db_path", None)
    if db is None:
        return
    with connect(db) as conn, tx(conn):
        prior = {row["rel"]: row for row in repository.list_model_files(conn, model_id)}
        for rec in installed:
            was = prior.get(rec.rel)
            old_sha = was["sha256"] if was else None
            if old_sha == rec.sha256 and rec.sha256:
                # Same bytes, new name (a #2739 relabel): the canonical blob
                # referent was the removed file, so re-point it at the live one.
                blob = repository.get_blob(conn, rec.sha256)
                if blob is not None and not Path(blob["blob_path"]).is_file():
                    repository.set_blob_path(conn, rec.sha256, str(rec.dest))
            if old_sha != rec.sha256:
                if old_sha:
                    repository.drop_blob_ref(conn, old_sha)
                if rec.sha256:
                    if repository.get_blob(conn, rec.sha256) is None:
                        repository.insert_blob(
                            conn,
                            sha256=rec.sha256,
                            size_bytes=rec.size_bytes,
                            blob_path=str(rec.dest),
                        )
                    else:
                        repository.bump_blob_ref(conn, rec.sha256)
            repository.upsert_model_file(
                conn,
                model_id=model_id,
                rel=rec.rel,
                dest=str(rec.dest),
                size_bytes=rec.size_bytes,
                sha256=rec.sha256,
                lfs=rec.sha256 is not None,
                role=rec.role,
                shard_index=rec.shard_index,
            )
        _drop_superseded_model_files(conn, model_id=model_id, prior=prior, installed=installed)


def _drop_superseded_model_files(
    conn: Any,
    *,
    model_id: str,
    prior: dict[str, Any],
    installed: list[InstalledFile],
) -> None:
    """Forget the weights a re-pull replaced with a differently named set.

    Re-pulling an id with another quant (``seed_registry_from_body``) or a
    different part count leaves ``model_file`` rows, blob references and
    files from the old set that nothing will read again. Drop the old
    ``model``/``shard`` rows this pull did not write, release their blob
    references, and delete their files unless the new set reuses the path
    (a capability-grouped install names every 3-part set
    ``model-0000i-of-00003.gguf``). Projector and sidecar rows are left
    alone: a pull without ``mmproj`` keeps the row's existing pairing.
    """
    from hal0.registry.gc import release_blob_ref

    new_rels = {rec.rel for rec in installed}
    new_dests = {str(rec.dest) for rec in installed}
    for rel, row in prior.items():
        if rel in new_rels or row["role"] not in ("model", "shard"):
            continue
        dest = row["dest"]
        reused = dest in new_dests
        conn.execute("DELETE FROM model_file WHERE model_id = ? AND rel = ?", (model_id, rel))
        release_blob_ref(conn, row["sha256"], dest, model_id=model_id, keep_bytes=reused)
        if dest and not reused:
            with contextlib.suppress(Exception):
                store.assert_under_store(dest, severity="fail").unlink(missing_ok=True)


# ── file-SET pulling (ML-2/ML-3) ──────────────────────────────────────────


def _maybe_hardlink_from_blob(sha256: str, dest: Path) -> bool:
    """Pre-fetch dedup (plan §23.3c): hardlink ``dest`` from an existing
    ``store_blob`` instead of streaming, when one is already registered for
    ``sha256``. Returns ``True`` on a successful hardlink install (the
    caller then skips the download entirely); ``False`` on any miss or
    failure (including cross-filesystem, where a hardlink can't exist) —
    the caller falls back to the ordinary stream-and-verify path.
    """
    with connect() as conn:
        row = repository.get_blob(conn, sha256)
        if row is None:
            return False
        blob_path = Path(row["blob_path"])
        try:
            if not blob_path.is_file():
                return False
            ensure_shared_dir(dest.parent)  # 2775, umask-proof (#1896)
            if os.stat(blob_path).st_dev != os.stat(dest.parent).st_dev:
                # Cross-filesystem — hardlinks are impossible; degrade to a
                # normal download rather than fail the pull (plan risk:
                # "Hardlink cross-fs ... silently degrades to copy").
                return False
            tmp = dest.parent / f".{dest.name}.hardlink-tmp-{os.getpid()}"
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            os.link(blob_path, tmp)
            os.replace(tmp, dest)
        except OSError as exc:
            log.debug("pull.hardlink_dedup_failed sha256=%s error=%s", sha256, exc)
            return False
        with tx(conn):
            repository.bump_blob_ref(conn, sha256)
        return True


def _register_blob_after_install(sha256: str | None, dest: Path, size_bytes: int) -> None:
    """Post-download dedup bookkeeping: a first-seen sha256 becomes the
    canonical ``store_blob`` (this ``dest`` IS the blob); an already-known
    sha256 (a race with a concurrent pull of the identical file) just bumps
    refcount rather than duplicating the row. No-op for non-LFS files
    (``sha256`` is ``None`` — nothing to dedup)."""
    if not sha256:
        return
    with connect() as conn:
        existing = repository.get_blob(conn, sha256)
        with tx(conn):
            if existing is None:
                repository.insert_blob(
                    conn, sha256=sha256, size_bytes=size_bytes, blob_path=str(dest)
                )
            else:
                repository.bump_blob_ref(conn, sha256)


def _register_pulled_fileset(
    registry: ModelRegistry,
    *,
    model_id: str,
    fileset: FileSetPlan,
    installed: list[tuple[FileSetEntry, Path]],
    entry_dest: Path,
    mmproj_dest: Path | None,
) -> None:
    """Upsert the ``model`` row + ``model_file`` rows after a file-set pull.

    Mirrors :func:`_register_pulled`'s add-vs-update branch, plus: pins
    ``model.revision`` to the resolved commit sha (update-detect over the
    whole set — plan §7.1c a5), and writes one ``model_file`` row per
    installed file (ML-2 is the first writer of that table — ML-1 imports
    it EMPTY).
    """
    # Explicit pull: un-ignore the entry path (ADR-0026).
    tombstones.clear(entry_dest)
    entry_sha = next((e.lfs_sha256 for e, dest in installed if dest == entry_dest), None)
    total_size = sum(dest.stat().st_size for _, dest in installed if dest.exists())
    fresh_meta: dict[str, Any] = {"pulled_at": int(time.time())}
    if entry_sha:
        fresh_meta["sha256"] = entry_sha
    # #1890: same quant detection as :func:`_register_pulled` — a file-set
    # pull (ML-2/3) is registration too, and must not leave ``quant`` null
    # any more than the single-file path does. Same source-filename fallback
    # (an installed name with no token / unmapped file_type still classifies
    # via the HF entry filename) and same unconditional write (a re-pull that
    # detection cannot classify clears the stale value with the stale bytes).
    from hal0.registry.detect import (
        detect,
        quant_from_filename,
        quant_from_rocmfpx_filename,
    )

    entry_name = Path(fileset.entry_rel).name
    detected_quant: str | None = None
    try:
        detected_quant = detect(entry_dest).quant
    except Exception:  # header parse must never fail a completed pull
        detected_quant = None
    if not detected_quant:
        detected_quant = quant_from_rocmfpx_filename(entry_name) or quant_from_filename(entry_name)

    updates: dict[str, Any] = {
        "path": str(entry_dest),
        "size_bytes": total_size,
        "hf_repo": fileset.repo,
        "hf_filename": entry_name,
        "quant": detected_quant,
    }
    if mmproj_dest is not None:
        updates["mmproj"] = str(mmproj_dest)

    try:
        existing = registry.get(model_id)
    except ModelNotFound:
        add_meta = dict(fresh_meta)
        registry.add(
            Model(
                id=model_id,
                name=model_id,
                path=str(entry_dest),
                size_bytes=total_size,
                hf_repo=fileset.repo,
                hf_filename=Path(fileset.entry_rel).name,
                quant=detected_quant,
                capabilities=["chat"],
                mmproj=str(mmproj_dest) if mmproj_dest else None,
                metadata=add_meta,
            )
        )
    else:
        merged_meta = dict(existing.metadata)
        merged_meta.update(fresh_meta)
        updates["metadata"] = merged_meta
        registry.update(model_id, updates)

    db_path = getattr(registry, "db_path", None)
    if db_path is None:
        return  # non-SQLite registry (TOML escape hatch) — no model_file table
    with connect(db_path) as conn, tx(conn):
        for entry, dest in installed:
            repository.upsert_model_file(
                conn,
                model_id=model_id,
                rel=entry.rel,
                dest=str(dest),
                size_bytes=entry.size_bytes or (dest.stat().st_size if dest.exists() else None),
                sha256=entry.lfs_sha256,
                lfs=entry.lfs_sha256 is not None,
                role=entry.role,
                shard_index=entry.shard_index,
            )
        # `revision` is a §7.1 reserved column with no Model field yet (ML-1's
        # DDL comment) — written directly until that lane lands a pydantic field.
        # (`preferred_runner` is no longer written here: the runner is slot-owned
        # now (spec-hw-slot-ownership §6); its column is KEPT-but-unbound, nulled,
        # with the physical drop deferred post-1.0.)
        conn.execute(
            "UPDATE model SET revision = ? WHERE id = ?",
            (fileset.revision, model_id),
        )


async def _run_pull_fileset(
    job: PullJob,
    *,
    fileset: FileSetPlan,
    registry: ModelRegistry,
    hf_token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Background-task body for a multi-file (shard-set) pull (ML-2/ML-3).

    Generalises the historic hardcoded two-file (main + mmproj) loop into
    an N-file loop over ``fileset.files`` (already shard/role ordered by
    :func:`hal0.registry.fileset.plan_fileset`). Each file: a pre-fetch
    dedup check (hardlink instead of stream on a ``store_blob`` hit), else
    the unchanged per-file :func:`_download_one` core (resume/integrity/
    atomic install all reused), pinned to ``fileset.revision`` so a
    mid-pull upstream re-tag can't stitch mismatched shards. On full
    success: registers the ``model`` + ``model_file`` rows and flips the
    ``by-id/<model_id>`` pointer at the entry file.
    """
    job.state = "running"
    job.started_at = time.time()
    job.files = [PullFile(hf_filename=f.rel, kind=f.role) for f in fileset.files]
    job._signal()

    base_headers: dict[str, str] = {"User-Agent": "hal0/installer"}
    if hf_token:
        base_headers["Authorization"] = f"Bearer {hf_token}"

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(_CONNECT_TIMEOUT_S, read=_READ_TIMEOUT_S),
            follow_redirects=True,
        )

    installed: list[tuple[FileSetEntry, Path]] = []
    try:
        base_done = 0
        base_total = 0
        for entry, rec in zip(fileset.files, job.files, strict=True):
            dest = store.file_dest(fileset.repo, fileset.revision, entry.rel)
            hardlinked = False
            if entry.lfs_sha256:
                rec.expected_sha256 = entry.lfs_sha256
                hardlinked = await asyncio.to_thread(
                    _maybe_hardlink_from_blob, entry.lfs_sha256, dest
                )
            if hardlinked:
                size = dest.stat().st_size
                rec.sha256 = entry.lfs_sha256
                rec.dest = str(dest)
                rec.bytes_done = size
                rec.bytes_total = size
                base_done += size
                base_total += size
                job.bytes_downloaded = base_done
                job.bytes_total = base_total
                job._signal()
            else:
                await _download_one(
                    job,
                    rec,
                    client=client,
                    hf_repo=fileset.repo,
                    base_headers=base_headers,
                    final=dest,
                    base_done=base_done,
                    base_total=base_total,
                    revision=fileset.revision,
                )
                base_done += rec.bytes_done
                base_total = max(base_total + rec.bytes_total, base_done)
                await asyncio.to_thread(
                    _register_blob_after_install, rec.sha256, dest, rec.bytes_done
                )
            installed.append((entry, dest))

        entry_dest = next(dest for e, dest in installed if e.rel == fileset.entry_rel)
        mmproj_dest = next((dest for e, dest in installed if e.role == "mmproj"), None)

        # Cancelled after the last file landed, or deleted after this job
        # started (ADR-0026): don't re-register it.
        if _registration_abandoned(job):
            raise _PullCancelled()

        await asyncio.to_thread(
            _register_pulled_fileset,
            registry,
            model_id=job.model_id,
            fileset=fileset,
            installed=installed,
            entry_dest=entry_dest,
            mmproj_dest=mmproj_dest,
        )
        with contextlib.suppress(Exception):
            store.set_entry_pointer(job.model_id, entry_dest)
        with contextlib.suppress(Exception):
            store.finalize_perms(entry_dest)

        job.path = str(entry_dest)
        job.sha256 = next((e.lfs_sha256 for e, dest in installed if dest == entry_dest), None) or ""
        job.bytes_downloaded = sum(f.bytes_done for f in job.files)
        total = sum(f.bytes_total for f in job.files)
        job.bytes_total = total if total > 0 else job.bytes_downloaded
        job.state = "completed"
        job.finished_at = time.time()
        job._signal()
    except _PullCancelled:
        job.state = "cancelled"
        job.finished_at = time.time()
        job._signal()
    except asyncio.CancelledError:
        job.state = "cancelled"
        job.finished_at = time.time()
        job._signal()
        raise
    except Hal0Error as exc:
        job.state = "failed"
        job.error = exc.message
        job.error_code = exc.code
        job.finished_at = time.time()
        job._signal()
        log.warning("model.pull_failed", extra={"model_id": job.model_id, "error": exc.message})
    except Exception as exc:
        job.state = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.error_code = "model.pull_failed"
        job.finished_at = time.time()
        job._signal()
        log.exception("model.pull_unexpected_error", extra={"model_id": job.model_id})
    finally:
        persist_pull_job(job)
        if owns_client:
            await client.aclose()


async def run_flm_pull(
    job: PullJob,
    *,
    tag: str,
    registry: ModelRegistry,
) -> None:
    """Background-task body: shell host ``flm pull <tag>`` (as the hal0 user).

    Mirrors :func:`run_pull`'s state machine (queued → running →
    {completed, failed, cancelled}) so the existing SSE / status routes
    work unchanged. Differs in two ways:

      * Bytes come from polling the on-disk dir size of the target install
        path. FLM models contain multiple files (config.json, model.q4nx,
        tokenizer.json, …) and FLM's stdout emits ``Downloading: X% (cur/tot)``
        for each file independently — leaning on per-file regex parsing made
        ``bytes_downloaded`` regress to 0 each time a new file began, which
        the dashboard rendered as a "hanging" progress bar. Dir-size polling
        is monotonic by construction and survives FLM stdout-format changes.
      * No sha256 is computed here: FLM verifies file hashes internally
        and refuses to use mismatched weights. Re-hashing would just
        double-read multi-GB files for the same guarantee.

    Cancellation works via SIGTERM on the ``flm`` subprocess — it aborts the
    download. The partial files are left on disk; FLM's next pull deletes &
    redownloads them (it checks file sizes against the manifest before reusing).

    On success the FLM probe cache is reset so the next ``/api/capabilities``
    GET flips this tag's ``downloaded`` flag to True without an api restart.
    """
    # Local import to keep providers.flm's docker subprocess out of the
    # base pull module's import graph (tests pull this module in
    # environments without docker).
    from hal0.providers.flm import (
        ensure_host_flm_store_link,
        flm_host_async_spawn,
        flm_pull_command,
        flm_served_models,
        flm_served_models_async,
        reset_flm_catalog_cache,
    )

    job.state = "running"
    job.started_at = time.time()
    job._signal()

    argv, host_models_dir = flm_pull_command(tag)

    # Setup awaits run inside the try so a cancellation or error here reaches
    # the same handlers as one during the download, and the job never stays
    # ``running`` (#2334).
    proc: asyncio.subprocess.Process | None = None
    try:
        # flm hardcodes ~/.config/flm/models and has no dir flag, so a host pull
        # writes to that default path — NOT the (possibly relocated) store the
        # progress poller + serving container use. Point flm's default at the store
        # via a symlink (host analog of the container bind-mount) before we spawn,
        # so weights land in the store, progress tracks the real bytes, and serving
        # finds them. Off-loaded to a thread: the one-time legacy-content migration
        # can copy multi-GB weights and must not block the event loop.
        await asyncio.to_thread(ensure_host_flm_store_link)

        # Resolve the install path + advertised total upfront so progress
        # reporting is monotonic. The install-path lookup keeps the raw
        # ``flm list -j`` answer for the whole pull (#2379); the advertised total
        # reads the cached catalog flm_served_models uses. Both fall back
        # gracefully when the probe failed (host without flm / image not present).
        #
        # These two probes (``flm list``) can transiently return an EMPTY catalog
        # right at pull start — observed live: the dir grew steadily on disk while
        # the job reported ``0/0`` the whole download because ``target_dir`` was
        # ``None`` here and never retried. So they are NOT resolved once-and-for-
        # all: :func:`_resolve_target_dir` / :func:`_resolve_advertised_total` below
        # re-attempt each tick until they succeed, then progress tracks growth.
        #
        # Both shell ``flm list -j`` (up to 30 s) on a cold cache, so every read
        # here runs on a worker thread, never on the event loop (#2334).
        install_path = _FlmInstallPathLookup(host_models_dir, tag)
        target_dir = await asyncio.to_thread(install_path)
        advertised_total = 0
        for entry in await flm_served_models_async():
            if entry["tag"] == tag:
                advertised_total = int(entry.get("size_bytes") or 0)
                break
        # ``_dir_size`` walks the whole model dir: a worker thread, never the
        # event loop, at start, per tick and after the pull (#2380).
        baseline_size = await asyncio.to_thread(_dir_size, target_dir) if target_dir else 0
        if advertised_total > baseline_size:
            job.bytes_total = advertised_total
            job._signal()

        # uvloop (hal0-api's event loop) rejects the user/group Popen kwargs, so
        # the drop to the hal0 user rides the argv (setpriv/runuser) instead.
        argv, spawn_kwargs = flm_host_async_spawn(argv)

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **spawn_kwargs,
        )
        assert proc.stdout is not None
        last_emit = time.monotonic()

        def _resolve_target_dir() -> None:
            """Retry the install-path probe until it resolves (see above).

            A fresh pull creates ``target_dir`` from scratch, so when we
            resolve it late its whole on-disk size IS this pull's progress —
            baseline 0 is correct. A re-pull of a partially-present model would
            undercount by the pre-existing bytes, a harmless display nuance
            versus the alternative of reporting 0 for the entire download.
            """
            nonlocal target_dir, baseline_size
            if target_dir:
                return
            resolved = install_path()
            if resolved:
                target_dir = resolved
                baseline_size = 0

        def _resolve_advertised_total() -> None:
            """Retry the advertised-size probe so the bar shows a real total."""
            nonlocal advertised_total
            if advertised_total > 0:
                return
            for entry in flm_served_models():
                if entry["tag"] == tag:
                    advertised_total = int(entry.get("size_bytes") or 0)
                    if advertised_total > job.bytes_total:
                        job.bytes_total = advertised_total
                    break

        async def _resolve_pending() -> None:
            """Retry whichever probe is unresolved, on a worker thread (#2334)."""
            if target_dir and advertised_total > 0:
                return

            def _resolve() -> None:
                """Both retries, together, on one worker-thread hop."""
                _resolve_target_dir()
                _resolve_advertised_total()

            await asyncio.to_thread(_resolve)

        async def _tick_progress() -> None:
            """Refresh bytes_downloaded from on-disk dir size if it grew."""
            nonlocal last_emit
            if not target_dir:
                return
            now = time.monotonic()
            if (now - last_emit) < _SSE_MIN_INTERVAL_S:
                return
            current = await asyncio.to_thread(_dir_size, target_dir) - baseline_size
            if current > job.bytes_downloaded:
                job.bytes_downloaded = current
                if current > job.bytes_total:
                    # FLM's advertised size is approximate; let actual
                    # bytes stretch bytes_total so the UI stays at ≤100%.
                    job.bytes_total = current
                last_emit = now
                job._signal()

        while True:
            if job.cancel_requested:
                proc.terminate()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=10.0)
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                job.state = "cancelled"
                job.finished_at = time.time()
                job._signal()
                return
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
            except TimeoutError:
                # No new line in 1s — loop back so cancellation observes
                # promptly. Also a good cadence for the dir-size poll.
                await _resolve_pending()
                await _tick_progress()
                continue
            if not raw:
                break
            # Reading the line is enough — we don't parse it for byte
            # accounting any more, but the readline() drains the pipe so
            # the docker process doesn't block on a full stdout buffer.
            await _resolve_pending()
            await _tick_progress()

        await proc.wait()
        if proc.returncode != 0:
            raise PullError(
                f"flm pull {tag!r} exited with status {proc.returncode}",
                details={"tag": tag, "exit_code": proc.returncode},
            )

        # Refresh the catalog so subsequent /api/capabilities picks up
        # the new installed=true flag without a process restart. Also drop
        # the cached NPU image-present probe so a first FLM pull (which may
        # have brought the toolbox image online) can flip the NPU backend on
        # the next capabilities GET without a restart.
        reset_flm_catalog_cache()
        from hal0.capabilities.catalog import reset_flm_image_present_cache

        reset_flm_image_present_cache()

        # Best-effort path bookkeeping. FLM stores each tag's weights at
        # ``<host_models_dir>/<HF-repo-name>/`` — we resolve the dir from
        # the FLM model_list lookup when available, falling back to the
        # bare host dir so a missing entry doesn't fail the job. A dir the
        # ticks already resolved is final (the tag's ``url`` does not change
        # across a pull), so only an unresolved one asks the lookup again.
        final_path = target_dir or (await asyncio.to_thread(install_path)) or host_models_dir
        # #2446: never report (or register) a path that is not on disk.
        await asyncio.to_thread(_verify_flm_landing, str(final_path), host_models_dir, tag)
        size_bytes = await asyncio.to_thread(_dir_size, final_path)
        if job.bytes_total <= 0 and size_bytes > 0:
            job.bytes_total = size_bytes
        if job.bytes_downloaded < size_bytes:
            job.bytes_downloaded = size_bytes
        job.path = str(final_path)

        # Read the freshly-reset probe's classification for this tag so the
        # registry row records the model's real modality (embed/stt/chat)
        # instead of assuming chat (#1647).
        pulled_capabilities: list[str] | None = None
        for entry in await flm_served_models_async():
            if entry["tag"] == tag:
                caps = entry.get("capabilities")
                if caps:
                    pulled_capabilities = list(caps)
                break

        # Cancelled while FLM finished, or deleted after this job started
        # (ADR-0026): don't re-register it.
        if _registration_abandoned(job):
            job.state = "cancelled"
            job.finished_at = time.time()
            job._signal()
            return

        # Register an FLM tag so the registry surfaces it for downstream
        # consumers (catalog, slot model resolution). hf_repo/filename
        # stay empty — FLM tags route through the toolbox, not HF directly.
        _register_flm_pulled(
            registry,
            tag=tag,
            path=str(final_path),
            size_bytes=size_bytes,
            capabilities=pulled_capabilities,
        )

        job.state = "completed"
        job.finished_at = time.time()
        job._signal()
        log.info(
            "model.pull_flm_completed",
            extra={"tag": tag, "path": str(final_path), "bytes": size_bytes},
        )
    except asyncio.CancelledError:
        if proc is not None and proc.returncode is None:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        job.state = "cancelled"
        job.finished_at = time.time()
        job._signal()
        raise
    except Hal0Error as exc:
        job.state = "failed"
        job.error = exc.message
        job.error_code = exc.code
        job.finished_at = time.time()
        job._signal()
        log.warning("model.pull_flm_failed", extra={"tag": tag, "error": exc.message})
    except Exception as exc:
        job.state = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.error_code = "model.pull_failed"
        job.finished_at = time.time()
        job._signal()
        log.exception("model.pull_flm_unexpected_error", extra={"tag": tag})


class _FlmInstallPathLookup:
    """Per-pull lookup of the on-disk subdir FLM uses for ``tag`` (#2379).

    ``run_flm_pull`` retries this every progress tick until it resolves, so it
    must not shell ``flm list -j`` (:func:`~hal0.providers.flm._probe_flm_catalog`,
    uncached, up to 30 s) on every call. The first non-empty catalog is kept
    for the life of the pull; only an empty or failed answer is probed again,
    since that is the transient the per-tick retry exists for. The cached
    catalog in ``providers/flm.py`` cannot be reused: it keeps parsed entries
    without the ``url`` this lookup needs.

    Not thread-safe; ``run_flm_pull`` awaits each call before the next.
    """

    def __init__(self, host_models_dir: str, tag: str) -> None:
        """Bind the lookup to one pull's host models dir and tag."""
        self.host_models_dir = host_models_dir
        self.tag = tag
        self._models: list[Any] | None = None

    def __call__(self) -> str | None:
        """Return the install dir for the tag, or None if it is unknown."""
        if not self._models:
            from hal0.providers.flm import _probe_flm_catalog

            self._models = _probe_flm_catalog() or None
        if not self._models:
            return None
        return _flm_install_path_from(self._models, self.host_models_dir, self.tag)


def _verify_flm_landing(path: str, host_models_dir: str, tag: str) -> None:
    """Raise :class:`PullError` unless ``path`` exists after a successful ``flm pull``.

    ``path`` is computed from the store and the tag's HF repo name, not read
    back from flm, so it can name a dir that flm never wrote: when flm's
    ``$HOME/.config/flm/models`` is not linked to the store, the weights land
    there instead (#2446). The error names that directory when it holds the
    tag's repo, so the user knows where the files went and how to move them.
    """
    if os.path.isdir(path):
        return
    from hal0.config.paths import default_flm_models_dir

    details = {"tag": tag, "path": path}
    stray = Path(default_flm_models_dir()) / Path(path).name
    is_repo_dir = os.path.normpath(path) != os.path.normpath(host_models_dir)
    if is_repo_dir and stray.is_dir() and os.path.realpath(stray) != os.path.realpath(path):
        raise PullError(
            f"flm pull {tag!r} finished, but the weights landed in {stray}, not in the "
            f"FLM store at {path}. The NPU slot only mounts the store; run "
            f"`sudo hal0 doctor perms --fix` to move them there and link flm's cache.",
            details={**details, "landed": str(stray)},
        )
    raise PullError(
        f"flm pull {tag!r} exited 0, but {path} does not exist.",
        details=details,
    )


def _flm_install_path_from(models: list[Any], host_models_dir: str, tag: str) -> str | None:
    """Find ``tag``'s install dir in a raw ``flm list -j`` model list.

    The toolbox image's bundled ``model_list.json`` maps family → variants →
    name=HF-repo; the flat ``flm list -j`` output drops that tree but keeps
    each entry's ``url``, so the HF repo name is read from there.
    """
    for entry in models:
        if not isinstance(entry, dict):
            continue
        if entry.get("model") == tag or entry.get("name") == tag:
            # The "name" field on the flat list is the same as model.
            # The HF repo name lives only in the nested model_list.json
            # tree; the flat list flattens it into ``files`` + ``url``.
            # Extract from the ``url`` field, which looks like
            # ``https://huggingface.co/FastFlowLM/Qwen3-0.6B-NPU2/resolve/...``.
            url = entry.get("url") or ""
            parts = url.split("/")
            try:
                idx = parts.index("huggingface.co")
                repo_name = parts[idx + 2]  # owner/<repo>
                return str(Path(host_models_dir) / repo_name)
            except (ValueError, IndexError):
                return None
    return None


def _dir_size(path: str | Path) -> int:
    """Sum file sizes under ``path``; 0 if path is missing/unreadable."""
    p = Path(path)
    if not p.exists():
        return 0
    total = 0
    try:
        for child in p.rglob("*"):
            if child.is_file():
                with contextlib.suppress(OSError):
                    total += child.stat().st_size
    except OSError:
        return total
    return total


def _register_flm_pulled(
    registry: ModelRegistry,
    *,
    tag: str,
    path: str,
    size_bytes: int,
    capabilities: list[str] | None = None,
) -> None:
    """Upsert a registry entry for an FLM-pulled model.

    FLM tags don't carry HF coords from a hal0 perspective (the toolbox
    image's ``flm pull`` resolves them itself), so ``hf_repo`` and
    ``hf_filename`` stay empty. ``metadata.runtime = "flm"`` flags the
    entry so other code (slot pick, model resolution) can route it to
    the FLM provider without re-deriving from the id.

    ``capabilities`` should come from the live ``flm_served_models()``
    classification (embed/stt/chat) rather than being assumed — a hardcoded
    ``["chat"]`` here mistyped every embed/STT tag pulled through FLM as
    chat-only, and ``models_service.list_all()``'s dedupe skip had no way to
    recover the real value afterward (#1647). Falls back to ``["chat"]``
    only when the caller couldn't resolve a classification (e.g. the probe
    was transiently empty right after the pull).
    """
    # Explicit pull: un-ignore the FLM model dir (ADR-0026).
    tombstones.clear(path)
    caps = list(capabilities) if capabilities else ["chat"]
    updates: dict[str, Any] = {
        "path": path,
        "size_bytes": size_bytes,
        "capabilities": caps,
        "metadata": {"runtime": "flm"},
    }
    try:
        existing = registry.get(tag)
    except ModelNotFound:
        registry.add(
            Model(
                id=tag,
                name=tag,
                path=path,
                size_bytes=size_bytes,
                capabilities=caps,
                # #2192: "npu" is a hardware-lane label, not a runtime family —
                # hal0.model_meta.modality.derive_modalities_from_model_info's
                # ``_family_runners`` set only recognises "flm" (matching
                # metadata.runtime above), so an FLM-routed stt/embed shadow
                # slot's model never resolved a modality and OmniRouter could
                # never route to it. derive_model_provider("npu") already
                # mapped to "flm" either way (_PROVIDER_TAGS), so this only
                # fixes the modality side, not provider derivation.
                backends=["flm"],
                # Task 3: stamp provider alongside the tag-era backends tag on
                # a NEW row — see the matching comment in ``_register_pulled``.
                provider=derive_model_provider(["flm"]),
                metadata={"runtime": "flm"},
            )
        )
        return
    merged_meta = dict(existing.metadata)
    merged_meta["runtime"] = "flm"
    updates["metadata"] = merged_meta
    # #2192: backfill a pre-fix row's wrong ["npu"] (or a never-set empty
    # list) the same "fill only if unset/wrong" way ``_register_pulled``
    # backfills backends — never clobbers an operator's own edit.
    if existing.backends in ([], ["npu"]):
        updates["backends"] = ["flm"]
    registry.update(tag, updates)


__all__ = [
    "PullChecksumMismatch",
    "PullError",
    "PullFile",
    "PullInsufficientDisk",
    "PullInvalidSource",
    "PullJob",
    "PullJobNotFound",
    "_sanitise_id",
    "get_job",
    "hf_download_url",
    "list_persisted_jobs",
    "make_job",
    "persist_pull_job",
    "pull_job_file",
    "run_flm_pull",
    "run_pull",
    "sweep_orphaned_partials",
    "sweep_pull_jobs",
]
