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

import asyncio
import contextlib
import errno
import logging
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from hal0.model_meta import RUNTIME_FAMILIES, classify, derive_model_provider
from hal0.registry import tombstones
from hal0.registry.detect import DetectionResult, detect, detected_architecture
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


def flm_dispatch_type(capabilities: Any) -> str:
    """Dispatcher-vocab slot type for an FLM model, chat first.

    FLM's multimodal chat tags also advertise ``stt`` (``gemma4-it:e4b`` is
    chat + asr). :func:`dispatch_type` ranks stt above chat, which is right
    for a GGUF registry row but files an FLM chat model under transcription
    and drops it out of the NPU chat picker. An FLM tag that can chat is a
    chat model; only a tag without chat takes its first capability.
    """
    caps = [str(c) for c in capabilities] if isinstance(capabilities, (list, tuple)) else []
    primary = "chat" if "chat" in caps else (caps[0] if caps else "chat")
    return FLM_DISPATCH_TYPE.get(primary, "llm")


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
    * ``runs_on`` — derived host-backend lanes (``gpu-rocm`` / ``gpu-vulkan``
      / ``cpu`` / ``npu``) this model can run under, via
      :func:`hal0.capabilities.catalog.runs_on_for_model`. Imported lazily
      below: ``hal0.capabilities.catalog`` imports from ``hal0.registry``,
      which this module is itself imported by, so a module-level import
      here would cycle.
    * ``provider_effective`` (Task 6) — the stored ``provider`` when set,
      else :func:`hal0.model_meta.derive_model_provider` off the stored
      ``backends`` tags. Read-only: the write paths (create/update) never
      accept this key back, and the derivation itself is total (always a
      :data:`hal0.model_meta.RUNTIME_FAMILIES` member), so the engine
      picker always has a concrete "Auto resolves to" preview even for a
      row with no explicit provider.
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
    from hal0.capabilities.catalog import runs_on_for_model

    dumped["runs_on"] = runs_on_for_model(model)
    dumped["provider_effective"] = dumped.get("provider") or derive_model_provider(
        getattr(model, "backends", None) or dumped.get("backends")
    )
    return dumped


def lazy_quant(dumped: dict[str, Any]) -> str | None:
    """Best-effort quant label for a serialised row missing ``quant``.

    Filename-token only (cheap enough for the list endpoint's 30s poll):
    the on-disk basename first, then the HF variant filename. Checks the
    ROCmFPX family resolver before the generic quant regex — same order
    as the eager path in ``detect()`` — so ROCmFPX repacks (hal0's own
    brain model included) resolve to their family label (``ROCmFP8``)
    instead of the raw preset token the generic regex would greedily
    match (``Q8_0_ROCMFPX``). Rows whose quant is only knowable from the
    GGUF header (hash-named blobs) get it at registration via detect()
    instead.
    """
    from hal0.registry.detect import quant_from_filename, quant_from_rocmfpx_filename

    for key in ("path", "hf_filename"):
        raw = dumped.get(key)
        if isinstance(raw, str) and raw:
            name = Path(raw).name
            quant = quant_from_rocmfpx_filename(name) or quant_from_filename(name)
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
        # Per-file progress (``PullFile.as_dict``) so the downloads pane can
        # say which part of a split model, or the projector, is in flight.
        "files": data.get("files") or [],
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
    detection: ``id``, ``name``, ``backends``, ``capabilities``, ``provider``,
    and the file facts ``chat_template`` / ``tokenizer_repo`` / ``mtp`` /
    ``jinja`` (ADR-0029 §6; a ``defaults`` table, a tuning key or a malformed
    fact skips the row — see :func:`screen_model_facts`). Missing fields are
    backfilled by re-running ``detect()`` on the path so the operator can
    edit only what matters and still get high-confidence defaults for the
    rest. ``provider`` defaults to ``derive_model_provider(backends)`` when
    not given, so a freshly-committed row always carries an explicit
    provider (Task 3) instead of leaving it for lazy derivation.
    """
    from hal0.errors import Hal0Error
    from hal0.registry.discover import is_comfyui_asset
    from hal0.registry.model import MODEL_FACT_FIELDS, Model
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

        # ComfyUI's tree belongs to the image catalog: skip the row, keep the batch.
        if is_comfyui_asset(path):
            skipped.append({"path": str(resolved), "reason": "image_asset"})
            continue

        # #1838: see add_from_path's matching comment — pass the operator-
        # typed name so a HF hub-cache symlink's resolved sha-named blob
        # doesn't hide a real filename capability token.
        detection = detect(resolved, filename_hint=path.name)
        backends = row.get("backends")
        capabilities = row.get("capabilities")
        if not isinstance(backends, list):
            backends = list(detection.suggested_backends)
        if not isinstance(capabilities, list):
            capabilities = list(detection.suggested_capabilities)
        # Task 3: an operator-edited ``provider`` override always wins (same
        # "explicit input wins" rule as backends/capabilities above);
        # otherwise derive it from whichever backends this row actually
        # landed on, so a NEW row never persists with provider=None.
        provider = row.get("provider")
        if not isinstance(provider, str) or not provider.strip():
            provider = derive_model_provider(backends)
        elif provider not in RUNTIME_FAMILIES:
            # Final-review fix: an operator-supplied provider string was
            # persisted unscreened, letting POST /api/models/scan mint an
            # out-of-vocab row (any tag outside RUNTIME_FAMILIES, e.g. a
            # curated-only tag the spec forbids on registry rows). Screen it against the same
            # vocab the create path enforces (see the ``model.provider_invalid``
            # check further down this module) and reject the row instead.
            skipped.append({"path": str(resolved), "reason": f"invalid_provider:{provider!r}"})
            continue

        suggested_id = row.get("id") or suggest_id_from_path(resolved)
        name = row.get("name") or resolved.stem

        # Facts are top-level (ADR-0029 §6); a ``defaults`` table or a tuning
        # key skips the row with the same code the create/PUT screen raises.
        try:
            screen_model_facts(row)
        except Hal0Error as exc:
            skipped.append({"path": str(resolved), "reason": exc.code, "message": exc.message})
            continue
        facts = {k: row[k] for k in MODEL_FACT_FIELDS if k in row}

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
                provider=provider,
                metadata=metadata,
                **facts,
                # GGUF general.architecture from the header read detect()
                # already performed — persisted so the model↔runner arch
                # fit-check (hal0#2118) can warn at slot assignment instead
                # of the operator discovering a crash-loop at load.
                architecture=detected_architecture(detection),
            )
        except (TypeError, ValueError) as exc:
            skipped.append({"path": str(resolved), "reason": f"invalid_model:{exc}"})
            continue

        try:
            registry.add(model)
        except ModelAlreadyExists:
            skipped.append({"path": str(resolved), "reason": "already_registered"})
            continue
        # The operator picked this row explicitly (scan commit) — un-ignore it.
        tombstones.clear(path)
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
        for mid in result.get("reconciled", []):
            with contextlib.suppress(Exception):
                await event_bus.emit(
                    "model.pruned",
                    "info",
                    f"model:{mid}",
                    f"{mid}: pruned (scan — path is under a skipped directory)",
                    data={"id": mid, "source": "scan"},
                )
    return result


def missing_registry_rows(
    registry: Any, roots: Sequence[str | Path] | None = None
) -> list[dict[str, Any]]:
    """Registry rows whose backing file is now absent on disk — drift preview.

    Surfaced by ``POST /api/models/scan/preview`` so the operator/UI can see
    what a ``{"prune": true}`` scan would remove BEFORE committing.
    ``referenced`` flags rows a slot/stack still points at — those are
    protected from prune (repair, don't delete); see
    :func:`hal0.registry.discover.referenced_model_ids`. Rows a prune would
    keep as ``missing_unverified`` (empty/absent root — an unmounted share)
    are left out, using the same predicate as ``prune_missing``. ``roots``
    defaults to the configured scan roots.
    """
    from hal0.registry.discover import is_runtime_managed, miss_checker, referenced_model_ids

    if roots is None:
        try:
            from hal0.config.loader import load_hal0_config

            roots = load_hal0_config().models.scan_roots()
        except Exception as exc:
            log.warning("models.scan_preview_roots_failed err=%s", exc)
            roots = []
    trustworthy = miss_checker(list(roots))
    missing: list[dict[str, Any]] = []
    try:
        referenced = referenced_model_ids()
        for m in registry.list():
            if is_runtime_managed(m):
                # prune_missing never drops these; don't preview them as drift.
                continue
            try:
                present = Path(m.path).exists()
            except OSError:
                present = False
            if present:
                continue
            if not trustworthy(m.path):
                continue  # prune would keep it as missing_unverified
            missing.append({"id": m.id, "path": m.path, "referenced": m.id in referenced})
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("models.scan_preview_missing_failed err=%s", exc)
    return missing


def preview_scan_rows(
    raw_paths: list[Any],
    recursive: bool,
    extensions: set[str],
    *,
    registry: Any = None,
    skipped: list[dict[str, Any]] | None = None,
    paired_drafts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Walk ``raw_paths`` and return :class:`DetectionResult` rows (no mutation).

    Inspection-only: selects files matching ``extensions`` when walking dirs,
    detects each, and dedups by resolved path then by (name, size, kind) so HF
    cache's reblobbed snapshots collapse to one row per distinct model.

    Speculative-decoding drafters (ADR-0031 §4 Scan) are not rows: a fitting one
    gets a non-selectable ``role="draft"`` row naming the model it pairs with
    and sets ``draft`` on that model's row (or lands in ``paired_drafts`` for an
    already-registered model); unpaired ones also go to ``skipped`` as
    ``{path, reason}``.
    """
    from hal0.registry.discover import (
        _scan_drafter,
        is_skippable,
        preview_drafter_pairing,
    )

    drafters = []

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
            drafter = _scan_drafter(p, resolved)
            if drafter is not None:
                drafters.append(drafter)
                continue
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
    if drafters:
        drafter_skipped, pairs, drafter_rows = preview_drafter_pairing(registry, unique, drafters)
        unique.extend(drafter_rows)
        if skipped is not None:
            skipped.extend(drafter_skipped)
        if paired_drafts is not None:
            paired_drafts.extend(pairs)
    return unique


# ── delete cascade (registry row + referencing slots) ─────────────────────────


def slots_referencing_model(model_id: str) -> list[dict[str, Any]]:
    """Return slot configs (as raw dicts) whose ``[model].default`` is ``model_id``.

    Reads slot TOMLs directly so the cascade also catches slots whose
    SlotManager hasn't been touched this process — the source of truth is
    the TOML on disk. A file shadowed by a same-named sibling is skipped (see
    :func:`hal0.slots.layout.shadowed_slot_files`). Each returned dict carries at minimum ``name`` +
    the parsed config body (used by callers to clear the default field).
    """
    import tomllib

    from hal0.config import paths as cfg_paths
    from hal0.slots.layout import shadowed_slot_files

    cfg_dir = cfg_paths.slots_config_dir()
    if not cfg_dir.exists():
        return []
    affected: list[dict[str, Any]] = []
    # #2542: a TOML shadowed by another file with the same name is not the
    # config the SlotManager runs. Reporting it would unload the live slot
    # (by name) for a model only the stale file references.
    shadowed = shadowed_slot_files(cfg_dir)
    for p in sorted(cfg_dir.glob("*.toml")):
        if p.name.startswith(".") or p in shadowed:
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
    *,
    delete_files: bool = True,
    roots: Sequence[str | Path] | None = None,
    pull_jobs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cascade-delete a model: stop its pull, unload referrers, remove row and files.

    Cascade order is load-bearing for the footer's ticker UX:

      0. mark the id as deleting (a new pull/update job for it is refused
         with ``model.deleting`` until the delete is done), then cancel a
         queued/running job and wait for it to stop
         (:func:`cancel_active_pull`); the row removal also records a
         deleted-at time, so a job created before it never registers
      1. unload running referrers (each fires ``slot.state``)
      2. clear ``[model].default`` in slot TOMLs
      3. registry delete, then
      4. remove the model's files (``delete_files=True``, the default) or
         tombstone them so a scan never re-adds them — see
         :func:`delete_model_files_on_disk` and ADR-0026
      5. GC the durable pull-job snapshot

    Steps 3 and 4 run in a worker thread under
    :data:`hal0.registry.discover.SCAN_DELETE_LOCK`, so a concurrent scan
    can't re-register the file between the row removal and its tombstone.
    The file set is captured inside that lock just before the row goes,
    because the row's ``model_file`` rows cascade away with it. File removal
    never raises and never undoes the row delete: a refused or failed file
    is reported in ``files_kept``. ``roots`` defaults to
    :func:`deletion_roots`.

    The caller emits ``model.deleted`` *after* this returns so subscribers
    see the slot transitions before the model disappears. Returns
    ``{"deleted": bool, "files_removed", "files_kept", "bytes_freed",
    "tombstoned", "warnings"}``.
    """
    import asyncio

    from hal0.registry.pull import mark_deleting, pull_job_file, unmark_deleting

    # No new pull/update job can start for this id until the delete is done.
    mark_deleting(model_id)
    try:
        await cancel_active_pull(pull_jobs, model_id)

        for entry in affected:
            await unload_slot_if_running(slot_manager, entry["name"])
        for entry in affected:
            clear_slot_default(Path(entry["path"]), entry["config"])

        removed, outcome = await asyncio.to_thread(
            _remove_row_and_files,
            registry,
            model_id,
            delete_files=delete_files,
            roots=roots if roots is not None else deletion_roots(),
        )
    finally:
        unmark_deleting(model_id)
    # Best-effort GC of the durable pull-job snapshot (#MR-8). A failed
    # unlink must never break the delete or the model.deleted emit; the
    # startup sweep reaps anything we miss here.
    with contextlib.suppress(OSError):
        pull_job_file(model_id).unlink(missing_ok=True)
    return {"deleted": removed, **outcome}


#: How long a delete waits for a cancelled pull/update job to stop.
PULL_CANCEL_WAIT_S = 30.0


async def cancel_active_pull(
    pull_jobs: dict[str, Any] | None, model_id: str, *, timeout_s: float | None = None
) -> None:
    """Cancel a queued/running pull or update job for ``model_id`` and wait.

    Sets the job's ``cancel_requested`` flag (the same flag
    ``POST /api/models/{id}/pull/cancel`` sets) and waits until the job is
    terminal. The pull layer re-checks the flag right before registering, so
    a cancelled job never re-adds the row. Raises ``Conflict``
    (``model.pull_active``) if the job has not stopped within the timeout,
    before anything is deleted.
    """
    import asyncio

    if not pull_jobs:
        return
    job = pull_jobs.get(model_id)
    if job is None or getattr(job, "state", None) not in ("queued", "running"):
        return
    job.cancel_requested = True
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (PULL_CANCEL_WAIT_S if timeout_s is None else timeout_s)
    while job.state in ("queued", "running"):
        remaining = deadline - loop.time()
        if remaining <= 0:
            from hal0.errors import Conflict

            raise Conflict(
                f"a pull for {model_id!r} is still running; it was asked to stop — "
                "retry the delete once it has",
                code="model.pull_active",
                details={"model_id": model_id, "state": job.state},
            )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(job.progress_event.wait(), timeout=min(0.5, remaining))


def _remove_row_and_files(
    registry: Any,
    model_id: str,
    *,
    delete_files: bool,
    roots: Sequence[str | Path],
) -> tuple[bool, dict[str, Any]]:
    """Row removal + file cleanup + tombstones, atomic with respect to scans."""
    from hal0.registry.discover import SCAN_DELETE_LOCK

    with SCAN_DELETE_LOCK:
        try:
            model = registry.get(model_id)
        except Exception:
            model = None
        planned = plan_model_files(registry, model) if model is not None else []
        removed = bool(registry.remove(model_id))
        if removed:
            from hal0.registry.pull import mark_deleted

            mark_deleted(model_id)
        outcome = _empty_file_outcome()
        if removed and model is not None:
            try:
                outcome = delete_model_files_on_disk(
                    registry, model, planned, delete_files=delete_files, roots=roots
                )
            except Exception as exc:  # pragma: no cover — defensive
                log.warning("models.delete_files_failed id=%s err=%s", model_id, exc)
                outcome["warnings"].append(f"file cleanup failed: {exc}")
        return removed, outcome


# ── model file removal on delete (ADR-0026) ───────────────────────────────────


def _empty_file_outcome() -> dict[str, Any]:
    return {
        "files_removed": [],
        "files_kept": [],
        "bytes_freed": 0,
        "tombstoned": [],
        "warnings": [],
    }


def deletion_roots() -> list[str]:
    """Directories a model delete may remove files under.

    The discovery scan roots (``[models].roots`` plus the effective store —
    :meth:`hal0.config.schema.ModelsConfig.scan_roots`) and the resolved
    store root hal0 pulls to. A config-load failure degrades to the store
    root alone, never to "anywhere".
    """
    from hal0.config import store as store_cfg

    out: list[str] = []
    try:
        from hal0.config.loader import load_hal0_config

        out.extend(load_hal0_config().models.scan_roots())
    except Exception as exc:
        log.warning("models.deletion_roots_config_failed err=%s", exc)
    with contextlib.suppress(Exception):
        sr = str(store_cfg.store_root())
        if sr not in out:
            out.append(sr)
    return out


def _model_file_rows(
    registry: Any, model_id: str | None = None, *, strict: bool = False
) -> list[dict[str, Any]]:
    """``model_file`` rows (all models, or one) — empty on a non-SQLite registry.

    A read error yields ``[]`` and a warning, unless ``strict`` — then it
    raises, for callers that must fail closed (:class:`_References`).
    """
    db_path = getattr(registry, "db_path", None)
    if db_path is None:
        return []
    from hal0.db.connection import connect

    try:
        with connect(db_path) as conn:
            if model_id is not None:
                from hal0.db import repository

                return repository.list_model_files(conn, model_id)
            rows = conn.execute(
                "SELECT model_id, dest, sha256 FROM model_file WHERE dest IS NOT NULL"
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("models.model_file_read_failed id=%s err=%s", model_id, exc)
        if strict:
            raise
        return []


def _model_paths(model: Any) -> list[str]:
    """The paths a registry row itself records: path, shards, mmproj, draft."""
    out: list[str] = []
    if getattr(model, "path", ""):
        out.append(str(model.path))
    shards = (getattr(model, "metadata", None) or {}).get("shards")
    if isinstance(shards, list):
        out.extend(str(s) for s in shards if isinstance(s, str) and s)
    if getattr(model, "mmproj", None):
        out.append(str(model.mmproj))
    if getattr(model, "draft", None):
        out.append(str(model.draft))
    return out


def plan_model_files(registry: Any, model: Any) -> list[tuple[str, str | None]]:
    """The file set a delete would remove, as ``(path, sha256 | None)``.

    Union, deduped, of the row's ``path``, ``metadata["shards"]``, ``mmproj``,
    ``draft`` and every ``model_file`` row's ``dest`` (pulled filesets record config,
    tokenizer and shard files there; ``sha256`` drives the ``store_blob``
    refcount). Must be called BEFORE ``registry.remove`` — the ``model_file``
    rows cascade away with the model row.
    """
    seen: dict[str, str | None] = {}
    for p in _model_paths(model):
        seen.setdefault(p, None)
    for row in _model_file_rows(registry, model.id):
        dest = row.get("dest")
        if dest:
            sha = row.get("sha256") or None
            if seen.get(dest) is None:
                seen[dest] = sha
    return list(seen.items())


def _resolve(p: str | Path) -> Path:
    path = Path(p).expanduser()
    try:
        return path.resolve()
    except OSError:
        return path


class _References:
    """Every path a REMAINING registry row still owns.

    ``exact`` maps a referenced file (resolved and literal form) to its row
    id. ``dirs`` lists directory-valued row paths (FLM model dirs, ONNX model
    directories…): everything below one of them belongs to that row too.
    """

    def __init__(self, registry: Any) -> None:
        self.exact: dict[str, str] = {}
        self.dirs: list[tuple[Path, str]] = []
        #: False when the remaining rows or their ``model_file`` rows could not
        #: be read. An incomplete owner set must never authorise a delete:
        #: the caller fails closed (``owner_check_failed``).
        self.complete = True
        self.error = ""
        try:
            rows = list(registry.list())
            file_rows = _model_file_rows(registry, strict=True)
        except Exception as exc:
            log.warning("models.owner_check_failed err=%s", exc)
            self.complete = False
            self.error = str(exc) or type(exc).__name__
            return
        for row in rows:
            for p in _model_paths(row):
                resolved = _resolve(p)
                self.exact.setdefault(str(resolved), row.id)
                self.exact.setdefault(str(Path(p).expanduser()), row.id)
                with contextlib.suppress(OSError):
                    if resolved.is_dir():
                        self.dirs.append((resolved, row.id))
        for frow in file_rows:
            dest = frow.get("dest")
            if dest:
                self.exact.setdefault(str(_resolve(dest)), str(frow.get("model_id")))
                self.exact.setdefault(str(Path(dest).expanduser()), str(frow.get("model_id")))

    def owner(self, *forms: Path) -> str | None:
        """The id of a remaining row that owns any of ``forms``, else ``None``."""
        for form in forms:
            hit = self.exact.get(str(form))
            if hit:
                return hit
        for form in forms:
            for d, mid in self.dirs:
                if form != d and form.is_relative_to(d):
                    return mid
        return None

    def dir_paths(self) -> set[Path]:
        return {d for d, _ in self.dirs}


def _containing_root(p: Path, roots: list[Path]) -> Path | None:
    """The DEEPEST root ``p`` sits strictly inside (never equal to), else ``None``.

    With overlapping roots (``/`` and ``/mnt/models``) the most specific one
    wins, so empty-parent pruning stops at ``/mnt/models``.
    """
    matches = [r for r in roots if p != r and p.is_relative_to(r)]
    if not matches:
        return None
    return max(matches, key=lambda r: len(r.parts))


class _StateGuard:
    """hal0's own config/state, which a model delete must never touch.

    * ``always`` — specific state paths protected no matter what: all of
      ``/etc/hal0``, the hal0 and activity SQLite files (with their
      ``-wal``/``-shm``/``-journal`` sidecars), the registry dir (which holds
      the tombstone file), slot data, stacks state, agent workspaces, Open
      WebUI data and the first-run lock.
    * ``trees`` — ``/etc/hal0`` and ``/var/lib/hal0`` as a whole.
    * ``exempt`` — model stores inside those trees that ARE fair game (the
      default models dir, the configured store). A store only exempts files
      when it sits STRICTLY inside a protected tree. A store configured as
      ``/var/lib/hal0`` itself, or an ancestor such as ``/var/lib``, can't
      swallow the state tree, and the ``always`` paths win over any
      exemption.
    """

    def __init__(self) -> None:
        from hal0.config import paths as cfg_paths
        from hal0.config import store as store_cfg

        var_lib = _resolve(cfg_paths.var_lib())
        etc = _resolve(cfg_paths.etc())
        self.trees = [etc, var_lib]
        always = [
            etc,
            _resolve(cfg_paths.registry_dir()),
            var_lib / "slots",
            var_lib / "stacks",
            var_lib / "agents",
            _resolve(cfg_paths.openwebui_data_dir()),
            _resolve(cfg_paths.first_run_lock()),
            _resolve(cfg_paths.model_tombstones_path()),
        ]
        for db in (cfg_paths.db_path(), cfg_paths.activity_db()):
            for suffix in ("", "-wal", "-shm", "-journal"):
                always.append(_resolve(f"{db}{suffix}"))
        self.always = always
        candidates = [_resolve(cfg_paths.models_dir())]
        with contextlib.suppress(Exception):
            candidates.append(_resolve(store_cfg.store_root()))
        self.exempt = [
            c for c in candidates if any(c != t and c.is_relative_to(t) for t in self.trees)
        ]


def _hal0_state_guard() -> _StateGuard:
    return _StateGuard()


def _in_hal0_state(p: Path, guard: _StateGuard) -> bool:
    """True when ``p`` is hal0 config/state a delete must not remove."""
    if any(p == a or p.is_relative_to(a) for a in guard.always):
        return True
    if any(p.is_relative_to(e) for e in guard.exempt):
        return False
    return any(p.is_relative_to(t) for t in guard.trees)


#: Flags for walking a path one component at a time: never follow a symlink,
#: and fail unless the component is a real directory.
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


class _PathChanged(OSError):
    """A path component changed kind between the guard checks and the unlink."""


def _open_dir_chain(root: Path, parts: Sequence[str]) -> list[int]:
    """Open ``root`` and each directory ``parts`` names below it, by fd.

    Every component is opened relative to its parent's fd with
    ``O_NOFOLLOW | O_DIRECTORY``, so a directory swapped for a symlink after
    the guards ran fails the walk (``ELOOP``/``ENOTDIR``) instead of being
    followed. Model roots can be writable by other accounts, so pathname
    operations after a check are not trustworthy. The caller closes the fds.
    """
    fds = [os.open(root, _DIR_FLAGS)]
    try:
        for part in parts:
            fds.append(os.open(part, _DIR_FLAGS, dir_fd=fds[-1]))
    except OSError:
        _close_fds(fds)
        raise
    return fds


def _close_fds(fds: list[int]) -> None:
    for fd in fds:
        with contextlib.suppress(OSError):
            os.close(fd)


def _unlink_under(path: Path, root: Path, *, expect_link: bool) -> os.stat_result:
    """Unlink ``path`` (strictly inside ``root``) through stable dir fds.

    The final component is re-checked with ``lstat`` against its fd right
    before ``unlink``: it must still be a symlink when ``expect_link``, and a
    non-directory, non-symlink otherwise. Raises :class:`_PathChanged` on a
    mismatch, ``FileNotFoundError`` if it vanished, other ``OSError`` as is.
    Returns the ``lstat`` result of what was removed.
    """
    parts = path.relative_to(root).parts
    if not parts:
        raise _PathChanged(f"{path} is the root itself")
    fds = _open_dir_chain(root, parts[:-1])
    try:
        st = os.stat(parts[-1], dir_fd=fds[-1], follow_symlinks=False)
        is_link = stat.S_ISLNK(st.st_mode)
        if stat.S_ISDIR(st.st_mode) or is_link != expect_link:
            raise _PathChanged(f"{path} changed kind before unlink")
        os.unlink(parts[-1], dir_fd=fds[-1])
        return st
    finally:
        _close_fds(fds)


def _prune_empty_parents(start: Path, root: Path, keep: set[Path]) -> None:
    """``rmdir`` empty directories from ``start`` up to (not including) ``root``.

    Works through the same ``O_NOFOLLOW`` fd chain as :func:`_unlink_under`,
    so a component swapped for a symlink stops the walk instead of letting
    ``rmdir`` act outside the root.
    """
    try:
        parts = start.relative_to(root).parts
    except ValueError:
        return
    if not parts:
        return
    try:
        fds = _open_dir_chain(root, parts)
    except OSError:
        return
    try:
        for i in range(len(parts), 0, -1):
            if root.joinpath(*parts[:i]) in keep:
                return
            try:
                os.rmdir(parts[i - 1], dir_fd=fds[i - 1])
            except OSError:
                return
    finally:
        _close_fds(fds)


def _record_tombstones(out: dict[str, Any], to_tombstone: list[str]) -> None:
    """Write tombstones; on failure warn instead of claiming them (fail-soft)."""
    if not to_tombstone:
        return
    try:
        out["tombstoned"] = tombstones.add(to_tombstone)
    except tombstones.TombstoneWriteError as exc:
        out["tombstoned"] = []
        out["warnings"].append(
            f"tombstone_failed: {exc} — kept files may be registered again by the next scan"
        )


def _release_refs(
    registry: Any,
    model_id: str,
    out: dict[str, Any],
    released: Sequence[tuple[str | None, str]],
    kept: Sequence[tuple[str | None, str]],
) -> None:
    """Drop the deleted row's ``store_blob`` references.

    ``released`` files were unlinked (normal release). ``kept`` files stay on
    disk: their ``model_file`` rows are gone with the model row, so the
    reference is dropped too, but with ``keep_bytes`` so the blob row is
    forgotten rather than its bytes unlinked.
    """
    db_path = getattr(registry, "db_path", None)
    if db_path is None or not any(sha for sha, _ in [*released, *kept]):
        return
    from hal0.registry import gc

    try:
        gc.release_blob_refs(db_path, released, model_id=model_id, kept=kept)
    except Exception as exc:
        log.warning("models.blob_release_failed id=%s err=%s", model_id, exc)
        out["warnings"].append(f"store refcount update failed: {exc}")


def delete_model_files_on_disk(
    registry: Any,
    model: Any,
    planned: list[tuple[str, str | None]],
    *,
    delete_files: bool,
    roots: Sequence[str | Path],
) -> dict[str, Any]:
    """Remove (or tombstone) a just-deleted model's files. Never raises.

    ``planned`` comes from :func:`plan_model_files`, captured before the
    registry row was removed. With ``delete_files=False`` nothing on disk is
    touched; every file is tombstoned instead. Otherwise each file is
    unlinked unless a guard refuses it (reported in ``files_kept`` with a
    reason):

    * ``outside_roots`` — the file (or, for a symlink, the link or its
      resolved target) is not strictly inside a :func:`deletion_roots` root.
    * ``shared:<id>`` — another remaining registry row references it, or
      it sits under a remaining row whose path is a directory.
    * ``missing_at_delete`` — the file is not there at delete time (an
      unmounted share, a dangling symlink target). It is tombstoned so it
      cannot reappear when the storage returns.
    * ``hal0_state`` — it sits under hal0's own config or state tree
      (``/etc/hal0``, ``/var/lib/hal0``) outside the model store, which a
      broad scan root such as ``/`` would otherwise expose.
    * ``runtime_managed`` — an FLM row
      (:func:`hal0.registry.discover.is_runtime_managed`): FLM owns its model
      directory, so nothing is removed and the directory is tombstoned.
    * ``directory`` — any other directory-valued path. Directories are never
      removed recursively; the directory is tombstoned instead.
    * ``owner_check_failed`` — the remaining rows (or their ``model_file``
      rows) could not be read, so sharing can't be ruled out. Nothing is
      deleted at all; every planned file is kept and tombstoned (fail
      closed), and a warning says so.
    * ``path_changed`` — a component was swapped (for example a directory
      replaced by a symlink) between the checks and the unlink.
    * ``unlink_failed:<error>`` — the OS refused.

    Every unlink and ``rmdir`` goes through ``O_NOFOLLOW`` directory fds
    walked down from the containing root (:func:`_unlink_under`), so another
    account that can write to a model root cannot redirect the delete with
    a symlink after the guards ran.

    A symlink (HF cache ``snapshots/…`` → ``blobs/<sha>``) is removed along
    with its resolved target. A file with other hardlinks is removed but
    frees no space; it is listed in ``warnings`` and left out of
    ``bytes_freed``. Emptied parent directories are removed up to the
    deepest root. Every kept file that no other row references is
    tombstoned so a scan never re-registers it; a tombstone write failure is
    reported in ``warnings`` (``tombstone_failed``) and ``tombstoned`` stays
    empty. ``store_blob`` references of the deleted row are released for
    every planned pulled file — removed ones normally, kept ones without
    touching their bytes (:func:`_release_refs`).
    """
    from hal0.config import store as store_cfg
    from hal0.registry.discover import is_runtime_managed

    out = _empty_file_outcome()
    to_tombstone: list[str] = []

    existing: list[tuple[str, str | None]] = []
    for p, sha in planned:
        if os.path.lexists(Path(p).expanduser()):
            existing.append((p, sha))
        else:
            # Not there right now — an unmounted share, or a file already
            # gone. Tombstone it anyway so it can't reappear when the storage
            # comes back.
            out["files_kept"].append({"path": p, "reason": "missing_at_delete"})
            to_tombstone.append(p)

    keep_reason = None
    if not delete_files:
        keep_reason = "kept_by_request"
    elif is_runtime_managed(model):
        # FLM owns these files (its model dir); never remove them from here.
        keep_reason = "runtime_managed"
    if keep_reason is not None:
        for p, _sha in existing:
            out["files_kept"].append({"path": p, "reason": keep_reason})
            to_tombstone.append(p)
        _release_refs(registry, model.id, out, [], [(sha, p) for p, sha in planned if sha])
        _record_tombstones(out, to_tombstone)
        return out

    root_paths: list[Path] = []
    for r in roots:
        rp = _resolve(r)
        if rp.is_dir() and rp not in root_paths:
            root_paths.append(rp)
    others = _References(registry)
    if not others.complete:
        # Fail closed: without the full owner set we can't tell whether a
        # remaining row still uses any of these files.
        for p, _sha in existing:
            out["files_kept"].append({"path": p, "reason": "owner_check_failed"})
            to_tombstone.append(p)
        out["warnings"].append(
            f"owner_check_failed: could not read the remaining models ({others.error}); "
            "no files were deleted"
        )
        _release_refs(registry, model.id, out, [], [(sha, p) for p, sha in planned if sha])
        _record_tombstones(out, to_tombstone)
        return out
    state_guard = _hal0_state_guard()
    removed_dirs: list[tuple[Path, Path]] = []
    gone: set[Path] = set()
    released: list[tuple[str | None, str]] = []
    kept_dirs: set[Path] = set()
    by_id: Path | None = None
    with contextlib.suppress(Exception):
        by_id = store_cfg.by_id_dir().resolve()

    def _keep(path: str | Path, reason: str, *, tombstone: bool = True) -> None:
        out["files_kept"].append({"path": str(path), "reason": reason})
        if tombstone:
            to_tombstone.append(str(path))

    def _unlink(path: Path, root: Path, *, expect_link: bool) -> bool:
        if path in gone:
            return True  # listed twice in the plan (link + target); done already
        try:
            st = _unlink_under(path, root, expect_link=expect_link)
        except FileNotFoundError:
            # A dangling symlink's target, or storage that went away.
            _keep(path, "missing_at_delete")
            return False
        except _PathChanged:
            _keep(path, "path_changed")
            return False
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                _keep(path, "path_changed")
            else:
                _keep(path, f"unlink_failed:{exc.strerror or exc}")
            return False
        size = 0 if stat.S_ISLNK(st.st_mode) else st.st_size
        entry: dict[str, Any] = {"path": str(path), "size_bytes": size}
        if size and st.st_nlink > 1:
            entry["hardlinks"] = st.st_nlink
            out["warnings"].append(
                f"{path} had {st.st_nlink - 1} other hardlink(s); its space is not freed"
            )
        else:
            out["bytes_freed"] += size
        out["files_removed"].append(entry)
        removed_dirs.append((path.parent, root))
        gone.add(path)
        return True

    for p, sha in existing:
        literal = Path(p).expanduser()
        target = _resolve(literal)
        if target.is_dir():
            _keep(p, "directory")
            kept_dirs.add(target)  # reported kept, so empty-parent pruning must not rmdir it
            continue
        is_link = literal.is_symlink()
        # The link's own location: resolve its PARENT only, so the check is
        # about where the link lives, not where it points.
        link_loc = _resolve(literal.parent) / literal.name if is_link else target

        if is_link:
            owner = others.owner(literal, link_loc)
            link_root = _containing_root(link_loc, root_paths)
            if owner:
                _keep(p, f"shared:{owner}", tombstone=False)
            elif link_root is None:
                _keep(p, "outside_roots")
            elif _in_hal0_state(link_loc, state_guard):
                _keep(p, "hal0_state")
            else:
                _unlink(link_loc, link_root, expect_link=True)

        owner = others.owner(target)
        target_root = _containing_root(target, root_paths)
        if owner:
            _keep(target, f"shared:{owner}", tombstone=False)
            continue
        if target_root is None:
            _keep(target, "outside_roots")
            continue
        if _in_hal0_state(target, state_guard):
            _keep(target, "hal0_state")
            continue
        if _unlink(target, target_root, expect_link=False):
            released.append((sha, p))

    kept_refs = [(sha, p) for p, sha in planned if sha and (sha, p) not in released]
    _release_refs(registry, model.id, out, released, kept_refs)

    # The by-id pointer (pulled models) dangles once its target is gone.
    with contextlib.suppress(Exception):
        store_root = store_cfg.store_root().resolve()
        pointer = store_root / store_cfg.entry_pointer(model.id).relative_to(store_cfg.store_root())
        if pointer.is_symlink() and not pointer.exists():
            _unlink_under(pointer, store_root, expect_link=True)

    # Never rmdir the by-id pointer dir or a remaining row's model directory.
    keep_dirs = others.dir_paths() | kept_dirs
    if by_id is not None:
        keep_dirs.add(by_id)
    for start, root in removed_dirs:
        _prune_empty_parents(start, root, keep_dirs)

    _record_tombstones(out, to_tombstone)
    return out


async def remove_model_draft(registry: Any, model_id: str) -> dict[str, Any]:
    """Unpair and delete a model's drafter file (``DELETE /api/models/{id}/draft``).

    The row is cleared first, so the delete guards (:func:`delete_model_files_on_disk`)
    see the file's remaining owners only: a drafter another model still pairs is
    kept (``bytes_freed`` 0). Raises ``404 model.draft_not_found`` when nothing
    is paired. Returns ``{"model_id", "removed", "bytes_freed"}``.
    """
    from hal0.errors import NotFound

    model = registry.get(model_id)
    draft = model.draft
    if not draft:
        raise NotFound(
            f"model {model_id!r} has no drafter paired",
            code="model.draft_not_found",
            details={"model_id": model_id},
        )
    metadata = {k: v for k, v in model.metadata.items() if k != "draft"}
    registry.update(model_id, {"draft": None, "draft_kind": None, "metadata": metadata})
    _tombstone_unless_shared(registry, model_id, draft)
    outcome = delete_model_files_on_disk(
        registry,
        model,
        [(draft, None)],
        delete_files=True,
        roots=deletion_roots(),
    )
    return {"model_id": model_id, "removed": draft, "bytes_freed": outcome["bytes_freed"]}


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
    # Registry-sourced rows keyed by id, so the FLM-probe dedupe below can
    # patch a surviving row in place instead of only being able to skip it
    # (see the ``fm["tag"] in seen`` branch — #1647).
    by_id: dict[str, dict[str, Any]] = {}
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
        if isinstance(chk, dict) and str(chk.get("reason") or "").startswith("shard_"):
            # A later part of a split GGUF moved (#2761): the first part's sha
            # still matches, so the per-row recompute below would hide it.
            # Re-check against the installed part rows so an applied update
            # clears the badge instead of replaying the cached verdict.
            from hal0.registry.update_check import load_shard_shas, shard_update_pending

            current = await asyncio.to_thread(load_shard_shas, registry, entry.id)
            dumped["update_available"] = shard_update_pending(chk, current)
        elif isinstance(chk, dict):
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
        data.append(dumped)
        seen.add(entry.id)
        by_id[entry.id] = dumped
    # "Don't surface invisible models": the composite ``hal0``/npu upstream
    # advertises FLM slot-default tags via /v1/models even when the weights are
    # not on disk, so they used to leak into the catalog as available-but-
    # uninstalled rows. The dedicated FLM probe below is the authoritative
    # source (it re-adds the INSTALLED ones with the right npu shape), so drop
    # every FLM-servable tag from the generic upstream advertisement. The probe
    # is module-cached, so the second call in the injector below is O(1); a
    # cold cache is probed off the event loop (#2334).
    flm_skip: set[str] = set()
    try:
        from hal0.providers.flm import flm_served_models_async as _flm_probe

        for _fm in await _flm_probe():
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
    # is FLM's native tag; ``backend``/``device`` map it to the npu device, and
    # ``capabilities`` + an explicit ``device`` let the slot-swap popover
    # derive type/device without requiring a registry entry.
    try:
        from hal0.providers.flm import flm_served_models_async

        for fm in await flm_served_models_async():
            if not fm.get("installed"):
                continue
            # A registry row already covers this installed model under FLM's
            # native colon tag (the FLM pull flow registers ``gemma4-it:e4b``
            # with backends=["npu"]) — emitting the probe row too listed every
            # pulled FLM model twice, once undeletable (#duplicate-FLM-rows).
            if fm["tag"] in seen:
                # The registry row can be mistyped (#1647: the FLM pull path
                # used to hardcode capabilities=["chat"] regardless of the
                # model's real modality). The live probe knows better — merge
                # its classification into the surviving row instead of
                # silently discarding it, so an embed/stt model pulled via
                # FLM doesn't masquerade as chat-only forever with no
                # migration path.
                row = by_id.get(fm["tag"])
                probe_caps = list(fm.get("capabilities") or [])
                if row is not None and probe_caps and row.get("capabilities") != probe_caps:
                    row["capabilities"] = probe_caps
                # Type the surviving row chat-first, the same rule the
                # probe-only rows below follow: a multimodal FLM chat tag
                # (chat + stt) typed "transcription" vanished from the NPU
                # chat picker, which only lists llm rows.
                if row is not None:
                    row["type"] = flm_dispatch_type(row.get("capabilities"))
                # #1656: this dedupe skip fires BEFORE the legacy ``<tag>-FLM``
                # id below is ever computed, so a slot whose persisted model
                # default is still that pre-#1629 generated id (created
                # before the dedupe change) no longer finds a matching row
                # via the UI's exact `m.id === id` lookup — dispatch still
                # routes it correctly server-side, only the model-editor
                # reference goes dangling. Stamp the legacy id onto the
                # surviving row's `aliases` so the UI lookup can fall back to
                # it (see `slotModelRow` in slot-shared.js).
                if row is not None:
                    legacy_id = fm["tag"].replace(":", "-") + "-FLM"
                    aliases = row.setdefault("aliases", [])
                    if legacy_id not in aliases:
                        aliases.append(legacy_id)
                continue
            # The row id is FLM's native colon tag, the same id a registry
            # row for a pulled FLM model carries, so the picker lists one
            # naming scheme. The pre-native ``<tag>-FLM`` id stays as an
            # alias for slots still bound to it (slotModelRow falls back to
            # ``aliases``); the slot-apply gate accepts either form.
            mid = fm["tag"]
            legacy_id = mid.replace(":", "-") + "-FLM"
            if mid in seen or legacy_id in seen:
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
                    "type": flm_dispatch_type(caps),
                    "capability": primary,
                    "capabilities": caps,
                    "device": "npu",
                    # The engine identity registry rows get from model_to_dict:
                    # the picker's runtime chip and isFlmModel read it.
                    "provider_effective": "flm",
                    "runs_on": ["npu"],
                    "aliases": [legacy_id],
                }
            )
    except Exception:
        # Probe unavailable (no flm binary / dev host) — skip silently; the
        # rest of the catalog still renders.
        pass
    return {"models": data, "count": len(data), "filtered_aliases": filtered}


# ── add-by-path (POST /api/models/add-from-path) ──────────────────────────────


def refuse_unsupported_quant(
    model: str, quant: str | None, *names: str | None, contents: str | None = None
) -> None:
    """Raise 422 ``models.unsupported_quant`` for a fork-only quant.

    ROCmFPX-family, IU4 and ActiveFPX GGUFs need a llama.cpp fork hal0 no
    longer ships (ADR-0028), so model add and pull refuse them up front
    instead of registering a model no slot can load. Pass file names only —
    never an HF repo id or an operator-chosen model id, which can name a fork
    family while the file is plain (``Hal0ai/hal0-brain-sft-ROCmFPX-GGUF``
    carries a plain F16).
    """
    from hal0.errors import UnprocessableEntity
    from hal0.registry.detect import unsupported_quant, unsupported_quant_message

    found = unsupported_quant(quant, *names) or contents
    if found is None:
        return
    raise UnprocessableEntity(
        unsupported_quant_message(model, found),
        code="models.unsupported_quant",
        details={"model": model, "quant": found},
    )


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
      * ``ImageAsset`` (422) ``model.image_asset`` — the file sits in
        ComfyUI's model tree, which the image catalog owns.
      * ``UnprocessableEntity`` ``models.unsupported_quant`` — a fork-only
        quant (ROCmFPX family, IU4, ActiveFPX) no hal0 runner can load.
      * ``UnprocessableEntity`` ``models.unsupported_moonshine_layout`` — a
        streaming Moonshine ``.ort`` set the runner cannot load.
      * ``ModelAlreadyExists`` (409) — id registered and ``overwrite=false``.

    Returns the serialised model dict (:func:`model_to_dict`).
    """
    from hal0.config.loader import load_hal0_config
    from hal0.errors import BadRequest
    from hal0.registry.discover import _normalise_id, is_comfyui_asset
    from hal0.registry.model import Model
    from hal0.registry.pull import ImageAsset

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

    if is_comfyui_asset(path):
        raise ImageAsset(
            f"{str(path)!r} is in ComfyUI's model tree, which the model registry does not "
            "hold; manage image models under Image Gen → Models or with "
            "`hal0 comfyui models install <id>`",
            details={"path": str(path)},
        )

    from hal0.registry.detect import (
        unsupported_moonshine_layout,
        unsupported_moonshine_layout_message,
    )

    if unsupported_moonshine_layout(path):
        from hal0.errors import UnprocessableEntity

        raise UnprocessableEntity(
            unsupported_moonshine_layout_message(path),
            code="models.unsupported_moonshine_layout",
            details={"path": str(path)},
        )

    # Enforce the same extension allow-list the scan walker uses so
    # accidentally pointing at a tokenizer.json or a README.md fails
    # loudly rather than landing in the registry.
    #
    # #1415: check the path the OPERATOR supplied, not the resolved target.
    # HuggingFace's hub cache stores bytes as extensionless sha-named blobs
    # and exposes the real filename only as a symlink
    # (``snapshots/<rev>/<name>.gguf -> ../../blobs/<sha256>``), which is also
    # the shape of hal0's own ``/mnt/ai-models/local/*`` farm and of anything
    # the ``hf`` tooling lands. Resolving first made every one of those fail
    # with ``file extension ''`` for a path that visibly ends in ``.gguf``.
    # The resolved path is still what gets detected and stored. The scan
    # walker (``registry.discover.find_candidates``) already tests the
    # un-resolved walk entry, so it needs no matching change.
    cfg = load_hal0_config()
    allowed_exts = {e.lower() for e in cfg.models.file_extensions}
    suffix = path.suffix.lower() or resolved.suffix.lower()
    if suffix not in allowed_exts:
        raise BadRequest(
            f"file extension {suffix!r} not in [models].file_extensions",
            code="model.unsupported_format",
            # Echo the operator's own path — quoting an opaque sha blob they
            # never typed reads like a hal0 bug rather than a rejected input.
            details={"path": str(path), "allowed": sorted(allowed_exts)},
        )

    # #1838: pass the operator-typed name for the capability filename-token
    # fallback — a HF hub-cache symlink resolves to a sha-named blob whose
    # name carries no signal at all, which would otherwise silently hide a
    # real filename token like "jina-reranker-*.gguf" from detect().
    detection = detect(resolved, filename_hint=path.name)
    refuse_unsupported_quant(path.name, detection.quant, path.name)
    # A fork build renamed to a generic name still declares its own tensor
    # types; check the file, not only its name.
    from hal0.registry.detect import unsupported_gguf_contents

    refuse_unsupported_quant(path.name, None, contents=unsupported_gguf_contents(resolved))
    raw_labels = body.get("labels")
    if isinstance(raw_labels, list) and raw_labels:
        capabilities = [str(c) for c in raw_labels if isinstance(c, str) and c.strip()]
    else:
        capabilities = list(detection.suggested_capabilities) or ["chat"]

    raw_id = body.get("id")
    if isinstance(raw_id, str) and raw_id.strip():
        model_id = raw_id.strip()
    else:
        # Slug of the filename stem — same source discover.scan_and_register
        # uses (``CandidateModel.suggested_id = _normalise_id(naming_source.stem)``,
        # registry/discover.py), so a file registered by hand and the same
        # file picked up by the install-time auto-scan land on the same id.
        # Previously this preferred the GGUF header's ``general.name`` /
        # ``general.basename`` (via ``detection.suggested_name``), which is
        # the model's upstream architecture label, not a filesystem identity
        # — e.g. a "jina-reranker-v2" file registering as id
        # "jina-bert-implementation" (#1796). The stem comes from the
        # operator's path, not the resolved target — a hub-cache symlink
        # resolves to a sha-named blob whose stem is 64 hex chars (#1415).
        model_id = _normalise_id(path.stem)

    raw_name = body.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        display_name = raw_name.strip()
    else:
        display_name = detection.suggested_name or path.stem

    overwrite = bool(body.get("overwrite", False))

    # Optional drafter (ADR-0031 §4): screened against the target's header
    # before anything is written, so a refusal registers nothing.
    draft_body: dict[str, Any] = {}
    drafter = None
    if body.get("draft") not in (None, ""):
        draft_body = {"draft": body["draft"], "path": str(resolved)}
        if "draft_kind" in body:
            draft_body["draft_kind"] = body["draft_kind"]
        drafter = screen_draft_write(None, draft_body, require_on_disk=True)

    try:
        size_bytes = resolved.stat().st_size
    except OSError:
        size_bytes = 0

    metadata: dict[str, Any] = {"discovered": True, "source": "add-from-path"}
    if detection.context_length is not None:
        metadata["context_length"] = detection.context_length
    # #1838: surface how confident detect() actually is instead of
    # dropping it on the floor — an operator who supplied no explicit
    # ``labels`` is trusting auto-detection, and a "medium"/"low" result
    # (filename guess) needs to reach the API response and the CLI line,
    # not just detect()'s return value.
    explicit_labels = isinstance(raw_labels, list) and raw_labels
    if not explicit_labels:
        metadata["detection_confidence"] = detection.confidence
    # A failed GGUF header read is a fact about the FILE, not the requested
    # capability — surface it even when the caller supplied explicit
    # ``labels`` (only capability got overridden; the bytes on disk may
    # still be garbage, and backends still come from detect()'s guess).
    if detection.raw_hints.get("gguf_header_read") == "failed":
        metadata["detection_warning"] = (
            "no valid GGUF header found (bad or missing magic bytes); "
            "capabilities/backends are a filename guess"
        )

    draft_facts: dict[str, Any] = {}
    if drafter is not None:
        stamp_draft_fact(draft_body, drafter, metadata)
        metadata = draft_body["metadata"]
        draft_facts = {"draft": draft_body["draft"], "draft_kind": draft_body["draft_kind"]}

    try:
        model = Model(
            id=model_id,
            name=display_name,
            path=str(resolved),
            size_bytes=size_bytes,
            quant=detection.quant,
            **draft_facts,
            capabilities=capabilities,
            backends=list(detection.suggested_backends),
            # Task 3: stamp provider alongside the detected backends on this
            # NEW row — see commit_scan_rows' matching stamp.
            provider=derive_model_provider(detection.suggested_backends),
            metadata=metadata,
            # GGUF general.architecture from the header read above — see
            # commit_scan_rows' matching stamp (model↔runner arch fit-check,
            # hal0#2118).
            architecture=detected_architecture(detection),
        )
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"invalid Model payload: {exc}") from exc

    if overwrite and registry.has(model_id):
        registry.remove(model_id)

    # ModelAlreadyExists (409) propagates to the typed envelope unchanged.
    registry.add(model)
    # An explicit add brings a deleted-but-kept file back: drop its
    # tombstone so a later registry-only delete + scan behaves normally
    # (ADR-0026).
    tombstones.clear(path)

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


# ── Drafter fit check (POST /api/models/inspect-draft, ADR-0031 §2) ─────────

#: Same 5 minute TTL as :data:`INSPECT_CACHE`; keyed by the four coordinates
#: ``(hf_repo, hf_filename, draft_repo, draft_filename)``. Failures are not cached.
INSPECT_DRAFT_CACHE: dict[tuple[str, str, str, str], tuple[float, dict[str, Any]]] = {}


async def inspect_draft(body: dict[str, Any]) -> dict[str, Any]:
    """Read a target's and a drafter's GGUF headers from the Hub and check the fit.

    Body: ``{"hf_repo", "hf_filename", "draft_repo", "draft_filename"}`` (the
    target's main quant and the candidate drafter). Reads the leading bytes of
    each file by HTTP range, so a check costs tens of MB. A fit failure is a
    200 with ``fit.ok = false`` and the reasons; a file whose header cannot be
    read is ``422 hf.header_unreadable`` naming it; a file that is not a
    drafter is ``422 model.draft_not_a_drafter``.
    """
    import time

    from hal0.errors import BadRequest, UnprocessableEntity
    from hal0.registry.drafters import classify_drafter, draft_fit_errors
    from hal0.upstreams.hf_header import fetch_gguf_header
    from hal0.upstreams.huggingface import normalise_repo_slug

    coords: list[str] = []
    for key in ("hf_repo", "hf_filename", "draft_repo", "draft_filename"):
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            raise BadRequest(f"'{key}' is required", code="hf.bad_request")
        coords.append(value.strip())
    target_repo = normalise_repo_slug(coords[0])
    draft_repo = normalise_repo_slug(coords[2])
    key4 = (target_repo, coords[1], draft_repo, coords[3])

    now = time.time()
    cached = INSPECT_DRAFT_CACHE.get(key4)
    if cached is not None and now - cached[0] < INSPECT_TTL_SECONDS:
        return cached[1]

    async def header(repo: str, filename: str) -> dict[str, Any]:
        found = await fetch_gguf_header(repo, filename)
        if found is None:
            raise UnprocessableEntity(
                f"could not read the GGUF header of {repo}/{filename}",
                code="hf.header_unreadable",
                details={"repo": repo, "filename": filename},
            )
        return found

    target = await header(target_repo, coords[1])
    draft_header = await header(draft_repo, coords[3])

    info = classify_drafter(draft_header)
    if info is None:
        arch = draft_header.get("general.architecture")
        raise UnprocessableEntity(
            f"not a speculative-decoding drafter (architecture {arch!r})",
            code="model.draft_not_a_drafter",
            details={"reasons": [f"architecture {arch!r} is not a drafter"]},
        )
    reasons = draft_fit_errors(target, info)
    t_arch = target.get("general.architecture")
    result = {
        "kind": info.kind,
        "variant": info.variant,
        "fit": {"ok": not reasons, "reasons": reasons},
        "draft": info.as_metadata(),
        "target": {
            "architecture": t_arch,
            "embedding_length": target.get(f"{t_arch}.embedding_length"),
            "block_count": target.get(f"{t_arch}.block_count"),
        },
    }
    INSPECT_DRAFT_CACHE[key4] = (now, result)
    return result


# ── the model-facts screen (ADR-0029 §6) ─────────────────────────────────────
#
# A model row holds file facts only — ``chat_template``, ``tokenizer_repo``,
# ``mtp`` (the file has MTP heads) and ``jinja`` — as top-level fields. Every
# tune lives on the profile of the slot that runs the model. A write in the
# old shape is refused rather than silently dropped: ``Model`` ignores unknown
# keys, so without this screen a ``defaults`` table or a top-level
# ``context_size`` would vanish behind an HTTP 200.

#: Top-level keys that used to tune a model (under ``defaults``) and now
#: belong on a profile. Named so a client that flattened its old ``defaults``
#: body gets a pointed 422 instead of a silent drop.
MODEL_TUNE_KEYS: tuple[str, ...] = (
    "context_size",
    "enable_thinking",
    "extra_args",
    "n_gpu_layers",
    "preferred_runner",
    "profile",
    "rope_freq_base",
    "vision",
)


def screen_model_facts(body: dict[str, Any]) -> None:
    """Refuse the pre-ADR-0029 write shape and malformed fact values.

    Three checks, in order:

    1. A ``defaults`` key at all (any value, ``null`` included) is ``422
       model.defaults_removed``: the facts are top-level now.
    2. A top-level tuning key (:data:`MODEL_TUNE_KEYS`) is ``422
       model.tune_key_removed``: tunes belong on a profile.
    3. A fact whose value is the wrong type (``{"mtp": "maybe"}``) is ``400
       model.fact_invalid`` instead of escaping to
       :func:`hal0.registry.store.merge_update`, which wraps every failure in a
       500.
    """
    from pydantic import ValidationError

    from hal0.errors import BadRequest, UnprocessableEntity
    from hal0.registry.model import MODEL_FACT_FIELDS, Model

    if "defaults" in body:
        nested = body.get("defaults")
        keys = sorted(str(k) for k in nested) if isinstance(nested, dict) else []
        raise UnprocessableEntity(
            "a model write no longer takes a 'defaults' object. Send the file facts "
            "(chat_template, tokenizer_repo, mtp, jinja) as top-level fields; every "
            "tune belongs on the profile of the slot that runs the model.",
            code="model.defaults_removed",
            details={"keys": keys, "facts": list(MODEL_FACT_FIELDS)},
        )

    tunes = sorted(k for k in MODEL_TUNE_KEYS if k in body)
    if tunes:
        raise UnprocessableEntity(
            f"a model row does not hold tunes: {', '.join(tunes)}. Put them on the "
            "profile of the slot that runs the model (profile_create / profile_update).",
            code="model.tune_key_removed",
            details={"keys": tunes, "facts": list(MODEL_FACT_FIELDS)},
        )

    facts = {k: body[k] for k in MODEL_FACT_FIELDS if k in body}
    if not facts:
        return
    try:
        Model.model_validate({"id": "fact-screen", "path": "fact-screen", **facts})
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ())) or "facts"
        raise BadRequest(
            f"{field} is not a valid value: {first.get('msg', 'invalid')}",
            code="model.fact_invalid",
            details={"field": field, "reason": first.get("msg")},
        ) from exc


# ── the vision↔mmproj invariant (#1380 → #1393) ───────────────────────────────
#
# A registry row that advertises ``vision`` but carries no ``mmproj`` gives the
# launch path no ``--mmproj`` to hand llama-server, so the slot loads a
# text-only model under a vision alias — a failure that surfaces minutes later
# at load time, far from the write that caused it. PR #1392 gated the model
# drawer client-side; ``screen_vision_mmproj`` is the wire-level half, shared by
# the model write screen (create/PUT/validate) and the pull route's label seed
# (#1394), which writes its row through a different door entirely.


def screen_vision_mmproj(
    capabilities: Any,
    mmproj: Any,
    *,
    require_on_disk: bool = True,
) -> None:
    """Reject a vision-capable model description with no usable projector.

    ``capabilities`` is the EFFECTIVE capability list and ``mmproj`` the
    EFFECTIVE projector for the row after the write lands — callers holding a
    sparse patch must merge it over the stored row before calling (see
    :func:`screen_model_write`), or the guard both false-negatives (a PUT adding
    ``vision`` without mentioning ``mmproj``) and false-positives (a
    defaults-only save on an already-valid vision row).

    Two violations, two codes:

    * ``model.vision_requires_mmproj`` — ``vision`` present, projector absent.
    * ``model.mmproj_not_found`` — a projector path that is not a file on this
      host. Checked only when ``require_on_disk``; the pull route passes
      ``require_on_disk=False`` because its ``mmproj`` is an HF *filename* that
      by definition has not been downloaded yet. Every path that reaches the
      registry with ``require_on_disk=True`` is a caller-supplied host path
      (``Model.mmproj`` is documented as absolute and is handed verbatim to
      ``--mmproj``); the pull worker writes the post-download path straight
      through ``registry.update``, never through this screen, so a
      still-downloading sidecar can never trip it.
    """
    from hal0.errors import BadRequest

    caps = {str(c).strip().lower() for c in capabilities or ()}
    if "vision" not in caps:
        return
    projector = mmproj.strip() if isinstance(mmproj, str) else mmproj
    if not projector:
        raise BadRequest(
            "a model advertising the 'vision' capability must carry an mmproj "
            "projector sidecar — without one the runner gets no --mmproj and "
            "loads text-only. Set mmproj, or drop the 'vision' capability.",
            code="model.vision_requires_mmproj",
            details={"capabilities": sorted(caps)},
        )
    if not require_on_disk:
        return
    from pathlib import Path

    if not Path(str(projector)).is_file():
        raise BadRequest(
            f"mmproj path does not exist on this host: {projector}. It is passed "
            "verbatim to the runner as --mmproj, so a missing file fails the "
            "slot at load time.",
            code="model.mmproj_not_found",
            details={"mmproj": str(projector)},
        )


# ── the drafter pairing (ADR-0031) ───────────────────────────────────────────


def _prior(existing: Any, key: str) -> Any:
    """``key`` from the stored row, whether it is a ``Model`` or its dump."""
    if existing is None:
        return None
    if isinstance(existing, dict):
        return existing.get(key)
    return getattr(existing, key, None)


# CodeQL note: the two drafter sinks in screen_draft_write, `os.path.isfile` and
# `os.path.getsize`, still report py/path-injection (the rule this repo already
# carries standing alerts for). Both take ONLY the value this guard returns,
# which is confined to a model-store mount root or the model's own directory
# by the lexical and the realpath `startswith` checks below. The model-directory
# root is derived from the model row's `path`, which CodeQL counts as user
# input, so the analyser cannot see the guard as a sanitiser. No new
# capability: screen_vision_mmproj already checks the projector path on disk
# the same way.
def _require_draft_inside_store(draft: str, target_path: Any) -> str:
    """The sanitised drafter path, or ``422 model.draft_outside_store``.

    The candidate is ``normpath(expanduser(draft))``; it must be absolute and
    sit inside a model-store root (:func:`hal0.config.paths.model_mount_roots`)
    or the model's own directory, lexically or once symlinks are resolved. Every
    later file operation on the drafter uses ONLY the returned value. The
    target path is normalised lexically to derive its directory and is never
    touched on disk here.
    """
    from hal0.config.paths import model_mount_roots
    from hal0.errors import UnprocessableEntity

    roots = [os.path.normpath(str(r)) for r in model_mount_roots()]
    if target_path:
        roots.append(os.path.dirname(os.path.normpath(os.path.expanduser(str(target_path)))))
    roots = [r for r in roots if os.path.isabs(r)]

    candidate = os.path.normpath(os.path.expanduser(draft))
    if os.path.isabs(candidate):
        for root in roots:
            if candidate == root or candidate.startswith(root + os.sep):
                return candidate
        real = os.path.realpath(candidate)
        for root in roots:
            real_root = os.path.realpath(root)
            if real == real_root or real.startswith(real_root + os.sep):
                return candidate
    raise UnprocessableEntity(
        f"draft must be an absolute path inside the model store or beside the model: {draft}",
        code="model.draft_outside_store",
        details={"draft": draft, "allowed_roots": roots},
    )


def screen_draft_write(
    existing: Any,
    body: dict[str, Any],
    *,
    require_on_disk: bool,
) -> Any:
    """Screen the ``draft`` fact of a model write; classify the drafter.

    ``existing`` is the stored row (a ``Model`` or its dump; ``None`` on
    create). Returns the :class:`~hal0.registry.drafters.DrafterInfo` when the
    body sets a drafter path, else ``None`` (the body leaves ``draft`` alone or
    clears it). Raises:

    * ``422 model.draft_kind_readonly`` — the body carries ``draft_kind``; it is
      derived from the file, never set.
    * ``422 model.draft_outside_store`` — the path is not absolute, or resolves
      outside every model-store root and outside the model's own directory, so
      a slot container (which mounts only those) could never open it.
    * ``422 model.draft_not_found`` — ``require_on_disk`` and the path is not a
      file. With ``require_on_disk=False`` (the pull route, whose drafter is
      not downloaded yet) a missing file is not screened and nothing is
      classified.
    * ``422 model.draft_not_a_drafter`` / ``422 model.draft_mismatch`` — from
      :func:`hal0.registry.drafters.screen_draft_fit`, against the target's
      header. A ``mtp`` drafter is also refused when the target's ``mtp`` fact
      is ``false`` — the header carries no such fact, so the check is here.
    """
    import dataclasses

    from hal0.errors import UnprocessableEntity
    from hal0.registry.drafters import screen_draft_fit
    from hal0.registry.gguf_header import read_gguf_header

    if "draft_kind" in body:
        raise UnprocessableEntity(
            "draft_kind is derived from the drafter file's header and cannot be "
            "set. Send draft (a path) and it is classified.",
            code="model.draft_kind_readonly",
        )
    if "draft" not in body:
        return None
    draft = body["draft"]
    if draft is None or (isinstance(draft, str) and not draft.strip()):
        return None
    if not isinstance(draft, str):
        raise UnprocessableEntity(
            "draft must be an absolute path string or null",
            code="model.fact_invalid",
            details={"field": "draft"},
        )
    target_path = body.get("path", _prior(existing, "path"))
    safe_draft = _require_draft_inside_store(draft, target_path)
    # The normalised path is the stored fact; nothing below reads the raw value.
    body["draft"] = safe_draft
    if not os.path.isfile(safe_draft):
        if not require_on_disk:
            return None
        raise UnprocessableEntity(
            f"draft path does not exist on this host: {draft}",
            code="model.draft_not_found",
            details={"draft": draft},
        )
    target_header = read_gguf_header(target_path) if target_path else None
    if target_header is None:
        raise UnprocessableEntity(
            "a drafter pairs only with a GGUF model whose header can be read",
            code="model.draft_mismatch",
            details={"reasons": ["target model header unreadable"]},
        )
    draft_header = read_gguf_header(safe_draft) or {}
    info = dataclasses.replace(
        screen_draft_fit(target_header, draft_header),
        size_bytes=os.path.getsize(safe_draft),
    )
    mtp = body["mtp"] if "mtp" in body else _prior(existing, "mtp")
    if info.kind == "mtp" and mtp is False:
        raise UnprocessableEntity(
            "drafter does not fit this model: target mtp fact is false",
            code="model.draft_mismatch",
            details={"reasons": ["target mtp fact is false"]},
        )
    return info


def stamp_draft_fact(
    body: dict[str, Any],
    info: Any,
    stored_metadata: dict[str, Any] | None = None,
) -> None:
    """Write the derived drafter facts into a model write body, in place.

    Call after :func:`screen_draft_write` (``info`` is its result). A body that
    does not mention ``draft`` is untouched, except that a replacing
    ``metadata`` keeps the stored ``metadata["draft"]``. Setting a path writes
    ``draft_kind`` and ``metadata["draft"]`` (the header facts plus the file's
    ``size_bytes``); clearing it (``null``) clears both. ``metadata`` is a flat
    replace in the registry, so it is rebuilt from the body's own, else the
    stored one.
    """
    if "draft" not in body:
        # ``metadata`` is a flat replace: keep the drafter facts the caller
        # did not mention so they stay in step with the draft columns.
        kept = (stored_metadata or {}).get("draft")
        if isinstance(body.get("metadata"), dict) and kept is not None:
            body["metadata"] = {**body["metadata"], "draft": kept}
        return
    metadata = dict(body.get("metadata") or stored_metadata or {})
    draft = body["draft"]
    if info is None or not draft:
        body["draft"] = None
        body["draft_kind"] = None
        metadata.pop("draft", None)
    else:
        body["draft_kind"] = info.kind
        metadata["draft"] = info.as_metadata()
        if info.size_bytes is not None:
            metadata["draft"]["size_bytes"] = info.size_bytes
    body["metadata"] = metadata


def _tombstone_unless_shared(registry: Any, model_id: str, draft: str) -> None:
    """Tombstone ``draft`` unless another model still pairs that file.

    Paths are compared resolved (the tombstone store and the scan both key on
    the resolved path, so a symlinked pairing and its target are one file).
    """

    def _resolved(p: str) -> Path:
        try:
            return Path(p).expanduser().resolve()
        except OSError:
            return Path(p)

    target = _resolved(draft)
    for other in registry.list():
        if other.id != model_id and other.draft and _resolved(other.draft) == target:
            return
    tombstones.add([target])


def settle_draft_tombstone(
    registry: Any, model_id: str, previous: str | None, body: dict[str, Any]
) -> None:
    """Keep scan from re-pairing a drafter the operator unpaired on purpose.

    Call after :func:`stamp_draft_fact`. A write that clears ``draft`` tombstones
    the drafter it had (ADR-0026's rule for a removed projector); a write that
    pairs a path lifts any tombstone on that path. A drafter another model still
    pairs is not tombstoned.
    """
    if "draft" not in body:
        return
    if body["draft"]:
        tombstones.clear(body["draft"])
    elif previous:
        _tombstone_unless_shared(registry, model_id, previous)


def screen_model_write(
    body: dict[str, Any],
    *,
    runner_images: Any = None,
    existing: dict[str, Any] | None = None,
) -> Any:
    """Validate a model create/update/validate body.

    Raises :class:`~hal0.errors.BadRequest` (or :class:`~hal0.errors.UnprocessableEntity`
    for the pre-ADR-0029 shape) on the first violation, with the same envelope
    the dashboard keys on. Shared by ``POST /api/models``, ``PUT /api/models/{id}``
    and ``POST /api/models/validate`` so the three can never drift. Screens:

    * the file facts (ADR-0029 §6) — a ``defaults`` key is ``422
      model.defaults_removed``, a top-level tuning key ``422
      model.tune_key_removed``, a malformed fact value ``400
      model.fact_invalid``. See :func:`screen_model_facts`.
    * ``capabilities`` + ``mmproj`` — the vision↔mmproj invariant (#1393), via
      :func:`screen_vision_mmproj`. ``existing`` is the stored row (``None`` on
      create) and the two fields are read body-over-stored, because PUT bodies
      are sparse: a body that adds ``vision`` must be screened against the
      STORED projector.

      Deliberately scoped to writes that TOUCH one of the two keys. Rows already
      in the broken state — written before this guard existed, or by the pull
      worker — stay editable, so an unrelated rename does not have to fix an
      invariant it never violated. Every write that could CREATE or PRESERVE the
      broken pairing is still screened.
    * ``draft`` / ``draft_kind`` — the speculative-decoding drafter pairing
      (ADR-0031), via :func:`screen_draft_write`. Returns its classification
      (``DrafterInfo``) so the write routes can stamp ``draft_kind`` without
      reading the headers twice; ``None`` when the body sets no drafter.
    * ``provider`` — must be a ``hal0.model_meta.RUNTIME_FAMILIES`` member when
      present, else ``400 model.provider_invalid``. ``None``/absent means
      "derive" and is never screened here.

    ``runner_images`` is accepted and unused so the call sites need no change.
    """
    del runner_images
    from hal0.errors import BadRequest

    if "capabilities" in body or "mmproj" in body:
        prior = existing or {}
        screen_vision_mmproj(
            body.get("capabilities", prior.get("capabilities")),
            body.get("mmproj", prior.get("mmproj")),
        )

    screen_model_facts(body)
    drafter = screen_draft_write(existing, body, require_on_disk=True)

    provider = body.get("provider")
    if provider is not None:
        from hal0.model_meta import RUNTIME_FAMILIES

        if provider not in RUNTIME_FAMILIES:
            raise BadRequest(
                f"provider must be one of {', '.join(RUNTIME_FAMILIES)} (got {provider!r})",
                code="model.provider_invalid",
            )
    return drafter


# (The duplicate-model service — ``POST /api/models/{id}/duplicate`` — was
# removed 2026-08: duplicate registry rows for "same weights, different
# settings" confused which row was the original. Running one file two ways is
# two profiles: a model holds file facts, the profile of the slot that runs it
# holds the tune.)
