"""Model-preferred-profile adoption + MTP defuse (P3-slots §1g).

Q1 (model profiles): on create/swap, a slot adopts the new model's
``defaults.profile`` preference when it fits the slot's device/type, and a
forced ``mtp=true`` override is cleared when swapping onto a model with no
MTP heads (would otherwise crash-loop llama-server). Extracted verbatim out
of ``hal0.slots.manager`` behind a narrow ``ProfileAdoptHost`` seam.

``SlotManager`` keeps every name as a thin delegator; ``_profile_fits_slot``
in particular stays a ``@staticmethod`` on the class (kept callable as
``SlotManager._profile_fits_slot(name, cfg_dict)`` — see
``tests/slots/test_model_preferred_profile.py::test_profile_fits_slot_matrix``,
which grabs the bound staticmethod off the class directly).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from hal0.slot_config import slot_write_lock, write_slot_toml
from hal0.slots._cfg_helpers import _cfg_to_dict
from hal0.slots.state import SlotConfigError

log = logging.getLogger(__name__)


class ProfileAdoptHost(Protocol):
    """Narrow seam this module needs from ``SlotManager``."""

    async def _resolve_model_info(self, model_id: str | None) -> dict[str, Any]: ...
    async def _load_slot_config(self, slot_name: str) -> dict[str, Any]: ...
    def _config_file(self, slot_name: str) -> Any: ...
    def _invalidate_cfg_cache(self, slot_name: str) -> None: ...


async def preferred_profile_for(host: ProfileAdoptHost, model_id: str | None) -> str | None:
    """The model's preferred runtime profile name (``defaults.profile``).

    A registry model may carry ``defaults.profile`` — the runtime profile
    it wants loaded with it. Returns the name or ``None`` (no preference /
    model not in registry).
    """
    if not model_id:
        return None
    info = await host._resolve_model_info(model_id)
    defaults = info.get("defaults")
    preferred = defaults.get("profile") if isinstance(defaults, dict) else None
    return preferred if isinstance(preferred, str) and preferred else None


def profile_fits_slot(profile_name: str, cfg_dict: dict[str, Any]) -> bool:
    """True when ``profile_name`` is safe to adopt for this slot.

    A model's profile preference is honoured only when the profile exists in
    the catalog AND matches the slot's device/type. We never flip a slot's
    hardware (device/backend) to satisfy a preference — an image or
    cross-backend profile on the wrong device is rejected so the caller
    keeps the slot's current/device-default profile.
    """
    from hal0.errors import NotFound
    from hal0.profiles import ProfileCatalog

    try:
        resolved = ProfileCatalog().resolve(profile_name)
    except NotFound:
        return False
    slot_type = cfg_dict.get("type")
    if slot_type and slot_type not in resolved.supported_slot_types:
        return False
    device = str(cfg_dict.get("device") or "")
    if device:
        slot_class = (
            "gpu"
            if device.startswith("gpu")
            else device
            if device in ("npu", "cpu", "img")
            else "cpu"
        )
        if resolved.device_class != slot_class:
            return False
        if resolved.backend:
            from hal0.model_meta import device_to_backend

            slot_backend = device_to_backend(device)[1]
            if slot_backend and slot_backend != resolved.backend:
                return False
    return True


# RETIRED (spec-hw-slot-ownership §2/§3): the model-driven preferred-runner
# adoption (``preferred_runner_for`` + ``apply_preferred_runner``) is gone — the
# runner is the slot's own ``SlotConfig.binary`` field, set by the operator, and
# the image resolves from ``slot.image_pin or RUNNER_IMAGES[slot.binary]`` (or
# the HW-gated default) at launch. Only the generic device/backend fit predicate
# below survives (reused by the §4 fit-check).


def runner_fits_slot(runner_key: str, cfg_dict: dict[str, Any]) -> bool:
    """True when ``RUNNER_IMAGES[runner_key]`` is safe to adopt for this slot.

    Mirrors :func:`profile_fits_slot`'s device/backend coherence derivation
    but delegates the actual match to :func:`hal0.runners.runner_matches`.
    Retained as a shared "does this runner fit this slot's device/backend"
    predicate (spec-hw-slot-ownership §4 fit-check reuse). An unknown key, or
    one whose device_class/backend doesn't match the slot, returns False.
    """
    from hal0.errors import NotFound
    from hal0.runners import get_runner, runner_matches

    try:
        runner = get_runner(runner_key)
    except NotFound:
        return False
    device = str(cfg_dict.get("device") or "")
    if not device:
        return True  # no device pinned yet — nothing to conflict with
    slot_class = (
        "gpu" if device.startswith("gpu") else device if device in ("npu", "cpu", "img") else "cpu"
    )
    from hal0.model_meta import device_to_backend

    slot_backend = device_to_backend(device)[1]
    return runner_matches(runner, device_class=slot_class, backend=slot_backend)


async def apply_preferred_profile(host: ProfileAdoptHost, slot_name: str, model_id: str) -> bool:
    """Adopt ``model_id``'s preferred profile for this slot when compatible.

    Q1 (model profiles): on every model swap the slot adopts the new
    model's ``defaults.profile`` — but only when it fits the slot (see
    :func:`profile_fits_slot`); an incompatible preference is logged and
    ignored. Writes the slot TOML BEFORE the reload so the container comes
    up on the new profile's image. Returns True when ``profile`` changed.
    """
    preferred = await preferred_profile_for(host, model_id)
    if not preferred:
        return False
    # Read + rewrite under the shared cross-process slot-TOML lock so a
    # concurrent config writer isn't silently dropped.
    with slot_write_lock():
        cfg = await host._load_slot_config(slot_name)
        cfg_dict = _cfg_to_dict(cfg)
        if cfg_dict.get("profile") == preferred:
            return False
        if not profile_fits_slot(preferred, cfg_dict):
            log.info(
                "slot.preferred_profile_skipped",
                extra={"slot": slot_name, "model_id": model_id, "profile": preferred},
            )
            return False
        cfg_dict = {**cfg_dict, "profile": preferred}
        try:
            write_slot_toml(host._config_file(slot_name), cfg_dict)
        except OSError as exc:
            raise SlotConfigError(
                f"failed to persist preferred profile to slot {slot_name}: {exc}",
                details={"slot": slot_name, "profile": preferred},
            ) from exc
        host._invalidate_cfg_cache(slot_name)
    log.info(
        "slot.preferred_profile_applied",
        extra={"slot": slot_name, "model_id": model_id, "profile": preferred},
    )
    return True


async def defuse_stale_mtp_on_swap(host: ProfileAdoptHost, slot_name: str, model_id: str) -> bool:
    """Clear a forced ``mtp = true`` when swapping onto a non-MTP model.

    MTP is a model property, and a force-on pointing at a model with no MTP
    heads makes llama-server exit at load ("context type MTP requested but
    model doesn't contain MTP layers") — the override would down the slot
    the moment the swapped container starts. Clears the override to AUTO
    for exactly that combination; a force-off, a force-on for an eligible
    model, and an unresolvable model (can't judge) all pass through
    untouched. Returns True when the override was cleared.
    """
    from hal0.model_meta import model_is_mtp_eligible

    with slot_write_lock():
        cfg = await host._load_slot_config(slot_name)
        cfg_dict = _cfg_to_dict(cfg)
        if cfg_dict.get("mtp") is not True:
            return False
        try:
            from hal0.registry.store import ModelRegistry

            model = ModelRegistry().get(model_id)
            info = model.model_dump() if hasattr(model, "model_dump") else dict(model)
        except Exception:
            return False  # unresolvable — leave the escape hatch alone
        info.setdefault("_model_key", model_id)
        if model_is_mtp_eligible(info):
            return False
        cfg_dict = dict(cfg_dict)
        cfg_dict.pop("mtp", None)  # absent = AUTO (TOML has no null)
        write_slot_toml(host._config_file(slot_name), cfg_dict)
        host._invalidate_cfg_cache(slot_name)
    log.warning(
        "slot.mtp_force_on_cleared_on_swap",
        extra={
            "slot": slot_name,
            "model_id": model_id,
            "note": (
                "forced MTP would crash llama-server for this model (no MTP "
                "heads); override cleared to AUTO. Tag the model 'mtp' or "
                "re-force in the drawer if it really ships MTP layers."
            ),
        },
    )
    return True


__all__ = [
    "ProfileAdoptHost",
    "apply_preferred_profile",
    "defuse_stale_mtp_on_swap",
    "preferred_profile_for",
    "profile_fits_slot",
    "runner_fits_slot",
]
