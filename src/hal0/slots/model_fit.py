"""Model <-> runtime agreement: a halogen checkpoint runs on halogen, and only there.

A halogen ``.hgn`` checkpoint is loadable by the halogen engine alone, and the
halogen engine loads nothing else. Without a check the mismatch is accepted and
the slot crash-loops at launch (a llama-server handed an ``.hgn``, or the
halogen engine handed a GGUF). This module is the one rule both seams share:

* the slot-config write guard (``_guard_model_fits_slot`` in
  ``hal0.api.routes.slots``) refuses before anything is stored, through
  :func:`slot_model_runtime_mismatch`;
* ``ContainerProvider.load_sync`` refuses before any unit is written, through
  :func:`model_runtime_mismatch` directly (it already holds the launch provider).

Only a positively identified mismatch is refused. A model the registry does not
know, or an empty binding, is *unknown*, not wrong: the halogen provider names a
missing model itself (``slot.model_missing``) and the existing resolvability
guards own the unknown-id case.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The ``backends`` tag a halogen checkpoint's registry row carries, and the
#: ``name`` of the halogen provider.
HALOGEN = "halogen"

#: What a slot runs when no spec provider claims it (``_spec_provider_for`` -> ``None``).
_DEFAULT_RUNTIME = "llama-server"


@dataclass(frozen=True)
class RuntimeRef:
    """A provider stand-in for the write seam: just the runtime's ``name``.

    A config write must not resolve a profile the way launch dispatch does
    (``ProfileCatalog.resolve`` can persist a shipped seed), so it cannot ask
    ``_spec_provider_for`` for the real provider. It carries the same one fact
    the rule reads from a provider.
    """

    name: str


def _backends(model_info: Mapping[str, Any]) -> frozenset[str]:
    raw = model_info.get("backends")
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(tag).strip().lower() for tag in raw)


def _is_halogen_model(model_info: Mapping[str, Any]) -> bool | None:
    """Whether a model is a halogen checkpoint; ``None`` when the info says nothing.

    A registry row always carries ``backends`` and ``provider`` (``provider``
    defaults to ``llama-server``), so for a registry hit this is a definite
    answer -- including for a GGUF whose ``backends`` list is empty, which is
    NOT halogen. Info with neither key is a registry miss (only ``_model_key``
    / ``flm_tag`` stamped) or empty: unknown, never judged.
    """
    raw = model_info.get("backends")
    provider = str(model_info.get("provider") or "").strip().lower()
    if raw is None and not provider:
        return None
    return HALOGEN in _backends(model_info) or provider == HALOGEN


def model_runtime_mismatch(
    model_info: Mapping[str, Any] | None,
    provider: Any | None,
    *,
    slot: str | None = None,
) -> str | None:
    """A refusal message when ``model_info`` and ``provider`` cannot run together.

    ``provider`` is what ``_spec_provider_for`` returns: ``None`` for the
    llama-server default, else the runtime's provider (read by its ``name``).
    ``slot`` is only for the message. Returns ``None`` (agree, or not enough
    known to judge) or one sentence naming both sides and the fix:

    * ``"halogen"`` in the model's ``backends`` while the provider is not
      halogen -- the checkpoint belongs to the halogen runtime;
    * the provider is halogen while the model is a known non-halogen row --
      the halogen engine cannot load it.

    Empty info, or a registry miss that stamped only ``_model_key`` /
    ``flm_tag``, is never judged (see :func:`_is_halogen_model`).
    """
    if not model_info:
        return None
    is_halogen_model = _is_halogen_model(model_info)
    if is_halogen_model is None:
        return None
    runtime = str(getattr(provider, "name", "") or _DEFAULT_RUNTIME)
    on_halogen = runtime == HALOGEN
    if is_halogen_model == on_halogen:
        return None
    model_id = model_info.get("_model_key") or model_info.get("id")
    subject = f"model {str(model_id)!r}" if model_id else "the model"
    where = f"slot {slot!r}" if slot else "the slot"
    if is_halogen_model:
        return (
            f"{subject} is a halogen checkpoint and {where} runs the {runtime} runtime; "
            "pick a halogen profile for the slot or another model"
        )
    return (
        f"{subject} is not a halogen checkpoint and {where} runs the halogen runtime; "
        "pick a halogen checkpoint for the slot or a profile for another runtime"
    )


def slot_runtime_provider(cfg: Any) -> RuntimeRef | None:
    """The runtime a slot config launches, for the write seam; read-only.

    ``cfg`` is the slot config as it will be after the write. Mirrors launch
    dispatch's order (``_provider_for_family``: FLM claims the slot first, then
    the profile's runtime family) without resolving -- and so without possibly
    persisting -- a profile. ``None`` means the llama-server default (or an
    engine this rule has no opinion on), matching ``_spec_provider_for``.
    """
    from hal0.slot_config import slot_scalar_table
    from hal0.slots._cfg_helpers import _cfg_to_dict
    from hal0.slots.config_write import profile_runtime_family, slot_is_flm

    if slot_is_flm(cfg):
        return RuntimeRef("flm")
    scalars = slot_scalar_table(_cfg_to_dict(cfg))
    if profile_runtime_family(scalars.get("profile")) == HALOGEN:
        return RuntimeRef(HALOGEN)
    return None


def slot_model_runtime_mismatch(
    slot_name: str, cfg: Any, model_id: str | None, registry: Any
) -> str | None:
    """The write-seam form of :func:`model_runtime_mismatch`.

    Looks ``model_id`` up in ``registry`` (``None`` / a miss -> unknown ->
    ``None``) and judges it against the slot's post-write runtime.
    """
    if not model_id or registry is None:
        return None
    try:
        model = registry.get(model_id)
    except Exception:
        return None
    if model is None:
        return None
    info = model.model_dump() if hasattr(model, "model_dump") else dict(model)
    info.setdefault("_model_key", model_id)
    return model_runtime_mismatch(info, slot_runtime_provider(cfg), slot=slot_name)


__all__ = [
    "HALOGEN",
    "RuntimeRef",
    "model_runtime_mismatch",
    "slot_model_runtime_mismatch",
    "slot_runtime_provider",
]
