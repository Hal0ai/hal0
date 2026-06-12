"""Shared fixtures for the OmniRouter test suite.

The OmniRouter only talks to two collaborators in production:

  * :class:`SlotManager` — for ``iter_configs`` + ``route_for_request``.
  * hal0-api's own ``/v1`` HTTP surface — for ``/v1/chat/completions``
    and the seven other tool endpoints.

We mock both with narrow, hand-rolled stubs so tests can drive
specific slot configurations + HTTP responses without standing up
the real subsystems.
"""

from __future__ import annotations

from typing import Any

import httpx

from hal0.omni_router.filter import SlotManagerLike
from hal0.slots.manager import LoadedSlot


class FakeSlotManager(SlotManagerLike):
    """Tiny stub that satisfies :class:`SlotManagerLike`.

    Tests build it with a list of slot config dicts; ``iter_configs``
    just returns the list and ``route_for_request`` replays the
    routing logic from the real ``SlotManager.route_for_request``
    (type match + default + label filter + enabled fall-through).
    """

    def __init__(self, configs: list[dict[str, Any]]) -> None:
        self._configs = configs

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self._configs)

    async def route_for_request(
        self,
        slot_type: str,
        *,
        required_labels: tuple[str, ...] = (),
    ) -> str | None:
        slot = await self.resolve_for_request(slot_type, required_labels=required_labels)
        return slot.name if slot is not None else None

    async def resolve_for_request(
        self,
        slot_type: str,
        *,
        required_labels: tuple[str, ...] = (),
    ) -> LoadedSlot | None:
        def labels_of(cfg: dict[str, Any]) -> set[str]:
            model = cfg.get("model") or {}
            if isinstance(model, dict):
                raw = model.get("labels", ())
                if isinstance(raw, (list, tuple)):
                    return {str(x) for x in raw}
            return set()

        def satisfies(cfg: dict[str, Any]) -> bool:
            if not required_labels:
                return True
            return set(required_labels).issubset(labels_of(cfg))

        configs = [c for c in self._configs if c.get("type") == slot_type]
        # Default-first.
        for cfg in configs:
            if not cfg.get("default"):
                continue
            if cfg.get("enabled", True) and satisfies(cfg):
                return _loaded_slot_from_config(cfg)
        # Fall-through.
        for cfg in configs:
            if not cfg.get("enabled", True):
                continue
            if not satisfies(cfg):
                continue
            return _loaded_slot_from_config(cfg)
        return None


def _loaded_slot_from_config(cfg: dict[str, Any]) -> LoadedSlot:
    model = cfg.get("model") or {}
    model_id = model.get("default", "") if isinstance(model, dict) else ""
    labels = model.get("labels", ()) if isinstance(model, dict) else ()
    return LoadedSlot(
        name=str(cfg.get("name", "")),
        model_id=str(model_id),
        slot_type=str(cfg.get("type", "")),
        device=str(cfg.get("device", "")),
        enabled=cfg.get("enabled", True) is not False,
        labels=frozenset(str(x) for x in labels)
        if isinstance(labels, (list, tuple))
        else frozenset(),
        system_prompt=str(cfg.get("system_prompt", "")),
        profile=str(cfg.get("profile")) if cfg.get("profile") else None,
        default=cfg.get("default") is True,
    )


def make_slot(
    name: str,
    *,
    type: str,
    model: str,
    labels: tuple[str, ...] = (),
    enabled: bool = True,
    default: bool = False,
    device: str = "gpu-rocm",
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """Build a slot config dict for tests."""
    cfg: dict[str, Any] = {
        "name": name,
        "type": type,
        "enabled": enabled,
        "default": default,
        "device": device,
        "model": {"default": model, "labels": list(labels)},
    }
    if system_prompt is not None:
        cfg["system_prompt"] = system_prompt
    return cfg


def make_http_client(handler) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient backed by ``httpx.MockTransport``.

    The ``handler`` callable receives an ``httpx.Request`` and returns
    an ``httpx.Response``.
    """
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
