"""Seeded slot catalogue + request-routing helpers (P3-slots §1i).

Pure config-query routing: no state-machine coupling (never touches
``_states``/``_transition``/locks). Extracted verbatim out of
``hal0.slots.manager`` — every function here takes a narrow ``host``
(anything exposing ``iter_configs``/``create``/``delete``, i.e. a
:class:`hal0.slots.manager.SlotManager`) instead of assuming the full
manager, so it stays independently readable. ``SlotManager`` keeps every
name as a thin delegator (P3-slots §5 contract): ``seeded_slots``,
``default_slot_for``, ``loaded_slot``, ``resolve_for_request``,
``route_for_request``, ``add_slot``, ``remove_slot`` are all still callable
exactly as before.

Module-level re-exports from ``hal0.slots.manager`` (unchanged import
paths): ``SEEDED_SLOTS``, ``NPU_SEEDED_SLOTS``, ``SLOT_ALIASES``,
``LoadedSlot``.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from hal0.model_meta import SLOT_TYPES
from hal0.slots.state import SlotConfigError

if TYPE_CHECKING:
    from hal0.slots.manager import Slot

log = logging.getLogger(__name__)

# ── Seeded slot catalogue (PR-10, plan §4.2 + §10.2) ────────────────────────

#: Slots that exist on every hal0 install regardless of hardware. The
#: dashboard creates these as empty cards at first run; the bundle
#: picker (Phase 5) populates their ``model.default`` fields. ``agent``
#: is the GPU MoE chat-role sibling of ``chat`` (moved here from the NPU
#: set in #679 — it is a GPU slot, not the NPU FLM anchor).
# ADR-0023: `utility` (cheap helper) + `agent` (capable/default anchor) are the two
# canonical llm seeds. `chat` is retired as a slot/role name (the `chat` *capability*
# is unaffected; any llm slot serves it). `utility` is seeded so the memory
# extraction target is always present on a fresh box.
SEEDED_SLOTS: tuple[str, ...] = (
    "utility",
    "embed",
    "rerank",
    "stt",
    "tts",
    "img",
    "vision",
    "agent",
)

#: NPU FLM shadow slots seeded only when the FastFlowLM ``.deb`` is
#: installed (``shutil.which('flm')`` truthy): the ASR + embed tags that
#: ride the same coresident FLM process as the NPU chat anchor — the
#: separate ``flm`` slot, NOT listed here. ``agent`` was previously
#: (wrongly) in this set; it is a GPU chat-role slot and moved to
#: SEEDED_SLOTS in #679. Opt-in at Pro+ bundle tier.
#:
#: Named ``{anchor}-stt`` / ``{anchor}-embed`` (anchor is ``flm``) to match
#: the occupancy-pane virtual sub-cards (:mod:`hal0.api.routes.npu` synthesises
#: ``s.name + "-stt"``) and the ``_touch_npu_shadow_count`` activity counters
#: (:mod:`hal0.api.routes.v1`), which both key off that convention. The legacy
#: ``stt-npu`` / ``embed-npu`` names never matched either surface and are
#: migrated forward by :meth:`hal0.slots.npu.trio.reconcile_trio_slots`.
NPU_SEEDED_SLOTS: tuple[str, ...] = ("flm-stt", "flm-embed")

#: Back-compat alias map: old slot names → canonical new names.
#: Aliases resolve transparently for dispatch and config lookup but are
#: NEVER stored on disk and NEVER appear in list() / iter_configs() /
#: /api/slots. ``agent-hermes`` maps to ``agent`` (a GPU seed slot, #679)
#: so no new TOML is created — the alias just redirects old references.
#: ADR-0023 retired the `primary` and `chat` aliases. The canonical roles are
#: `agent` (default anchor) + `utility` (helper); a lingering operator-custom `chat`
#: slot is reachable by its own name via generalized `hal0/<slot>` resolution, not an
#: alias. Only the Hermes-era `agent-hermes` → `agent` redirect remains.
SLOT_ALIASES: dict[str, str] = {
    "agent-hermes": "agent",
    # Route curated NPU model IDs to the FLM trio slot.
    "qwen3:4b": "flm",
    "qwen3-4b": "flm",
}

#: Slot ``type`` vocabulary (plan §4.1) — sourced from the canonical
#: taxonomy (:data:`hal0.model_meta.SLOT_TYPES`) so validation, profiles,
#: and /api/meta/enums can never drift.
_VALID_SLOT_TYPES: frozenset[str] = frozenset(SLOT_TYPES)

#: Slot-name policy: kebab-case, max 32 chars, leading alphanumeric.
_SLOT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True, slots=True)
class LoadedSlot:
    """Typed routing result for a slot with a model bound.

    Returned by :meth:`SlotManager.resolve_for_request` and
    :meth:`SlotManager.loaded_slot` so callers do not have to route to a
    bare name and then reopen raw slot TOML to recover the model id,
    device, labels, or system prompt.

    ``tool_calling`` is the §7.1d 🔴 routing-gate fix: the omni-router's
    master "does this caller get any tools at all" gate reads this typed
    bool (sourced from the registry model's ``capability_flags.tool_calling``
    when available) instead of ``"tool-calling" in labels`` — a model
    tagged ``tool-calling`` in the registry no longer needs a hand-authored
    mirror in the slot TOML's ``[model].labels`` to actually ship tools.
    ``labels`` stays for the OTHER per-tool label overlays
    (``required_labels`` — vision/edit/image/tts/embeddings/reranking).
    Those were out of scope for the §7.1d fix; #1469 gives them the same
    treatment as ``tool_calling`` — see ``modalities`` below.

    ``modalities`` (#1469): the registry's already-populated, fact-derived
    capability signal (:func:`hal0.model_meta.modality.
    derive_modalities_from_model_info` — mmproj presence, pooling_type,
    backend family), used as a fallback source for the ``required_labels``
    overlay when the slot TOML never hand-authored a matching label — which
    is every slot TOML in practice, since nothing writes ``[model].labels``.
    Excludes the uninformative ``chat`` default (no tool ever requires it).
    """

    name: str
    model_id: str
    slot_type: str
    device: str
    labels: frozenset[str]
    system_prompt: str = ""
    profile: str | None = None
    default: bool = False
    tool_calling: bool = False
    modalities: frozenset[str] = frozenset()


def seeded_slots(*, include_npu: bool | None = None) -> tuple[str, ...]:
    """Return the seeded slot list, optionally including the NPU trio.

    :data:`SEEDED_SLOTS` lands on every hal0 install. The NPU trio
    shadows (``flm-stt`` / ``flm-embed``) only seed when the
    FastFlowLM ``.deb`` is installed (``shutil.which('flm')``
    truthy). Per plan §10.2 + §4.2.

    Args:
        include_npu: ``None`` (default) detects FLM presence at
            runtime; ``True`` forces inclusion (tests + the bundle
            picker's preview mode); ``False`` forces exclusion.
    """
    if include_npu is None:
        include_npu = bool(shutil.which("flm"))
    if include_npu:
        return SEEDED_SLOTS + NPU_SEEDED_SLOTS
    return SEEDED_SLOTS


def loaded_slot_from_config(
    cfg: dict[str, Any],
    *,
    model_info: Mapping[str, Any] | None = None,
) -> LoadedSlot | None:
    """Convert one raw slot config dict into a :class:`LoadedSlot`.

    Returns ``None`` when the config does not name a slot type or has no
    ``[model].default`` — model-presence is the activation signal (#1369),
    so a model-less slot is simply not routable. The raw TOML shapes are
    intentionally absorbed here so request routers and tool dispatchers
    consume a typed result.

    ``model_info`` (optional) is the registry ``Model.model_dump()``-shaped
    dict for ``cfg``'s bound model — pass it when the caller already has a
    registry handle (:func:`loaded_slot` / :func:`resolve_for_request` do,
    via ``host._resolve_model_info``) so ``tool_calling`` reflects the
    registry's ``capability_flags.tool_calling`` rather than the slot
    TOML's ``[model].labels`` (the §7.1d 🔴 fix). When omitted, or when the
    resolved model has no explicit ``tool_calling`` (``None``), this falls
    back to the pre-fix ``"tool-calling" in labels`` check so slot TOMLs
    that predate the registry field still route tool calls.
    """
    name = str(cfg.get("name") or "").strip()
    slot_type = str(cfg.get("type") or "").strip()
    if not name or not slot_type:
        return None

    model_section = cfg.get("model") or {}
    model_id = ""
    if isinstance(model_section, dict):
        raw_model = model_section.get("default", "")
        if isinstance(raw_model, str):
            model_id = raw_model.strip()
    if not model_id:
        return None

    from hal0.model_meta import labels_of, model_capabilities_of
    from hal0.model_meta.modality import Modality, derive_modalities_from_model_info

    raw_prompt = cfg.get("system_prompt")
    system_prompt = raw_prompt if isinstance(raw_prompt, str) else ""
    if not system_prompt:
        extra = cfg.get("extra")
        if isinstance(extra, dict) and isinstance(extra.get("system_prompt"), str):
            system_prompt = extra["system_prompt"]

    profile = cfg.get("profile")
    labels = frozenset(labels_of(cfg))
    tool_calling = model_capabilities_of(model_info).get("tool_calling")
    if tool_calling is None:
        tool_calling = "tool-calling" in labels
    # #1469: fact-derived fallback for resolve_for_request's required_labels
    # overlay (see LoadedSlot.modalities docstring). CHAT is dropped — it's
    # derive_modalities's "nothing else matched" default, not a real signal,
    # and no tool ever requires it.
    modalities = frozenset()
    if model_info:
        modalities = frozenset(
            str(m) for m in derive_modalities_from_model_info(model_info) if m is not Modality.CHAT
        )
    return LoadedSlot(
        name=name,
        model_id=model_id,
        slot_type=slot_type,
        device=str(cfg.get("device") or ""),
        labels=labels,
        system_prompt=system_prompt,
        profile=profile if isinstance(profile, str) and profile else None,
        default=cfg.get("default") is True,
        tool_calling=bool(tool_calling),
        modalities=modalities,
    )


class RoutingHost(Protocol):
    """Narrow seam routing needs from :class:`hal0.slots.manager.SlotManager`."""

    async def iter_configs(self) -> list[dict[str, Any]]: ...
    async def _load_slot_config(self, slot_name: str) -> dict[str, Any]: ...
    @staticmethod
    def _resolve_alias(name: str) -> str: ...
    async def create(self, slot_name: str, slot_cfg: dict[str, Any]) -> Slot: ...
    async def delete(self, slot_name: str, *, force: bool = False) -> None: ...
    # Optional: registry lookup for the §7.1d tool_calling gate. Not part
    # of the hard Protocol contract — hosts without it (e.g. test stubs)
    # just get the label-based fall-through in loaded_slot_from_config.
    async def _resolve_model_info(self, model_id: str | None) -> dict[str, Any]: ...


async def default_slot_for(host: RoutingHost, slot_type: str) -> str | None:
    """Return the name of the slot with ``type=slot_type`` and ``default=true``.

    Plan §4.4 step 1. Exactly one ``default = true`` per type is
    allowed; two defaults raise :class:`SlotConfigError` so the
    misconfiguration surfaces at the routing call site instead of
    silently picking one. Returns ``None`` when no slot of the
    type has ``default = true`` (the caller is expected to
    fall-through to the first model-bound slot — see
    :func:`route_for_request`).
    """
    candidates: list[str] = []
    for cfg in await host.iter_configs():
        if cfg.get("type") != slot_type:
            continue
        if cfg.get("default") is True:
            candidates.append(str(cfg.get("name", "")))
    if len(candidates) > 1:
        raise SlotConfigError(
            f"slot type {slot_type!r} has multiple default=true slots: "
            f"{candidates}; exactly one is allowed",
            details={"type": slot_type, "candidates": candidates},
        )
    return candidates[0] if candidates else None


async def _model_info_for(host: RoutingHost, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort registry lookup for ``cfg``'s bound model.

    Hosts that don't implement ``_resolve_model_info`` (test stubs) get
    ``None`` back, which sends :func:`loaded_slot_from_config` down the
    label fall-through path instead of raising.
    """
    resolver = getattr(host, "_resolve_model_info", None)
    if resolver is None:
        return None
    model_section = cfg.get("model") or {}
    model_id = model_section.get("default") if isinstance(model_section, dict) else None
    if not isinstance(model_id, str) or not model_id:
        return None
    try:
        return await resolver(model_id)
    except Exception:  # registry outage must not break routing
        log.warning("routing.model_info_unavailable", extra={"model_id": model_id})
        return None


async def loaded_slot(host: RoutingHost, name: str) -> LoadedSlot | None:
    """Return a typed view of a model-bound configured slot, or ``None``.

    Resolves back-compat aliases transparently. This is a read-only
    inventory helper; it does not probe runtime state.
    """
    resolved = host._resolve_alias(name)
    try:
        cfg = await host._load_slot_config(resolved)
    except SlotConfigError:
        return None
    model_info = await _model_info_for(host, cfg)
    return loaded_slot_from_config(cfg, model_info=model_info)


async def resolve_for_request(
    host: RoutingHost,
    slot_type: str,
    *,
    required_labels: tuple[str, ...] = (),
) -> LoadedSlot | None:
    """Resolve a request of type ``slot_type`` to a loaded slot.

    Plan §4.4 four-step routing:

      1. **Type match + default.** If a slot of ``type=slot_type``
         carries ``default = true``, prefer it.
      2. **Label filter overlay.** When ``required_labels`` is
         non-empty, the chosen slot's model must advertise every
         required label — sourced from the slot's hand-authored
         ``model.labels`` list, falling back to the slot's registry-
         derived ``modalities`` (#1469; see ``LoadedSlot.modalities``)
         for any label that folds onto the closed :class:`Modality`
         taxonomy via :func:`hal0.model_meta.modality.normalize_modality`
         (``vision``/``tts``/``image``, and the tool-taxonomy aliases
         ``transcription``/``embeddings``/``reranking``). A label with
         no modality equivalent (``edit``) only ever matches an explicit
         TOML label — there is no fact-derived signal for it. The
         default is dropped if it can't satisfy the overlay.
      3. **Fall-through.** Otherwise pick the first model-bound slot
         of ``slot_type`` in TOML declaration order (still satisfying
         the label overlay if any).
      4. ``None`` when nothing matches.
    Returning :class:`LoadedSlot` keeps callers from reopening raw slot
    configs to discover the model id, labels, device, or system prompt.
    """
    from hal0.model_meta.modality import normalize_modality

    def _satisfies(slot: LoadedSlot) -> bool:
        if not required_labels:
            return True
        if set(required_labels).issubset(slot.labels):
            return True
        folded = {normalize_modality(label) for label in required_labels}
        if None in folded:
            # At least one required label has no modality equivalent
            # (e.g. "edit") — only an explicit TOML label can satisfy it,
            # and the check above already ruled that out.
            return False
        return {str(m) for m in folded}.issubset(slot.modalities)

    slots: list[LoadedSlot] = []
    for cfg in await host.iter_configs():
        if cfg.get("type") != slot_type:
            continue
        model_info = await _model_info_for(host, cfg)
        slot = loaded_slot_from_config(cfg, model_info=model_info)
        if slot is not None:
            slots.append(slot)

    # Step 1+2: try the default first.
    default_name = await default_slot_for(host, slot_type)
    if default_name is not None:
        default_slot = next((slot for slot in slots if slot.name == default_name), None)
        if default_slot is not None and _satisfies(default_slot):
            return default_slot

    # Step 3: fall-through to first model-bound + label-matching slot.
    for slot in slots:
        if not _satisfies(slot):
            continue
        return slot

    return None


async def route_for_request(
    host: RoutingHost,
    slot_type: str,
    *,
    required_labels: tuple[str, ...] = (),
) -> str | None:
    """Resolve a request of type ``slot_type`` to a concrete slot name.

    Compatibility wrapper for callers that have not moved to
    :func:`resolve_for_request` yet.
    """
    slot = await resolve_for_request(host, slot_type, required_labels=required_labels)
    return slot.name if slot is not None else None


async def add_slot(
    host: RoutingHost,
    name: str,
    *,
    type: str,
    model: str,
    device: str = "gpu-rocm",
    port: int | None = None,
) -> Slot:
    """Programmatic ``hal0 slot add`` (plan §4.3).

    Validates kebab-case name, rejects seeded-name collisions,
    rejects unknown slot types. The SlotConfig schema requires a
    port in the 8081-8099 range.

    Args:
        name: Kebab-case identifier; must not collide with a
            seeded slot (``SEEDED_SLOTS`` plus ``NPU_SEEDED_SLOTS``,
            independently of whether FLM is installed).
        type: One of ``llm | embedding | reranking | transcription
            | tts | image``.
        model: Model id to load by default.
        device: Hardware preference (``gpu-rocm | gpu-vulkan | cpu
            | npu``); see ``map_backend_to_device``. Default
            ``gpu-rocm`` matches Strix Halo seed semantics.
        port: SlotConfig.port — the container's loopback port. ``None``
            (the default, rework §11.2) auto-assigns the lowest free port
            from the pool rather than baking in a fixed ``8081`` that
            collided with an existing slot; when the manager has a live
            :class:`hal0.ports.authority.PortAuthority`, ``create`` then
            re-issues the authoritative claim on top of this seed.
    """
    if not _SLOT_NAME_RE.match(name):
        raise SlotConfigError(
            f"slot name {name!r}: use lowercase alphanumeric, hyphens, underscores; "
            f"start with alphanumeric; max 32 chars",
            details={"slot": name},
        )
    # Reject collisions with ALL seeded slots (include the NPU trio
    # regardless of FLM presence — the names are reserved).
    reserved = set(SEEDED_SLOTS) | set(NPU_SEEDED_SLOTS)
    if name in reserved:
        raise SlotConfigError(
            f"slot {name!r} collides with a seeded slot; pick a different name",
            details={"slot": name, "reserved": sorted(reserved)},
        )
    if type not in _VALID_SLOT_TYPES:
        raise SlotConfigError(
            f"slot type {type!r} is not one of {sorted(_VALID_SLOT_TYPES)}",
            details={"slot": name, "type": type},
        )
    if port is None:
        # Auto-assign the lowest free pool port (rework §11.2 — no baked-in
        # 8081 default). Falls back to the pool floor if the harvester can't
        # answer; create() re-issues the authoritative claim when wired.
        from hal0.config.paths import slots_config_dir
        from hal0.config.schema import _SLOT_PORT_MIN, _SLOT_PORT_POOL_END
        from hal0.ports import collect_claims, next_free

        try:
            claims = collect_claims(
                slots_dir=slots_config_dir(),
                pool=(_SLOT_PORT_MIN, _SLOT_PORT_POOL_END),
                reserved={8080: "api"},
            )
            port = next_free(claims, _SLOT_PORT_MIN, _SLOT_PORT_POOL_END) or _SLOT_PORT_MIN
        except Exception:
            port = _SLOT_PORT_MIN
    cfg = {
        "name": name,
        "port": port,
        "type": type,
        "device": device,
        "provider": "llama-server",
        "model": {"default": model},
    }
    return await host.create(name, cfg)


async def remove_slot(host: RoutingHost, name: str) -> None:
    """Programmatic ``hal0 slot remove`` (plan §4.3).

    Rejects seeded-slot names (use :meth:`SlotManager.unload` or
    ``capabilities.toml`` to disable a seeded slot, not delete it).
    No side effect on the underlying model files — they stay in
    the registry.
    """
    name = host._resolve_alias(name)
    reserved = set(SEEDED_SLOTS) | set(NPU_SEEDED_SLOTS)
    if name in reserved:
        raise SlotConfigError(
            f"slot {name!r} is seeded; cannot remove (disable it via capabilities.toml)",
            details={"slot": name, "reserved": sorted(reserved)},
        )
    await host.delete(name)


__all__ = [
    "NPU_SEEDED_SLOTS",
    "SEEDED_SLOTS",
    "SLOT_ALIASES",
    "LoadedSlot",
    "RoutingHost",
    "add_slot",
    "default_slot_for",
    "loaded_slot",
    "loaded_slot_from_config",
    "remove_slot",
    "resolve_for_request",
    "route_for_request",
    "seeded_slots",
]
