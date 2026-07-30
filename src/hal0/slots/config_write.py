"""Shared slot-config write pipeline (guards for every writer).

``SlotManager.update_config``, ``SlotManager.create``, and the stacks apply
engine (:mod:`hal0.stacks.apply`) all project "partial updates onto an
existing slot config". Historically only ``update_config``/``create`` ran
the full guard pipeline; the stacks engine hand-rolled its own merge and
could persist a vulkan-device+rocm-profile incoherence or a second NPU
anchor. These module-level, synchronous functions are the ONE pipeline
every writer calls.

P3-slots §1f extraction: moved verbatim out of ``hal0.slots.manager``
(was module scope there too). ``hal0.slots.manager`` re-exports every name
here (P3-slots §5 contract) — ``stacks/apply.py`` and
``slot_view/__init__.py`` keep importing from ``hal0.slots.manager``
unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hal0.config import paths
from hal0.slot_config import merge_slot_config
from hal0.slots._cfg_helpers import _cfg_to_dict
from hal0.slots.activation import claims_npu_anchor as _claims_npu_anchor
from hal0.slots.state import NpuExclusivityViolation, SlotConfigError

# ── device/profile coherence ────────────────────────────────────────────────


def _cfg_effective_backend(cfg: Any) -> str | None:
    """Derive the EFFECTIVE runtime backend token from a slot config.

    W3 truth fix: ``device`` is the authoritative hardware-intent
    field. The dashboard's SlotCard backend chip must reflect what
    ``device`` will run — NOT the legacy, never-resynced ``backend``
    TOML field which drifts the moment a user flips backend (which only
    rewrites ``device``).

    Returns the normalized token ``rocm`` | ``vulkan`` | ``cpu`` |
    ``flm`` (NPU → ``flm``), or ``None`` when neither ``device`` nor a
    legacy ``backend`` is set so callers can fall through to "unknown".
    Pure/synchronous — safe on the status hot path.
    """
    d = _cfg_to_dict(cfg)
    device = d.get("device")
    if not device:
        # Legacy TOMLs may carry only ``backend``; promote it the same way
        # SlotConfig._promote_backend_to_device would, so we still emit the
        # device-derived token rather than the raw legacy string.
        legacy = d.get("backend")
        if not legacy:
            return None
        from hal0.config.schema import map_backend_to_device

        device = map_backend_to_device(str(legacy))
    # Reuse the single device→(recipe, llamacpp_backend) mapping so the
    # displayed token can never diverge from what the load path derives.
    from hal0.model_meta import device_to_backend

    recipe, llamacpp_backend = device_to_backend(str(device))
    # NPU → recipe="flm" with no llamacpp_backend; surface "flm".
    return llamacpp_backend or (recipe if recipe == "flm" else None)


def _base_profile_for_backend(catalog: Any, backend: str) -> str:
    """Pick the canonical (non-MTP) seed profile name for a GPU backend.

    Prefers the seed profile named after the backend (``rocm`` / ``vulkan``);
    falls back to any non-MTP then any profile that declares ``backend``.

    This is the deliberate *non-MTP* counterpart of
    :func:`hal0.install.profile_derive.derive_profile`'s ``rocm-dnse``
    preference: it answers backend→base-profile from the live catalog so a
    drawer device-flip re-derives a plain base image (``rocm``/``vulkan``) and
    never silently switches a slot onto the MTP ``rocm-dnse`` image. Do NOT
    fold it into the device→profile helper (finding PS-4).
    """
    if "chat" in catalog.profile:
        return "chat"
    for name, prof in catalog.profile.items():
        if getattr(prof, "backend", None) == backend and not getattr(prof, "mtp", False):
            return str(name)
    for name, prof in catalog.profile.items():
        if getattr(prof, "backend", None) == backend:
            return str(name)
    return backend


def _reconcile_device_profile(cfg_dict: dict[str, Any], changed: set[str]) -> None:
    """Keep a GPU slot's ``device`` and ``profile.backend`` coherent in place.

    A GPU slot implies its backend twice: ``device`` (``gpu-rocm`` /
    ``gpu-vulkan``) drives the llama-server backend, while ``profile`` selects
    the container image + flags. They must agree — a vulkan device under a
    rocm-dnse profile launches a Vulkan binary with ROCm-only MTP draft flags
    (issue: utility slot). The field the operator changed wins; the stale side
    is re-derived. Both changed to conflicting backends → operator error.

    No-ops for slots without a GPU profile (npu/cpu/img profiles declare
    ``backend=None``) and for ``auto`` device (empty) unless the profile
    itself changed. Mutates ``cfg_dict`` in place.
    """
    profile_name = cfg_dict.get("profile")
    if not isinstance(profile_name, str) or not profile_name:
        return

    from hal0.config.loader import load_profiles_config

    prof = load_profiles_config().profile.get(profile_name)
    prof_backend = getattr(prof, "backend", None) if prof is not None else None
    if not prof_backend:
        # Non-GPU profile (or unknown profile with no backend) — leave alone.
        return

    from hal0.config.schema import map_backend_to_device
    from hal0.model_meta import device_to_backend

    device = cfg_dict.get("device")
    dev_backend = device_to_backend(str(device))[1] if device else None
    if dev_backend == prof_backend:
        return  # already coherent

    prof_changed = "profile" in changed
    dev_changed = "device" in changed

    if not device:
        # ``auto``/unset device: only adopt the profile's backend when the
        # operator explicitly (re)selected the profile; otherwise leave auto.
        if prof_changed:
            cfg_dict["device"] = map_backend_to_device(prof_backend)
        return

    if prof_changed and not dev_changed:
        cfg_dict["device"] = map_backend_to_device(prof_backend)
    elif dev_changed and not prof_changed and dev_backend is not None:
        catalog = load_profiles_config()
        cfg_dict["profile"] = _base_profile_for_backend(catalog, dev_backend)
    elif prof_changed and dev_changed:
        raise SlotConfigError(
            f"slot device {device!r} (backend {dev_backend!r}) conflicts with "
            f"profile {profile_name!r} (backend {prof_backend!r}); "
            "pick a device and profile with the same backend",
            details={
                "device": device,
                "profile": profile_name,
                "device_backend": dev_backend,
                "profile_backend": prof_backend,
            },
        )
    # neither changed (pre-existing on-disk drift surfaced by an unrelated
    # update): leave both fields untouched so the unrelated edit doesn't
    # silently mutate hardware intent. Drift heals on the next device/profile
    # edit.


# ── guard/merge pipeline (shared with stacks/apply.py) ──────────────────────


def _read_slot_toml_dict(path: Path) -> dict[str, Any] | None:
    """Best-effort raw read of one ``slots/*.toml`` (with the [slot] hoist).

    Returns ``None`` on a missing or malformed file — guard peer-walks skip
    those rather than blocking the caller's legitimate write (the malformed
    slot surfaces its own error on its own paths).
    """
    import tomllib

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    slot_tbl = data.pop("slot", None)
    if isinstance(slot_tbl, dict):
        for k, v in slot_tbl.items():
            data[k] = v
    return data


def _iter_peer_configs(
    slot_name: str, slots_dir: Path | None = None
) -> list[tuple[str, dict[str, Any]]]:
    """(name, cfg) for every readable configured slot other than ``slot_name``."""
    base = Path(slots_dir) if slots_dir is not None else paths.slots_config_dir()
    if not base.is_dir():
        return []
    peers: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(base.glob("*.toml")):
        if path.stem == slot_name:
            continue
        peer = _read_slot_toml_dict(path)
        if peer is not None:
            peers.append((path.stem, peer))
    return peers


def check_npu_exclusivity(
    slot_name: str,
    cfg_dict: dict[str, Any],
    *,
    slots_dir: Path | None = None,
) -> None:
    """Reject a write that would land a second model-bound NPU LLM anchor.

    Sync core of :meth:`SlotManager._check_npu_exclusivity` (see its
    docstring for the full contract), shared with the stacks apply engine.
    ``slots_dir`` overrides the default config dir for engines constructed
    against a custom directory (tests).

    A slot claims the AMDXDNA chat context when it is ``device=npu,
    type=llm`` AND has a non-empty ``[model].default`` — model-presence is
    the activation signal since #1369, so a model-less NPU LLM slot is inert
    config that may coexist and the model write is what gets refused.
    """
    if not _claims_npu_anchor(cfg_dict):
        return
    offenders = [
        name for name, peer in _iter_peer_configs(slot_name, slots_dir) if _claims_npu_anchor(peer)
    ]
    if offenders:
        raise NpuExclusivityViolation(
            "only one NPU LLM slot may have a model configured at a time "
            f"(slot {slot_name!r} would conflict with {offenders[0]!r})",
            details={
                "slot": slot_name,
                "conflicting_slots": sorted(offenders),
                "hint": "clear the existing NPU LLM slot's model before configuring another",
            },
        )


def check_default_uniqueness(
    slot_name: str,
    cfg_dict: dict[str, Any],
    *,
    slots_dir: Path | None = None,
) -> None:
    """Reject a write that would land a second ``default=true`` per type.

    Sync core of :meth:`SlotManager._check_default_uniqueness` (see its
    docstring for the full contract), shared with the stacks apply engine.
    """
    if cfg_dict.get("default") is not True:
        return
    type_ = cfg_dict.get("type")
    if not type_:
        return
    offenders = [
        name
        for name, peer in _iter_peer_configs(slot_name, slots_dir)
        if peer.get("type") == type_ and peer.get("default") is True
    ]
    if offenders:
        raise SlotConfigError(
            f"slot type {type_!r} already has a default=true slot "
            f"(slot {slot_name!r} would conflict with {offenders[0]!r})",
            details={
                "slot": slot_name,
                "type": type_,
                "conflicting_slots": sorted(offenders),
                "hint": "clear the existing default before setting another",
            },
        )


def reconcile_slot_updates(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Normalize + merge ``updates`` onto ``base`` and keep device/profile coherent.

    The write-side projection shared by ``SlotManager.update_config`` and the
    stacks apply engine: the copy-safe one-level merge + #585 ctx_size fold
    (:func:`hal0.slot_config.merge_slot_config`) followed by
    :func:`_reconcile_device_profile` driven by exactly the keys the caller
    changed. Returns a fresh dict; ``base`` is never mutated.
    """
    merged = merge_slot_config(base, updates)
    _reconcile_device_profile(merged, set(updates.keys()))
    return merged


def reconcile_and_guard_slot_config(
    slot_name: str,
    base: dict[str, Any],
    updates: dict[str, Any],
    *,
    slots_dir: Path | None = None,
) -> dict[str, Any]:
    """The full guarded write pipeline: normalize + merge + reconcile + guards.

    Raises :class:`SlotConfigError` / :class:`NpuExclusivityViolation` when the
    projected config is incoherent (conflicting device+profile backends) or
    violates a cross-slot invariant (second model-bound NPU anchor, second
    default=true of a type). Used by the stacks apply engine so a stack can no
    longer persist what ``update_config`` would have refused.
    """
    merged = reconcile_slot_updates(base, updates)
    check_npu_exclusivity(slot_name, merged, slots_dir=slots_dir)
    check_default_uniqueness(slot_name, merged, slots_dir=slots_dir)
    return merged


__all__ = [
    "_base_profile_for_backend",
    "_cfg_effective_backend",
    "_iter_peer_configs",
    "_read_slot_toml_dict",
    "_reconcile_device_profile",
    "check_default_uniqueness",
    "check_npu_exclusivity",
    "reconcile_and_guard_slot_config",
    "reconcile_slot_updates",
]
