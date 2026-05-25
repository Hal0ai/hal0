"""Model registry endpoints (mounted under /api/models).

This is the *internal* models surface for the dashboard — distinct from
OpenAI-compat `/v1/models`.  Aggregates entries from every configured
upstream so the dashboard's Models view shows what's actually reachable,
plus any locally-registered models from the ModelRegistry.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import StreamingResponse

from hal0.api.middleware.error_codes import BadRequest, NotFound
from hal0.config.loader import load_hal0_config
from hal0.errors import Hal0Error
from hal0.registry.curated import CURATED, CuratedModel, HaloaiModel, get_curated
from hal0.registry.detect import DetectionResult, detect
from hal0.registry.discover import is_skippable, scan_and_register
from hal0.registry.model import _derive_ns
from hal0.registry.pull import (
    PullInvalidSource,
    PullJob,
    PullJobNotFound,
    make_job,
    run_flm_pull,
    run_pull,
)

# See slots.py for the writer-gate rationale.

router = APIRouter()


# Known-alias model ids that upstream gateways advertise as routing
# shortcuts (haloai's hermes-proxy exposes them as "primary", "tiny",
# etc., plus haloai:* namespaced variants).  Filtered from the dashboard
# Models view because they're not real models — they're routes.
_ALIAS_NAMES = frozenset(
    {
        "primary",
        "medium",
        "tiny",
        "embed",
        "rerank",
        "npu",
        "coding",
        "coder",
        "whisper",
        "moonshine",
        "vibevoice",
        "kokoro",
        "tts-1",
        "tts-1-hd",
        "bge-reranker",
        "nomic-embed",
    }
)


def _is_alias(model_id: str) -> bool:
    """Filter out routing aliases that aren't real models."""
    if model_id.startswith("haloai:"):
        return True
    return model_id in _ALIAS_NAMES


@router.get("")
async def list_models(request: Request) -> dict[str, Any]:
    """Aggregate models from the local registry + every upstream.

    Local registry entries (a real file on disk) win on id collision —
    the upstream might still advertise the id, but the user has the
    bytes locally and that's the truth. Each row carries ``installed``
    so the UI can render an installed/advertised badge.
    """
    registry = request.app.state.model_registry
    upstreams = request.app.state.upstreams
    cache = getattr(request.app.state, "model_cache", {})
    now = int(time.time())
    data: list[dict[str, Any]] = []
    seen: set[str] = set()
    filtered = 0
    for entry in registry.list():
        dumped = _model_to_dict(entry)
        dumped["installed"] = True
        dumped.setdefault("object", "model")
        dumped.setdefault("created", now)
        dumped.setdefault("owned_by", "local")
        data.append(dumped)
        seen.add(entry.id)
    for u in upstreams.list():
        try:
            ids = cache.get(u.name) or await upstreams.fetch_models(u.name)
            cache[u.name] = ids
        except Exception:
            ids = []
        for mid in ids:
            if mid in seen:
                continue
            if _is_alias(mid):
                filtered += 1
                continue
            seen.add(mid)
            data.append(
                {
                    "id": mid,
                    "name": mid,
                    "object": "model",
                    "created": now,
                    "owned_by": u.name,
                    "upstream": u.name,
                    "installed": False,
                    # Upstream-only rows have no local path → "pulled"
                    # by the path-shape rule (issue #220). The
                    # blessed bucket is reserved for files actually
                    # laid out under the blessed recipe tree.
                    "ns": "pulled",
                }
            )
    return {"models": data, "count": len(data), "filtered_aliases": filtered}


@router.get("/catalogue")
async def list_catalogue() -> dict[str, Any]:
    """Curated catalogue split into pullable (HF) and upstream-routed entries."""
    pullable: list[dict[str, Any]] = []
    upstream: list[dict[str, Any]] = []
    for entry in CURATED:
        if isinstance(entry, CuratedModel):
            pullable.append(entry.model_dump(mode="json"))
        elif isinstance(entry, HaloaiModel):
            upstream.append(entry.model_dump(mode="json"))
    return {
        "pullable": pullable,
        "upstream": upstream,
        "counts": {
            "pullable": len(pullable),
            "upstream": len(upstream),
            "total": len(pullable) + len(upstream),
        },
    }


@router.post("/scan/preview")
async def scan_preview(request: Request) -> dict[str, Any]:
    """Walk the requested paths and return :class:`DetectionResult` rows.

    Inspection-only: no registry mutation, no event emission. The UI uses
    this to populate the "Scan directory" preview table where the user
    edits backends + capabilities + id before committing via POST /scan.

    Body::

        {
          "paths":     ["/abs/dir/or/file", ...],   # required
          "recursive": bool                          # default False
        }

    Files matching the configured ``[models].file_extensions`` are
    selected when walking directories. A path that is a file is detected
    directly regardless of extension.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    raw_paths = body.get("paths") or []
    if not isinstance(raw_paths, list) or not raw_paths:
        raise BadRequest("'paths' must be a non-empty list of absolute paths")
    recursive = bool(body.get("recursive", False))

    cfg = load_hal0_config()
    extensions = {e.lower() for e in cfg.models.file_extensions}

    preview: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for raw in raw_paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        root = Path(raw).expanduser()
        if not root.exists():
            continue
        candidates: list[Path] = []
        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            it = root.rglob("*") if recursive else root.iterdir()
            try:
                for p in it:
                    # Reuse the discovery skip rules so the preview list
                    # obeys the same filters as the on-disk auto-scan:
                    # mmproj sidecars, multi-file shards, hex blobs,
                    # HF/ComfyUI accessory dirs, etc.
                    if is_skippable(p):
                        continue
                    try:
                        if not p.is_file():
                            continue
                    except OSError:
                        continue
                    if p.suffix.lower() not in extensions:
                        continue
                    candidates.append(p)
            except OSError:
                continue
        for p in candidates:
            try:
                resolved = p.resolve()
            except OSError:
                resolved = p
            # NOTE: do NOT run is_skippable(resolved). HF cache always
            # resolves symlinks through `blobs/<hex>`, and the hex-stem +
            # blobs-dir checks would reject every snapshot symlink. We
            # trust the symlink's filename for naming; resolved is only
            # used for the in-this-scan dedup below.
            if resolved in seen:
                continue
            seen.add(resolved)
            result: DetectionResult = detect(p)
            try:
                size_bytes = resolved.stat().st_size
            except OSError:
                size_bytes = 0
            preview.append(
                {
                    "path": str(p),
                    "resolved_path": str(resolved),
                    "size_bytes": size_bytes,
                    "suggested_backends": list(result.suggested_backends),
                    "suggested_capabilities": list(result.suggested_capabilities),
                    "context_length": result.context_length,
                    "confidence": result.confidence,
                    "suggested_name": result.suggested_name,
                    "kind": result.kind,
                    "raw_hints": dict(result.raw_hints),
                }
            )

    # Dedup snapshots-of-the-same-model. HF cache keeps multiple
    # `snapshots/<rev>/` directories; the resolved-path dedup catches
    # files that share a blob but misses files that share suggested_name +
    # size + kind across reblobbed snapshots. Keep the first occurrence
    # (matches the rglob walk order — usually the most recent rev first
    # in inode order, but we surface every distinct content via
    # resolved-path dedup above, so this is purely repo-level grouping).
    unique: list[dict[str, Any]] = []
    by_sig: set[tuple[str | None, int, str]] = set()
    for row in preview:
        sig = (
            (row["suggested_name"] or "").strip().lower() or row["path"],
            int(row.get("size_bytes") or 0),
            row.get("kind", "unknown"),
        )
        if sig in by_sig:
            continue
        by_sig.add(sig)
        unique.append(row)
    preview = unique

    return {"preview": preview, "count": len(preview)}


@router.post("/scan")
async def scan_models(request: Request) -> dict[str, Any]:
    """Walk model roots and register new files — legacy auto-scan, or
    commit a user-edited preview when ``rows`` is supplied.

    Two body shapes:

    * **Legacy / empty body** — walk the configured ``[models].roots`` and
      auto-register every new candidate via the discover module. Each
      added model fires a ``model.registered`` event with
      ``source='scan'``.

    * **``{"rows": [...]}``** — commit pre-vetted preview rows. Each row
      may carry user-edited ``backends`` / ``capabilities`` / ``defaults``
      / ``name`` / ``id`` overrides; otherwise we fall back to the
      detection output for that path. User overrides always win — that's
      the whole point of the preview round-trip.

    Returns ``{added, skipped, scanned_roots}`` in both modes so the UI's
    toast-render path is unchanged.
    """
    registry = request.app.state.model_registry
    event_bus = getattr(request.app.state, "events", None)

    body: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        body = await request.json()
    rows = body.get("rows") if isinstance(body, dict) else None

    if isinstance(rows, list) and rows:
        result = await _commit_scan_rows(rows, registry, event_bus)
        return result

    cfg = load_hal0_config()
    result = scan_and_register(registry, cfg.models)
    if event_bus is not None:
        for mid in result.get("added", []):
            try:
                model = registry.get(mid)
            except Exception:
                continue
            await event_bus.emit(
                "model.registered",
                "info",
                f"model:{mid}",
                f"{mid}: registered (scan)",
                data={
                    "id": mid,
                    "backends": list(getattr(model, "backends", []) or []),
                    "capabilities": list(getattr(model, "capabilities", []) or []),
                    "source": "scan",
                },
            )
    return result


async def _commit_scan_rows(
    rows: list[Any],
    registry: Any,
    event_bus: Any | None,
) -> dict[str, Any]:
    """Persist user-edited preview rows into the registry.

    Each row is a dict with at least ``path``. Optional fields override
    detection: ``id``, ``name``, ``backends``, ``capabilities``,
    ``defaults`` (nested ``ModelDefaults`` shape). Missing fields are
    backfilled by re-running ``detect()`` on the path so the operator can
    edit only what matters and still get high-confidence defaults for the
    rest.
    """
    from hal0.registry.model import Model, ModelDefaults
    from hal0.registry.store import ModelAlreadyExists

    added: list[str] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            skipped.append({"path": "", "reason": "row_not_an_object"})
            continue
        raw_path = row.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            skipped.append({"path": "", "reason": "missing_path"})
            continue
        path = Path(raw_path).expanduser()
        try:
            resolved = path.resolve() if path.exists() else path
        except OSError:
            resolved = path

        detection = detect(resolved)
        backends = row.get("backends")
        capabilities = row.get("capabilities")
        if not isinstance(backends, list):
            backends = list(detection.suggested_backends)
        if not isinstance(capabilities, list):
            capabilities = list(detection.suggested_capabilities)

        suggested_id = row.get("id") or _suggest_id_from_path(resolved)
        name = row.get("name") or resolved.stem

        defaults_payload = row.get("defaults")
        defaults_obj: ModelDefaults | None = None
        if isinstance(defaults_payload, dict) and defaults_payload:
            try:
                defaults_obj = ModelDefaults.model_validate(defaults_payload)
            except Exception as exc:
                skipped.append({"path": str(resolved), "reason": f"invalid_defaults:{exc}"})
                continue

        size_bytes = 0
        with contextlib.suppress(OSError):
            size_bytes = resolved.stat().st_size

        metadata: dict[str, Any] = {"discovered": True, "source": "scan"}
        if detection.context_length is not None:
            metadata["context_length"] = detection.context_length

        try:
            model = Model(
                id=str(suggested_id),
                name=str(name),
                path=str(resolved),
                size_bytes=size_bytes,
                capabilities=[str(c) for c in capabilities],
                backends=[str(b) for b in backends],
                defaults=defaults_obj,
                metadata=metadata,
            )
        except (TypeError, ValueError) as exc:
            skipped.append({"path": str(resolved), "reason": f"invalid_model:{exc}"})
            continue

        try:
            registry.add(model)
        except ModelAlreadyExists:
            skipped.append({"path": str(resolved), "reason": "already_registered"})
            continue
        added.append(model.id)
        if event_bus is not None:
            await event_bus.emit(
                "model.registered",
                "info",
                f"model:{model.id}",
                f"{model.id}: registered (scan)",
                data={
                    "id": model.id,
                    "backends": list(model.backends),
                    "capabilities": list(model.capabilities),
                    "source": "scan",
                },
            )

    return {
        "added": added,
        "skipped": skipped,
        "scanned_roots": [],
    }


def _suggest_id_from_path(p: Path) -> str:
    """Derive a registry-friendly id from a file path.

    Re-uses :func:`hal0.registry.discover._normalise_id` so single-file
    register and full-root scan land on the same id for the same file.
    """
    from hal0.registry.discover import _normalise_id

    return _normalise_id(p.stem)


@router.post("/add-from-path", status_code=201)
async def add_model_from_path(request: Request) -> dict[str, Any]:
    """Register a single already-downloaded model file by absolute path.

    Convenience wrapper around ``detect()`` + ``ModelRegistry.add()``
    aimed at the dashboard's "Add by path" flow — the operator points at
    one file, we read its header (or fall back to filename heuristic),
    derive id + capabilities + backends, then write the entry. No
    network, no copy — the file stays where it lives.

    Body::

        {
          "path":      "/abs/path/to/model.gguf",  # required
          "id":        "optional explicit registry id",
          "name":      "optional display name",
          "labels":    ["llm", "chat", ...],       # optional capabilities override
          "overwrite": false                       # default false
        }

    Errors:
      * ``400 validation.invalid`` — body shape wrong.
      * ``400 model.path_missing`` — file does not exist or is not readable.
      * ``400 model.unsupported_format`` — extension not in the registry's
        ``[models].file_extensions`` allow-list.
      * ``409 model.already_exists`` — id already registered and
        ``overwrite=false``.

    The file must be readable by the hal0-api process; we do **not**
    `chown` or copy.  When the file lives under a scan root pinned in
    ``[models].roots`` we trust the operator owns the path; when it's
    elsewhere we still allow it (the operator can point anywhere they
    have read access to).
    """
    from hal0.registry.detect import detect
    from hal0.registry.discover import _normalise_id
    from hal0.registry.model import Model
    from hal0.registry.store import ModelAlreadyExists

    registry = request.app.state.model_registry
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    raw_path = body.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise BadRequest("'path' must be a non-empty absolute path string")

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise BadRequest(
            f"'path' must be absolute (got {raw_path!r})",
            code="model.path_relative",
        )
    if not path.exists() or not path.is_file():
        raise BadRequest(
            f"path {str(path)!r} is not a readable file",
            code="model.path_missing",
            details={"path": str(path)},
        )
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path

    # Enforce the same extension allow-list the scan walker uses so
    # accidentally pointing at a tokenizer.json or a README.md fails
    # loudly rather than landing in the registry.
    cfg = load_hal0_config()
    allowed_exts = {e.lower() for e in cfg.models.file_extensions}
    if resolved.suffix.lower() not in allowed_exts:
        raise BadRequest(
            f"file extension {resolved.suffix!r} not in [models].file_extensions",
            code="model.unsupported_format",
            details={"path": str(resolved), "allowed": sorted(allowed_exts)},
        )

    detection = detect(resolved)
    raw_labels = body.get("labels")
    if isinstance(raw_labels, list) and raw_labels:
        capabilities = [str(c) for c in raw_labels if isinstance(c, str) and c.strip()]
    else:
        capabilities = list(detection.suggested_capabilities) or ["chat"]

    raw_id = body.get("id")
    if isinstance(raw_id, str) and raw_id.strip():
        model_id = raw_id.strip()
    else:
        # Prefer the detector's suggested_name (post-GGUF arch+param sniff)
        # falling back to the slug of the stem so two paths to the same
        # file land on the same id as the auto-scan would.
        model_id = _normalise_id(detection.suggested_name or resolved.stem)

    raw_name = body.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        display_name = raw_name.strip()
    else:
        display_name = detection.suggested_name or resolved.stem

    overwrite = bool(body.get("overwrite", False))

    try:
        size_bytes = resolved.stat().st_size
    except OSError:
        size_bytes = 0

    metadata: dict[str, Any] = {"discovered": True, "source": "add-from-path"}
    if detection.context_length is not None:
        metadata["context_length"] = detection.context_length

    try:
        model = Model(
            id=model_id,
            name=display_name,
            path=str(resolved),
            size_bytes=size_bytes,
            capabilities=capabilities,
            backends=list(detection.suggested_backends),
            metadata=metadata,
        )
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"invalid Model payload: {exc}") from exc

    if overwrite and registry.has(model_id):
        registry.remove(model_id)

    try:
        registry.add(model)
    except ModelAlreadyExists as exc:
        # Convert to the structured envelope shape (409) so the UI can
        # branch on the code rather than the message text.
        raise exc

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "model.registered",
            "info",
            f"model:{model.id}",
            f"{model.id}: registered (add-from-path)",
            data={
                "id": model.id,
                "backends": list(model.backends),
                "capabilities": list(model.capabilities),
                "source": "add-from-path",
            },
        )
    return _model_to_dict(model)


@router.post("", status_code=201)
async def create_model(request: Request) -> dict[str, Any]:
    """Register a new model in the local ModelRegistry.

    Body shape: serialized ``Model`` — see ``hal0.registry.store.Model``.
    The model must already exist on disk (e.g. dropped into
    ``/var/lib/hal0/models/``) — this endpoint records metadata, it does
    not download. Use POST /api/models/{id}/pull for downloads.

    Optional ``source`` (top-level, not part of ``Model``) tags the
    emitted ``model.registered`` event so the footer can colour-code
    catalogue picks vs hand-registered files. Defaults to ``"manual"``.
    """
    from hal0.registry.store import Model

    registry = request.app.state.model_registry
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    # Pop ``source`` before validation — it's an event-only tag, not a
    # Model field. Default to "manual" for hand-registered single files.
    source = body.pop("source", "manual")
    try:
        model = Model(**body)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"invalid Model payload: {exc}") from exc
    registry.add(model)

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "model.registered",
            "info",
            f"model:{model.id}",
            f"{model.id}: registered ({source})",
            data={
                "id": model.id,
                "backends": list(model.backends),
                "capabilities": list(model.capabilities),
                "source": str(source),
            },
        )
    return _model_to_dict(model)


def _model_to_dict(model: Any) -> dict[str, Any]:
    """Serialise a registry Model to the dashboard's flat shape.

    Always attaches the ``ns`` ("blessed" | "pulled") namespace bucket
    so the dashboard's Models view can group rows without re-deriving
    it client-side. The rule is path-shape only (see :func:`_derive_ns`
    + issue #220).
    """
    if hasattr(model, "model_dump"):
        dumped = model.model_dump(mode="json")
    else:
        dumped = {**getattr(model, "__dict__", {})}
    # Only registry-backed Model instances have a ``path``; the upstream
    # rows assembled in :func:`list_models` already set ``ns`` directly.
    if "ns" not in dumped and hasattr(model, "path"):
        try:
            dumped["ns"] = _derive_ns(model)
        except Exception:
            dumped["ns"] = "pulled"
    return dumped


@router.get("/{model_id}")
async def get_model(model_id: str, request: Request) -> dict[str, Any]:
    """Return a single model by id, preferring the local registry then
    falling back to whichever upstream advertises it."""
    registry = request.app.state.model_registry
    if registry.has(model_id):
        return _model_to_dict(registry.get(model_id))
    listing = await list_models(request)
    for m in listing["models"]:
        if m["id"] == model_id:
            return m
    raise NotFound(
        f"model {model_id!r} not found in registry or any upstream catalog",
        details={"model_id": model_id},
        code="model.not_found",
    )


@router.put("/{model_id}")
async def update_model(model_id: str, request: Request) -> dict[str, Any]:
    """Apply partial updates to a registered model's metadata.

    Body accepts any subset of: ``name``, ``capabilities``, ``backends``,
    ``defaults`` (nested ``ModelDefaults``), plus the legacy fields
    (``license``, ``tags``, ``metadata`` …). Emits ``model.updated`` with
    ``changed_fields`` so the footer ticker can render a "you edited X"
    chip.
    """
    registry = request.app.state.model_registry
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    # Snapshot the pre-update model so we can diff the field set the
    # client actually changed (vs the wire-format keys, which may include
    # unchanged values). Without this the footer's "changed X, Y" toast
    # would lie whenever the UI sends the full row.
    try:
        before = registry.get(model_id).model_dump(mode="python")
    except Exception:
        before = {}

    model = registry.update(model_id, body)

    after = model.model_dump(mode="python")
    changed: list[str] = []
    for key in body:
        if key == "id":
            continue
        if before.get(key) != after.get(key):
            changed.append(key)

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "model.updated",
            "info",
            f"model:{model.id}",
            f"{model.id}: updated ({', '.join(changed) or 'no-op'})",
            data={"id": model.id, "changed_fields": changed},
        )
    return _model_to_dict(model)


# ── DELETE + cascade helpers ───────────────────────────────────────────────


def _slots_referencing_model(request: Request, model_id: str) -> list[dict[str, Any]]:
    """Return slot configs (as raw dicts) whose ``[model].default`` is ``model_id``.

    Reads slot TOMLs directly so the cascade also catches slots whose
    SlotManager hasn't been touched this process — the source of truth is
    the TOML on disk. Each returned dict carries at minimum ``name`` +
    the parsed config body (used by callers to clear the default field).
    """
    import tomllib

    from hal0.config import paths as cfg_paths

    cfg_dir = cfg_paths.slots_config_dir()
    if not cfg_dir.exists():
        return []
    affected: list[dict[str, Any]] = []
    for p in sorted(cfg_dir.glob("*.toml")):
        if p.name.startswith("."):
            continue
        try:
            with open(p, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        model_sect = data.get("model")
        default = ""
        if isinstance(model_sect, dict):
            default = str(model_sect.get("default") or "")
        if default != model_id:
            continue
        name = data.get("name") or p.stem
        affected.append({"name": str(name), "path": str(p), "config": data})
    return affected


def _clear_slot_default(slot_path: Path, slot_cfg: dict[str, Any]) -> None:
    """Rewrite a slot TOML with ``[model].default = ""`` cleared in-place.

    Best-effort: a write failure logs a warning but does not abort the
    cascade — the model row is already going away, and a slot left
    pointing at a vanished id will surface as ``model.not_found`` on its
    next ``load()`` (which is the correct UX).
    """
    import tomli_w

    new_cfg = dict(slot_cfg)
    model_sect = new_cfg.get("model")
    if isinstance(model_sect, dict):
        new_model = dict(model_sect)
        new_model["default"] = ""
        new_cfg["model"] = new_model
    # Cascade continues if the write fails; the dangling reference is
    # surfaced later.
    with contextlib.suppress(OSError):
        slot_path.write_bytes(tomli_w.dumps(new_cfg).encode("utf-8"))


async def _unload_slot_if_running(request: Request, slot_name: str) -> None:
    """Best-effort unload of a referencing slot.

    Imports the SlotManager off ``app.state`` (lifespan-wired) and asks
    it to unload, catching every failure so one stuck slot can't block
    the cascade. The SlotManager itself emits ``slot.state`` events for
    each transition — we rely on that instead of duplicating the emit
    here, which keeps ``slot.state`` ordering authoritative.
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return
    try:
        snap = await sm.status(slot_name)
    except Exception:
        return
    if snap.state.value == "offline":
        return
    with contextlib.suppress(Exception):
        await sm.unload(slot_name)


@router.delete("/{model_id}")
async def delete_model(
    model_id: str,
    request: Request,
    force_cascade: bool = True,
) -> dict[str, object]:
    """Remove a model from the registry, cascading through referencing slots.

    Query param ``force_cascade`` (default ``true``) controls behaviour
    when at least one slot has this model as its ``[model].default``:

    * ``force_cascade=true`` (default): unload every running referrer,
      clear ``[model].default = ""`` in each slot's TOML, delete the
      registry row, then emit ``model.deleted`` *last* — so subscribers
      see the slot transitions before the model disappears.
    * ``force_cascade=false``: return 409 with the ``affected_slots`` list
      so the UI can render a confirm-cascade dialog.

    The actual model file on disk is never touched — that's the
    operator's call. Registry rows hold metadata, not bytes.
    """
    registry = request.app.state.model_registry
    if not registry.has(model_id):
        # Mirror the registry's typed envelope. We don't raise ModelNotFound
        # directly to avoid importing it at module scope; the registry's
        # remove() returns False silently, so we need an explicit guard.
        from hal0.registry.store import ModelNotFound

        raise ModelNotFound(
            f"model {model_id!r} not in registry",
            details={"model_id": model_id},
        )

    affected = _slots_referencing_model(request, model_id)
    affected_names = [entry["name"] for entry in affected]

    if affected and not force_cascade:
        from hal0.errors import Conflict

        raise Conflict(
            f"model {model_id!r} is referenced by {len(affected)} slot(s); "
            f"retry with force_cascade=true to cascade",
            code="model.in_use",
            details={"model_id": model_id, "affected_slots": affected_names},
        )

    # Cascade order is load-bearing for the footer's ticker UX:
    #   1. unload running referrers (each fires slot.state)
    #   2. clear [model].default in slot TOMLs
    #   3. registry delete
    #   4. emit model.deleted LAST
    for entry in affected:
        await _unload_slot_if_running(request, entry["name"])
    for entry in affected:
        _clear_slot_default(Path(entry["path"]), entry["config"])

    removed = registry.remove(model_id)

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "model.deleted",
            "info",
            f"model:{model_id}",
            f"{model_id}: deleted"
            + (f" (cascaded {len(affected_names)} slot(s))" if affected_names else ""),
            data={"id": model_id, "affected_slots": affected_names},
        )
    return {
        "id": model_id,
        "deleted": bool(removed),
        "affected_slots": affected_names,
    }


def _resolve_pull_source(request: Request, model_id: str) -> tuple[str, str]:
    """Resolve the (hf_repo, hf_file) tuple for a pull.

    Priority:
      1. The registry entry's ``hf_repo`` + ``hf_filename`` (set by
         ``pick-default`` when the curated catalogue is the source).
      2. The curated catalogue entry for ``model_id``.

    Raises ``PullInvalidSource`` (422) when neither path yields a repo
    + filename — typically because the caller hand-registered a model
    and never set its HF coordinates.
    """
    registry = request.app.state.model_registry
    try:
        existing = registry.get(model_id)
        repo = (existing.hf_repo or "").strip()
        filename = (existing.hf_filename or "").strip()
        if repo and filename:
            return repo, filename
    except Exception:
        pass
    curated = get_curated(model_id)
    if curated is not None:
        return curated.hf_repo, curated.hf_file
    raise PullInvalidSource(
        f"no hugging face source for model {model_id!r} — set hf_repo + hf_filename"
        " on the registry entry or pick a curated model id",
        details={"model_id": model_id},
    )


async def _run_pull_with_events(
    job: PullJob,
    *,
    hf_repo: str,
    hf_file: str,
    registry: Any,
    hf_token: str | None,
    event_bus: Any | None,
) -> None:
    """Wrap ``run_pull`` so footer-visible progress events fan out.

    Emits ``pull.progress`` at each 10% decile (computed lazily — we
    snapshot the deciles already reached on the job's ``_last_decile``
    attr) plus terminal events on success / failure / cancellation. The
    HF download itself is untouched; we listen to the same progress
    signal SSE listens to so the byte counts stay authoritative.
    """
    if event_bus is None:
        await run_pull(job, hf_repo=hf_repo, hf_file=hf_file, registry=registry, hf_token=hf_token)
        return

    async def _emit_progress() -> None:
        last_decile: int = getattr(job, "_last_pull_decile", -1)
        while job.state in ("queued", "running"):
            event = job.progress_event
            try:
                await asyncio.wait_for(event.wait(), timeout=2.0)
            except TimeoutError:
                continue
            if job.bytes_total > 0:
                pct = int((job.bytes_downloaded / job.bytes_total) * 100)
                decile = pct // 10
                if decile > last_decile and decile >= 1:
                    last_decile = decile
                    job._last_pull_decile = last_decile
                    speed = _speed_bps(job)
                    eta = _eta_s(job, speed)
                    await event_bus.emit(
                        "pull.progress",
                        "info",
                        f"pull:{job.model_id}",
                        f"{job.model_id}: {decile * 10}%",
                        data={
                            "model_id": job.model_id,
                            "downloaded": job.bytes_downloaded,
                            "total": job.bytes_total,
                            "pct": decile * 10,
                            "speed_bps": speed,
                            "eta_s": eta,
                        },
                    )

    progress_task = asyncio.create_task(_emit_progress())
    try:
        await run_pull(job, hf_repo=hf_repo, hf_file=hf_file, registry=registry, hf_token=hf_token)
    finally:
        progress_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await progress_task

    await _emit_terminal_pull_event(event_bus, job)


async def _emit_terminal_pull_event(event_bus: Any, job: PullJob) -> None:
    """Emit the success/failure/cancellation footer event for a pull.

    Shared between the HF pull wrapper and the FLM pull background task
    so both surfaces produce the same dashboard footer events.
    """
    if event_bus is None:
        return
    if job.state == "completed":
        await event_bus.emit(
            "pull.completed",
            "info",
            f"pull:{job.model_id}",
            f"{job.model_id}: download complete",
            data={
                "model_id": job.model_id,
                "downloaded": job.bytes_downloaded,
                "total": job.bytes_total,
                "sha256": job.sha256,
                "path": job.path,
            },
        )
    elif job.state == "failed":
        await event_bus.emit(
            "pull.failed",
            "error",
            f"pull:{job.model_id}",
            f"{job.model_id}: {job.error or 'pull failed'}",
            data={
                "model_id": job.model_id,
                "downloaded": job.bytes_downloaded,
                "total": job.bytes_total,
                "error": job.error,
                "error_code": job.error_code,
            },
        )
    elif job.state == "cancelled":
        await event_bus.emit(
            "pull.cancelled",
            "warn",
            f"pull:{job.model_id}",
            f"{job.model_id}: pull cancelled",
            data={
                "model_id": job.model_id,
                "downloaded": job.bytes_downloaded,
                "total": job.bytes_total,
            },
        )


def _speed_bps(job: PullJob) -> float:
    """Approximate average bytes/s since the job started."""
    elapsed = max(time.time() - (job.started_at or time.time()), 0.001)
    return job.bytes_downloaded / elapsed


def _eta_s(job: PullJob, speed_bps: float) -> float | None:
    """Estimate seconds-to-completion from current rolling speed."""
    if speed_bps <= 0 or job.bytes_total <= 0:
        return None
    remaining = max(job.bytes_total - job.bytes_downloaded, 0)
    return remaining / speed_bps


@router.post("/{model_id}/pull", status_code=202)
async def pull_model(
    model_id: str,
    request: Request,
    background: BackgroundTasks,
) -> dict[str, object]:
    """Start a background HuggingFace pull and return a job handle.

    Idempotent-ish: if a pull for this model_id is already in
    ``queued``/``running`` state, the existing job's handle is returned
    rather than spawning a duplicate. A completed/failed/cancelled job
    is replaced.
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs

    # Don't double-pull. A user spamming the wizard's Download button
    # shouldn't kick off two streams against the same HF URL.
    existing = jobs.get(model_id)
    if existing is not None and existing.state in ("queued", "running"):
        return {
            "id": existing.job_id,
            "model_id": model_id,
            "state": existing.state,
            "resumed": True,
        }

    # FLM/NPU tags route through the toolbox container instead of HF.
    # The ``model:tag`` shape is the dispatch signal (HF ids never use
    # colons), validated against the FLM probe so a stray ``foo:bar``
    # falls through to the HF resolver and gets a clean 422.
    from hal0.providers.flm import is_flm_tag

    if is_flm_tag(model_id):
        return await _start_flm_pull(model_id, request, background, jobs)

    hf_repo, hf_file = _resolve_pull_source(request, model_id)
    job = make_job(model_id)
    jobs[model_id] = job

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "pull.queued",
            "info",
            f"pull:{model_id}",
            f"{model_id}: queued ({hf_repo}/{hf_file})",
            data={"model_id": model_id, "hf_repo": hf_repo, "hf_file": hf_file},
        )

    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    registry = request.app.state.model_registry
    background.add_task(
        _run_pull_with_events,
        job,
        hf_repo=hf_repo,
        hf_file=hf_file,
        registry=registry,
        hf_token=hf_token,
        event_bus=event_bus,
    )
    return {
        "id": job.job_id,
        "model_id": model_id,
        "state": job.state,
        "hf_repo": hf_repo,
        "hf_file": hf_file,
    }


async def _start_flm_pull(
    model_id: str,
    request: Request,
    background: BackgroundTasks,
    jobs: dict[str, PullJob],
) -> dict[str, object]:
    """Spawn a background ``flm pull`` job and return the job handle.

    Shares the PullJob/SSE plumbing with the HF pull path so the
    dashboard's pull progress UI works unchanged. The HF-specific
    progress decile + speed/ETA wrapper (:func:`_run_pull_with_events`)
    isn't reused here because its progress event payload assumes byte
    deltas from a single HTTP stream — FLM's container emits multiple
    files with its own progress lines and we don't want to misreport
    rate. Footer events are emitted directly around the run.
    """
    job = make_job(model_id)
    jobs[model_id] = job
    registry = request.app.state.model_registry
    event_bus = getattr(request.app.state, "events", None)

    if event_bus is not None:
        await event_bus.emit(
            "pull.queued",
            "info",
            f"pull:{model_id}",
            f"{model_id}: queued (FLM/NPU)",
            data={"model_id": model_id, "source": "flm"},
        )

    async def _run_flm_with_events() -> None:
        try:
            await run_flm_pull(job, tag=model_id, registry=registry)
        finally:
            if event_bus is not None:
                await _emit_terminal_pull_event(event_bus, job)

    background.add_task(_run_flm_with_events)
    return {
        "id": job.job_id,
        "model_id": model_id,
        "state": job.state,
        "source": "flm",
    }


@router.get("/{model_id}/pull/status")
async def pull_status(model_id: str, request: Request) -> dict[str, object]:
    """Return the current pull job for ``model_id``.

    Mirror of the updater route shape — `id`, `state`, `bytes_*`,
    `error*`, `path`, `sha256`. Polling at ~500ms is fine; for live
    progress prefer the SSE stream.
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs
    job = jobs.get(model_id)
    if job is None:
        raise PullJobNotFound(
            f"no pull job for model {model_id!r}",
            details={"model_id": model_id},
        )
    return job.as_dict()


@router.get("/{model_id}/pull/stream")
async def pull_stream(model_id: str, request: Request) -> StreamingResponse:
    """SSE stream of pull progress.

    Emits one ``data:`` frame at start, then one per ~256 KiB or every
    500ms (whichever is rarer), and a final frame on completion
    /failure/cancellation. Idempotent: subscribing after the job has
    finished yields one frame with the terminal state and closes.
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs
    job = jobs.get(model_id)
    if job is None:
        raise PullJobNotFound(
            f"no pull job for model {model_id!r}",
            details={"model_id": model_id},
        )

    async def _gen() -> Any:
        # Emit an immediate snapshot so SSE clients don't sit at zero
        # while waiting for the first progress signal.
        yield f"data: {json.dumps(job.as_dict())}\n\n"
        while job.state in ("queued", "running"):
            event = job.progress_event
            try:
                await asyncio.wait_for(event.wait(), timeout=5.0)
            except TimeoutError:
                # Keep-alive — surfaces stuck downloads without closing
                # the stream.
                yield f"data: {json.dumps(job.as_dict())}\n\n"
                continue
            yield f"data: {json.dumps(job.as_dict())}\n\n"
        # One terminal frame so the UI sees the final state and can close.
        yield f"data: {json.dumps(job.as_dict())}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── HuggingFace inspect (POST /api/models/inspect) ────────────────────────────


# In-process TTL cache keyed by normalised HF repo id. Storing the whole
# response shape (variants + tags + metadata) keeps repeat Inspect clicks
# on the same modal session free; the 5 minute TTL is short enough that
# a freshly-uploaded quant lands within one render.
_INSPECT_TTL_SECONDS = 300
_INSPECT_TIMEOUT_SECONDS = 8.0
_INSPECT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_INSPECT_GGUF_SUFFIX = ".gguf"


class _HFUpstreamError(Hal0Error):
    """502 — fetching huggingface.co failed (network, 5xx, or unparseable)."""

    code = "hf.unreachable"
    status = 502


def _normalise_hf_repo(value: str) -> str:
    """Reduce a HF repo input to ``org/name``.

    Accepts the canonical ``org/name`` slug and a full
    ``https://huggingface.co/org/name[/...]`` URL — both are surfaced
    in the dashboard's Add-by-HF modal. Trims trailing slashes and
    drops the ``/tree/<rev>`` / ``/blob/<rev>/...`` suffixes that the
    HF UI tends to copy along with the slug.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    # Strip protocol + host so we can normalise URL + slug uniformly.
    for prefix in ("https://huggingface.co/", "http://huggingface.co/", "huggingface.co/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    raw = raw.strip("/")
    # Drop /tree/<rev> or /blob/<rev>/<path> if the user pasted a deep link.
    parts = raw.split("/")
    repo = f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else raw
    return repo


def _extract_readme_excerpt(card_data: Any, limit: int = 400) -> str:
    """Pull a short README excerpt from the HF model API payload.

    HF returns the model card body under different shapes depending on
    the endpoint. ``cardData`` carries YAML frontmatter; the actual
    README body comes back under ``description`` or ``card``. Use
    whatever is present and truncate hard so the modal stays light.
    """
    candidates: list[str] = []
    if isinstance(card_data, dict):
        for key in ("description", "card", "readme"):
            v = card_data.get(key)
            if isinstance(v, str) and v.strip():
                candidates.append(v.strip())
    if not candidates:
        return ""
    text = candidates[0]
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _format_size(size_bytes: int | None) -> str:
    """Format bytes as a short human label used in the variant dropdown."""
    if not isinstance(size_bytes, int) or size_bytes <= 0:
        return "—"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:.1f} MB"
    return f"{size_bytes / 1024**3:.2f} GB"


async def _fetch_hf_repo(repo: str) -> dict[str, Any]:
    """Fetch HF model metadata + tree listing for ``repo``.

    Returns the shape consumed by :func:`inspect_model`. Raises a
    typed :class:`hal0.errors.Hal0Error` subclass on transport failure
    or 404 so the route maps it to the dashboard envelope.
    """
    headers = {"Accept": "application/json"}
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    meta_url = f"https://huggingface.co/api/models/{repo}"
    tree_url = f"https://huggingface.co/api/models/{repo}/tree/main"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_INSPECT_TIMEOUT_SECONDS),
            follow_redirects=True,
            headers=headers,
        ) as client:
            meta_res, tree_res = await asyncio.gather(
                client.get(meta_url),
                client.get(tree_url),
            )
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        raise _HFUpstreamError(
            f"failed to reach huggingface.co for {repo!r}: {exc.__class__.__name__}",
            code="hf.unreachable",
            details={"repo": repo, "error": str(exc)},
        ) from exc

    if meta_res.status_code == 404:
        raise NotFound(
            f"hugging face repo {repo!r} not found",
            code="hf.repo_not_found",
            details={"repo": repo},
        )
    if meta_res.status_code >= 400:
        raise _HFUpstreamError(
            f"hugging face metadata fetch returned {meta_res.status_code}",
            code="hf.upstream_error",
            details={"repo": repo, "status": meta_res.status_code},
        )
    if tree_res.status_code >= 400:
        # A missing tree (private repo, gated, etc.) is recoverable —
        # we still surface tags + metadata, just with no variants.
        tree_payload: list[Any] = []
    else:
        try:
            tree_payload = tree_res.json() or []
        except ValueError:
            tree_payload = []

    try:
        meta_payload = meta_res.json() or {}
    except ValueError:
        meta_payload = {}

    variants: list[dict[str, Any]] = []
    for entry in tree_payload:
        if not isinstance(entry, dict):
            continue
        rel = entry.get("path") or entry.get("rfilename")
        if not isinstance(rel, str):
            continue
        if not rel.lower().endswith(_INSPECT_GGUF_SUFFIX):
            continue
        # HF's tree API reports the *pointer file* size in ``size`` for
        # LFS objects; the real bytes live under ``lfs.size``. Prefer
        # the LFS size when present so the modal shows the real
        # download size, not the 100-byte pointer.
        size_raw: Any = None
        lfs = entry.get("lfs")
        if isinstance(lfs, dict):
            size_raw = lfs.get("size")
        if size_raw is None:
            size_raw = entry.get("size")
        try:
            size_bytes = int(size_raw) if size_raw is not None else 0
        except (TypeError, ValueError):
            size_bytes = 0
        # Use the GGUF filename as the canonical variant id — that's
        # also what the pull endpoint resolves against (hf_filename).
        variants.append(
            {
                "id": rel,
                "size_bytes": size_bytes,
                "size": _format_size(size_bytes),
                "info": _format_size(size_bytes) + " · single file",
            }
        )
    variants.sort(key=lambda v: v.get("size_bytes") or 0)

    tags_raw = meta_payload.get("tags") or []
    tags = [t for t in tags_raw if isinstance(t, str)]

    license_label = ""
    card = meta_payload.get("cardData")
    if isinstance(card, dict):
        lic = card.get("license")
        if isinstance(lic, str):
            license_label = lic
    if not license_label:
        # Fallback: HF exposes the top-level "license" sometimes.
        lic = meta_payload.get("license")
        if isinstance(lic, str):
            license_label = lic

    return {
        "variants": variants,
        "tags": tags,
        "metadata": {
            "license": license_label,
            "readme_excerpt": _extract_readme_excerpt(card),
        },
    }


@router.post("/inspect")
async def inspect_model(request: Request) -> dict[str, Any]:
    """Inspect a HuggingFace repo and return pullable variants + metadata.

    Body shape (either key accepted, ``hf_url`` is the dashboard's older
    alias)::

        {"hf_repo": "unsloth/Qwen3-8B-GGUF"}
        {"hf_url":  "https://huggingface.co/unsloth/Qwen3-8B-GGUF"}

    Response::

        {
          "repo": "...",
          "variants": [{"id": "qwen3-8b-q4_k_m.gguf", "size_bytes": ..., "info": "..."}],
          "tags": ["text-generation", ...],
          "metadata": {"license": "...", "readme_excerpt": "..."}
        }

    Cached for ~5 minutes per repo. HF unreachable / 5xx → ``502``
    with ``hf.unreachable`` / ``hf.upstream_error``. Repo missing →
    ``404`` with ``hf.repo_not_found``.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    repo_input = body.get("hf_repo")
    if not isinstance(repo_input, str) or not repo_input.strip():
        repo_input = body.get("hf_url")
    if not isinstance(repo_input, str) or not repo_input.strip():
        raise BadRequest(
            "either 'hf_repo' (org/name) or 'hf_url' is required",
            code="hf.bad_request",
        )

    repo = _normalise_hf_repo(repo_input)
    if "/" not in repo:
        raise BadRequest(
            f"'{repo_input}' is not a valid org/name HF repo coordinate",
            code="hf.bad_request",
            details={"input": repo_input},
        )

    now = time.time()
    cached = _INSPECT_CACHE.get(repo)
    if cached is not None and now - cached[0] < _INSPECT_TTL_SECONDS:
        payload = dict(cached[1])
        payload["repo"] = repo
        payload["cached"] = True
        return payload

    result = await _fetch_hf_repo(repo)
    _INSPECT_CACHE[repo] = (now, result)
    return {"repo": repo, "cached": False, **result}


@router.post("/{model_id}/pull/cancel")
async def pull_cancel(model_id: str, request: Request) -> dict[str, object]:
    """Request cancellation of an in-flight pull.

    Sets a cancel flag the background task observes on the next chunk
    boundary; the partial download is unlinked, the job transitions to
    ``cancelled``. Idempotent — cancelling a completed job is a no-op.
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs
    job = jobs.get(model_id)
    if job is None:
        raise PullJobNotFound(
            f"no pull job for model {model_id!r}",
            details={"model_id": model_id},
        )
    if job.state in ("queued", "running"):
        job.cancel_requested = True
    return job.as_dict()
