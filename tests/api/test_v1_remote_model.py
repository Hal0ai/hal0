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


def _peer() -> Upstream:
    return Upstream(name="peer", kind="remote", url="http://peer.invalid/v1", hal0_peer=True)


def test_is_pinned_owned_only_for_pinned_owned_ids() -> None:
    pinned = Upstream(
        name="hermes.coder",
        kind="remote",
        url="http://h.invalid/v1",
        pinned=True,
        owned_models=("coder",),
    )
    req = _req([_peer(), pinned], {"peer": ["agent", "coder"], "hermes.coder": ["alias"]})
    assert v1._is_pinned_owned(req, "coder") is True
    assert v1._is_pinned_owned(req, "agent") is False  # a peer's cache owns nothing
    assert v1._is_pinned_owned(req, "alias") is False  # a cached alias owns nothing


async def test_peer_advertising_slot_alias_does_not_suppress_rewrite() -> None:
    """I1: a hal0 peer whose cache holds ``agent`` must not steal the local alias."""

    class _Slots:
        async def iter_configs(self) -> list[dict]:
            return [
                {"name": "agent", "type": "llm", "port": 8001, "model": {"default": "qwen3-4b"}}
            ]

    class _Stop(Exception):
        pass

    seen: list[dict] = []

    class _Dispatcher:
        async def dispatch(self, request, body=None):
            seen.append(dict(body))
            raise _Stop

    request = SimpleNamespace(
        _body=b"",
        url=SimpleNamespace(path="/v1/chat/completions"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                upstreams=SimpleNamespace(list=lambda: [_peer()]),
                upstream_models={"peer": ["agent"]},
                slot_manager=_Slots(),
            )
        ),
    )
    with pytest.raises(_Stop):
        await v1._dispatch_and_forward(request, _Dispatcher(), body={"model": "agent"})
    assert seen[0]["model"] == "qwen3-4b"
