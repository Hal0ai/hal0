"""Where ComfyUI's web UI answers: the LAN or the box's loopback.

One reader for ``[network].comfyui_listen`` (hal0.toml), so the img slot's
bind (:mod:`hal0.providers.comfyui`), the Services registry's mDNS eligibility
(:mod:`hal0.services.registry`) and ``GET /api/config/urls`` agree. ADR-0041.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

LAN = "lan"
LOOPBACK = "loopback"


def comfyui_listen() -> str:
    """Live ``[network].comfyui_listen``: ``"lan"`` (default) or ``"loopback"``.

    Fail-soft to the schema default: a config that cannot load must not fence
    an editor the operator never asked to fence (the slot would still start,
    and the dashboard would send people to a tunnel they do not need).
    """
    try:
        from hal0.config.loader import load_hal0_config

        mode = (load_hal0_config().network.comfyui_listen or "").strip().lower()
    except Exception:
        log.warning("comfyui.listen_config_load_failed", exc_info=True)
        return LAN
    return mode if mode in (LAN, LOOPBACK) else LAN


def editor_on_lan() -> bool:
    """True when ComfyUI's web UI binds the LAN (``comfyui_listen = "lan"``)."""
    return comfyui_listen() == LAN
