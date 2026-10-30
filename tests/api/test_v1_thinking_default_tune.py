"""``_slot_thinking_default`` reads the slot's effective tune (ADR-0045)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from hal0.api.routes.v1 import _slot_thinking_default
from hal0.config.schema import ProfileConfig


class _Sm:
    def __init__(self, configs: list[dict[str, Any]]):
        self._configs = configs

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self._configs)


def _request(configs: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(slot_manager=_Sm(configs))))


def _slot(**kw: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": "chat",
        "type": "llm",
        "port": 8101,
        "profile": "p",
        "model": {"default": "m"},
    }
    cfg.update(kw)
    return cfg


async def _default(cfg: dict[str, Any], profile: ProfileConfig) -> bool | None:
    with patch("hal0.providers.container._resolve_profile", return_value=profile):
        return await _slot_thinking_default(_request([cfg]), "m", "chat")


@pytest.mark.asyncio
async def test_thinking_default_follows_slot_override() -> None:
    assert await _default(_slot(tune={"enable_thinking": True}), ProfileConfig()) is True


@pytest.mark.asyncio
async def test_thinking_default_slot_can_turn_profile_off() -> None:
    prof = ProfileConfig(enable_thinking=True)
    assert await _default(_slot(tune={"enable_thinking": False}), prof) is False
    assert await _default(_slot(), prof) is True


@pytest.mark.asyncio
async def test_thinking_default_pinned_by_flags() -> None:
    prof = ProfileConfig(flags="--reasoning off")
    assert await _default(_slot(tune={"enable_thinking": True}), prof) is False


@pytest.mark.asyncio
async def test_thinking_default_unset_is_none() -> None:
    assert await _default(_slot(), ProfileConfig()) is None
