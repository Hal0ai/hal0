"""First-run wizard backend endpoints (mounted under /api/install).

These are the *server* contract that the FirstRun wizard UI (Team B/E)
consumes; the wizard itself is a separate Vue view.

Endpoints:
    GET  /api/install/state    — surface first-run + has-models +
                                 has-default-slot + openwebui-running
                                 flags so the dashboard can decide
                                 whether to route to FirstRun.
    POST /api/install/probe    — re-run the hardware probe and write
                                 ``/etc/hal0/hardware.json`` atomically.
    POST /api/install/complete — marker call after the wizard finishes;
                                 writes ``/var/lib/hal0/.first_run_done``
                                 so subsequent boots skip the wizard.

The curated-models picker and the actual model pull (POST /pick-default)
are Team B's wave (HF streaming download + SSE progress) — left as 501
stubs with a clearer ``code: "model.pull_pending"`` envelope so the UI
can detect "feature not landed yet" vs "broken".
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request

from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.bundles import store as bundle_store
from hal0.config import paths
from hal0.hardware.probe import HardwareProbe
from hal0.registry.curated import CURATED_MODELS, get_curated
from hal0.registry.model import Model
from hal0.registry.pull import make_job, run_pull
from hal0.registry.store import ModelAlreadyExists

# Auth was removed in ADR-0012. All endpoints are open on the local
# network; the first-run wizard runs without any credential.

# Slot-name policy — mirrors ``SlotConfig.name`` in hal0.config.schema so a
# slot name accepted by the API installer endpoints is also accepted by the
# CLI / TOML loader. Reject anything else BEFORE the path is built so a
# value like ``"../../tmp/pwn"`` can't resolve to an arbitrary on-disk file.
# See FINDINGS §30.
_SLOT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _validate_slot_name(slot: object) -> str:
    """Return ``slot`` as a string if it matches the slot-name policy.

    Raises :class:`BadRequest` (code ``slot.invalid_name``) on any value
    that doesn't match the regex — including non-string types, the empty
    string, path-traversal payloads (``"../foo"``), and absolute paths
    (``"/etc/passwd"``). The error envelope echoes back the policy so a
    well-behaved client can correct itself.
    """
    if not isinstance(slot, str) or not _SLOT_NAME_RE.match(slot):
        raise BadRequest(
            "invalid slot name",
            details={
                "slot": slot if isinstance(slot, str) else repr(slot),
                "policy": _SLOT_NAME_RE.pattern,
            },
            code="slot.invalid_name",
        )
    return slot


router = APIRouter()


class PickDefaultError(Hal0Error):
    """Errors specific to ``POST /api/install/pick-default``."""

    code = "install.pick_default_failed"
    status = 400


class CuratedModelNotFound(PickDefaultError):
    """404 — caller asked for a curated id that's not in the catalogue."""

    code = "install.curated_not_found"
    status = 404


_DEFAULT_SLOT = "chat"


def _first_run_sentinel() -> Path:
    """Path to the marker file written after the FirstRun wizard finishes."""
    return paths.var_lib() / ".first_run_done"


def _models_dir_populated() -> bool:
    """True if ``/var/lib/hal0/models/`` exists and contains at least one file."""
    d = paths.models_dir()
    if not d.exists():
        return False
    try:
        for entry in d.iterdir():
            # A nested directory or a model file both count as "populated"
            # — fresh installs have the directory missing or empty.
            if entry.name.startswith("."):
                continue
            return True
    except OSError:
        return False
    return False


def _has_default_slot() -> bool:
    """True if the chat slot TOML exists (chat.toml or legacy primary.toml)."""
    slots_dir = paths.slots_config_dir()
    return (slots_dir / "chat.toml").exists() or (slots_dir / "primary.toml").exists()


async def _openwebui_running() -> bool:
    """Best-effort: ask systemd whether hal0-openwebui.service is active.

    Returns False on hosts without systemctl (CI, mac dev boxes) and on
    any subprocess failure — this flag drives a UI hint, not a hard gate,
    so we tolerate missing infra.
    """
    if shutil.which("systemctl") is None:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "is-active",
            "hal0-openwebui.service",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except (TimeoutError, OSError):
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()  # type: ignore[has-type]
        return False
    return stdout.decode("utf-8", errors="replace").strip() == "active"


@router.get("/state")
async def install_state(request: Request) -> dict[str, Any]:
    """Return the dashboard's first-run gating state.

    Shape::

        {
            "first_run": true,
            "has_models": false,
            "has_default_slot": false,
            "openwebui_running": false,
            "sentinel_path": "/var/lib/hal0/.first_run_done",
            "bundle": {
                "name": "hal0-Pro",
                "skipped": false,
                "npu_opt_in": false,
                "chosen_at": "2026-05-25T18:00:00+00:00"
            } | null
        }

    ``first_run`` is true when ``/var/lib/hal0/models/`` is empty AND the
    sentinel hasn't been written. Either condition flipping false hides
    the FirstRun wizard. ``bundle`` is the persisted bundle-picker
    decision (or ``null`` before the picker runs) — the dashboard reads
    this to render the actual tier name in the post-install hero and
    "currently have …" banners (issue #214).
    """
    has_models = _models_dir_populated()
    sentinel = _first_run_sentinel()
    sentinel_present = sentinel.exists()
    has_default = _has_default_slot()
    openwebui = await _openwebui_running()
    first_run = (not has_models) and (not sentinel_present)
    choice = bundle_store.read_choice()
    return {
        "first_run": first_run,
        "has_models": has_models,
        "has_default_slot": has_default,
        "openwebui_running": openwebui,
        "sentinel_path": str(sentinel),
        "bundle": choice.to_dict() if choice is not None else None,
    }


@router.post("/probe")
async def install_probe(request: Request) -> dict[str, Any]:
    """Re-run the hardware probe and rewrite ``/etc/hal0/hardware.json``.

    Returns the freshly-probed HardwareInfo as JSON. Uses the existing
    ``HardwareProbe.write()`` which already does atomic tempfile+replace.
    """
    probe: HardwareProbe = getattr(request.app.state, "hardware_probe", None) or HardwareProbe()
    info = await probe.probe_async()
    # ``write()`` raises HardwareProbeError on disk failures, which is a
    # Hal0Error subclass — let the envelope middleware surface it.
    target = probe.write(info)
    # Cache on app.state so subsequent /api/hardware reads can skip the
    # re-probe round-trip.
    request.app.state.hardware_info = info
    return {
        "hardware": info.model_dump(mode="json"),
        "path": str(target),
    }


@router.post("/complete")
async def install_complete(request: Request) -> dict[str, Any]:
    """Mark the FirstRun wizard as complete by writing the sentinel.

    Atomic: tempfile + os.replace in the parent directory so a partial
    write can't leave a half-written marker. Idempotent — re-calling
    after the sentinel already exists is a no-op.

    Also consumes the ``.first-run.lock`` file. The lockfile already
    disappears on a successful POST /api/auth/password, but operators
    who chose "Skip — leave open" never hit that path. Without this
    cleanup the claim window would stay open indefinitely — the
    sentinel hides the wizard route in the UI, but the anonymous
    pass-through on wizard writer routes would survive. Consuming
    here closes that window the moment the wizard signals completion.
    """
    sentinel = _first_run_sentinel()
    sentinel.parent.mkdir(parents=True, exist_ok=True)

    payload = "first_run_done\n"
    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(
            prefix=f".{sentinel.name}.",
            suffix=".tmp",
            dir=sentinel.parent,
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, sentinel)
        tmp_path = None
    except OSError as exc:
        raise Hal0Error(
            f"could not write first-run sentinel: {exc}",
            details={"path": str(sentinel), "error": str(exc)},
        ) from exc
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)

    return {"first_run": False, "sentinel_path": str(sentinel)}


# ── Curated picker + pick-default ──────────────────────────────────────────


@router.get("/curated-models")
async def curated_models() -> dict[str, Any]:
    """Return the curated chat-model catalogue the FirstRun wizard renders.

    Shape::

        {
            "models": [{...CuratedModel...}, ...],
            "custom_allowed": true
        }

    Filtered to ``recommended_slot == "chat"`` — image models and any
    future non-chat picks live in the same source list but have their own
    placement in the wizard (step 4 capability pickers). Leaving them in
    the chat picker would let an operator install Flux as their "chat
    model", which is meaningless and previously confused users. Sourcing
    capability picks happens through ``/api/capabilities``.
    """
    chat_picks = [
        m for m in CURATED_MODELS if m.recommended_slot in ("chat", "primary") and not m.bundle_only
    ]
    return {
        "models": [m.model_dump(mode="json") for m in chat_picks],
        "custom_allowed": True,
    }


def _ensure_registry_entry(registry: Any, model_id: str) -> Model:
    """Create the registry entry for a curated id if it isn't there yet.

    Mirrors what ``pull_model`` will record on completion, but populates
    the entry *before* the download starts so the dashboard can show
    "downloading…" against a real registry row instead of a phantom id.
    The path is provisional — ``run_pull`` rewrites it to the final
    location on success.
    """
    curated = get_curated(model_id)
    if curated is None:
        raise CuratedModelNotFound(
            f"curated model {model_id!r} not in catalogue",
            details={"model_id": model_id, "available": [m.id for m in CURATED_MODELS]},
        )
    if registry.has(model_id):
        return registry.get(model_id)
    # Provisional path: the pull will overwrite this on success. We need
    # *some* string here because ``Model.path`` is required and TOML
    # can't hold None.
    if curated.comfyui_subdir:
        # ComfyUI assets land in the ComfyUI models tree (matches what
        # registry.pull writes on completion). Mirror that layout here so
        # the provisional path is correct from the very first state-poll.
        provisional = (
            paths.var_lib() / "comfyui" / "models" / curated.comfyui_subdir / curated.hf_file
        )
    else:
        provisional = paths.models_dir() / curated.id / curated.hf_file
    capabilities = [curated.capability] if curated.capability else ["chat"]
    extra_meta: dict[str, Any] = {
        "license_url": curated.license_url,
        "context_length": curated.context_length,
        "family": curated.family,
    }
    if curated.model_class:
        extra_meta["model_class"] = curated.model_class
    if curated.comfyui_subdir:
        extra_meta["comfyui_subdir"] = curated.comfyui_subdir
    entry = Model(
        id=curated.id,
        name=curated.display_name,
        path=str(provisional),
        size_bytes=0,
        license=curated.license,
        capabilities=capabilities,
        hf_repo=curated.hf_repo,
        hf_filename=curated.hf_file,
        tags=["curated", *curated.tags],
        metadata=extra_meta,
    )
    try:
        registry.add(entry)
    except ModelAlreadyExists:
        # Race with another request — fine; whoever lost the race uses
        # the existing entry.
        return registry.get(model_id)
    return entry


def _assign_to_slot(slot: str, model_id: str) -> Path:
    """Atomically update ``/etc/hal0/slots/<slot>.toml`` so ``model.default = <id>``.

    Routes through ``hal0.slot_config.write_slot_toml`` — the single
    atomic slots/*.toml write path (issue #697) — so a half-written TOML
    can't take out the slot. Creates the file from scratch if it doesn't
    exist — this is the FirstRun path on a fresh install where the
    primary slot was scaffolded but never had a model assigned.
    """
    from hal0.slot_config import write_slot_toml

    slot_path = paths.slots_config_dir() / f"{slot}.toml"
    slot_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing if present so we don't clobber port/backend.
    data: dict[str, Any] = {}
    if slot_path.exists():
        import tomllib

        try:
            with open(slot_path, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise PickDefaultError(
                f"could not parse existing slot TOML {slot_path}: {exc}",
                details={"slot": slot, "path": str(slot_path)},
            ) from exc

    data.setdefault("name", slot)
    # Built-in slot port + provider defaults. The 'img' slot is the
    # image-generation lane (ComfyUI, ROCm-first); everything else
    # remains llama-server / Vulkan.
    if slot == "img":
        data.setdefault("port", 8186)
        data.setdefault("backend", "rocm")
        data.setdefault("provider", "comfyui")
    else:
        # chat slot (canonical "chat" or legacy alias "primary") gets port 8081.
        data.setdefault(
            "port", 8081 if slot in ("chat", "primary") else 8080 + abs(hash(slot)) % 100
        )
        data.setdefault("backend", "vulkan")
        data.setdefault("provider", "llama-server")
    model_section = data.get("model")
    if not isinstance(model_section, dict):
        model_section = {}
    model_section["default"] = model_id
    data["model"] = model_section

    try:
        write_slot_toml(slot_path, data)
    except OSError as exc:
        raise PickDefaultError(
            f"could not write slot TOML {slot_path}: {exc}",
            details={"slot": slot, "path": str(slot_path)},
        ) from exc
    return slot_path


@router.post("/pick-default")
async def pick_default(
    request: Request,
    background: BackgroundTasks,
) -> dict[str, Any]:
    """End-to-end "pick + download + assign" for the FirstRun wizard.

    Body::

        { "model_id": "qwen3-4b", "slot": "chat" }

    Slot defaults to ``chat`` if omitted. Flow:

    1. Look up the curated entry — 404 if unknown.
    2. Seed the registry row (so the dashboard can show progress).
    3. Update ``/etc/hal0/slots/<slot>.toml`` so ``model.default = <id>``.
    4. Kick off the same pull background task ``POST /api/models/{id}/pull``
       runs (single source of truth for the download logic).
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise PickDefaultError(
            f"body must be valid JSON: {exc}",
            details={"error": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise PickDefaultError("body must be a JSON object")
    model_id = body.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise PickDefaultError(
            "body.model_id is required (must be a non-empty string)",
            details={"got": body},
        )
    slot = body.get("slot") or _DEFAULT_SLOT
    # Validate the slot name BEFORE any filesystem op — see FINDINGS §30.
    # A traversal payload (e.g. ``"../../tmp/pwn"``) is rejected here with
    # a typed 400 rather than escaping the slots config dir on disk.
    slot = _validate_slot_name(slot)

    registry = request.app.state.model_registry
    _ensure_registry_entry(registry, model_id)
    slot_path = _assign_to_slot(slot, model_id)

    # Kick off the pull — same code path as the dedicated /pull endpoint.
    jobs = request.app.state.model_pull_jobs
    existing = jobs.get(model_id)
    if existing is not None and existing.state in ("queued", "running"):
        job = existing
    else:
        curated = get_curated(model_id)
        if curated is None:
            # _ensure_registry_entry would have raised — defensive only.
            raise CuratedModelNotFound(
                f"curated model {model_id!r} disappeared between lookup and pull",
                details={"model_id": model_id},
            )
        job = make_job(model_id)
        jobs[model_id] = job
        hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        background.add_task(
            run_pull,
            job,
            hf_repo=curated.hf_repo,
            hf_file=curated.hf_file,
            registry=registry,
            hf_token=hf_token,
            comfyui_subdir=curated.comfyui_subdir or None,
        )

    return {
        "model_id": model_id,
        "slot": slot,
        "slot_path": str(slot_path),
        "pull_job_id": job.job_id,
        "next": f"poll /api/models/{model_id}/pull/status",
    }


@router.put("/slots/{slot}/model")
async def set_slot_default_model(slot: str, request: Request) -> dict[str, Any]:
    # Persist-only counterpart to /api/slots/{name}/swap.  Hot-swap changes
    # the running container; this writes model.default into
    # /etc/hal0/slots/<slot>.toml so the change survives a restart.  UI
    # and CLI call both for the common "change and remember" flow.
    #
    # Validate the slot name BEFORE reading the body or touching disk —
    # see FINDINGS §30. ``slot="../../tmp/pwn"`` would otherwise resolve
    # under /tmp via the f-string in ``_assign_to_slot``.
    slot = _validate_slot_name(slot)
    try:
        body = await request.json()
    except Exception as exc:
        raise PickDefaultError(
            f"body must be valid JSON: {exc}",
            details={"slot": slot, "error": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise PickDefaultError("body must be a JSON object", details={"slot": slot})
    model_id = body.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise PickDefaultError(
            "model_id is required (non-empty string)",
            details={"slot": slot},
        )
    model_id = model_id.strip()

    registry = getattr(request.app.state, "model_registry", None)
    if registry is not None and not registry.has(model_id):
        raise PickDefaultError(
            f"model_id {model_id!r} is not in the registry",
            details={"slot": slot, "model_id": model_id},
        )

    slot_path = _assign_to_slot(slot, model_id)
    return {
        "slot": slot,
        "model_id": model_id,
        "slot_path": str(slot_path),
        "persisted": True,
    }
