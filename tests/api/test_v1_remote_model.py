"""``_is_remote_model`` recognises ids owned by a pinned remote with a cold cache."""

from __future__ import annotations

from types import SimpleNamespace

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
