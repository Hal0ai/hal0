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
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from hal0.config import paths
from hal0.errors import Hal0Error
from hal0.registry.model import Model
from hal0.registry.store import ModelNotFound, ModelRegistry

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


# ── Job record ───────────────────────────────────────────────────────────────


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
    # Async signalling — set every time the background task makes
    # progress. SSE waits on this rather than polling.
    progress_event: asyncio.Event = field(default_factory=asyncio.Event)

    def as_dict(self) -> dict[str, Any]:
        """Serialisable snapshot for /pull/status and SSE frames."""
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

    Reads ``[models].store`` (the v0.3 single-source-of-truth setting)
    from hal0.toml on each call so a Settings save takes effect without
    an API restart. Falls back to the legacy ``[models].pull_root`` when
    ``store`` is empty (PR-#313 compatibility), and to
    :func:`paths.models_dir` if config load fails — keeps pulls working
    during bootstrap before the config exists.
    """
    try:
        from hal0.config.loader import load_hal0_config

        cfg = load_hal0_config()
        return Path(cfg.models.effective_store())
    except Exception:
        return paths.models_dir()


def _final_path(model_id: str, filename: str) -> Path:
    """Resolve the final on-disk path: <pull_root>/<id>/<file>."""
    return _pull_root() / _sanitise_id(model_id) / filename


def _comfyui_models_dir(subdir: str) -> Path:
    """ComfyUI checkpoints/loras/vae directory under the persistent base dir.

    Hal0 ComfyUI slots bind-mount ``/var/lib/hal0/comfyui`` into the
    container at the same path, with ``models/<subdir>/<file>`` being
    the layout ComfyUI's own ``CheckpointLoaderSimple`` /
    ``LoraLoader`` / etc. expect when ``--base-directory`` points at
    that root.

    The subdir name is sanitised the same way model ids are so a curated
    entry can't escape the comfyui tree by setting
    ``comfyui_subdir="../../etc/passwd"``.
    """
    cleaned = _SANITISE_RE.sub("-", subdir).strip("-.") or "checkpoints"
    return paths.var_lib() / "comfyui" / "models" / cleaned


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


async def run_pull(
    job: PullJob,
    *,
    hf_repo: str,
    hf_file: str,
    registry: ModelRegistry,
    hf_token: str | None = None,
    client: httpx.AsyncClient | None = None,
    comfyui_subdir: str | None = None,
    capability: str | None = None,
) -> None:
    """Background-task body: stream the file, hash it, install it, register it.

    Mutates ``job`` in place and pulses ``job._signal()`` on every chunk
    boundary or 500ms tick (whichever is rarer) so SSE consumers see
    progress without polling.

    Cancellation: callers set ``job.cancel_requested = True``. The next
    chunk read checks the flag, deletes the partial, transitions to
    ``cancelled``, and returns.

    Args:
        comfyui_subdir: When set (e.g. ``"checkpoints"``), the file lands
            under ``/var/lib/hal0/comfyui/models/<subdir>/<filename>``
            instead of the default ``/var/lib/hal0/models/<id>/<filename>``.
            Curated image-gen entries set this so ComfyUI's own model
            loaders find the file at the path their workflow nodes expect.
    """
    job.state = "running"
    job.started_at = time.time()
    job._signal()

    url = hf_download_url(hf_repo, hf_file)
    headers: dict[str, str] = {"User-Agent": "hal0/installer"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    tmp_dir = _tmp_dir()
    tmp_dir.mkdir(parents=True, exist_ok=True)
    rand_tag = secrets.token_hex(4)
    tmp_path = tmp_dir / f"{_sanitise_id(job.model_id)}.{rand_tag}.part"

    hasher = hashlib.sha256()
    last_emit = time.monotonic()
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(_CONNECT_TIMEOUT_S, read=_READ_TIMEOUT_S),
            follow_redirects=True,
        )

    try:
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 401 or resp.status_code == 403:
                raise PullError(
                    f"hugging face returned {resp.status_code} for {hf_repo}/{hf_file}"
                    " (gated repo? set HF_TOKEN)",
                    details={
                        "status": resp.status_code,
                        "repo": hf_repo,
                        "file": hf_file,
                    },
                )
            if resp.status_code == 404:
                raise PullError(
                    f"hugging face has no file {hf_file!r} in {hf_repo!r} at main",
                    details={"repo": hf_repo, "file": hf_file},
                )
            if resp.status_code >= 400:
                raise PullError(
                    f"hugging face returned HTTP {resp.status_code} for {url}",
                    details={"status": resp.status_code, "url": url},
                )

            content_length = resp.headers.get("content-length")
            if content_length:
                try:
                    job.bytes_total = int(content_length)
                except ValueError:
                    job.bytes_total = 0
            job._signal()

            with open(tmp_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_BYTES):
                    if job.cancel_requested:
                        # Caller asked for cancellation — drop the partial
                        # and exit cleanly.
                        f.close()
                        with contextlib.suppress(OSError):
                            tmp_path.unlink(missing_ok=True)
                        job.state = "cancelled"
                        job.finished_at = time.time()
                        job._signal()
                        return
                    if not chunk:
                        continue
                    f.write(chunk)
                    hasher.update(chunk)
                    job.bytes_downloaded += len(chunk)
                    now = time.monotonic()
                    if (now - last_emit) >= _SSE_MIN_INTERVAL_S:
                        last_emit = now
                        job._signal()

        # Download complete — atomic install.
        final = _final_path_for_entry(job.model_id, hf_file, comfyui_subdir, capability)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_path, final)
        tmp_path = final  # so the cleanup `finally` doesn't unlink the installed file
        size_bytes = final.stat().st_size
        digest = hasher.hexdigest()

        # Register / update the registry entry.
        _register_pulled(
            registry,
            model_id=job.model_id,
            path=str(final),
            size_bytes=size_bytes,
            sha256=digest,
            hf_repo=hf_repo,
            hf_filename=hf_file,
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
        job.bytes_downloaded = size_bytes
        if job.bytes_total == 0:
            job.bytes_total = size_bytes
        job.state = "completed"
        job.finished_at = time.time()
        job._signal()
    except asyncio.CancelledError:
        # Task itself was cancelled by the event loop — treat as cancellation.
        with contextlib.suppress(OSError):
            if tmp_path and tmp_path.exists() and tmp_path.suffix == ".part":
                tmp_path.unlink()
        job.state = "cancelled"
        job.finished_at = time.time()
        job._signal()
        raise
    except Hal0Error as exc:
        with contextlib.suppress(OSError):
            if tmp_path and tmp_path.exists() and tmp_path.suffix == ".part":
                tmp_path.unlink()
        job.state = "failed"
        job.error = exc.message
        job.error_code = exc.code
        job.finished_at = time.time()
        job._signal()
        log.warning("model.pull_failed", extra={"model_id": job.model_id, "error": exc.message})
    except Exception as exc:
        with contextlib.suppress(OSError):
            if tmp_path and tmp_path.exists() and tmp_path.suffix == ".part":
                tmp_path.unlink()
        job.state = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.error_code = "model.pull_failed"
        job.finished_at = time.time()
        job._signal()
        log.exception("model.pull_unexpected_error", extra={"model_id": job.model_id})
    finally:
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
) -> None:
    """Upsert the registry entry after a successful pull."""
    updates: dict[str, Any] = {
        "path": path,
        "size_bytes": size_bytes,
        "hf_repo": hf_repo,
        "hf_filename": hf_filename,
        "metadata": {"sha256": sha256},
    }
    try:
        existing = registry.get(model_id)
    except ModelNotFound:
        registry.add(
            Model(
                id=model_id,
                name=model_id,
                path=path,
                size_bytes=size_bytes,
                hf_repo=hf_repo,
                hf_filename=hf_filename,
                capabilities=["chat"],
                metadata={"sha256": sha256},
            )
        )
        return
    # Preserve license / capabilities / tags from any pre-pull register
    # call (e.g. pick-default seeded the entry from the curated catalogue
    # before kicking off the pull).
    merged_meta = dict(existing.metadata)
    merged_meta["sha256"] = sha256
    updates["metadata"] = merged_meta
    registry.update(model_id, updates)


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
        flm_host_spawn_kwargs,
        flm_pull_command,
        flm_served_models,
        reset_flm_catalog_cache,
    )

    job.state = "running"
    job.started_at = time.time()
    job._signal()

    argv, host_models_dir = flm_pull_command(tag)

    # Resolve the install path + advertised total upfront so progress
    # reporting is monotonic. _flm_install_path reads the same cached
    # catalog flm_served_models uses; both fall back gracefully when
    # the probe failed (host without docker / image not present).
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

    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **flm_host_spawn_kwargs(),
        )
        assert proc.stdout is not None
        last_emit = time.monotonic()

        def _tick_progress() -> None:
            """Refresh bytes_downloaded from on-disk dir size if it grew."""
            nonlocal last_emit
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
        # the new installed=true flag without a process restart.
        reset_flm_catalog_cache()

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
    "PullError",
    "PullInvalidSource",
    "PullJob",
    "PullJobNotFound",
    "_sanitise_id",
    "get_job",
    "hf_download_url",
    "make_job",
    "run_flm_pull",
    "run_pull",
]
