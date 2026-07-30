"""Shared NPU-slot helpers for dispatcher modules (Phase A container cutover)."""

from typing import Any

from hal0.slots.activation import is_activated


def is_container_npu_cfg(cfg: dict[str, Any] | None) -> bool:
    """True when this slot config describes a live containerized NPU slot.

    Detection: device=="npu" AND container runtime (profile set, or
    runtime=="container") AND a model bound — model-presence is the
    activation signal (#1369), and there is nothing to dispatch to a
    model-less slot anyway.
    """
    if not isinstance(cfg, dict):
        return False
    if str(cfg.get("device", "")) != "npu":
        return False
    if not (cfg.get("profile") or str(cfg.get("runtime", "")) == "container"):
        return False
    return is_activated(cfg)


__all__ = ["is_container_npu_cfg"]
