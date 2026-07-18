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
from pathlib import Path
from typing import Any

from hal0.model_meta import classify
from hal0.registry.detect import DetectionResult, detect
from hal0.registry.model import _derive_ns

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
) -> dict[str, Any]:
    """Walk the configured ``[models].roots`` and auto-register new candidates.

    The empty-body branch of ``POST /api/models/scan``: every newly discovered
    file fires a ``model.registered`` event with ``source='scan'``.
    """
    from hal0.registry.discover import scan_and_register

    result = scan_and_register(registry, models_cfg)
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
