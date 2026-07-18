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
from pathlib import Path
from typing import Any

import httpx

from hal0.config import paths, store
from hal0.db import repository
from hal0.db.connection import connect, tx
from hal0.errors import Hal0Error
from hal0.registry.fileset import FileSetEntry, FileSetPlan
from hal0.registry.model import Model
from hal0.registry.store import ModelNotFound, ModelRegistry, _fsync_dir

log = logging.getLogger(__name__)


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
    kind: str = "model"  # "model" | "mmproj"
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
    the model lands in a capability-grouped tree with one canonical
    ``model.gguf`` filename: ``<pull_root>/<capability>/<id>/model.gguf``.
    This keeps the store self-documenting by role and gives the slot config
    a stable path that never chases a HF-specific filename. A sidecar
    ``meta.json`` (written by :func:`write_model_meta`) preserves provenance.

    Everything else uses the default ``/var/lib/hal0/models/<id>/<filename>``
    layout (back-compat for the standalone ``/api/models/{id}/pull`` path).
    """
    if comfyui_subdir:
        return _comfyui_models_dir(comfyui_subdir) / filename
    if capability:
        return _pull_root() / _sanitise_id(capability) / _sanitise_id(model_id) / "model.gguf"
    return _final_path(model_id, filename)


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
        path.parent.mkdir(parents=True, exist_ok=True)
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
    tmp_dir.mkdir(parents=True, exist_ok=True)
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
        final.parent.mkdir(parents=True, exist_ok=True)
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
    dest_override: str | None = None,
    fileset: FileSetPlan | None = None,
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
        dest_override: Absolute final path for the MAIN file. The update
            flow (``POST /api/models/{id}/update``) pins this to the
            registry row's existing ``path`` so a re-pull replaces the
            installed bytes in place — never relocating an older
            flat-layout model into the capability-grouped tree and
            orphaning the previous file. When set, ``capability`` /
            ``comfyui_subdir`` routing is bypassed for the main file.
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

    job.state = "running"
    job.started_at = time.time()
    # Per-file manifest — main model first, optional mmproj sidecar second.
    job.files = [PullFile(hf_filename=hf_file, kind="model")]
    if mmproj_file:
        job.files.append(PullFile(hf_filename=mmproj_file, kind="mmproj"))
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
        # ── Main model file ────────────────────────────────────────────────
        main_rec = job.files[0]
        if dest_override:
            final = Path(dest_override)
        else:
            final = _final_path_for_entry(job.model_id, hf_file, comfyui_subdir, capability)
        digest = await _download_one(
            job,
            main_rec,
            client=client,
            hf_repo=hf_repo,
            base_headers=base_headers,
            final=final,
            base_done=0,
            base_total=0,
        )
        size_bytes = main_rec.bytes_done

        # ── Optional mmproj sidecar (WS-11) ────────────────────────────────
        # Same directory as the main model, so the association survives the
        # discover scan's same-directory heuristic AND is set directly below.
        mmproj_final: Path | None = None
        if len(job.files) > 1:
            mm_rec = job.files[1]
            mmproj_final = final.parent / Path(mm_rec.hf_filename).name
            await _download_one(
                job,
                mm_rec,
                client=client,
                hf_repo=hf_repo,
                base_headers=base_headers,
                final=mmproj_final,
                base_done=main_rec.bytes_done,
                base_total=main_rec.bytes_total,
            )

        # Register / update the registry entry (mmproj set directly — the
        # row is vision-ready without waiting for a directory scan).
        _register_pulled(
            registry,
            model_id=job.model_id,
            path=str(final),
            size_bytes=size_bytes,
            sha256=digest,
            hf_repo=hf_repo,
            hf_filename=hf_file,
            capability=capability,
            comfyui_subdir=comfyui_subdir,
            mmproj=str(mmproj_final) if mmproj_final is not None else None,
        )

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
    """
    # ``pulled_at`` provenance lets the dashboard show when the installed
    # bytes were fetched — and thereby how stale they are vs an available
    # HF update (registry/update_check.py compares the shas).
    fresh_meta = {"sha256": sha256, "pulled_at": int(time.time())}
    updates: dict[str, Any] = {
        "path": path,
        "size_bytes": size_bytes,
        "hf_repo": hf_repo,
        "hf_filename": hf_filename,
        "metadata": fresh_meta,
    }
    if mmproj is not None:
        updates["mmproj"] = mmproj
    try:
        existing = registry.get(model_id)
    except ModelNotFound:
        is_comfyui = bool(comfyui_subdir)
        cap = (capability or "").strip()
        caps = [cap] if cap else (["image"] if is_comfyui else ["chat"])
        backends = ["comfyui"] if is_comfyui else []
        registry.add(
            Model(
                id=model_id,
                name=model_id,
                path=path,
                size_bytes=size_bytes,
                hf_repo=hf_repo,
                hf_filename=hf_filename,
                capabilities=caps,
                backends=backends,
                mmproj=mmproj,
                metadata=dict(fresh_meta),
            )
        )
        return
    # Preserve license / capabilities / tags from any pre-pull register
    # call (e.g. pick-default seeded the entry from the curated catalogue
    # before kicking off the pull).
    merged_meta = dict(existing.metadata)
    merged_meta.update(fresh_meta)
    updates["metadata"] = merged_meta
    registry.update(model_id, updates)


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
            dest.parent.mkdir(parents=True, exist_ok=True)
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
    entry_sha = next((e.lfs_sha256 for e, dest in installed if dest == entry_dest), None)
    total_size = sum(dest.stat().st_size for _, dest in installed if dest.exists())
    fresh_meta: dict[str, Any] = {"pulled_at": int(time.time())}
    if entry_sha:
        fresh_meta["sha256"] = entry_sha
    updates: dict[str, Any] = {
        "path": str(entry_dest),
        "size_bytes": total_size,
        "hf_repo": fileset.repo,
        "hf_filename": Path(fileset.entry_rel).name,
    }
    if mmproj_dest is not None:
        updates["mmproj"] = str(mmproj_dest)

    try:
        existing = registry.get(model_id)
    except ModelNotFound:
        registry.add(
            Model(
                id=model_id,
                name=model_id,
                path=str(entry_dest),
                size_bytes=total_size,
                hf_repo=fileset.repo,
                hf_filename=Path(fileset.entry_rel).name,
                capabilities=["chat"],
                mmproj=str(mmproj_dest) if mmproj_dest else None,
                metadata=dict(fresh_meta),
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
        # `revision`/`preferred_runner` are §7.1 reserved columns with no
        # Model field yet (ML-1's DDL comment) — written directly until
        # that lane lands a pydantic field for them.
        conn.execute(
            "UPDATE model SET revision = ?, preferred_runner = COALESCE(preferred_runner, ?) WHERE id = ?",
            (fileset.revision, fileset.runner_hint, model_id),
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
        reset_flm_catalog_cache,
    )

    job.state = "running"
    job.started_at = time.time()
    job._signal()

    argv, host_models_dir = flm_pull_command(tag)

    # flm hardcodes ~/.config/flm/models and has no dir flag, so a host pull
    # writes to that default path — NOT the (possibly relocated) store the
    # progress poller + serving container use. Point flm's default at the store
    # via a symlink (host analog of the container bind-mount) before we spawn,
    # so weights land in the store, progress tracks the real bytes, and serving
    # finds them. Off-loaded to a thread: the one-time legacy-content migration
    # can copy multi-GB weights and must not block the event loop.
    await asyncio.to_thread(ensure_host_flm_store_link)

    # Resolve the install path + advertised total upfront so progress
    # reporting is monotonic. _flm_install_path reads the same cached
    # catalog flm_served_models uses; both fall back gracefully when
    # the probe failed (host without docker / image not present).
    #
    # These two probes (``flm list``) can transiently return an EMPTY catalog
    # right at pull start — observed live: the dir grew steadily on disk while
    # the job reported ``0/0`` the whole download because ``target_dir`` was
    # ``None`` here and never retried. So they are NOT resolved once-and-for-
    # all: :func:`_resolve_target_dir` / :func:`_resolve_advertised_total` below
    # re-attempt each tick until they succeed, then progress tracks growth.
    target_dir = _flm_install_path(host_models_dir, tag)
    advertised_total = 0
    for entry in flm_served_models():
        if entry["tag"] == tag:
            advertised_total = int(entry.get("size_bytes") or 0)
            break
    baseline_size = _dir_size(target_dir) if target_dir else 0
    if advertised_total > baseline_size:
        job.bytes_total = advertised_total
        job._signal()

    # uvloop (hal0-api's event loop) rejects the user/group Popen kwargs, so
    # the drop to the hal0 user rides the argv (setpriv/runuser) instead.
    argv, spawn_kwargs = flm_host_async_spawn(argv)

    proc: asyncio.subprocess.Process | None = None
    try:
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
            resolved = _flm_install_path(host_models_dir, tag)
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

        def _tick_progress() -> None:
            """Refresh bytes_downloaded from on-disk dir size if it grew."""
            nonlocal last_emit
            _resolve_target_dir()
            _resolve_advertised_total()
            if not target_dir:
                return
            now = time.monotonic()
            if (now - last_emit) < _SSE_MIN_INTERVAL_S:
                return
            current = _dir_size(target_dir) - baseline_size
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
                _tick_progress()
                continue
            if not raw:
                break
            # Reading the line is enough — we don't parse it for byte
            # accounting any more, but the readline() drains the pipe so
            # the docker process doesn't block on a full stdout buffer.
            _tick_progress()

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
        # bare host dir so a missing entry doesn't fail the job.
        final_path = _flm_install_path(host_models_dir, tag) or host_models_dir
        size_bytes = _dir_size(final_path)
        if job.bytes_total <= 0 and size_bytes > 0:
            job.bytes_total = size_bytes
        if job.bytes_downloaded < size_bytes:
            job.bytes_downloaded = size_bytes
        job.path = str(final_path)

        # Register an FLM tag so the registry surfaces it for downstream
        # consumers (catalog, slot model resolution). hf_repo/filename
        # stay empty — FLM tags route through the toolbox, not HF directly.
        _register_flm_pulled(
            registry,
            tag=tag,
            path=str(final_path),
            size_bytes=size_bytes,
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


def _flm_install_path(host_models_dir: str, tag: str) -> str | None:
    """Look up the on-disk subdir FLM uses for ``tag``, or None if unknown.

    Walks the toolbox image's bundled ``model_list.json`` schema (family
    → variants → name=HF-repo). We probe it via ``flm_served_models``
    indirectly: the cached entry exposes a ``family`` field but not the
    HF name, so we read FLM's own JSON by shelling ``flm list -j`` and
    matching tag → ``name``. The probe is cached, so this lookup is
    O(1) after the first call.
    """
    from hal0.providers.flm import _probe_flm_catalog

    models = _probe_flm_catalog()
    if not models:
        return None
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
) -> None:
    """Upsert a registry entry for an FLM-pulled model.

    FLM tags don't carry HF coords from a hal0 perspective (the toolbox
    image's ``flm pull`` resolves them itself), so ``hf_repo`` and
    ``hf_filename`` stay empty. ``metadata.runtime = "flm"`` flags the
    entry so other code (slot pick, model resolution) can route it to
    the FLM provider without re-deriving from the id.
    """
    updates: dict[str, Any] = {
        "path": path,
        "size_bytes": size_bytes,
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
                capabilities=["chat"],
                backends=["npu"],
                metadata={"runtime": "flm"},
            )
        )
        return
    merged_meta = dict(existing.metadata)
    merged_meta["runtime"] = "flm"
    updates["metadata"] = merged_meta
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
