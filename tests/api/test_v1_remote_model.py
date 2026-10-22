"""``_is_remote_model`` recognises ids owned by a pinned remote with a cold cache."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hal0.api.routes import v1
from hal0.upstreams.registry import Upstream


def _req(upstreams: list[Upstream], cache: dict[str, list[str]]):
    reg = SimpleNamespace(list=lambda: upstreams)
    state = SimpleNamespace(upstreams=reg, upstream_models=cache)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_is_remote_model_true_for_owned_id_with_cold_cache() -> None:
    u = Upstream(
        name="hermes.coder",
        kind="remote",
        url="http://h.invalid/v1",
        pinned=True,
        owned_models=("coder",),
    )
    assert v1._is_remote_model(_req([u], {}), "coder") is True
    assert v1._is_remote_model(_req([u], {}), "other") is False


async def test_dispatch_skips_slot_alias_rewrite_for_owned_id() -> None:
    """A slot named like a pinned-owned id must not capture the request."""

    class _Slots:
        async def iter_configs(self) -> list[dict]:
            return [
                {"name": "coder", "type": "llm", "port": 8001, "model": {"default": "qwen3-4b"}}
            ]

    class _Stop(Exception):
        pass

    seen: list[dict] = []

    class _Dispatcher:
        async def dispatch(self, request, body=None):
            seen.append(dict(body))
            raise _Stop

    u = Upstream(
        name="hermes.coder",
        kind="remote",
        url="http://h.invalid/v1",
        pinned=True,
        owned_models=("coder",),
    )
    request = SimpleNamespace(
        _body=b"",
        url=SimpleNamespace(path="/v1/chat/completions"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                upstreams=SimpleNamespace(list=lambda: [u]),
                upstream_models={},
                slot_manager=_Slots(),
            )
        ),
    )
    with pytest.raises(_Stop):
        await v1._dispatch_and_forward(request, _Dispatcher(), body={"model": "coder"})
    assert seen == [{"model": "coder"}]
