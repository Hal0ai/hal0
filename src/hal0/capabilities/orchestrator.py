"""CapabilityOrchestrator — bridge between capability children and slots.

The dashboard treats embed/voice/img as "capability slots" with multiple
children each (embed.embed + embed.rerank, voice.stt + voice.tts,
img.img). Under the hood every child maps 1:1 to a regular hal0 slot
managed by :class:`~hal0.slots.manager.SlotManager`. This module owns:

  - The ``_CHILD_TO_SLOT`` mapping that defines that bridge.
  - Persistence of the operator's selections in
    ``/etc/hal0/capabilities.toml``.
  - The lifecycle dispatch — ``apply()`` flips slots load/swap/unload to
    match the new selection. Reconciling the selection against the
    underlying slot's TOML is delegated to
    :class:`hal0.slot_config.SlotConfigStore` (issue #697): the store
    computes a before/after ChangeSet and commits both files atomically
    before any lifecycle call, so capabilities.toml and slots/*.toml can
    no longer drift across a half-finished apply.

NPU multiplex (NPU Phase 2): a ``device=npu`` selection for a trio
modality (``embed`` / ``voice.stt``) does NOT spawn a standalone process.
Instead ``apply()`` drives the FLM trio — one ``flm serve`` anchor process
serving chat coresident with embed/asr — by writing the anchor's
``[npu]`` TOML toggles and a ``device=npu``,
``type=embedding|transcription`` slot RECORD for dispatch gating
(``v1._is_npu_trio_request``). The modality slot is never
load/swap/unloaded; the anchor is never eagerly restarted — the change
returns ``pending_reload`` and the operator applies it via the dashboard
NPU section's reload affordance. Non-trio children (rerank/tts/img/vision)
and non-NPU devices keep spawning their own slot via the regular
``load()`` path.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from hal0.capabilities.catalog import models_for_capability
from hal0.capabilities.config import (
    CapabilityConfig,
    CapabilitySelection,
    capabilities_toml_path,
    load_capabilities_config,
    save_capabilities_config,
)
from hal0.config import paths
from hal0.config.loader import load_slot_config, write_toml_atomic
from hal0.config.schema import DEVICE_DEFAULT_PROFILES
from hal0.dispatcher._npu_common import is_container_npu_cfg
from hal0.errors import BadRequest, Hal0Error, NotFound
from hal0.model_fit import evaluate_model_fit
from hal0.model_meta import canonical_device, device_to_legacy_backend
from hal0.profiles import ProfileCatalog, ResolvedProfile
from hal0.registry.store import ModelRegistry
from hal0.slot_config import SlotConfigStore, SlotSelection
from hal0.slots.manager import SlotManager

log = logging.getLogger(__name__)


# ── The bridge: capability child → underlying slot name ──────────────────────

# Hardcoded as per the spec. NPU multiplex (one slot for multiple
# children) is deferred — each child currently spawns its own slot.
_CHILD_TO_SLOT: dict[tuple[str, str], str] = {
    ("embed", "embed"): "embed",
    ("embed", "rerank"): "embed-rerank",
    ("voice", "stt"): "stt",
    ("voice", "tts"): "tts",
    ("img", "img"): "img",
    ("vision", "vision"): "vision",
}

# Inverse for status surfacing ("which child is this slot serving").
_SLOT_TO_CHILD: dict[str, tuple[str, str]] = {
    slot_name: key for key, slot_name in _CHILD_TO_SLOT.items()
}

# ── Trio dispatch discriminator: capability child → slot ``type`` ─────────────
# The FLM trio (one ``flm serve`` process serving chat + embed + asr) is gated
# in v1._is_npu_trio_request on the slot record's ``type`` field. Only the two
# trio modalities carry a type; rerank/tts/img/vision are NOT trio members and
# get no ``type`` key (their auto-created slots are plain llama-server/dedicated
# providers). Mirrors providers/flm._classify_flm_model emitting "embed"/"stt".
_CHILD_TO_SLOT_TYPE: dict[str, str] = {
    "embed": "embedding",
    "stt": "transcription",
}

# The legal capability/child surface — used by HTTP validation.
LEGAL_SLOTS: tuple[str, ...] = ("embed", "voice", "img", "vision")


# child → the ``[npu]`` TOML boolean field that controls it on container slots.
# "stt" maps to "asr" (FLM CLI flag name); "embed" maps to "embed".
_CHILD_TO_NPU_FIELD: dict[str, str] = {
    "stt": "asr",
    "embed": "embed",
}


def legal_children(slot: str) -> list[str]:
    """Return the child names valid for ``slot``."""
    return [child for (s, child) in _CHILD_TO_SLOT if s == slot]


def child_to_slot(slot: str, child: str) -> str:
    """Resolve a (slot, child) tuple to its underlying slot name.

    Raises :class:`BadRequest` for unknown combinations so HTTP routes
    surface a 400 envelope rather than a 500.
    """
    key = (slot, child)
    if key not in _CHILD_TO_SLOT:
        raise BadRequest(
            f"unknown capability child {slot!r}.{child!r}",
            code="capability.unknown_child",
            details={"slot": slot, "child": child},
        )
    return _CHILD_TO_SLOT[key]


# ── The capability mapping that drives "which capability tag does a child want" ──
_CHILD_TO_CAPABILITY: dict[tuple[str, str], str] = {
    ("embed", "embed"): "embed",
    ("embed", "rerank"): "rerank",
    ("voice", "stt"): "stt",
    ("voice", "tts"): "tts",
    ("img", "img"): "image",
    ("vision", "vision"): "vision",
}

_CAPABILITY_TO_SLOT_TYPE: dict[str, str] = {
    "chat": "llm",
    "embed": "embedding",
    "rerank": "reranking",
    "stt": "transcription",
    "tts": "tts",
    "image": "image",
    "vision": "llm",
}


# ── Errors ────────────────────────────────────────────────────────────────────


class CapabilityApplyFailed(Hal0Error):
    """503 — the underlying SlotManager call failed.

    Surfaced to the dashboard as ``{ code: "capability.apply_failed",
    detail: ... }`` so the picker UI can render a banner without
    swallowing the original message.
    """

    code = "capability.apply_failed"
    status = 503


# ── Orchestrator ──────────────────────────────────────────────────────────────


class CapabilityOrchestrator:
    """Thin overlay that maps capability selections onto slot lifecycle.

    Held as a singleton on ``app.state.capability_orchestrator`` (see
    :mod:`hal0.api`); the route handlers get one via
    :data:`hal0.api.deps.CapabilityOrchestratorDep`.
    """

    def __init__(
        self,
        slot_manager: SlotManager,
        *,
        config_path: Path | None = None,
        registry: ModelRegistry | None = None,
    ) -> None:
        self._slot_manager = slot_manager
        self._config_path = Path(config_path) if config_path else capabilities_toml_path()
        # Reconciliation seam (#697): capabilities.toml + slots/*.toml are
        # committed as one ChangeSet through the store, never via ad-hoc
        # rewrites in this class.
        self._store = SlotConfigStore(capabilities_path=self._config_path)
        self._registry = registry

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> CapabilityConfig:
        return load_capabilities_config(self._config_path)

    def _save(self, cfg: CapabilityConfig) -> None:
        save_capabilities_config(cfg, self._config_path)

    async def initialize_if_missing(self) -> None:
        """Seed ``capabilities.toml`` from existing slot configs on first boot.

        Idempotent: when the file already exists, this method first runs
        :func:`auto_migrate_capabilities_file` so a stale v1 file is
        promoted to v2 (ADR-0006 §7) before any read. When the file is
        missing we walk ``/etc/hal0/slots/{embed,stt,tts,img}.toml`` and
        lift each slot's current device/provider/model + ``enabled`` flag
        into the matching child. Slots that don't exist on disk get an
        empty selection so the dashboard can still render an "unset"
        picker.
        """
        from hal0.capabilities.config import auto_migrate_capabilities_file

        if self._config_path.exists():
            # Migrate v1 → v2 in place if needed. Idempotent on v2 files.
            try:
                auto_migrate_capabilities_file(self._config_path)
            except Exception as exc:
                # Don't let a migration crash block API startup — the
                # orchestrator already swallows initialise failures one
                # level up.
                log.warning(
                    "capabilities.migrate.skipped",
                    extra={"path": str(self._config_path), "error": str(exc)},
                )
            return

        cfg = CapabilityConfig()
        for (slot, child), slot_name in _CHILD_TO_SLOT.items():
            cfg.selections.setdefault(slot, {})
            selection = CapabilitySelection()
            try:
                slot_cfg = load_slot_config(slot_name)
            except Exception:
                # Slot TOML missing or invalid — leave the selection blank.
                cfg.selections[slot][child] = selection
                continue
            # Lift the fields we care about. v0.2: write ``device``
            # (not the deprecated ``backend``). SlotConfig auto-promotes
            # legacy ``backend`` on load, so ``slot_cfg.device`` is the
            # right v0.2 surface here.
            selection.device = canonical_device(slot_cfg.device)
            selection.provider = slot_cfg.provider
            selection.model = slot_cfg.model.default or ""
            selection.enabled = bool(slot_cfg.enabled) and bool(selection.model)
            cfg.selections[slot][child] = selection

        self._save(cfg)

    # ── shape helpers ────────────────────────────────────────────────────────
    #
    # The device-namespace normalisation helpers that used to live here
    # (``_canonical_device_id`` / ``_canonical_backend_id`` /
    # ``_slot_device_for_catalog_id`` / ``_slot_backend_for_catalog_id``)
    # moved to :mod:`hal0.model_meta` (issue #695) as
    # :func:`canonical_device` and :func:`device_to_legacy_backend`.

    def _selection_with_defaults(
        self, cfg: CapabilityConfig, slot: str, child: str
    ) -> CapabilitySelection:
        """Return the persisted selection for (slot, child), filling defaults."""
        slot_bucket = cfg.selections.setdefault(slot, {})
        return slot_bucket.setdefault(child, CapabilitySelection())

    # ── public reads ─────────────────────────────────────────────────────────

    async def get_state(self) -> dict[str, Any]:
        """Build the full GET /api/capabilities response payload.

        Resolves: catalogs (per-child picker rows from the registry),
        backends (from the hardware probe), and selections (persisted
        with live ``slot`` + ``status`` derived from SlotManager).
        """
        # Import locally so the orchestrator stays cheap to import (no
        # SlotManager dependency on module load).
        from hal0.capabilities.catalog import available_backends, catalogs_by_slot

        cfg = self._load()
        backends = available_backends()
        catalogs = catalogs_by_slot(registry=self._registry)

        selections_out: dict[str, dict[str, dict[str, Any]]] = {}
        for slot in LEGAL_SLOTS:
            selections_out[slot] = {}
            for child in legal_children(slot):
                selection = self._selection_with_defaults(cfg, slot, child)
                slot_name = _CHILD_TO_SLOT[(slot, child)]
                status_str = await self._slot_status_string(slot_name)
                # #733: npu-device selections are served coresident by the
                # FLM trio anchor — their own shadow slot never runs, so
                # "offline" there is meaningless; report the anchor's state.
                if selection.device == "npu" and selection.enabled and status_str == "offline":
                    anchor_status = await self._npu_anchor_status()
                    if anchor_status is not None:
                        status_str = anchor_status
                selections_out[slot][child] = {
                    # ``device`` is the v0.2 canonical key (ADR-0006 §7).
                    "device": selection.device,
                    # ``backend`` is emitted as a one-release alias so the
                    # v0.1.x dashboard frontend keeps rendering until the
                    # UI rework lands in v0.2.1.
                    "backend": selection.device,
                    "provider": selection.provider,
                    "model": selection.model,
                    "enabled": selection.enabled,
                    "slot": slot_name,
                    "status": status_str,
                }

        return {
            "backends": backends,
            "catalogs": catalogs,
            "selections": selections_out,
        }

    async def _npu_anchor_status(self) -> str | None:
        """Status of the FLM trio anchor slot (device=npu, type=llm).

        Shadow selections (stt/embed served coresident by the trio) have
        no process of their own — their effective status is the anchor's.
        Returns ``None`` when no enabled npu-llm slot exists so callers
        keep the selection's own status.
        """
        try:
            configs = await self._slot_manager.iter_configs()
        except Exception:
            return None
        for cfg in configs:
            if (
                cfg.get("device") == "npu"
                and cfg.get("type") == "llm"
                and cfg.get("enabled") is not False
            ):
                name = str(cfg.get("name", ""))
                if name:
                    return await self._slot_status_string(name)
        return None

    async def _slot_status_string(self, slot_name: str) -> str:
        """Return the slot's current state.value, or 'offline' if unknown.

        SlotManager.status() raises SlotNotFound for slots that haven't
        been configured yet (the embed-rerank slot, for instance, is
        only auto-created on first enable). Treat those as 'offline' so
        the dashboard always gets a string.
        """
        try:
            snap = await self._slot_manager.status(slot_name)
            return snap.state.value
        except Exception:
            return "offline"

    # ── apply ────────────────────────────────────────────────────────────────

    async def apply(
        self,
        slot: str,
        child: str,
        partial: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge a partial selection update and reconcile slot lifecycle.

        Returns the merged selection as a dict in the same shape the
        ``selections`` block of ``get_state()`` exposes. Wraps any
        underlying SlotManager / config error as :class:`CapabilityApplyFailed`
        so the HTTP layer renders a 503 envelope.
        """
        if slot not in LEGAL_SLOTS:
            raise BadRequest(
                f"unknown capability slot {slot!r}",
                code="capability.unknown_slot",
                details={"slot": slot, "legal": list(LEGAL_SLOTS)},
            )
        if child not in legal_children(slot):
            raise BadRequest(
                f"child {child!r} not valid for capability {slot!r}",
                code="capability.unknown_child",
                details={"slot": slot, "child": child, "legal": legal_children(slot)},
            )

        slot_name = _CHILD_TO_SLOT[(slot, child)]
        cfg = self._load()
        existing = self._selection_with_defaults(cfg, slot, child)
        before_enabled = existing.enabled
        before_model = existing.model
        before_device = existing.device

        # Shallow-merge the partial into the existing selection.
        # ``backend`` is accepted for one release as an alias for
        # ``device`` so legacy clients (dashboard built against v0.1.x)
        # still POST a backend key and survive.
        merged_data: dict[str, Any] = existing.model_dump()
        # Drop the deprecated alias before re-validating; the model's
        # ``_promote_backend_to_device`` hook would otherwise overwrite
        # a legitimate device update if both fields are present in the
        # dump (e.g. partial only sent ``device``).
        merged_data.pop("backend", None)
        for key in ("device", "backend", "provider", "model", "enabled"):
            if key in partial:
                if key == "backend":
                    # Translate legacy alias forward.
                    merged_data["device"] = canonical_device(str(partial[key]))
                else:
                    merged_data[key] = partial[key]
        try:
            merged = CapabilitySelection.model_validate(merged_data)
        except Exception as exc:
            raise BadRequest(
                f"invalid capability selection: {exc}",
                code="capability.invalid_selection",
                details={"slot": slot, "child": child, "partial": partial},
            ) from exc

        # Validate the model against the catalog when one is set + the
        # caller didn't explicitly clear it. We don't fail when the model
        # is empty — that's the "unset" state.
        if merged.model:
            self._validate_model_in_catalog(slot, child, merged.model, merged.device)

        # ── reconcile + persist (issue #697) ──────────────────────────────
        # The SlotConfigStore computes the post-state of BOTH
        # capabilities.toml and the underlying slot TOML as one ChangeSet
        # (compute-only), then commits it atomically BEFORE any lifecycle
        # dispatch. This replaces the old unconditional in-place rewrite:
        #
        #   - drift can no longer survive a half-finished apply — a failed
        #     commit rolls disk back to ``before``;
        #   - a lifecycle failure below leaves both files already mutually
        #     consistent, preserving the pre-#697 "persist the user's
        #     intent even if the slot bounce failed" behaviour;
        #   - the reconciliation itself (enabled selections projected onto
        #     an existing slot TOML, model_meta-translated device/backend)
        #     lives in the store where it is independently tested.
        change_set = self._store.apply(
            SlotSelection(slot=slot, child=child, slot_name=slot_name, selection=merged)
        )
        try:
            self._store.commit(change_set)
        except Exception as exc:
            raise CapabilityApplyFailed(
                f"failed to persist capability change: {exc}",
                details={"slot": slot, "child": child, "error": str(exc)},
            ) from exc

        # ── lifecycle dispatch ────────────────────────────────────────────
        enabled_changed = merged.enabled != before_enabled
        model_changed = merged.model != before_model
        backend_changed = merged.device != before_device
        # provider change does not gate any branch today — the slot TOML
        # rewrite below covers it. Reintroduce if the swap path grows a
        # provider-aware case.

        # NPU-trio fork (NPU Phase 2). When the child is a trio modality
        # (embed/stt), a device=npu selection drives the FLM trio (write the
        # anchor's [npu] TOML toggle + a device=npu,
        # type=embedding|transcription slot RECORD) instead of spawning a
        # standalone FLM process. ``is_npu_target`` gates lifecycle routing;
        # ``is_npu_modality`` gates the toggle side-effect, which must
        # also fire on DEPARTURE from npu (Case 5: npu→gpu must zero the
        # anchor's embed/asr toggle even though the new device isn't npu).
        is_npu_modality = child in _CHILD_TO_SLOT_TYPE
        is_npu_target = is_npu_modality and merged.device == "npu"
        leaving_npu = is_npu_modality and before_device == "npu" and merged.device != "npu"
        pending_reload = False

        try:
            if is_npu_target:
                # NPU trio: drive flm_args + a device=npu slot RECORD; never
                # load/swap/unload the embed/stt slot. (Decision 4: the
                # store commit above — which sets the model sub-table on an
                # enabled selection — has already run.)
                pending_reload = await self._apply_npu_trio_modality(slot_name, child, merged)
            else:
                if enabled_changed and merged.enabled:
                    # off → on: ensure the slot exists, then load with the model.
                    await self._ensure_slot_exists(slot_name, merged)
                    if merged.model:
                        await self._slot_manager.load(slot_name, model_id=merged.model)
                elif enabled_changed and not merged.enabled:
                    # on → off: best-effort unload; tolerate slots that were
                    # never loaded (status() returns OFFLINE → unload is a no-op).
                    with contextlib.suppress(Exception):
                        await self._slot_manager.unload(slot_name)
                elif merged.enabled and (model_changed or backend_changed) and merged.model:
                    # Still on, but model / backend changed → hot-swap.
                    await self._ensure_slot_exists(slot_name, merged)
                    await self._slot_manager.swap(slot_name, merged.model)

                # Leaving the NPU (npu→gpu/cpu) for a trio modality: drop it
                # from the anchor's flm_args so the FLM child stops serving
                # it (Case 5). The standard lifecycle above already loaded
                # the new GPU/CPU slot.
                if leaving_npu:
                    await self._set_flm_modality(child, enable=False)
                    pending_reload = True
        except Hal0Error:
            # Re-raise typed errors so the UI surfaces a single,
            # recognisable code. The selection is already committed (the
            # user's intent persists even if the slot bounce failed).
            raise
        except Exception as exc:
            raise CapabilityApplyFailed(
                f"failed to apply capability change: {exc}",
                details={"slot": slot, "child": child, "error": str(exc)},
            ) from exc

        # A capability change (enable/disable/model/backend) just altered
        # live slot state — refresh Hermes's context files (detached;
        # best-effort, never blocks the API response). NB: the hot-swap
        # branch above already triggers manager.swap()'s own refresh, so a
        # model/backend change fires this twice; that's harmless — the
        # render is idempotent and content-hash gated, and manager.swap()
        # must keep its spawn for direct /api/slots/<name>/swap callers.
        from hal0.agents.hermes_refresh import spawn_context_refresh

        spawn_context_refresh()

        status_str = await self._slot_status_string(slot_name)
        return {
            "backend": merged.backend,
            "provider": merged.provider,
            "model": merged.model,
            "enabled": merged.enabled,
            "slot": slot_name,
            "status": status_str,
            # NPU Phase 2: when an embed/stt change altered the FLM anchor's
            # flm_args, the live anchor must be reloaded to take effect. We
            # never auto-restart it (Decision 1) — the dashboard NPU section
            # surfaces this via its "⟳ reload to apply" affordance.
            "pending_reload": pending_reload,
        }

    # ── slot TOML helpers ────────────────────────────────────────────────────

    def _validate_model_in_catalog(
        self,
        slot: str,
        child: str,
        model_id: str,
        backend_id: str,
    ) -> None:
        # NOTE: ``backend_id`` is named for back-compat with v0.1.x call
        # sites; in v0.2 it carries a ``device`` value (gpu-rocm etc).
        # The catalog still keys on the same enum so the rename is
        # source-only.
        """Reject the merged selection if it's illegal against the catalog.

        Two distinct failure modes:

          1. ``model_id`` isn't advertised at all for this capability →
             :class:`NotFound` ``capability.unknown_model``. Likely a
             stale dashboard cache or a manual TOML edit referencing a
             removed model. The registry is consulted as a permissive
             secondary check, since users can install models the curated
             catalogue doesn't know about.

          2. ``model_id`` IS advertised, but ``backend_id`` is not in
             that model's ``backends`` list → :class:`BadRequest`
             ``capability.illegal_backend_model_pair``. This is the
             foot-gun the model-first catalog reshape was built to
             prevent (e.g. picking ``backend=npu`` with a llama.cpp GGUF
             which then crashes FLM at start-up with "Model not found").

        Empty ``backend_id`` skips the pair check — backend is allowed
        to be unset transiently while the user is still mid-selection;
        the slot lifecycle won't start until a backend lands anyway.
        """
        capability = _CHILD_TO_CAPABILITY.get((slot, child))
        if capability is None:
            return
        rows = models_for_capability(capability, registry=self._registry)
        match = next((row for row in rows if row["id"] == model_id), None)
        if match is None:
            if self._registry is not None and self._registry.has(model_id):
                return
            raise NotFound(
                f"model {model_id!r} not advertised for {slot}.{child}",
                code="capability.unknown_model",
                details={"slot": slot, "child": child, "model": model_id},
            )
        if not backend_id:
            return
        legal_backends = [b["id"] for b in match.get("backends", [])]
        if backend_id not in legal_backends:
            raise BadRequest(
                f"backend {backend_id!r} cannot serve model {model_id!r} for {slot}.{child}",
                code="capability.illegal_backend_model_pair",
                details={
                    "slot": slot,
                    "child": child,
                    "model": model_id,
                    "backend": backend_id,
                    "legal_backends": legal_backends,
                },
            )
        slot_type = _CAPABILITY_TO_SLOT_TYPE.get(capability)
        if slot_type is None:
            return
        profile = self._profile_for_fit(capability, backend_id)
        registry_for_fit = None
        if self._registry is not None:
            try:
                if self._registry.has(model_id):
                    registry_for_fit = self._registry
            except Exception:
                registry_for_fit = None
        fit = evaluate_model_fit(
            model_id=model_id,
            slot_type=slot_type,
            device=backend_id,
            profile=profile,
            registry=registry_for_fit,
            capabilities=match.get("capabilities"),
        )
        if not fit.allowed:
            details: dict[str, Any] = {
                "slot": slot,
                "child": child,
                "model": model_id,
                "backend": backend_id,
                "slot_type": slot_type,
                "fit_status": fit.status,
                "fit_reasons": list(fit.reasons),
            }
            if profile is not None:
                details["profile"] = profile.name
                details["runtime_family"] = profile.runtime_family
            raise BadRequest(
                f"model {model_id!r} is not compatible with {slot}.{child} on {backend_id!r}",
                code="capability.illegal_model_fit",
                details=details,
            )

    def _profile_for_fit(self, capability: str, device: str) -> ResolvedProfile | None:
        """Infer the runtime profile implied by a capability selection.

        The capability selection schema does not yet carry an explicit
        profile. Keep inference conservative: use profiles where the
        existing device/capability already identifies a runtime family, and
        avoid treating generic CPU as kokoro except for TTS.
        """
        profile_name: str | None = None
        if device == "npu":
            profile_name = DEVICE_DEFAULT_PROFILES.get("npu")
        elif device in {"gpu-rocm", "gpu-vulkan"}:
            profile_name = DEVICE_DEFAULT_PROFILES.get(device)
        elif capability == "tts":
            profile_name = "kokoro-cpu"
        elif capability == "image":
            profile_name = "comfyui"
        if not profile_name:
            return None
        try:
            return ProfileCatalog().resolve(profile_name)
        except Hal0Error:
            log.warning(
                "capability.profile_fit_skipped profile=%s capability=%s device=%s",
                profile_name,
                capability,
                device,
            )
            return None

    async def _ensure_slot_exists(self, slot_name: str, selection: CapabilitySelection) -> None:
        """Auto-create the slot TOML on first use of a non-builtin child.

        ``embed-rerank`` is the canonical example: it isn't a builtin
        slot, so the SlotManager would raise SlotNotFound on the first
        load. We synthesise a minimal config from the selection and let
        ``SlotManager.create()`` do the persist + state initialisation.
        """
        cfg_path = paths.slots_config_dir() / f"{slot_name}.toml"
        if cfg_path.exists():
            return

        port = self._next_free_slot_port()
        # Emit both ``device`` (v0.2 canonical) and ``backend`` (v0.1.x
        # alias) so downgrades remain legible. SlotConfig's
        # ``_promote_backend_to_device`` validator keeps them in sync.
        slot_backend = device_to_legacy_backend(selection.device)
        slot_device = canonical_device(selection.device)
        provider = selection.provider or "llama-server"
        cfg_dict: dict[str, Any] = {
            "name": slot_name,
            "port": port,
            "backend": slot_backend or "vulkan",
            "device": slot_device or "gpu-rocm",
            "provider": provider,
            "enabled": True,
            "model": {"default": selection.model or ""},
        }
        # Stamp the trio dispatch discriminator for embed/stt children so
        # v1._is_npu_trio_request can gate on it. Non-trio children
        # (rerank/tts/img/vision) get no ``type`` key.
        _, child = _SLOT_TO_CHILD.get(slot_name, (None, None))
        slot_type = _CHILD_TO_SLOT_TYPE.get(child) if child else None
        if slot_type:
            cfg_dict["type"] = slot_type
        try:
            await self._slot_manager.create(slot_name, cfg_dict)
        except Exception as exc:
            raise CapabilityApplyFailed(
                f"failed to create slot {slot_name!r}: {exc}",
                details={"slot": slot_name, "error": str(exc)},
            ) from exc

    # ── NPU trio (NPU Phase 2) ────────────────────────────────────────────────

    async def _apply_npu_trio_modality(
        self,
        slot_name: str,
        child: str,
        selection: CapabilitySelection,
    ) -> bool:
        """Drive an NPU embed/stt modality through the FLM trio.

        Never load/swap/unload the embed/stt slot — the FLM anchor (a
        single ``flm serve`` process) serves the modality coresident with
        chat. We instead:

          1. Ensure the device=npu, type=embedding|transcription slot
             RECORD exists (create path stamps ``type``).
          2. Write ``{enabled, type}`` via ``update_config`` (covers enable
             AND disable so ``v1._is_npu_trio_request``'s ``enabled is
             False`` check blocks dispatch). The ``type`` stamp is essential
             for PRE-EXISTING slots: ``_ensure_slot_exists_npu`` early-returns
             when the TOML exists, so a real drifted ``embed.toml`` with no
             ``type`` would otherwise never gate trio dispatch. ``type`` is a
             top-level SCALAR, so co-writing it is safe under Decision 4 —
             that prohibition is specifically about the nested ``model`` dict
             (replaced wholesale by the shallow merge), which we still NEVER
             pass here. Runs AFTER the store commit that reconciled the
             slot TOML.
          3. Toggle the modality on the anchor via
             :meth:`_set_flm_modality`, which writes the anchor's ``[npu]``
             TOML toggle. The anchor is never bounced.
          4. Return ``pending_reload=True`` ALWAYS (Decision 1: never
             auto-restart the live anchor; if the anchor is offline the new
             toggle won't take effect until the user loads it — still
             pending). The anchor is found by scanning ``iter_configs()``
             for ``type==llm && device==npu``; we never restart it here.
        """
        await self._ensure_slot_exists_npu(slot_name, child, selection)
        # Stamp {enabled, type} — NEVER the nested ``model`` (Decision 4).
        # ``type`` is required even for an existing slot whose TOML predates
        # Phase 2 and carries no ``type``; without it trio dispatch no-ops.
        await self._slot_manager.update_config(
            slot_name,
            {"enabled": selection.enabled, "type": _CHILD_TO_SLOT_TYPE[child]},
        )
        await self._set_flm_modality(child, enable=selection.enabled)
        # Decision 1: surface pending_reload whether or not the anchor is
        # live; we never eagerly bounce it.
        return True

    async def _set_flm_modality(self, child: str, *, enable: bool) -> None:
        """Toggle a trio modality on the FLM anchor slot.

        For containerized NPU slots the ``[npu]`` TOML table is the single
        source of truth (Phase A).  The anchor is located by scanning
        ``iter_configs()`` for ``type==llm && device==npu``; when it is a
        container slot (``is_container_npu_cfg`` returns True), the toggle is
        written via ``SlotManager.update_config``.  It takes effect on the
        next slot reload (``pending_reload``) — we NEVER bounce the anchor
        here (Decision 1: never auto-restart the live anchor; the operator
        drives the reload via the dashboard NPU section).

        Field mapping (``child`` → ``[npu]`` key): ``"stt"`` → ``"asr"``,
        ``"embed"`` → ``"embed"``.  The one-level deep merge in
        ``update_config`` preserves sibling fields (e.g. writing
        ``{"npu": {"asr": True}}`` never clobbers ``"embed"``).

        No-op when no container NPU anchor exists.
        """
        # Locate the npu LLM anchor and decide which path to take.
        anchor_name: str | None = None
        anchor_cfg: dict[str, Any] | None = None
        try:
            configs = await self._slot_manager.iter_configs()
        except Exception:
            configs = []
        for cfg in configs:
            if cfg.get("type") != "llm" or cfg.get("device") != "npu":
                continue
            name = str(cfg.get("name", "")).strip()
            if not name:
                continue
            anchor_name = name
            anchor_cfg = cfg
            break

        if anchor_name is not None and is_container_npu_cfg(anchor_cfg):
            # Container path: write the [npu] TOML toggle only. Decision 1:
            # never auto-restart the anchor — the change takes effect on the
            # next operator-driven reload (pending_reload surfaces it).
            npu_field = _CHILD_TO_NPU_FIELD[child]
            await self._slot_manager.update_config(anchor_name, {"npu": {npu_field: enable}})
            log.info(
                "npu container modality toggled: slot=%s npu.%s=%s (pending reload)",
                anchor_name,
                npu_field,
                enable,
            )
            return

    async def _ensure_slot_exists_npu(
        self, slot_name: str, child: str, selection: CapabilitySelection
    ) -> None:
        """Create the device=npu, type=embedding|transcription slot RECORD.

        Like :meth:`_ensure_slot_exists` but forces the FLM-trio shape:
        ``device=npu``, ``provider=flm``, ``backend=flm``, and always
        stamps the trio ``type`` so dispatch gating activates. No-op when
        the slot TOML already exists (its existing fields, including
        ``type``, survive — ``update_config`` is a shallow top-level merge).
        """
        cfg_path = paths.slots_config_dir() / f"{slot_name}.toml"
        if cfg_path.exists():
            return
        port = self._next_free_slot_port()
        cfg_dict: dict[str, Any] = {
            "name": slot_name,
            "port": port,
            "backend": "flm",
            "device": "npu",
            "provider": "flm",
            "enabled": bool(selection.enabled),
            "model": {"default": selection.model or ""},
            "type": _CHILD_TO_SLOT_TYPE[child],
        }
        try:
            await self._slot_manager.create(slot_name, cfg_dict)
        except Exception as exc:
            raise CapabilityApplyFailed(
                f"failed to create slot {slot_name!r}: {exc}",
                details={"slot": slot_name, "error": str(exc)},
            ) from exc

    def _next_free_slot_port(self) -> int:
        """Pick a free port in the slot range.

        Scans every existing slot TOML for its ``port``, returns the
        first gap inside ``8081-8099``. The SlotConfig validator pins
        ports into that range; collisions here would surface as a
        validation error from ``SlotManager.create``.
        """
        used: set[int] = set()
        cfg_dir = paths.slots_config_dir()
        if cfg_dir.exists():
            for p in cfg_dir.glob("*.toml"):
                try:
                    slot_cfg = load_slot_config(p.stem)
                    used.add(slot_cfg.port)
                except Exception:
                    # Malformed TOMLs don't reserve ports — they'll be
                    # surfaced via the slot routes the next time the
                    # operator looks.
                    continue
        for port in range(8081, 8100):
            if port not in used:
                return port
        # Pool is full — surface as apply_failed rather than silently
        # collide. The user will see the envelope and know to clean up.
        raise CapabilityApplyFailed(
            "no free slot port available in 8081-8099",
            details={"used": sorted(used)},
        )


__all__ = [
    "LEGAL_SLOTS",
    "CapabilityApplyFailed",
    "CapabilityOrchestrator",
    "child_to_slot",
    "legal_children",
]


# ── tomli_w guard ────────────────────────────────────────────────────────────
# ``write_toml_atomic`` (used by ``save_capabilities_config``) needs tomli_w
# at runtime. We don't re-import it here, but referencing the symbol keeps
# import-time errors loud + visible to the test harness that asserts the
# orchestrator imports cleanly.
_ = write_toml_atomic
