"""Tiny leaf-level slot-config accessors shared across the slots subtree.

These four pure, dependency-free functions (``_cfg_to_dict``, ``_cfg_port``,
``_cfg_provider``, ``_model_default``) are used by nearly every collaborator
in :mod:`hal0.slots` (``manager``, ``config_write``, ``routing``, ``npu``,
``drift``, ``profile_adopt``, ``reaper``, ``watchdog``).  They live in their
own leaf module — instead of :mod:`hal0.slots.manager` — purely to avoid an
import cycle: several of those collaborator modules are imported BY
``manager.py`` at module scope, so if the accessors lived in ``manager.py``
those collaborators could not import them back at module scope without
tripping a partially-initialized-module error.

``hal0.slots.manager`` re-exports all four names (P3-slots §5 contract) —
external callers keep using ``from hal0.slots.manager import _cfg_port``
unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hal0.config.schema import SlotConfig


def _cfg_to_dict(cfg: SlotConfig | dict[str, Any]) -> dict[str, Any]:
    # Deferred import: hal0.slots.state has no import-time dependency on
    # this module, but importing it eagerly here would still work fine —
    # kept deferred anyway so this module stays a trivially-importable leaf
    # with no intra-package top-level imports.
    from hal0.slots.state import SlotConfigError

    if hasattr(cfg, "model_dump"):
        d: dict[str, Any] = cfg.model_dump()
    elif isinstance(cfg, dict):
        d = dict(cfg)
    else:
        raise SlotConfigError(f"unsupported slot cfg type {type(cfg).__name__}")
    # An unset context_size (schema default None) must never reach the TOML
    # writer: write_toml_atomic rejects None, and a persisted 4096 was the
    # chat@4096 incident. Drop the key so the load path derives the model's
    # native window instead (see providers.container._resolve_context_size).
    model = d.get("model")
    if isinstance(model, dict) and model.get("context_size") is None:
        model.pop("context_size", None)
    return d


def _cfg_port(cfg: SlotConfig | dict[str, Any]) -> int:
    d = _cfg_to_dict(cfg)
    port = d.get("port") or d.get("slot", {}).get("port") or 0
    return int(port)


def _cfg_provider(cfg: SlotConfig | dict[str, Any]) -> str:
    d = _cfg_to_dict(cfg)
    return str(d.get("provider") or d.get("slot", {}).get("provider") or "llama-server")


def _model_default(cfg: SlotConfig | dict[str, Any]) -> str:
    d = _cfg_to_dict(cfg)
    model = d.get("model") or {}
    if isinstance(model, dict):
        return str(model.get("default") or "")
    return ""


def heal_missing_llm_type(data: dict[str, Any]) -> bool:
    """Default a type-less, llm-shaped slot config to ``type="llm"`` in place.

    A chat/llm slot TOML that predates the seed fix (O23) can ship WITHOUT an
    explicit ``type``. ``hal0_llm_slot_views`` hard-filters ``type != "llm"``,
    so such a slot never enters the ``LiveSlotResolver`` views and
    ``hal0/<slot>`` aliases fail to resolve — the steward is down by default
    (live: ``brain.toml`` lacked ``type``). Heal-on-load so existing boxes
    self-correct without a hand edit.

    Infers llm from the llm-shaped signal: a ``[model]`` table served by
    ``llama-server`` (the default provider) with no image-gen ``[image]``
    table. Explicit types (``image``/``tts``/``reranking``/``embed``…) are
    always respected — the heal only fires when ``type`` is absent/empty.
    Returns True iff it mutated ``data``. Operates on the flat, ``[slot]``-
    hoisted dict shape (``[model]``/``[image]`` still nested).
    """
    if not isinstance(data, dict):
        return False
    if str(data.get("type") or "").strip():
        return False
    if not isinstance(data.get("model"), dict):
        return False
    # An [image] table (or the comfyui provider) marks an image-gen slot.
    if isinstance(data.get("image"), dict):
        return False
    provider = str(data.get("provider") or "llama-server").strip().lower()
    if provider not in ("", "llama-server"):
        return False
    data["type"] = "llm"
    return True


__all__ = [
    "_cfg_port",
    "_cfg_provider",
    "_cfg_to_dict",
    "_model_default",
    "heal_missing_llm_type",
]
