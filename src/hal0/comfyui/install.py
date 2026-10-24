"""Installs and removes image models from the catalog (ADR-0037).

An install is a :class:`~hal0.registry.pull.PullJob` run over the model's missing
components, each streamed by the registry downloader
(:func:`~hal0.registry.pull._download_one`): resume from a partial, the sha256 check
against the hash Hugging Face advertises, an atomic move into place, and
cooperative cancel all come from there. This module only decides *what* to fetch
(the components :func:`~hal0.comfyui.catalog.install_state` reports missing), *where*
it lands (``Component.dest``), and what to write once it has landed (the workflow).

Bytes are staged in the registry's tmp dir and renamed into the models tree. A rename
cannot cross filesystems, so a layout where the two differ is refused before anything
streams (:func:`require_one_filesystem`); on one filesystem a single free-space check
(:func:`preflight_disk`) covers both. A destination directory hal0 cannot write is
refused the same way (:func:`require_writable_destinations`), since otherwise the
failure shows up only at the final rename, after the whole file has been fetched.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

from hal0.comfyui import paths
from hal0.comfyui.catalog import (
    CATALOG,
    Component,
    ImageModel,
    component_path,
    get_model,
    install_state,
    models_using,
    render_template,
)
from hal0.config.paths import var_lib
from hal0.errors import Hal0Error
from hal0.install.perms import ensure_shared_dir
from hal0.registry.pull import (
    _CONNECT_TIMEOUT_S,
    _READ_TIMEOUT_S,
    PullFile,
    PullJob,
    _download_one,
    _others_total,
    _PullCancelled,
    _staging_paths,
    _tmp_dir,
    make_job,
)

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


class InstallStagingCrossDevice(Hal0Error):
    """The registry staging dir and the models tree are on different filesystems."""

    code = "comfyui.staging_cross_device"
    status = 409


class InstallDirNotWritable(Hal0Error):
    """A directory an install must write into is not writable by the hal0 user."""

    code = "comfyui.models_dir_not_writable"
    status = 409


class RemoveFailed(Hal0Error):
    """A file of a model being removed could not be deleted; ``details`` says how far it got."""

    code = "comfyui.remove_failed"
    status = 409


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


def _staging_key(model: ImageModel) -> str:
    """The model half of the staging key the installer and the preflight both use.

    The curated registry still lists some of the same ids (``sdxl-turbo``,
    ``sd-1.5-pruned-emaonly``), and a registry pull stages under the bare id, so two
    downloaders with that id and filename would share one ``.part`` and either could
    discard or replace the other's. Image installs get their own namespace instead;
    job ids and the API keep the catalog id. ``_sanitise_id`` leaves the prefix intact.
    """
    return f"comfyui-{model.id}"


def _staged_bytes(model: ImageModel, component: Component) -> int:
    """Bytes of ``component`` an interrupted install of ``model`` left staged and can resume.

    The downloader keeps ``<staging>/<key>.part`` and its ``.part.json`` sidecar after a
    transport error or a shutdown, keyed by :func:`_staging_key` and the file's name in the
    repo (``Component.hf_path``, the ``rec.hf_filename`` :func:`_download_missing` passes);
    on the next run it continues from them. A ``.part`` without its sidecar is not resumable,
    so the downloader discards it and it counts for nothing. Not re-hashed here: the
    downloader validates the prefix when it resumes. Capped at the catalog's estimate for
    the file, so a partial larger than that cannot cover another component's download.
    """
    part, sidecar = _staging_paths(_staging_key(model), component.hf_path)
    try:
        if not sidecar.is_file():
            return 0
        staged = part.stat().st_size
    except OSError:  # no part, or it cannot be inspected: not resumable as far as we can tell
        return 0
    return min(staged, round(component.approx_gb * 1e9))


def preflight_disk(model: ImageModel) -> None:
    """Raise :class:`InstallInsufficientDisk` when what is still to download cannot fit.

    Measures the filesystem under the models tree (its nearest existing ancestor, since a
    fresh store has no tree yet) against what is still to fetch: for each missing component
    its catalog estimate in bytes (``round(approx_gb * 1e9)``, the figure the job's totals
    start from), less the bytes already staged and resumable (:func:`_staged_bytes`, never
    more than that estimate), which the downloader does not fetch again. The staging dir is
    on the same filesystem, which :func:`run_install` enforces, so that one check covers
    both. ``details["path"]`` is the directory measured, ``details["required_bytes"]`` what
    is still to fetch and ``details["staged_bytes"]`` what was taken off. A filesystem that
    cannot be measured is skipped, as the downloader's own per-file check does.
    """
    required = staged = 0
    for component in install_state(model).missing:
        resumable = _staged_bytes(model, component)  # already capped at the estimate
        required += round(component.approx_gb * 1e9) - resumable
        staged += resumable
    if required <= 0:
        return
    probe = _existing_ancestor(paths.models_root())
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return
    if required > free:
        need = f"{round(required / 1e9, 2):g} GB"
        if staged:
            need += f" more ({round(staged / 1e9, 2):g} GB already staged)"
        raise InstallInsufficientDisk(
            f"insufficient disk for {model.label}: need about {need}, "
            f"{free / 1e9:.1f} GB free at {probe}",
            details={
                "required_bytes": required,
                "free_bytes": free,
                "staged_bytes": staged,
                "path": str(probe),
            },
        )


def require_one_filesystem() -> None:
    """Raise :class:`InstallStagingCrossDevice` when staging and models tree are on two devices.

    The downloader stages a file in the registry tmp dir and ``os.replace``s it into the
    models tree; across filesystems that fails with ``EXDEV`` after the whole file has been
    fetched, and the partial is discarded, so every retry would fetch it again. A path that
    cannot be inspected is not treated as a mismatch: the rename is the final arbiter.

    The install route calls this next to :func:`preflight_disk`, so a bad layout is a
    synchronous 409 before any job exists; :func:`run_install` calls it again as the backstop.
    """
    models, staging = paths.models_root(), _tmp_dir()
    try:
        same = _device_of(_existing_ancestor(models)) == _device_of(_existing_ancestor(staging))
    except OSError:
        return
    if not same:
        raise InstallStagingCrossDevice(
            f"the ComfyUI models tree ({models}) and the download staging dir ({staging}) "
            "are on different filesystems, so a finished download cannot be moved into place. "
            "Keep the ComfyUI data root on the same filesystem as the model store "
            "(unset HAL0_COMFYUI_DATA_ROOT or move it).",
            details={"models_root": str(models), "staging_dir": str(staging)},
        )


def require_writable_destinations(model: ImageModel) -> None:
    """Raise :class:`InstallDirNotWritable` when a missing component has nowhere to land.

    For each component still to download, its destination directory, or the nearest
    existing ancestor when it does not exist yet (the installer creates it), must be
    writable by this process. Without this a read-only directory is found only at the
    final rename, after the whole file has streamed and been thrown away, and every retry
    downloads it again. ``details["path"]`` is the first directory that is not writable.

    The install route calls this next to :func:`require_one_filesystem`;
    :func:`run_install` calls it again as the backstop.
    """
    root = paths.models_root()
    checked: set[Path] = set()
    for component in install_state(model, root).missing:
        probe = _existing_ancestor(component_path(component, root).parent)
        if probe in checked:
            continue
        checked.add(probe)
        if not os.access(probe, os.W_OK | os.X_OK):
            raise InstallDirNotWritable(
                f"cannot install {model.label}: {probe} is not writable by the hal0 user; "
                "make it writable by the hal0 user or group "
                "(e.g. chgrp <hal0 group> + chmod 2775), then retry",
                details={"path": str(probe)},
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
    it as a node. The file is replaced atomically. Each write stages in a file of its own,
    since :func:`ensure_workflows` and an install can write the same workflow at once and a
    shared staging name would let one rename the other's file away.
    """
    graph = {key: value for key, value in render_template(model).items() if key != "_meta"}
    dest = paths.workflows_dir() / model.workflow_filename
    ensure_shared_dir(dest.parent)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(graph, indent=2) + "\n")
        tmp.chmod(0o644)  # mkstemp creates 0600; the ComfyUI container reads this file
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)  # already gone after a successful replace
    return dest


# ── operator removals ───────────────────────────────────────────────────────
#
# Removing a model whose weights other complete models hold deletes only its workflow, and the
# model still reads ``complete``. :func:`ensure_workflows` would write the workflow straight
# back on the next API boot or install, so an explicit removal leaves an empty marker file
# and an explicit install takes it away.


def _removed_marker(model_id: str) -> Path:
    """The marker recording that the operator removed ``model_id`` (hal0's state dir)."""
    return var_lib() / "state" / "comfyui-removed" / model_id


def mark_removed(model_id: str) -> None:
    """Record that the operator removed ``model_id``; logs and carries on if it cannot."""
    marker = _removed_marker(model_id)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError as exc:
        log.warning(
            "comfyui.removal_marker_failed", extra={"model_id": model_id, "error": str(exc)}
        )


def clear_removed(model_id: str) -> None:
    """Forget a removal: an explicit install of ``model_id`` is the operator changing their mind."""
    try:
        _removed_marker(model_id).unlink(missing_ok=True)
    except OSError as exc:
        log.warning(
            "comfyui.removal_marker_failed", extra={"model_id": model_id, "error": str(exc)}
        )


def is_removed(model_id: str) -> bool:
    """True while ``model_id``'s removal stands; an unreadable marker reads as "not removed"."""
    try:
        return _removed_marker(model_id).is_file()
    except OSError:
        return False


def ensure_workflows() -> list[str]:
    """Write the workflow of every complete catalog model that has none; return the filenames.

    An install writes its model's workflow, but a model whose weights were put on disk by
    hand reports ``complete`` with no workflow, and the install route then refuses it, so
    nothing else would ever provision it. This covers those. A workflow file that exists is
    never replaced, whatever it holds, and a model the operator removed (:func:`mark_removed`)
    is left without one until an install clears the removal. Best-effort: a model whose state
    cannot be read, whose template will not render or whose workflow cannot be written (a
    directory the hal0 user cannot write) is logged and skipped, and the models after it are
    still done.
    """
    root = paths.models_root()
    written: list[str] = []
    for model in CATALOG.values():
        try:
            if is_removed(model.id):
                continue
            if install_state(model, root).state != "complete":
                continue
            if (paths.workflows_dir() / model.workflow_filename).is_file():
                continue
            _write_workflow(model)
        except Exception as exc:
            log.warning(
                "comfyui.ensure_workflow_failed", extra={"model_id": model.id, "error": str(exc)}
            )
            continue
        written.append(model.workflow_filename)
    return written


async def _download_missing(
    job: PullJob,
    missing: tuple[Component, ...],
    *,
    client: httpx.AsyncClient,
    hf_token: str | None,
    staging_key: str,
) -> None:
    """Fetch each component in ``missing``, seeding ``job.files`` and the job totals first.

    Every file starts with its catalog estimate as ``bytes_total``, so the job's total spans
    the whole install from the first byte and progress cannot reach 100% at the end of one
    component. A file's total becomes exact once the server reports its length, and again
    when it lands on disk.
    """
    job.files = [
        PullFile(hf_filename=c.hf_path, kind=c.kind.value, bytes_total=round(c.approx_gb * 1e9))
        for c in missing
    ]
    job.bytes_downloaded = 0
    job.bytes_total = sum(f.bytes_total for f in job.files)
    job._signal()

    headers = {"User-Agent": "hal0/installer"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    base_done = 0
    for component, rec in zip(missing, job.files, strict=True):
        if job.cancel_requested:
            raise _PullCancelled()
        dest = component.dest
        if dest.is_file():
            # Another install of a model sharing this file landed it since we looked.
            rec.dest = str(dest)
            rec.bytes_done = dest.stat().st_size
        else:
            ensure_shared_dir(dest.parent)
            # The downloader keys its staging file by (staging_key, rec.hf_filename);
            # hf_path is unique within a model, so two components never share a partial.
            await _download_one(
                job,
                rec,
                client=client,
                hf_repo=component.hf_repo,
                base_headers=headers,
                final=dest,
                base_done=base_done,
                base_total=_others_total(job, rec),
                staging_key=staging_key,
            )
        rec.bytes_total = rec.bytes_done  # on disk: the size is known, not estimated
        base_done += rec.bytes_done
        job.bytes_downloaded = base_done
        job.bytes_total = sum(f.bytes_total for f in job.files)


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

    Refuses, before streaming anything, a layout where the staging dir and the models tree
    are on different filesystems (:class:`InstallStagingCrossDevice`) and a destination
    directory the hal0 user cannot write (:class:`InstallDirNotWritable`).

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
        missing = install_state(model).missing
        if missing:
            require_one_filesystem()
            require_writable_destinations(model)
        await _download_missing(
            job, missing, client=client, hf_token=hf_token, staging_key=_staging_key(model)
        )
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


def remove_model(model: ImageModel, *, include_shared: bool = False) -> dict[str, Any]:
    """Delete ``model``'s weight files and its provisioned workflow; report what happened.

    By default a file another catalog model loads stays while that model is complete (a model
    only partly installed does not hold files back: it cannot run without its missing ones).
    Two models can share a whole file set (``ltx2-t2v`` and ``ltx2-i2v``, or ``qwen-image``
    and its Lightning variant), and then each keeps the other complete, so the default frees
    nothing but the workflow. Which of the two the operator still wants is not for hal0 to
    guess: ``include_shared=True`` deletes every component, shared or not, and the result
    lists the models that stopped being complete.

    The workflow (``<workflows dir>/<id>.json``, written by :func:`run_install`, which is its
    only writer) belongs to this model alone, so it goes in either mode. An affected model
    no longer has its weights, so its workflow goes too and the launcher never offers one
    that cannot run.

    The removal is recorded (:func:`mark_removed`) once the model's own workflow is dealt with,
    so a model that stays ``complete`` because others hold its weights is not given its
    workflow back by :func:`ensure_workflows`. The affected models are not recorded: they stop
    being complete, and return with their weights.

    Returns ``{"removed", "kept", "affected"}``:

    * ``removed``: the weight paths relative to the models root, then
      ``workflows/<filename>`` for the workflow, then (with ``include_shared``) the workflow
      of each affected model. Files already gone are not reported.
    * ``kept``: ``{"relpath", "models"}`` for each weight file left in place, ``models``
      being the complete models that hold it. Always empty with ``include_shared=True``.
    * ``affected``: the other models that were complete before and are not after, in catalog
      order; computed before anything is deleted. Always empty by default.

    Raises :class:`RemoveFailed` at the first file that cannot be deleted (a read-only
    directory, say): the removal stops there, and ``details["removed"]`` lists what had
    already gone, so the operator sees how far it got. The models root is resolved once.
    """
    root = paths.models_root()
    complete: dict[str, bool] = {}

    def holders_of(component: Component) -> list[str]:
        """Ids of the other catalog models that are complete and load ``component``."""
        ids = [other for other in models_using(component, root) if other != model.id]
        for other in ids:
            if other not in complete:
                complete[other] = install_state(CATALOG[other], root).state == "complete"
        return [other for other in ids if complete[other]]

    # Read every neighbour's state before the first delete: it is what "affected" is measured from.
    holders = [(component, holders_of(component)) for component in model.components]
    broken = {other for _, held in holders for other in held} if include_shared else set()
    affected = [model_id for model_id in CATALOG if model_id in broken]  # catalog order

    removed: list[str] = []
    kept: list[dict[str, Any]] = []
    for component, held in holders:
        dest = component_path(component, root)
        relpath = dest.relative_to(root).as_posix()
        if held and not include_shared:
            kept.append({"relpath": relpath, "models": held})
        elif _unlink(dest, removed):
            removed.append(relpath)
    for owner in (model, *(CATALOG[model_id] for model_id in affected)):
        if _unlink(paths.workflows_dir() / owner.workflow_filename, removed):
            removed.append(f"workflows/{owner.workflow_filename}")
        if owner is model:
            mark_removed(model.id)  # before a neighbour's stuck workflow can raise
    return {"removed": removed, "kept": kept, "affected": affected}


def _unlink(path: Path, removed: list[str]) -> bool:
    """Delete ``path``; False when it was already gone, :class:`RemoveFailed` on any other error."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RemoveFailed(
            f"could not delete {path}: {exc.strerror or exc}; {len(removed)} file(s) were "
            "removed before it. Make the directory writable by the hal0 user or group "
            "(e.g. chgrp <hal0 group> + chmod 2775), then remove the model again",
            details={"removed": list(removed), "failed_path": str(path), "error": str(exc)},
        ) from exc
    return True


# ── setup path ──────────────────────────────────────────────────────────────

# Seconds between the progress lines a blocking install prints for a model still downloading.
_PROGRESS_INTERVAL_S = 5.0


async def _report_progress(job: PullJob, say: Callable[[str], None]) -> None:
    """Say how far ``job`` has got once per :data:`_PROGRESS_INTERVAL_S`, until cancelled.

    A ``say`` that raises ends the reporting for this job (logged), so a broken sink can
    neither cost a finished install its result nor stop the models after it.
    """
    while True:
        await asyncio.sleep(_PROGRESS_INTERVAL_S)
        if job.state == "running" and job.bytes_total > 0:
            pct = int(job.bytes_downloaded * 100 / job.bytes_total)
            try:
                say(
                    f"  {job.model_id}: {pct}% "
                    f"({job.bytes_downloaded / 1e9:.1f}/{job.bytes_total / 1e9:.1f} GB)"
                )
            except Exception:
                log.exception(
                    "comfyui.install_progress_report_failed", extra={"model_id": job.model_id}
                )
                return


async def install_each(
    model_ids: Sequence[str], hf_token: str | None, say: Callable[[str], None]
) -> list[tuple[str, str]]:
    """Install ``model_ids`` one after another and return ``(model_id, final_state)`` for each.

    The async body of :func:`install_models_blocking`, for a caller that already owns an event
    loop (:func:`hal0.install.image_models.provision_image_models`). Each model's lines go to
    ``say`` as that function documents.
    """
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
            clear_removed(model.id)  # asking for it is the operator changing their mind
            job = make_job(model_id)
            progress = asyncio.create_task(_report_progress(job, say))
            try:
                await run_install(job, model, hf_token=hf_token, client=client)
            finally:
                progress.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await progress
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
    ``on_line`` and comes back ``failed``; the rest still run. Each model's lines are a
    start line, ``  <id>: <pct>% (<done>/<total> GB)`` at most every
    :data:`_PROGRESS_INTERVAL_S` while it downloads, and an end line.
    """
    return asyncio.run(install_each(model_ids, hf_token, on_line or (lambda _line: None)))


__all__ = [
    "ImageModelNotFound",
    "InstallDirNotWritable",
    "InstallInsufficientDisk",
    "InstallStagingCrossDevice",
    "RemoveFailed",
    "install_each",
    "install_models_blocking",
    "preflight_disk",
    "remove_model",
    "require_one_filesystem",
    "require_writable_destinations",
    "run_install",
]
