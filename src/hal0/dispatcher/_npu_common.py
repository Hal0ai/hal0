"""Shared NPU-slot helpers for dispatcher modules (Phase A container cutover)."""

from typing import Any

from hal0.slots.activation import is_activated, is_xdna_slot


def is_container_npu_cfg(cfg: dict[str, Any] | None) -> bool:
    """True when this slot config describes a live containerized NPU slot.

    Detection: device=="npu" AND container runtime (profile set, or
    runtime=="container") AND a model bound — model-presence is the
    activation signal (#1369), and there is nothing to dispatch to a
    model-less slot anyway. An xdna slot (``binary = "xdna"``, ADR-0057) is a
    llama-server launch on the NPU, not the FLM process, so it never counts.
    """
    if not isinstance(cfg, dict):
        return False
    if str(cfg.get("device", "")) != "npu" or is_xdna_slot(cfg):
        return False
    if not (cfg.get("profile") or str(cfg.get("runtime", "")) == "container"):
        return False
    return is_activated(cfg)


__all__ = ["is_container_npu_cfg"]
