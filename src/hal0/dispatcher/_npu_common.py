"""Shared NPU-slot helpers for dispatcher modules (Phase A container cutover)."""

from typing import Any


def is_container_npu_cfg(cfg: dict[str, Any] | None) -> bool:
    """True when this slot config describes a containerized NPU slot.

    Detection: device=="npu" AND container runtime (profile set, or
    runtime=="container") AND not explicitly disabled.
    """
    if not isinstance(cfg, dict):
        return False
    if str(cfg.get("device", "")) != "npu":
        return False
    if not (cfg.get("profile") or str(cfg.get("runtime", "")) == "container"):
        return False
    return cfg.get("enabled") is not False


__all__ = ["is_container_npu_cfg"]
