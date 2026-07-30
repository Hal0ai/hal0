"""Business logic behind the ``/api/models`` route surface (P3-routers §J).

The route handlers in ``hal0.api.routes.models`` are request→service→envelope
shells: they decode the request, call one function here, and render the result.
Everything that is *not* request/response plumbing — registry classification,
row serialisation, directory scanning, and the slot-cascade on delete — lives
in this module so it can be unit-tested without a live FastAPI app and reused
by non-route callers (CLI, bundle-tier installs).

Scope note (P3-routers first pass): the HuggingFace *pull orchestration* block
(`_run_pull_with_events`, `_resolve_pull_source*`, `_schedule_pull_task`,
`_start_flm_pull`, the persisted-snapshot reconcile helpers) deliberately stays
in the route module this pass — it is monkeypatched by the pull test-suite and
is the largest/highest-risk extraction (tracked as ``registry/pull_jobs.py`` in
spec-p3-routers §5 step 9). This module owns the pure + self-contained slices
that land first.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from hal0.model_meta import classify
from hal0.registry.detect import DetectionResult, detect
from hal0.registry.model import _derive_ns
from hal0.upstreams.filters import apply_filters

log = logging.getLogger(__name__)

# ── model-id classification (pure) ────────────────────────────────────────────

# Known-alias model ids that upstream gateways advertise as routing
# shortcuts (haloai's hermes-proxy exposes them as "primary", "tiny",
# etc., plus haloai:* namespaced variants).  Filtered from the dashboard
# Models view because they're not real models — they're routes.
ALIAS_NAMES = frozenset(
    {
        "chat",
        "primary",  # back-compat alias
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


def is_alias(model_id: str) -> bool:
    """Filter out routing aliases that aren't real models."""
    if model_id.startswith("haloai:"):
        return True
    return model_id in ALIAS_NAMES


# FLM capability → dispatcher-vocab slot type. The NPU slot pickers
# (ui/dash/slots.jsx ``modelSlotType``) speak the dispatcher vocabulary
# (llm/embedding/transcription), NOT the W7 ``_CAPABILITY_TO_TYPE`` vocab, so
# probe-sourced FLM rows must carry these values to be selectable.
FLM_DISPATCH_TYPE: dict[str, str] = {
    "chat": "llm",
    "embed": "embedding",
    "stt": "transcription",
    "asr": "transcription",
    "rerank": "reranking",
    "tts": "tts",
    "image": "image",
}

# ``classify()`` returns a coarse MODALITY bucket (chat/embed/rerank/stt/tts/
# img — the ``_TYPE_PRIORITY`` vocab), but every ``type`` we emit on an
# /api/models row must speak the DISPATCHER vocabulary (llm/embedding/…), the
# same one FLM rows carry above and slots use — the UI joins models↔slots on
# ``model.type === slot.type`` (ui/lib/normalizeApiModel + slot pickers). Emit
# the modality bucket verbatim and the picker matches nothing ("chat" ≠ "llm"),
# collapsing every slot's model dropdown to just its current default. Map every
# classify() output through here; ``img`` (classify) → ``image`` (dispatcher).
MODALITY_TO_SLOT_TYPE: dict[str, str] = {
    "chat": "llm",
    "embed": "embedding",
    "rerank": "reranking",
    "stt": "transcription",
    "tts": "tts",
    "img": "image",
}


def dispatch_type(model_id: str = "", capabilities: Any = None) -> str:
    """Dispatcher-vocab slot type for a row (classify → dispatcher)."""
    modality = classify(model_id, capabilities=capabilities)
    return MODALITY_TO_SLOT_TYPE.get(modality, "llm")


# ── per-type default marker (single-holder chokepoint) ────────────────────────
#
# THE one place the "at most one default MODEL per type" invariant is enforced.
# Both the dedicated route (POST /api/models/{id}/default) and the slot-create
# wiring (routes/slots.create_slot, when the body carries ``default: true``)
# funnel through here — no demote logic is duplicated anywhere else. "type" is
# the dispatcher-vocab slot type (:func:`dispatch_type`) derived from the
# model's id + capabilities: the same axis the create modal surfaces as
# ``selModel.type``. This is the MODEL default; it is orthogonal to the SLOT
# default (SC-4 :func:`hal0.slots.config_write.check_default_uniqueness`).


def set_model_type_default(
    registry: Any,
    model_id: str,
    *,
    default: bool = True,
) -> dict[str, Any]:
    """Promote (``default=True``) or clear (``default=False``) a model's per-type default.

    Promote: mark ``model_id`` as its type's default, atomically demoting any
    OTHER model of the SAME type that currently holds the flag. Idempotent —
    re-promoting the current holder is a no-op that still demotes stray peers
    (self-healing if two ever slipped onto disk).

    Clear: drop ``model_id``'s flag without promoting anything; the type is
    left with NO default.

    Returns a summary ``{"model_id", "type", "default", "demoted": [...],
    "changed": bool}``. Raises ``ModelNotFound`` when ``model_id`` is absent
    (propagated from ``registry.get``).
    """
    target = registry.get(model_id)  # raises ModelNotFound
    target_type = dispatch_type(target.id, capabilities=target.capabilities)
    currently = bool(getattr(target, "default", False))
    demoted: list[str] = []

    if default:
        # Demote every same-type peer that still carries the flag. Runs even
        # when the target is already default so a duplicate holder is cleaned
        # up (single-holder is a hard invariant, not a hope).
        for m in registry.list():
            if m.id == model_id:
                continue
            if not bool(getattr(m, "default", False)):
                continue
            if dispatch_type(m.id, capabilities=m.capabilities) != target_type:
                continue
            registry.update(m.id, {"default": False})
            demoted.append(m.id)
        if not currently:
            registry.update(model_id, {"default": True})
        return {
            "model_id": model_id,
            "type": target_type,
            "default": True,
            "demoted": demoted,
            "changed": (not currently) or bool(demoted),
        }

    # Clear (unset) — no promotion.
    if currently:
        registry.update(model_id, {"default": False})
    return {
        "model_id": model_id,
        "type": target_type,
        "default": False,
        "demoted": [],
        "changed": currently,
    }


def comfyui_category(path: Any) -> str | None:
    """ComfyUI models subdir for a path under ``.../comfyui/models/<subdir>/``.

    Returns the subdir (``checkpoints`` / ``loras`` / ``vae`` / ``upscale_models``
    / …) that the dashboard's dedicated image-gen surface groups by, or ``None``
    for a non-ComfyUI path. Path-derived so a row mis-tagged by an older pull
    (``capabilities=["chat"]``, ``backends=[]``) is still recognised as ComfyUI
    without a data migration.
    """
    if not isinstance(path, str) or "/comfyui/models/" not in path:
        return None
    seg = path.split("/comfyui/models/", 1)[1].split("/", 1)[0].strip()
    return seg or None


# ── row serialisation (pure) ──────────────────────────────────────────────────


def model_to_dict(model: Any) -> dict[str, Any]:
    """Serialise a registry Model to the dashboard's flat shape.

    Always attaches the ``ns`` ("blessed" | "pulled") namespace bucket
    so the dashboard's Models view can group rows without re-deriving
    it client-side. The rule is path-shape only (see :func:`_derive_ns`
    + issue #220).

    Also attaches (WS-13):

    * ``origin`` — always ``"local"`` here: every row that flows through
      this serialiser is registry-backed (bytes or a registration on this
      host). The upstream-advertised rows assembled inline in
      ``list_models`` carry ``origin="upstream"`` so clients no longer
      infer remoteness from ``installed`` + ``upstream``.
    * ``quant`` — the stored ``Model.quant`` when present, else lazily
      derived from the filename (path basename, then ``hf_filename``) so
      registries written before the field existed surface quant without
      re-registration. Filename-only on this hot path — no header read.
    """
    if hasattr(model, "model_dump"):
        dumped = model.model_dump(mode="json")
    else:
        dumped = {**getattr(model, "__dict__", {})}
    # Only registry-backed Model instances have a ``path``; the upstream
    # rows assembled in ``list_models`` already set ``ns`` directly.
    if "ns" not in dumped and hasattr(model, "path"):
        try:
            dumped["ns"] = _derive_ns(model)
        except Exception:
            dumped["ns"] = "pulled"
    dumped.setdefault("origin", "local")
    if not dumped.get("quant"):
        quant = lazy_quant(dumped)
        if quant:
            dumped["quant"] = quant
    return dumped


def lazy_quant(dumped: dict[str, Any]) -> str | None:
    """Best-effort quant label for a serialised row missing ``quant``.

    Filename-token only (cheap enough for the list endpoint's 30s poll):
    the on-disk basename first, then the HF variant filename. Rows whose
    quant is only knowable from the GGUF header (hash-named blobs) get it
    at registration via detect() instead.
    """
    from hal0.registry.detect import quant_from_filename

    for key in ("path", "hf_filename"):
        raw = dumped.get(key)
        if isinstance(raw, str) and raw:
            quant = quant_from_filename(Path(raw).name)
            if quant:
                return quant
    return None


# ── pull-job downloads-pane entries (pure) ────────────────────────────────────


def pull_entry(data: dict[str, Any], model_id: str, registry: Any) -> dict[str, Any]:
    """Build a downloads-pane entry from a pull-job snapshot dict."""
    state = data.get("state", "unknown")
    speed = speed_for_entry(data, state)
    eta = eta_for_entry(data, state, speed)
    hf_repo = hf_repo_for_model(registry, model_id)

    return {
        "model_id": model_id,
        "job_id": data.get("id"),
        "state": state,
        "bytes_downloaded": data.get("bytes_downloaded", 0),
        "bytes_total": data.get("bytes_total", 0),
        "speed_bps": speed,
        "eta_s": eta,
        "hf_repo": hf_repo,
        "dest_path": data.get("path"),
        "error": data.get("error"),
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
    }


def speed_for_entry(data: dict[str, Any], state: str) -> float:
    """Compute average bytes/s for a pull-job entry snapshot."""
    import time

    if state not in ("queued", "running"):
        return 0.0
    started = data.get("started_at")
    if not isinstance(started, (int, float)) or started <= 0:
        return 0.0
    elapsed = max(time.time() - started, 0.001)
    return data.get("bytes_downloaded", 0) / elapsed


def eta_for_entry(data: dict[str, Any], state: str, speed: float) -> float | None:
    """Estimate seconds-to-completion for a pull-job entry snapshot."""
    if state not in ("queued", "running") or speed <= 0:
        return None
    total = data.get("bytes_total", 0)
    if total <= 0:
        return None
    remaining = max(total - data.get("bytes_downloaded", 0), 0)
    return remaining / speed


def hf_repo_for_model(registry: Any, model_id: str) -> str | None:
    """Resolve the HF repo from the model registry, or None if unknown."""
    try:
        model = registry.get(model_id)
        return getattr(model, "hf_repo", None) or None
    except Exception:
        return None


# ── directory scan → registry commit ──────────────────────────────────────────


def suggest_id_from_path(p: Path) -> str:
    """Derive a registry-friendly id from a file path.

    Re-uses :func:`hal0.registry.discover._normalise_id` so single-file
    register and full-root scan land on the same id for the same file.
    """
    from hal0.registry.discover import _normalise_id

    return _normalise_id(p.stem)


async def commit_scan_rows(
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

        suggested_id = row.get("id") or suggest_id_from_path(resolved)
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
                quant=detection.quant,
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


async def auto_scan_and_register(
    registry: Any,
    models_cfg: Any,
    event_bus: Any | None,
    *,
    prune: bool = False,
) -> dict[str, Any]:
    """Walk the configured ``[models].roots`` and auto-register new candidates.

    The empty-body branch of ``POST /api/models/scan``: every newly discovered
    file fires a ``model.registered`` event with ``source='scan'``.

    ``prune=True`` also reconciles the registry (:func:`hal0.registry.discover
    .prune_missing`): rows whose backing file is missing on disk are removed
    — each firing a ``model.pruned`` event — UNLESS the id is referenced by a
    slot or stack (:func:`hal0.registry.discover.referenced_model_ids`), in
    which case it is reported under ``missing_referenced`` for repair rather
    than deleted. The ``pruned``/``missing_referenced`` result keys are always
    present (both ``[]`` when ``prune`` is False).
    """
    from hal0.registry.discover import referenced_model_ids, scan_and_register

    protected_ids = referenced_model_ids() if prune else set()
    result = scan_and_register(registry, models_cfg, prune=prune, protected_ids=protected_ids)
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
        # Best-effort: surface pruned rows as a distinct event so the UI /
        # audit trail records the reconcile (mirrors model.registered).
        for mid in result.get("pruned", []):
            with contextlib.suppress(Exception):
                await event_bus.emit(
                    "model.pruned",
                    "info",
                    f"model:{mid}",
                    f"{mid}: pruned (scan — backing file missing)",
                    data={"id": mid, "source": "scan"},
                )
    return result


def missing_registry_rows(registry: Any) -> list[dict[str, Any]]:
    """Registry rows whose backing file is now absent on disk — drift preview.

    Surfaced by ``POST /api/models/scan/preview`` so the operator/UI can see
    what a ``{"prune": true}`` scan would remove BEFORE committing.
    ``referenced`` flags rows a slot/stack still points at — those are
    protected from prune (repair, don't delete); see
    :func:`hal0.registry.discover.referenced_model_ids`.
    """
    from hal0.registry.discover import referenced_model_ids

    missing: list[dict[str, Any]] = []
    try:
        referenced = referenced_model_ids()
        for m in registry.list():
            try:
                present = Path(m.path).exists()
            except OSError:
                present = False
            if present:
                continue
            missing.append({"id": m.id, "path": m.path, "referenced": m.id in referenced})
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("models.scan_preview_missing_failed err=%s", exc)
    return missing


def preview_scan_rows(
    raw_paths: list[Any], recursive: bool, extensions: set[str]
) -> list[dict[str, Any]]:
    """Walk ``raw_paths`` and return :class:`DetectionResult` rows (no mutation).

    Inspection-only: selects files matching ``extensions`` when walking dirs,
    detects each, and dedups by resolved path then by (name, size, kind) so HF
    cache's reblobbed snapshots collapse to one row per distinct model.
    """
    from hal0.registry.discover import is_skippable

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
    # size + kind across reblobbed snapshots. Keep the first occurrence.
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
    return unique


# ── delete cascade (registry row + referencing slots) ─────────────────────────


def slots_referencing_model(model_id: str) -> list[dict[str, Any]]:
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


def clear_slot_default(slot_path: Path, slot_cfg: dict[str, Any]) -> None:
    """Rewrite a slot TOML with ``[model].default = ""`` cleared in-place.

    Best-effort: a write failure logs a warning but does not abort the
    cascade — the model row is already going away, and a slot left
    pointing at a vanished id will surface as ``model.not_found`` on its
    next ``load()`` (which is the correct UX).

    Routes through :func:`hal0.slot_config.write_slot_toml` (issue
    #697) — the single, atomic slots/*.toml write path.
    """
    from hal0.slot_config import write_slot_toml

    new_cfg = dict(slot_cfg)
    model_sect = new_cfg.get("model")
    if isinstance(model_sect, dict):
        new_model = dict(model_sect)
        new_model["default"] = ""
        new_cfg["model"] = new_model
    # Cascade continues if the write fails; the dangling reference is
    # surfaced later.
    with contextlib.suppress(OSError):
        write_slot_toml(slot_path, new_cfg)


async def unload_slot_if_running(slot_manager: Any, slot_name: str) -> None:
    """Best-effort unload of a referencing slot.

    Asks the SlotManager to unload, catching every failure so one stuck
    slot can't block the cascade. The SlotManager itself emits
    ``slot.state`` events for each transition — we rely on that instead of
    duplicating the emit here, which keeps ``slot.state`` ordering
    authoritative.
    """
    if slot_manager is None:
        return
    try:
        snap = await slot_manager.status(slot_name)
    except Exception:
        return
    if snap.state.value == "offline":
        return
    with contextlib.suppress(Exception):
        await slot_manager.unload(slot_name)


async def cascade_delete_model(
    registry: Any,
    slot_manager: Any,
    model_id: str,
    affected: list[dict[str, Any]],
) -> bool:
    """Cascade-delete a model: unload referrers, clear defaults, remove row.

    Cascade order is load-bearing for the footer's ticker UX:

      1. unload running referrers (each fires ``slot.state``)
      2. clear ``[model].default`` in slot TOMLs
      3. registry delete
      4. GC the durable pull-job snapshot

    The caller emits ``model.deleted`` *after* this returns so subscribers
    see the slot transitions before the model disappears. Returns whether
    the registry row was actually removed.
    """
    from hal0.registry.pull import pull_job_file

    for entry in affected:
        await unload_slot_if_running(slot_manager, entry["name"])
    for entry in affected:
        clear_slot_default(Path(entry["path"]), entry["config"])

    removed = registry.remove(model_id)
    # Best-effort GC of the durable pull-job snapshot (#MR-8). A failed
    # unlink must never break the delete or the model.deleted emit; the
    # startup sweep reaps anything we miss here.
    with contextlib.suppress(OSError):
        pull_job_file(model_id).unlink(missing_ok=True)
    return bool(removed)


# ── catalog aggregation (GET /api/models) ─────────────────────────────────────


async def list_all(
    *, registry: Any, upstreams: Any, cache: dict[str, Any], update_state: Any
) -> dict[str, Any]:
    """Aggregate models from the local registry + every configured upstream.

    Local registry entries (real files on disk) win on id collision; each row
    carries ``installed`` so the UI can render an installed/advertised badge.
    Folds in three sources: registry rows (with a per-row ``update_available``
    recomputed against the row's CURRENT ``metadata.sha256`` from the last
    ``/updates/check`` snapshot in ``update_state``), genuine-remote upstream
    advertisements (operator-filtered, FLM-servable tags dropped so un-pulled
    NPU tags never show as available), and the host FLM probe's INSTALLED tags
    shaped for the NPU slot pickers. ``cache`` is the shared ``model_cache``
    dict and is populated in place with each upstream's fetched ids.

    Returns ``{"models": [...], "count": int, "filtered_aliases": int}``.
    """
    import time

    now = int(time.time())
    data: list[dict[str, Any]] = []
    seen: set[str] = set()
    filtered = 0
    # Last HF update-check snapshot (populated by /api/models/updates/check;
    # never fetched on this hot path). The flag is recomputed against the
    # row's CURRENT metadata.sha256 rather than replayed from the snapshot,
    # so applying an update clears the badge on the next poll without
    # waiting for the check TTL to expire.
    update_checks: dict[str, Any] = {}
    if isinstance(update_state, dict):
        update_checks = update_state.get("models") or {}
    for entry in registry.list():
        dumped = model_to_dict(entry)
        dumped["installed"] = True
        chk = update_checks.get(entry.id)
        if isinstance(chk, dict):
            remote_sha = chk.get("remote_sha256")
            local_sha = (entry.metadata or {}).get("sha256")
            dumped["update_available"] = bool(
                isinstance(remote_sha, str)
                and remote_sha
                and isinstance(local_sha, str)
                and local_sha
                and remote_sha != local_sha.lower()
            )
        dumped.setdefault("object", "model")
        dumped.setdefault("created", now)
        dumped.setdefault("owned_by", "local")
        dumped["type"] = dispatch_type(
            dumped.get("id", ""), capabilities=dumped.get("capabilities")
        )
        # ComfyUI discriminator + category for the dashboard's dedicated
        # image-gen surface. Path-derived so it self-heals rows an older pull
        # mis-tagged (capabilities=["chat"], backends=[]) with no migration:
        # any model whose bytes live under the ComfyUI models tree — or that
        # already carries the comfyui backend — is owned_by "comfyui" and
        # advertises its subdir as ``comfyui_category``. The UI groups the
        # image-gen surface on this, not on the (possibly stale) capability.
        _cat = comfyui_category(dumped.get("path"))
        _bes = list(dumped.get("backends") or [])
        if _cat is not None or "comfyui" in _bes:
            dumped["owned_by"] = "comfyui"
            if "comfyui" not in _bes:
                _bes.append("comfyui")
                dumped["backends"] = _bes
            if _cat is not None:
                dumped["comfyui_category"] = _cat
        data.append(dumped)
        seen.add(entry.id)
    # "Don't surface invisible models": the composite ``hal0``/npu upstream
    # advertises FLM slot-default tags via /v1/models even when the weights are
    # not on disk, so they used to leak into the catalog as available-but-
    # uninstalled rows. The dedicated FLM probe below is the authoritative
    # source (it re-adds the INSTALLED ones with the right npu shape), so drop
    # every FLM-servable tag from the generic upstream advertisement. The probe
    # is module-cached, so the second call in the injector below is O(1).
    flm_skip: set[str] = set()
    try:
        from hal0.providers.flm import flm_served_models as _flm_probe

        for _fm in _flm_probe():
            _tag = _fm.get("tag")
            if isinstance(_tag, str) and _tag:
                flm_skip.add(_tag)
                flm_skip.add(_tag.replace(":", "-") + "-FLM")
    except Exception:
        # Probe unavailable (no flm binary / dev host) — nothing to skip.
        pass
    for u in upstreams.list():
        # Slot-backed entries serve LOCAL models: the composite ``hal0``
        # aggregate (kind="slot") and container slots (kind="remote" with
        # slot_name) advertise ids that live on this host's disk — labeling
        # them origin="upstream" put local slot models in the Models page
        # Upstream tab whenever the advertised id differed from the registry
        # id (raw GGUF casing vs normalized alias). Only genuine remotes
        # contribute upstream rows here.
        if u.kind != "remote" or u.slot_name:
            continue
        if not getattr(u, "enabled", True) or not getattr(u, "advertise_models", True):
            continue
        try:
            ids = cache.get(u.name) or await upstreams.fetch_models(u.name)
            cache[u.name] = ids
        except Exception:
            ids = []
        # Same operator curation as /v1/models — the Models page is a
        # discovery surface, so per-upstream filters apply here too
        # (dispatch stays unfiltered; hidden models remain addressable).
        ids = apply_filters(ids, getattr(u, "model_filters", None))
        for mid in ids:
            if mid in seen:
                continue
            if is_alias(mid):
                filtered += 1
                continue
            # FLM-servable tag advertised before its weights are pulled — the
            # dedicated probe below re-adds the installed ones. Skip here so an
            # un-pulled FLM model never shows as an available upstream row.
            if mid in flm_skip:
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
                    # Explicit origin (WS-13): advertised by a remote
                    # provider's /v1/models, never on this host's disk.
                    # Clients should prefer this over inferring from
                    # installed+upstream.
                    "origin": "upstream",
                    # Upstream-only rows have no local path → "pulled"
                    # by the path-shape rule (issue #220). The
                    # blessed bucket is reserved for files actually
                    # laid out under the blessed recipe tree.
                    "ns": "pulled",
                    # Upstream rows carry no capabilities; classify from
                    # the id so W7 still counts embed/rerank/voice/img.
                    "type": dispatch_type(mid),
                }
            )
    # Installed FLM/NPU models — surfaced straight from the host-flm probe so
    # the NPU slot pickers can select any model on disk, not just the one a slot
    # already defaults to (the composite ``hal0`` upstream advertises only slot
    # defaults, so without this only the configured npu model appeared). The id
    # uses the ``<tag>-FLM`` convention so the dashboard maps it to the
    # npu device; ``capabilities`` + an explicit ``device`` let the slot-swap
    # popover derive type/device without requiring a registry entry.
    try:
        from hal0.providers.flm import flm_served_models

        for fm in flm_served_models():
            if not fm.get("installed"):
                continue
            mid = fm["tag"].replace(":", "-") + "-FLM"
            if mid in seen:
                continue
            seen.add(mid)
            caps = list(fm.get("capabilities") or [])
            # FLM chat tags are chat-first even when multimodal (gemma4 also
            # advertises ``stt``); pick chat as the primary role so they land
            # in the NPU chat picker, not under stt.
            primary = "chat" if "chat" in caps else (caps[0] if caps else "chat")
            # The NPU slot pickers (ui/dash/slots.jsx) gate on the FLM-seed
            # shape: ``isFlmModel`` needs backend=="flm" / upstream=="npu", and
            # ``modelSlotType`` needs the DISPATCHER vocabulary (chat→llm,
            # embed→embedding, stt→transcription) — not the W7 type vocab. Match
            # that shape exactly so probe-sourced models are selectable.
            data.append(
                {
                    "id": mid,
                    "name": mid,
                    "object": "model",
                    "created": now,
                    "owned_by": "flm",
                    "upstream": "npu",
                    "backend": "flm",
                    "installed": True,
                    # FLM rows carry upstream="npu" for the slot pickers but
                    # are installed host-side — explicitly local (WS-13).
                    "origin": "local",
                    "ns": "pulled",
                    "type": FLM_DISPATCH_TYPE.get(primary, "llm"),
                    "capability": primary,
                    "capabilities": caps,
                    "device": "npu",
                }
            )
    except Exception:
        # Probe unavailable (no flm binary / dev host) — skip silently; the
        # rest of the catalog still renders.
        pass
    return {"models": data, "count": len(data), "filtered_aliases": filtered}


# ── add-by-path (POST /api/models/add-from-path) ──────────────────────────────


async def add_from_path(body: dict[str, Any], *, registry: Any, event_bus: Any) -> dict[str, Any]:
    """Register a single already-downloaded model file by absolute path.

    Detection + id/name/capability derivation + registry write + the
    ``model.registered`` emit for the dashboard's "Add by path" flow. The
    file must already exist on disk and be readable by this process — no
    network, no copy. Raises the typed 4xx envelope subclasses on bad input:

      * ``BadRequest`` ``validation.invalid`` — body ``path`` missing/wrong.
      * ``BadRequest`` ``model.path_relative`` — path not absolute.
      * ``BadRequest`` ``model.path_missing`` — file absent / unreadable.
      * ``BadRequest`` ``model.unsupported_format`` — extension not allowed.
      * ``ModelAlreadyExists`` (409) — id registered and ``overwrite=false``.

    Returns the serialised model dict (:func:`model_to_dict`).
    """
    from hal0.config.loader import load_hal0_config
    from hal0.errors import BadRequest
    from hal0.registry.discover import _normalise_id
    from hal0.registry.model import Model

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
            quant=detection.quant,
            capabilities=capabilities,
            backends=list(detection.suggested_backends),
            metadata=metadata,
        )
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"invalid Model payload: {exc}") from exc

    if overwrite and registry.has(model_id):
        registry.remove(model_id)

    # ModelAlreadyExists (409) propagates to the typed envelope unchanged.
    registry.add(model)

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
    return model_to_dict(model)


# ── HuggingFace inspect (POST /api/models/inspect) ────────────────────────────

# In-process TTL cache keyed by normalised HF repo id. Storing the whole
# response shape (variants + tags + metadata) keeps repeat Inspect clicks
# on the same modal session free; the 5 minute TTL is short enough that
# a freshly-uploaded quant lands within one render.
INSPECT_TTL_SECONDS = 300
INSPECT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


async def inspect_hf_repo(body: dict[str, Any]) -> dict[str, Any]:
    """Inspect a HuggingFace repo and return pullable variants + metadata.

    Accepts ``{"hf_repo": "org/name"}`` or the dashboard's older ``{"hf_url":
    ...}`` alias; normalises the coordinate, serves a ~5-minute per-repo TTL
    cache, and otherwise fetches from the Hub. Raises ``BadRequest``
    (``hf.bad_request``) on a missing/invalid coordinate; HF unreachable / 5xx
    / 404 surface as the typed ``hf.*`` envelopes raised by the fetch layer.
    """
    import time

    from hal0.errors import BadRequest
    from hal0.upstreams.huggingface import fetch_repo, normalise_repo_slug

    repo_input = body.get("hf_repo")
    if not isinstance(repo_input, str) or not repo_input.strip():
        repo_input = body.get("hf_url")
    if not isinstance(repo_input, str) or not repo_input.strip():
        raise BadRequest(
            "either 'hf_repo' (org/name) or 'hf_url' is required",
            code="hf.bad_request",
        )

    repo = normalise_repo_slug(repo_input)
    if "/" not in repo:
        raise BadRequest(
            f"'{repo_input}' is not a valid org/name HF repo coordinate",
            code="hf.bad_request",
            details={"input": repo_input},
        )

    now = time.time()
    cached = INSPECT_CACHE.get(repo)
    if cached is not None and now - cached[0] < INSPECT_TTL_SECONDS:
        payload = dict(cached[1])
        payload["repo"] = repo
        payload["cached"] = True
        return payload

    result = await fetch_repo(repo)
    INSPECT_CACHE[repo] = (now, result)
    return {"repo": repo, "cached": False, **result}


# ── O10 guard: bare double-quoted JSON eaten by shell semantics ───────────────
#
# spec-flags-ownership §3 "JSON-token integrity": editable flag text carrying a
# JSON value (e.g. ``--chat-template-kwargs '{"enable_thinking":false}'``) must
# survive shlex-splitting intact to reach the runner. A common operator mistake
# is to omit the surrounding SINGLE quotes:
#
#     --chat-template-kwargs {"enable_thinking":false}     # WRONG
#
# ``shlex.split`` then treats the double quotes as shell quoting and strips them,
# yielding the token ``{enable_thinking:false}`` — no longer valid JSON, and the
# runner rejects it (or worse, silently mis-parses). This guard catches exactly
# that shape at SAVE time (model PUT + any slot-config writer that opts in) and
# tells the operator to single-quote the JSON value, rather than letting a tune
# that can never load through to launch. It runs BEFORE the managed-arg denylist
# so the more actionable quoting message wins.


def screen_extra_args_json(raw: str, *, segment: str = "extra_args") -> None:
    """Reject ``raw`` when a bare double-quoted JSON value was eaten by the shell.

    Detection (deliberately narrow — warning-level UX, one precise cause):
    a ``shlex``-split token that *starts with* ``{`` but is NOT valid JSON,
    while the RAW string contains the substring ``{"`` (proof a
    double-quoted JSON object was typed). That combination only arises when
    shell quote-stripping removed the JSON's own double quotes — a correctly
    single-quoted ``'{"…":…}'`` survives as a token that IS valid JSON and is
    never flagged.

    Raises :class:`~hal0.errors.BadRequest` (``model.extra_args_json_quoting``)
    with a message telling the operator to single-quote the value. A ``raw``
    that does not shlex-parse at all is left for the caller's own
    unparseable-string handling (this guard only inspects a clean token
    list).
    """
    import json
    import shlex

    from hal0.errors import BadRequest

    if not raw or not raw.strip():
        return
    if '{"' not in raw:
        # No double-quoted JSON object present at all — nothing this guard
        # is responsible for. (A single-quoted JSON value shlex-splits to a
        # token that still literally contains ``{"``, so the correctly
        # quoted case is NOT short-circuited here — it is passed through and
        # accepted below because its token parses as valid JSON.)
        return
    try:
        tokens = shlex.split(raw)
    except ValueError:
        # Unparseable as a whole — the caller (model PUT) raises its own
        # ``model.extra_args_unparseable``; don't double-report.
        return
    for tok in tokens:
        if not tok.startswith("{"):
            continue
        try:
            json.loads(tok)
        except ValueError:
            raise BadRequest(
                f"{segment} contains a JSON value whose double quotes were "
                f"stripped by shell parsing (got token {tok!r}). Wrap the "
                "JSON in SINGLE quotes so it survives intact, e.g. "
                "--chat-template-kwargs '{\"enable_thinking\":false}'",
                code="model.extra_args_json_quoting",
                details={"segment": segment, "token": tok},
            ) from None


# #1414 / #1378: the launchable range for ``defaults.context_size``.
#
# FLOOR mirrors the model drawer's client-side reject (#1378, hardened in #1386)
# and the slot drawer's own ≥ 128 gate, so the three surfaces agree on one
# number. 0 / negative are the shapes the live box happily persisted before the
# screen existed; both are unlaunchable — llama-server refuses a non-positive
# --ctx-size, so the row could only ever fail at slot start.
#
# CEILING is an absurdity screen, not a capability claim: 2**24 tokens is ~16x
# the largest advertised window on any shipping model (Llama 4 Scout's 10M) and
# ~64x the biggest GGUF ``context_length`` seen in the wild catalog (262144).
# It exists so a fat-fingered 99999999 fails at SAVE with an actionable message
# instead of reserving a KV cache no host can allocate. Deliberately NOT
# cross-checked against the row's own GGUF ``metadata.context_length``:
# over-riding the header upward is legitimate (RoPE scaling), so that stays a
# warning-shaped concern for a later pass.
CONTEXT_SIZE_MIN = 128
CONTEXT_SIZE_MAX = 2**24


def _screen_model_defaults(defaults: dict[str, Any]) -> None:
    """Typed screen for the incoming ``defaults`` patch (#1414).

    Two jobs, in order:

    1. Parse the patch through ``ModelDefaults`` so client garbage
       (``{"context_size": "abc"}``) fails as ``400 model.defaults_invalid``.
       Before this, an unparseable value escaped all the way to
       ``Model.model_validate`` inside :func:`hal0.registry.store.merge_update`,
       which wraps EVERY failure in ``RegistryError`` — a 500 with a raw
       pydantic traceback in ``details.reason`` for a plain bad body.
    2. Range-check ``context_size`` against
       ``[CONTEXT_SIZE_MIN, CONTEXT_SIZE_MAX]`` → ``400
       model.context_size_out_of_range``.

    Lives here rather than as a ``Field(ge=…, le=…)`` constraint on
    ``ModelDefaults.context_size`` on purpose: a model-level constraint also
    applies on the READ path, so any already-persisted row carrying an
    out-of-range value would stop loading — the exact "policy applied to data
    that predates it" trap #1411 documents on the profile side. Screening at
    the write boundary rejects new garbage while leaving existing rows
    readable (and therefore fixable through the drawer).
    """
    from pydantic import ValidationError

    from hal0.errors import BadRequest
    from hal0.registry.model import ModelDefaults

    # Only the keys the client actually sent — `merge_update` keeps the rest, and
    # a stored value that predates this screen must not fail someone else's patch.
    try:
        parsed = ModelDefaults.model_validate(defaults)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ())) or "defaults"
        raise BadRequest(
            f"defaults.{loc} is not a valid value: {first.get('msg', 'invalid')}",
            code="model.defaults_invalid",
            details={"field": f"defaults.{loc}", "reason": first.get("msg")},
        ) from exc

    ctx = parsed.context_size
    if ctx is not None and not (CONTEXT_SIZE_MIN <= ctx <= CONTEXT_SIZE_MAX):
        raise BadRequest(
            f"defaults.context_size must be between {CONTEXT_SIZE_MIN} and "
            f"{CONTEXT_SIZE_MAX} tokens (got {ctx}) — a context size outside "
            "that range cannot launch",
            code="model.context_size_out_of_range",
            details={
                "field": "defaults.context_size",
                "value": ctx,
                "min": CONTEXT_SIZE_MIN,
                "max": CONTEXT_SIZE_MAX,
            },
        )


def screen_model_write(body: dict[str, Any], *, runner_images: Any = None) -> None:
    """Validate the launch-affecting fields of a model create/update/validate body.

    Raises :class:`~hal0.errors.BadRequest` on the first violation, with the same
    envelope the launch path and the dashboard key on. Shared by
    ``POST /api/models``, ``PUT /api/models/{id}`` and ``POST /api/models/validate``
    so the three can never drift (UI-API-1 item 1). Screens:

    * ``defaults`` as a whole — parses against ``ModelDefaults`` so a malformed
      value is ``400 model.defaults_invalid`` instead of a 500 leaking a pydantic
      traceback, then range-checks ``context_size`` (``400
      model.context_size_out_of_range``). See :func:`_screen_model_defaults` (#1414).
    * ``defaults.extra_args`` — shell-quoting integrity (O10), then the slot-
      hardware partition guard (spec-hw-slot-ownership §5: ``-ngl``/``-dev``/
      ``--threads`` belong on the slot, not the device-agnostic model — raised as
      ``slot.hardware_flag_denied``), then the managed-arg denylist (§21.7), so a
      smuggled ``--model``/``--host``/``--port``/``--ctx-size``/``--alias`` fails
      at SAVE with the same ``slot.managed_arg_denied`` code the launcher raises,
      instead of persisting a row that can never load (or silently rebinds a slot).

    spec-hw-slot-ownership: ``preferred_runner`` is no longer a model field (the
    runner is slot-owned via ``SlotConfig.binary``) — it is neither validated nor
    persisted here; a stray key in the body is silently ignored. ``runner_images``
    is retained (accepted, unused) so the three call sites need no change.
    """
    del runner_images  # preferred_runner is gone from the model — nothing to screen
    from hal0.errors import BadRequest

    defaults = body.get("defaults")
    if isinstance(defaults, dict):
        _screen_model_defaults(defaults)
    if isinstance(defaults, dict) and isinstance(defaults.get("extra_args"), str):
        import shlex

        from hal0.slots.argv import _deny_managed_flags, _deny_slot_hardware_flags

        seg = "model defaults.extra_args"
        raw = defaults["extra_args"]
        # Strip trailing backslash that would otherwise make shlex.split()
        # raise "No escaped character" (a common copy-paste artefact).
        stripped = raw.rstrip().rstrip("\\")
        # O10 guard (spec §3): catch a bare double-quoted JSON value the shell
        # would eat BEFORE the denylist/parse checks so the operator gets the
        # actionable "single-quote it" message.
        screen_extra_args_json(stripped, segment=seg)
        try:
            tokens = shlex.split(stripped)
        except ValueError as exc:
            raise BadRequest(
                f"defaults.extra_args is not parseable as a flag string: {exc}",
                code="model.extra_args_unparseable",
            ) from exc
        # spec-hw-slot-ownership §5: reject the grid-owned hardware flags FIRST so
        # ``-ngl``/``-dev``/``--threads`` get the "belongs on the slot" message
        # (mirrors the client-side model-drawer reject) rather than the generic
        # managed-arg one — ``-ngl`` is in both sets.
        _deny_slot_hardware_flags(tokens, segment=seg)
        _deny_managed_flags(tokens, segment=seg)


# ── duplicate a model row (UI-API-1 item 3) ───────────────────────────────────
#
# Study note (spec deliverable): a model's *weights* live on disk at
# ``Model.path``; the store's refcount lives in the ``store_blob`` table, keyed
# by sha256, with one ``model_file`` row per (model_id, rel) referencing a blob
# (``hal0.registry.gc`` / ``hal0.db.repository``). Duplicating therefore copies
# METADATA only — the new row shares the SAME ``path`` (no byte copy) — and,
# when the source carries ``model_file`` rows (pulled models do; hand-registered
# ones do not), replicates them under the new id and bumps each blob's refcount,
# exactly as a same-sha pull does (``pull._register_blob_after_install`` /
# ``_maybe_hardlink_from_blob``). That keeps ``store_blob.refcount`` honest so a
# later delete of either row never orphans bytes the other still uses.


def _copy_model_files_refcounted(db_path: Any, *, source_id: str, new_id: str) -> int:
    """Replicate ``source_id``'s ``model_file`` rows under ``new_id``, bumping
    each referenced blob's refcount. Returns the number of files replicated.

    No-op (returns 0) when the source has no ``model_file`` rows — the common
    case for hand-registered single-file models, which are tracked only by
    ``Model.path`` and have no blob accounting. Byte content is never touched:
    the new rows point at the SAME ``dest`` as the source.
    """
    from hal0.db import repository
    from hal0.db.connection import connect, tx

    copied = 0
    with connect(db_path) as conn:
        rows = repository.list_model_files(conn, source_id)
        if not rows:
            return 0
        with tx(conn):
            for row in rows:
                sha256 = row.get("sha256")
                repository.insert_model_file(
                    conn,
                    model_id=new_id,
                    rel=row["rel"],
                    dest=row.get("dest"),
                    size_bytes=row.get("size_bytes"),
                    sha256=sha256,
                    lfs=bool(row["lfs"]) if row.get("lfs") is not None else None,
                    role=row.get("role"),
                    shard_index=row.get("shard_index"),
                )
                # Bump the shared blob's refcount so the new row is a real
                # referent — mirrors the same-sha pull path. Only when a blob
                # row actually exists for this sha (non-LFS rows carry none).
                if sha256 and repository.get_blob(conn, sha256) is not None:
                    repository.bump_blob_ref(conn, sha256)
                copied += 1
    return copied


def _strip_denied_flags(flags: str) -> str:
    """Drop hal0-managed + slot-hardware flags from a profile stamp text.

    The duplicate path persists a profile's flags straight into the new row's
    ``defaults.extra_args`` without going through :func:`screen_model_write`,
    so a stale profile still carrying ``-c``/``-ngl`` would mint a row that
    hard-fails every launch with ``slot.managed_arg_denied``. Silently strip
    the denied tokens (+ values) instead — the duplicate is a fresh row, not
    an operator edit, so there is nothing to reject interactively.
    """
    import shlex

    from hal0.slots.argv import (
        MANAGED_ARGS_DENYLIST,
        SLOT_HARDWARE_FLAGS,
        strip_managed_flags,
    )

    if not flags or not flags.strip():
        return flags
    try:
        tokens = shlex.split(flags)
    except ValueError:
        return flags  # malformed quoting — leave verbatim for the edit screens
    clean, removed = strip_managed_flags(
        tokens, denylist=MANAGED_ARGS_DENYLIST | SLOT_HARDWARE_FLAGS
    )
    if not removed:
        return flags
    log.warning(
        "profile stamp carried hal0-owned flag(s); stripped from duplicate",
        extra={"event": "model.duplicate_flags_sanitized", "flags": removed},
    )
    return " ".join(shlex.quote(tok) for tok in clean)


def duplicate_model(
    registry: Any,
    *,
    source_id: str,
    new_id: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """Create a new registry row referencing the SAME weights as ``source_id``.

    Copies the source's metadata/defaults/capabilities/backends into a new
    ``Model`` keyed by ``new_id`` (same ``path`` — NO byte copy), replicates
    its ``model_file`` rows with refcount bumps (:func:`_copy_model_files_refcounted`),
    and — when ``profile`` is given — stamps that profile's flags into the new
    row's ``defaults.extra_args`` (and records the pointer in
    ``defaults.profile``), exactly as the drawer's copy-not-layer stamp does.

    Raises:
        ModelNotFound: ``source_id`` is not registered.
        ModelAlreadyExists: ``new_id`` is already taken.
        BadRequest: ``new_id`` empty, or equal to ``source_id``.
        NotFound (profiles.not_found): ``profile`` given but unknown.
    """
    from hal0.errors import BadRequest
    from hal0.registry.model import Model, ModelDefaults

    if not new_id or not new_id.strip():
        raise BadRequest("'new_id' must be a non-empty model id", code="validation.invalid")
    new_id = new_id.strip()
    if new_id == source_id:
        raise BadRequest(
            "'new_id' must differ from the source model id",
            code="validation.invalid",
            details={"model_id": source_id},
        )

    source = registry.get(source_id)  # raises ModelNotFound

    dumped = source.model_dump(mode="python")
    dumped["id"] = new_id
    # A duplicate must never silently inherit the source's per-type default
    # flag — that would create two default holders of the same type (the
    # single-holder invariant lives on the set path, not on copy). The new row
    # starts non-default; promote it explicitly via set_model_type_default.
    dumped["default"] = False

    # Optional profile stamp: materialise the profile's flag text into the new
    # row's editable defaults (copy-not-layer — the profile is never mutated).
    if profile:
        from hal0.profiles import ProfileCatalog

        resolved = ProfileCatalog().resolve(profile)  # raises NotFound if unknown
        existing_defaults = dumped.get("defaults") or {}
        if not isinstance(existing_defaults, dict):
            existing_defaults = {}
        existing_defaults = dict(existing_defaults)
        existing_defaults["profile"] = profile
        existing_defaults["extra_args"] = _strip_denied_flags(resolved.flags)
        dumped["defaults"] = existing_defaults

    new_model = Model.model_validate(dumped)
    # Normalise an all-None defaults table back to None so the row matches a
    # freshly-created one (mirrors _model_to_toml's "collapse when empty").
    if new_model.defaults is not None and new_model.defaults == ModelDefaults():
        new_model = new_model.model_copy(update={"defaults": None})

    registry.add(new_model)  # raises ModelAlreadyExists if new_id is taken

    db_path = getattr(registry, "db_path", None)
    files_copied = 0
    try:
        files_copied = _copy_model_files_refcounted(db_path, source_id=source_id, new_id=new_id)
    except Exception:  # pragma: no cover - defensive
        # The registry row is the source of truth for "does this model exist";
        # a blob-accounting hiccup must not leave the duplicate half-created.
        # Roll the row back and re-raise so the caller returns a clean error.
        with contextlib.suppress(Exception):
            registry.remove(new_id)
        raise

    out = model_to_dict(new_model)
    out["duplicated_from"] = source_id
    out["files_refcounted"] = files_copied
    return out
