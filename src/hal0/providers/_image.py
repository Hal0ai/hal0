"""Shared container-image resolution tiers for every slot provider.

The image contract (spec-hw-slot-ownership §3, runner-image-catalogue v2)::

    effective = slot.image_pin
                or [slots].default_images[family]
                or resolve_runner_image(RUNNER_IMAGES[family])

The llama/container path (:func:`hal0.providers.container._resolve_image_ref`)
and the non-llama providers (comfyui, flm, kokoro, moonshine, qwen3tts) all
read the middle tier through :func:`operator_default_image`, so an operator
family default set from the runner-images page applies to every family the
``[slots].default_images`` schema accepts (#2234).

Kept import-light (no :mod:`hal0.providers.container` import) so the small
provider modules can use it without pulling in the llama renderer.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

log = logging.getLogger(__name__)


def slot_default_images() -> Mapping[str, str]:
    """Live ``[slots].default_images`` — per-family operator image overrides.

    Read fresh each resolve so a Settings change lands on the next slot
    (re)start without an api process bounce. Fail-soft to ``{}``: a
    malformed/unreadable hal0.toml must never wedge a slot launch.
    """
    try:
        from hal0.config.loader import load_hal0_config

        overrides = load_hal0_config().slots.default_images
        return overrides if isinstance(overrides, Mapping) else {}
    except Exception:
        log.warning("container.default_images_load_failed", exc_info=True)
        return {}


def operator_default_image(runner_key: str) -> str | None:
    """The ``[slots].default_images`` ref for ``runner_key``'s family, if set.

    Exact key first (an existing alias-keyed override such as ``vulkanfpx``
    keeps working during deprecation), then the canonical family — so an
    override set under ``rocmfpx`` also applies when the effective runner is
    the ``vulkanfpx`` alias.
    """
    from hal0.runners import canonical_family

    defaults_map = slot_default_images()
    override = defaults_map.get(runner_key) or defaults_map.get(canonical_family(runner_key))
    return override if isinstance(override, str) and override else None


def slot_image_pin(slot_cfg: Any) -> str | None:
    """The slot's ``image_pin`` (top-level, else ``[slot]``-nested), if set."""
    if not isinstance(slot_cfg, Mapping):
        return None
    pin = slot_cfg.get("image_pin")
    if not (isinstance(pin, str) and pin):
        nested = slot_cfg.get("slot")
        pin = nested.get("image_pin") if isinstance(nested, Mapping) else None
    return pin if isinstance(pin, str) and pin else None


def resolve_family_image(slot_cfg: Any, family: str) -> str:
    """Resolve a fixed-family provider's image through the shared tier order.

    ``image_pin`` (honored verbatim) → ``[slots].default_images[family]`` →
    :func:`hal0.runners.resolve_runner_image` (env override → manifest pin →
    bundled default).
    """
    pin = slot_image_pin(slot_cfg)
    if pin is not None:
        return pin
    override = operator_default_image(family)
    if override is not None:
        return override

    from hal0.runners import get_runner, resolve_runner_image

    return resolve_runner_image(get_runner(family))
