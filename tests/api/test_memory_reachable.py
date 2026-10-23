"""``memory_reachable``: the constant-time availability check behind the overview (#2957)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hal0.api.agents.memory_stats import memory_reachable


def _request(provider: Any) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(memory_provider=provider)))


class _Provider:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail

    async def bank_stats(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._fail:
            raise ConnectionError("down")
        return {}


@pytest.mark.asyncio
async def test_reachable_uses_bank_stats_not_a_list_walk() -> None:
    provider = _Provider()
    assert await memory_reachable("hermes", _request(provider)) is True
    assert provider.calls == [{"dataset": "private:hermes", "client_id": "hermes"}]


@pytest.mark.asyncio
async def test_no_provider_or_no_bank_stats_is_not_reachable() -> None:
    assert await memory_reachable("hermes", _request(None)) is False
    assert await memory_reachable("hermes", _request(SimpleNamespace())) is False


@pytest.mark.asyncio
async def test_engine_error_propagates_for_the_caller_to_guard() -> None:
    with pytest.raises(ConnectionError):
        await memory_reachable("hermes", _request(_Provider(fail=True)))
