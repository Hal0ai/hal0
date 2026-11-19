"""Remove a downloaded FLM (FastFlowLM, NPU) model from disk.

``DELETE /api/models/{id}`` deliberately keeps an FLM row's files: FLM owns its
model directory (:func:`hal0.registry.discover.is_runtime_managed`). This module
is the FLM-owned way to free that space: it runs ``flm remove <tag>`` in a
one-shot container of the FLM slot's runner image, against the store the slot
mounts, through the ``hal0-flmctl`` seam's ``run-remove`` verb — the same route
``flm pull`` takes (:func:`hal0.registry.pull.run_flm_pull`).

Refused (409) before anything runs when:

* the tag is what an FLM role is configured or serving: the NPU anchor's chat
  model (``[model].default``), ``embed-gemma:300m`` while ``[npu].embed`` is on,
  ``whisper-v3:turbo`` while ``[npu].asr`` is on, or the ``[npu].decision`` tag;
* a pull of the tag is queued or running (in this process, or a persisted
  snapshot whose writer is still alive);
* the catalogue does not report the tag as installed and hal0 has no
  FLM-managed registry row for it. With such a row (its files were removed
  outside hal0) the route drops the row and the stale pull job instead,
  without running ``flm remove``.

While the remove runs the tag is marked "deleting" (ADR-0026), so a pull of it
started meanwhile gets ``model.deleting``. FastFlowLM 1.0.12's ``remove`` exits
0 even when it fails to delete (``main.cpp`` ignores ``remove_model``'s
result), so success is judged by the tag's install directory being gone, not by
the exit code. On success the tag's hal0 registry row (id == tag, as
:func:`hal0.registry.pull._register_flm_pulled` writes it) and its terminal pull
job are dropped, and the FLM catalogue cache is reset so ``installed`` flips.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from hal0.errors import Conflict, Hal0Error, NotFound, UnprocessableEntity

log = logging.getLogger(__name__)

#: Seconds ``flm remove`` gets before it is killed. Deleting a model directory
#: is a handful of unlinks; this only bounds a wedged podman.
REMOVE_TIMEOUT_S = 120.0

_ACTIVE_STATES = ("queued", "running")


class FlmRemoveFailed(Hal0Error):
    """``flm remove`` ran but the model's files are still there (or it errored)."""

    code = "flm.remove_failed"
    status = 502


def flm_tags_in_use(
    configs: list[dict[str, Any]],
    live: list[tuple[str, str | None]] | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Map each FLM tag an FLM role is configured or serving to its users.

    ``configs`` are flattened slot TOMLs. Only an activated FastFlowLM NPU
    ``llm`` slot (the anchor, :func:`hal0.slots.activation.claims_npu_anchor`)
    counts: its ``[model].default`` is the chat role, its effective ``[npu]``
    table switches the embed and ASR roles (each loads FLM's one bundled model)
    and names the decision tag. The ``transcription`` / ``embedding`` shadow
    slots always carry a placeholder model, so their own ``[model].default`` is
    not read. ``live`` is ``(slot name, model id)`` for running slots: an FLM
    slot serving a tag its config no longer names still holds it.

    Values are ``[{"role": "chat"|"embed"|"asr"|"decision", "slot": name}]``.
    """
    from hal0.providers.flm import FLM_EMBED_TAG
    from hal0.slots.activation import claims_npu_anchor, effective_npu_table, slot_model_id

    out: dict[str, list[dict[str, str]]] = {}

    def _add(tag: str, role: str, slot: str) -> None:
        tag = _as_tag(tag)
        if not tag:
            return
        users = out.setdefault(tag, [])
        entry = {"role": role, "slot": slot}
        if entry not in users:
            users.append(entry)

    flm_slots: set[str] = set()
    for cfg in configs:
        if not claims_npu_anchor(cfg):
            continue
        name = str(cfg.get("name") or "")
        flm_slots.add(name)
        _add(slot_model_id(cfg), "chat", name)
        table = effective_npu_table(cfg)
        if table.get("embed"):
            _add(FLM_EMBED_TAG, "embed", name)
        if table.get("asr"):
            _add(_FLM_ASR_TAG, "asr", name)
        decision = table.get("decision")
        if isinstance(decision, str) and decision:
            _add(decision, "decision", name)
    for name, model_id in live or []:
        if name in flm_slots and model_id:
            _add(model_id, "chat", name)
    return out


#: The ASR model FLM's ``--asr 1`` loads (no per-role selection; see
#: ``hal0.config.schema.NpuConfig``). Same literal as ``hal0.api._FLM_ASR_TAG``.
_FLM_ASR_TAG = "whisper-v3:turbo"


def _as_tag(model_id: str) -> str:
    """FLM's ``family:variant`` tag for ``model_id`` (an older ``<tag>-FLM`` id maps back)."""
    model_id = (model_id or "").strip()
    if model_id.endswith("-FLM"):
        from hal0.providers.flm import flm_id_to_tag

        return flm_id_to_tag(model_id) or model_id
    return model_id


def _active_pull(tag: str, jobs: dict[str, Any] | None, registry: Any) -> str | None:
    """State of a queued/running pull of ``tag``, or ``None``."""
    job = (jobs or {}).get(tag)
    if job is not None and getattr(job, "state", None) in _ACTIVE_STATES:
        return str(job.state)
    from hal0.registry.pull_jobs import load_persisted

    persisted = load_persisted(tag, registry)
    if persisted and persisted.get("state") in _ACTIVE_STATES:
        return str(persisted["state"])
    return None


def _catalog_entry(tag: str) -> dict[str, Any] | None:
    from hal0.providers.flm import flm_catalog

    for entry in flm_catalog() or []:
        if entry.get("tag") == tag:
            return entry
    return None


def _has_registry_row(registry: Any, tag: str) -> bool:
    """True when hal0 still records an FLM-managed row for ``tag``.

    Such a row is a ghost once its files are gone. A row under the same id that
    FLM does not manage (a GGUF registered as ``qwen3:0.6b``) is not ours to
    drop here, so it does not count. Rows are matched on ``id == tag`` (what
    ``_register_flm_pulled`` writes); a legacy ``<tag>-FLM`` id is not matched.
    """
    from hal0.registry.discover import is_runtime_managed

    if registry is None:
        return False
    try:
        row = registry.get(tag)
        return row is not None and is_runtime_managed(row)
    except Exception:
        return False


def _install_dir(tag: str, store: str) -> str | None:
    """The tag's directory in ``store``, read from a fresh raw ``flm list -j``."""
    from hal0.providers.flm import _probe_flm_catalog
    from hal0.registry.pull import _flm_install_path_from

    models = _probe_flm_catalog() or []
    return _flm_install_path_from(models, store, tag)


def _flm_messages(output: str, level: str) -> list[str]:
    """``[LEVEL]`` lines from flm's output, prefix stripped (``header_print``)."""
    marker = f"[{level}]"
    out: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(marker):
            out.append(stripped[len(marker) :].strip())
    return out


async def _run_remove(argv: list[str], tag: str) -> tuple[int, str]:
    """Run the seam's ``run-remove``; return ``(exit code, stdout+stderr)``."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=REMOVE_TIMEOUT_S)
    except TimeoutError as exc:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise FlmRemoveFailed(
            f"flm remove {tag} did not finish within {int(REMOVE_TIMEOUT_S)} s.",
            details={"tag": tag},
        ) from exc
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, raw.decode("utf-8", errors="replace") if raw else ""


async def check_removable(
    tag: str,
    *,
    configs: list[dict[str, Any]],
    live: list[tuple[str, str | None]],
    jobs: dict[str, Any] | None,
    registry: Any,
) -> dict[str, Any]:
    """Raise the 4xx a remove of ``tag`` must refuse with; else its catalogue entry."""
    from hal0.providers.flm_cli import is_valid_flm_tag

    if not is_valid_flm_tag(tag):
        raise UnprocessableEntity(
            f"{tag!r} is not an FLM model tag (family:variant).",
            code="flm.invalid_tag",
            details={"tag": tag},
        )
    users = flm_tags_in_use(configs, live).get(tag)
    if users:
        roles = ", ".join(f"{u['role']} on slot {u['slot']}" for u in users)
        raise Conflict(
            f"{tag} is in use ({roles}); switch that role to another model first.",
            code="flm.model_in_use",
            details={"tag": tag, "in_use_by": users},
        )
    pull_state = await asyncio.to_thread(_active_pull, tag, jobs, registry)
    if pull_state is not None:
        raise Conflict(
            f"a pull of {tag} is {pull_state}; cancel it or wait for it to finish.",
            code="flm.pull_running",
            details={"tag": tag, "pull_state": pull_state},
        )
    entry = await asyncio.to_thread(_catalog_entry, tag)
    if entry is None:
        raise NotFound(
            f"{tag} is not in the FLM catalogue.",
            code="flm.model_not_found",
            details={"tag": tag},
        )
    if not entry.get("installed") and not _has_registry_row(registry, tag):
        raise Conflict(
            f"{tag} is not downloaded, so there is nothing to remove.",
            code="flm.model_not_installed",
            details={"tag": tag},
        )
    return entry


async def _drop_ghost_row(
    tag: str, *, registry: Any, jobs: dict[str, Any] | None
) -> dict[str, Any]:
    """Drop the hal0 records of an FLM tag whose files are already gone."""
    from hal0.providers.flm import reset_flm_catalog_cache
    from hal0.registry.pull import mark_deleted, mark_deleting, pull_job_file, unmark_deleting

    mark_deleting(tag)
    try:
        registry_removed = False
        if registry is not None:
            with contextlib.suppress(Exception):
                registry_removed = bool(await asyncio.to_thread(registry.remove, tag))
        mark_deleted(tag)
    finally:
        unmark_deleting(tag)
        reset_flm_catalog_cache()
    if jobs is not None:
        stale = jobs.get(tag)
        if stale is not None and getattr(stale, "state", None) not in _ACTIVE_STATES:
            jobs.pop(tag, None)
    with contextlib.suppress(OSError):
        pull_job_file(tag).unlink(missing_ok=True)
    log.info("flm.ghost_row_removed tag=%s", tag)
    return {
        "tag": tag,
        "removed": True,
        "path": None,
        "bytes_freed": 0,
        "registry_removed": registry_removed,
        "warnings": [],
    }


async def remove_flm_model(
    tag: str,
    *,
    registry: Any,
    jobs: dict[str, Any] | None,
    live: list[tuple[str, str | None]] | None = None,
) -> dict[str, Any]:
    """Remove ``tag``'s files with ``flm remove`` and drop its hal0 records.

    Returns ``{"tag", "removed": True, "path", "bytes_freed", "registry_removed",
    "warnings"}``; ``warnings`` carries flm's own ``[WARNING]`` lines (it warns,
    without cascading, when installed tags are built on the one removed).
    """
    from hal0.providers.flm import reset_flm_catalog_cache
    from hal0.providers.flm_cli import RC_IMAGE_MISSING, _slot_configs, flm_remove_invocation
    from hal0.registry.pull import (
        _dir_size,
        ensure_not_deleting,
        mark_deleted,
        mark_deleting,
        pull_job_file,
        unmark_deleting,
    )

    configs = await asyncio.to_thread(_slot_configs)
    entry = await check_removable(
        tag, configs=configs, live=live or [], jobs=jobs, registry=registry
    )
    # Check-and-mark with no await between, so a pull started from here on is
    # refused (``model.deleting``) and a second remove of the same tag waits its turn.
    ensure_not_deleting(tag)
    job = (jobs or {}).get(tag)
    if job is not None and getattr(job, "state", None) in _ACTIVE_STATES:
        raise Conflict(
            f"a pull of {tag} is {job.state}; cancel it or wait for it to finish.",
            code="flm.pull_running",
            details={"tag": tag, "pull_state": job.state},
        )
    if not entry.get("installed"):
        # The files are already gone (removed outside hal0), but the registry
        # row survives: ADR-0026 keeps runtime-managed rows out of prune, so
        # this route is the only way to clear it. There is nothing for
        # ``flm remove`` to do. This sits below the pull recheck above so a
        # pull that started during the catalogue lookup is still refused.
        return await _drop_ghost_row(tag, registry=registry, jobs=jobs)
    mark_deleting(tag)
    try:
        invocation, store = await asyncio.to_thread(flm_remove_invocation, tag)
        path = await asyncio.to_thread(_install_dir, tag, store)
        size = await asyncio.to_thread(_dir_size, path) if path else 0

        rc, output = await _run_remove(invocation.argv, tag)
        if rc == RC_IMAGE_MISSING:
            raise FlmRemoveFailed(
                f"flm remove {tag} needs the flm runner image, which is not downloaded. "
                "Download it from Runner Images (or run `hal0 runner-images pull flm`).",
                details={"tag": tag, "exit_code": rc},
            )
        errors = _flm_messages(output, "ERROR")
        still_there = bool(path) and await asyncio.to_thread(Path(path).exists)
        if rc != 0 or errors or still_there:
            if errors:
                reason = errors[-1]
            elif rc != 0:
                reason = f"exited with status {rc}"
            else:
                reason = f"{path} is still on disk"
            raise FlmRemoveFailed(
                f"flm remove {tag} failed: {reason}",
                details={"tag": tag, "exit_code": rc, "path": path, "output": output[-2000:]},
            )

        registry_removed = False
        if registry is not None:
            with contextlib.suppress(Exception):
                registry_removed = bool(await asyncio.to_thread(registry.remove, tag))
        mark_deleted(tag)
    finally:
        unmark_deleting(tag)
        # The catalogue (and its installed flags) may have changed even on a
        # failure partway, so the next read always probes.
        reset_flm_catalog_cache()

    # A finished pull job for the tag would read as still downloaded.
    if jobs is not None:
        stale = jobs.get(tag)
        if stale is not None and getattr(stale, "state", None) not in _ACTIVE_STATES:
            jobs.pop(tag, None)
    with contextlib.suppress(OSError):
        pull_job_file(tag).unlink(missing_ok=True)

    log.info("flm.model_removed tag=%s path=%s bytes=%s", tag, path, size)
    return {
        "tag": tag,
        "removed": True,
        "path": path,
        "bytes_freed": size or int(entry.get("size_bytes") or 0),
        "registry_removed": registry_removed,
        "warnings": _flm_messages(output, "WARNING"),
    }


__all__ = [
    "REMOVE_TIMEOUT_S",
    "FlmRemoveFailed",
    "check_removable",
    "flm_tags_in_use",
    "remove_flm_model",
]
