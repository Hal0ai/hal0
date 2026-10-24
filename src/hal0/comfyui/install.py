"""Installs and removes image models from the catalog (ADR-0036).

An install is a :class:`~hal0.registry.pull.PullJob` run over the model's missing
components, each streamed by the registry downloader
(:func:`~hal0.registry.pull._download_one`): resume from a partial, the sha256 check
against the hash Hugging Face advertises, an atomic move into place, and
cooperative cancel all come from there. This module only decides *what* to fetch
(the components :func:`~hal0.comfyui.catalog.install_state` reports missing), *where*
it lands (``Component.dest``), and what to write once it has landed (the workflow).

Bytes are staged in the registry's tmp dir and renamed into the models tree, so
both directories need room: :func:`preflight_disk` measures each.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import httpx

from hal0.comfyui import paths
from hal0.comfyui.catalog import (
    CATALOG,
    ImageModel,
    get_model,
    install_state,
    models_using,
    render_template,
)
from hal0.errors import Hal0Error
from hal0.install.perms import ensure_shared_dir
from hal0.registry.pull import (
    _CONNECT_TIMEOUT_S,
    _READ_TIMEOUT_S,
    PullFile,
    PullJob,
    _download_one,
    _PullCancelled,
    _tmp_dir,
    make_job,
)
from hal0.upstreams.huggingface import hf_token_from_env

log = logging.getLogger(__name__)

# Code for a failure that is not already a typed Hal0Error (a dropped connection, a full disk).
_INSTALL_FAILED = "comfyui.install_failed"


class ImageModelNotFound(Hal0Error):
    """No catalog entry has this id."""

    code = "comfyui.model_unknown"
    status = 404


class InstallInsufficientDisk(Hal0Error):
    """A filesystem the install writes to cannot hold what is still to download."""

    code = "comfyui.insufficient_disk"
    status = 507  # Insufficient Storage


# ── disk preflight ──────────────────────────────────────────────────────────


def _existing_ancestor(path: Path) -> Path:
    """``path`` itself, or its nearest parent that exists (a fresh store has no models tree yet)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe


def _device_of(path: Path) -> int:
    """The id of the filesystem ``path`` lives on."""
    return os.stat(path).st_dev


def preflight_disk(model: ImageModel) -> None:
    """Raise :class:`InstallInsufficientDisk` when what is missing cannot fit.

    The models tree and the registry staging dir are each measured against the whole
    download; when both sit on one device that is a single check. ``details["path"]``
    is the directory that came up short. A filesystem that cannot be measured is
    skipped, as the downloader's own per-file check does.
    """
    state = install_state(model)
    required = int(state.missing_gb * 1024**3)
    if required <= 0:
        return
    measured: set[int] = set()
    for target in (paths.models_root(), _tmp_dir()):
        probe = _existing_ancestor(target)
        try:
            device = _device_of(probe)
            if device in measured:
                continue
            measured.add(device)
            free = shutil.disk_usage(probe).free
        except OSError:
            continue
        if required > free:
            raise InstallInsufficientDisk(
                f"insufficient disk for {model.label}: need about {state.missing_gb:g} GB, "
                f"{free / 1024**3:.1f} GB free at {probe}",
                details={"required_bytes": required, "free_bytes": free, "path": str(probe)},
            )


# ── install ─────────────────────────────────────────────────────────────────


def _new_client() -> httpx.AsyncClient:
    """A client set up like the pull engine's own: bounded connect, unbounded body, redirects on."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(_CONNECT_TIMEOUT_S, read=_READ_TIMEOUT_S),
        follow_redirects=True,
    )


def _finish(
    job: PullJob, state: str, *, error: str | None = None, error_code: str | None = None
) -> None:
    job.state = state
    job.error = error
    job.error_code = error_code
    job.finished_at = time.time()
    job._signal()


def _write_workflow(model: ImageModel) -> Path:
    """Write the model's rendered workflow, without ``_meta``, where the launcher lists it.

    ``_meta`` describes the template to hal0 (parameters, defaults); ComfyUI would read
    it as a node. The file is replaced atomically.
    """
    graph = {key: value for key, value in render_template(model).items() if key != "_meta"}
    dest = paths.workflows_dir() / model.workflow_filename
    ensure_shared_dir(dest.parent)
    tmp = dest.with_name(f"{dest.name}.tmp")
    try:
        tmp.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)  # already gone after a successful replace
    return dest


async def _download_missing(
    job: PullJob, model: ImageModel, *, client: httpx.AsyncClient, hf_token: str | None
) -> None:
    """Fetch every component of ``model`` that is not on disk; seed ``job.files`` first."""
    missing = install_state(model).missing
    job.files = [PullFile(hf_filename=c.hf_path, kind=c.kind.value) for c in missing]
    job._signal()

    headers = {"User-Agent": "hal0/installer"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    base_done = 0
    base_total = 0
    for component, rec in zip(missing, job.files, strict=True):
        if job.cancel_requested:
            raise _PullCancelled()
        dest = component.dest
        if dest.is_file():
            # Another install of a model sharing this file landed it since we looked.
            size = dest.stat().st_size
            rec.dest = str(dest)
            rec.bytes_done = rec.bytes_total = size
        else:
            ensure_shared_dir(dest.parent)
            # The downloader keys its staging file by (job.model_id, rec.hf_filename);
            # hf_path is unique within a model, so two components never share a partial.
            await _download_one(
                job,
                rec,
                client=client,
                hf_repo=component.hf_repo,
                base_headers=headers,
                final=dest,
                base_done=base_done,
                base_total=base_total,
            )
        base_done += rec.bytes_done
        base_total = max(base_total + rec.bytes_total, base_done)
        job.bytes_downloaded = base_done
        job.bytes_total = base_total


async def run_install(
    job: PullJob,
    model: ImageModel,
    *,
    hf_token: str | None,
    client: httpx.AsyncClient | None = None,
    on_complete: Callable[[ImageModel], Awaitable[None]] | None = None,
) -> None:
    """Background-task body: download what ``model`` is missing, then provision its workflow.

    Drives ``job`` through ``running`` to ``completed``, ``failed`` or ``cancelled`` and
    pulses ``job._signal()`` on each change, like the registry pull. Callers cancel by
    setting ``job.cancel_requested``; the next chunk read notices. Components that
    already landed stay on disk after a failure or a cancel, so a retry carries on from
    there.

    Never raises: a typed error becomes ``failed`` with its own code, anything else
    ``failed`` with ``comfyui.install_failed``. Only the task's own cancellation
    propagates, after the job is marked ``cancelled``. ``on_complete`` runs after the
    job is ``completed``; if it raises, the install stays completed and the error is
    logged. A client is created and closed here when none is passed.
    """
    job.state = "running"
    job.started_at = time.time()
    job._signal()

    owns_client = client is None
    if client is None:
        client = _new_client()
    try:
        await _download_missing(job, model, client=client, hf_token=hf_token)
        if job.cancel_requested:
            raise _PullCancelled()  # cancelled while the last file landed
        _write_workflow(model)
        _finish(job, "completed")
    except _PullCancelled:
        _finish(job, "cancelled")
    except asyncio.CancelledError:
        _finish(job, "cancelled")
        raise
    except Hal0Error as exc:
        _finish(job, "failed", error=exc.message, error_code=exc.code)
        log.warning(
            "comfyui.install_failed", extra={"model_id": job.model_id, "error": exc.message}
        )
    except Exception as exc:
        _finish(job, "failed", error=f"{type(exc).__name__}: {exc}", error_code=_INSTALL_FAILED)
        log.exception("comfyui.install_unexpected_error", extra={"model_id": job.model_id})
    finally:
        if owns_client:
            await client.aclose()

    if job.state == "completed" and on_complete is not None:
        try:
            await on_complete(model)
        except Exception:
            log.exception("comfyui.install_on_complete_failed", extra={"model_id": job.model_id})


# ── remove ──────────────────────────────────────────────────────────────────


def remove_model(model: ImageModel) -> list[str]:
    """Delete ``model``'s weight files and return their paths relative to the models root.

    A file another catalog model loads stays while that model is complete. A model that is
    only partly installed does not hold files back: it cannot run without its missing ones.
    Files already gone are ignored and not reported. The provisioned workflow is left for
    the caller.
    """
    root = paths.models_root()
    removed: list[str] = []
    for component in model.components:
        neighbours = (CATALOG[other_id] for other_id in models_using(component))
        if any(n.id != model.id and install_state(n).state == "complete" for n in neighbours):
            continue
        try:
            component.dest.unlink()
        except FileNotFoundError:
            continue
        removed.append(component.dest.relative_to(root).as_posix())
    return removed


# ── setup path ──────────────────────────────────────────────────────────────


async def _install_each(
    model_ids: Sequence[str], hf_token: str | None, say: Callable[[str], None]
) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    async with _new_client() as client:
        for model_id in model_ids:
            model = get_model(model_id)
            if model is None:
                say(f"{model_id}: unknown image model")
                results.append((model_id, "failed"))
                continue
            try:
                preflight_disk(model)
            except InstallInsufficientDisk as exc:
                say(f"{model_id}: {exc.message}")
                results.append((model_id, "failed"))
                continue
            say(f"{model_id}: installing {model.label}")
            job = make_job(model_id)
            await run_install(job, model, hf_token=hf_token, client=client)
            say(f"{model_id}: {job.state}" + (f" ({job.error})" if job.error else ""))
            results.append((model_id, job.state))
    return results


def install_models_blocking(
    model_ids: Sequence[str],
    *,
    hf_token: str | None,
    on_line: Callable[[str], None] | None = None,
) -> list[tuple[str, str]]:
    """Install ``model_ids`` one after another and return ``(model_id, final_state)`` for each.

    The setup CLI's path: it owns an event loop (``asyncio.run``), so it cannot be called
    from async code. An unknown id or a model that will not fit on disk is reported through
    ``on_line`` and comes back ``failed``; the rest still run.
    """
    return asyncio.run(_install_each(model_ids, hf_token, on_line or (lambda _line: None)))


__all__ = [
    "ImageModelNotFound",
    "InstallInsufficientDisk",
    "hf_token_from_env",
    "install_models_blocking",
    "preflight_disk",
    "remove_model",
    "run_install",
]
