"""CapabilityOrchestrator — bridge between capability children and slots.

The dashboard treats embed/voice/img as "capability slots" with multiple
children each (embed.embed + embed.rerank, voice.stt + voice.tts,
img.img). Under the hood every child maps 1:1 to a regular hal0 slot
managed by :class:`~hal0.slots.manager.SlotManager`. This module owns:

  - The ``_CHILD_TO_SLOT`` mapping that defines that bridge.
  - Persistence of the operator's selections in
    ``/etc/hal0/capabilities.toml``.
  - The lifecycle dispatch — ``apply()`` flips slots load/swap/unload to
    match the new selection and rewrites the underlying slot's TOML when
    the user changes backend/provider.

NPU multiplex (one ``flm`` process serving multiple capability children)
is OUT OF SCOPE for this round; NPU children spawn their own slot via
the regular ``load()`` path when needed.
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
from hal0.errors import BadRequest, Hal0Error, NotFound
from hal0.registry.store import ModelRegistry
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
}

# Inverse for status surfacing ("which child is this slot serving").
_SLOT_TO_CHILD: dict[str, tuple[str, str]] = {
    slot_name: key for key, slot_name in _CHILD_TO_SLOT.items()
}

# The legal capability/child surface — used by HTTP validation.
LEGAL_SLOTS: tuple[str, ...] = ("embed", "voice", "img")


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
            selection.device = self._canonical_device_id(slot_cfg.device)
            selection.provider = slot_cfg.provider
            selection.model = slot_cfg.model.default or ""
            selection.enabled = bool(slot_cfg.enabled) and bool(selection.model)
            cfg.selections[slot][child] = selection

        self._save(cfg)

    # ── shape helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _canonical_device_id(slot_device: str) -> str:
        """Normalise a slot's ``device`` to the capabilities catalog id.

        After ADR-0006 §7 both surfaces speak the same enum
        (``gpu-rocm | gpu-vulkan | cpu | npu``), so this is a near-identity.
        It still tolerates a legacy ``backend``-style input
        (``vulkan|rocm|flm|moonshine|kokoro``) for forward compatibility
        with hand-edited slot TOMLs by routing through
        :func:`hal0.config.schema.map_backend_to_device`.
        """
        from hal0.config.schema import _VALID_DEVICES, map_backend_to_device

        if not slot_device:
            return ""
        if slot_device in _VALID_DEVICES:
            return slot_device
        return map_backend_to_device(slot_device)

    @staticmethod
    def _canonical_backend_id(slot_backend: str) -> str:
        """DEPRECATED — kept for tests / external callers.

        Forward to :meth:`_canonical_device_id`. Removed when the
        ``backend`` field is excised in v0.3.
        """
        return CapabilityOrchestrator._canonical_device_id(slot_backend)

    @staticmethod
    def _slot_device_for_catalog_id(device_id: str) -> str:
        """Catalog ``device`` id → SlotConfig.device string (identity)."""
        from hal0.config.schema import _VALID_DEVICES

        if device_id in _VALID_DEVICES:
            return device_id
        # Legacy round-trip — accept ``vulkan|rocm|flm|cpu`` and forward.
        from hal0.config.schema import map_backend_to_device

        return map_backend_to_device(device_id) if device_id else ""

    @staticmethod
    def _slot_backend_for_catalog_id(backend_id: str) -> str:
        """DEPRECATED — translate catalog id to the legacy ``backend`` string.

        Still used by code paths that write the deprecated SlotConfig.backend
        field (kept until v0.3 for downgrade legibility). New write paths
        should call :meth:`_slot_device_for_catalog_id` instead.
        """
        mapping = {
            "gpu-vulkan": "vulkan",
            "gpu-rocm": "rocm",
            "npu": "flm",
            "cpu": "cpu",
        }
        return mapping.get(backend_id, backend_id)

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
                    merged_data["device"] = self._canonical_device_id(str(partial[key]))
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

        cfg.selections[slot][child] = merged

        # ── lifecycle dispatch ────────────────────────────────────────────
        enabled_changed = merged.enabled != before_enabled
        model_changed = merged.model != before_model
        backend_changed = merged.device != before_device
        # provider change does not gate any branch today — the slot TOML
        # rewrite below covers it. Reintroduce if the swap path grows a
        # provider-aware case.

        try:
            # Reconcile the slot TOML against the merged selection whenever
            # the slot is going to be enabled. We rewrite unconditionally —
            # not just on a selection diff — because capabilities.toml and
            # the slot TOML can drift independently: a previous apply() that
            # failed mid-flight, a manual edit, or an install/migration seed
            # can leave the two disagreeing. Diffing the new selection
            # against the *old selection* would miss that drift and let
            # load()/swap() spawn against the stale slot TOML while we
            # report the new selection as live.
            if merged.enabled:
                await self._rewrite_underlying_slot(slot_name, merged)

            if enabled_changed and merged.enabled:
                # off → on: ensure the slot exists, then load with the model.
                await self._ensure_slot_exists(slot_name, merged)
                if merged.model:
                    await self._slot_manager.load(slot_name, model_id=merged.model)
            elif enabled_changed and not merged.enabled:
                # on → off: best-effort unload; tolerate slots that were
                # never loaded (status() will return OFFLINE → unload is a no-op).
                with contextlib.suppress(Exception):
                    await self._slot_manager.unload(slot_name)
            elif merged.enabled and (model_changed or backend_changed) and merged.model:
                # Still on, but model / backend changed → hot-swap.
                await self._ensure_slot_exists(slot_name, merged)
                await self._slot_manager.swap(slot_name, merged.model)
        except Hal0Error:
            # Re-raise typed errors as the apply_failed envelope so the UI
            # surfaces a single, recognisable code.
            self._save(cfg)  # persist the user's intent even if the slot bounce failed
            raise
        except Exception as exc:
            self._save(cfg)
            raise CapabilityApplyFailed(
                f"failed to apply capability change: {exc}",
                details={"slot": slot, "child": child, "error": str(exc)},
            ) from exc

        # Persist after the side effects so an interrupted lifecycle
        # call doesn't leave a stale selection on disk.
        self._save(cfg)

        status_str = await self._slot_status_string(slot_name)
        return {
            "backend": merged.backend,
            "provider": merged.provider,
            "model": merged.model,
            "enabled": merged.enabled,
            "slot": slot_name,
            "status": status_str,
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
        slot_backend = self._slot_backend_for_catalog_id(selection.device)
        slot_device = self._slot_device_for_catalog_id(selection.device)
        provider = selection.provider or "llama-server"
        cfg_dict = {
            "name": slot_name,
            "port": port,
            "backend": slot_backend or "vulkan",
            "device": slot_device or "gpu-rocm",
            "provider": provider,
            "enabled": True,
            "model": {"default": selection.model or ""},
        }
        try:
            await self._slot_manager.create(slot_name, cfg_dict)
        except Exception as exc:
            raise CapabilityApplyFailed(
                f"failed to create slot {slot_name!r}: {exc}",
                details={"slot": slot_name, "error": str(exc)},
            ) from exc

    async def _rewrite_underlying_slot(
        self, slot_name: str, selection: CapabilitySelection
    ) -> None:
        """Persist backend / provider changes into the underlying slot TOML.

        Routes through :meth:`SlotManager.update_config` so the override
        drop-in + env file get regenerated alongside the TOML. If the slot
        doesn't exist yet, this is a no-op — the create path below will
        write the config fresh.
        """
        cfg_path = paths.slots_config_dir() / f"{slot_name}.toml"
        if not cfg_path.exists():
            return
        slot_backend = self._slot_backend_for_catalog_id(selection.device)
        slot_device = self._slot_device_for_catalog_id(selection.device)
        updates: dict[str, Any] = {}
        if slot_backend:
            # Deprecated field, kept for one release — see ADR-0006 §7.
            updates["backend"] = slot_backend
        if slot_device:
            updates["device"] = slot_device
        if selection.provider:
            updates["provider"] = selection.provider
        if selection.model:
            updates["model"] = {"default": selection.model}
        if not updates:
            return
        try:
            await self._slot_manager.update_config(slot_name, updates)
        except Exception as exc:
            raise CapabilityApplyFailed(
                f"failed to rewrite slot {slot_name!r}: {exc}",
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
