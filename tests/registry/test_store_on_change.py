"""ModelRegistry.on_change post-mutation hook.

A generic post-mutation callback: callers can regenerate downstream
artifacts after any registry mutation (add/update/remove) without every
call site remembering to do it.
See docs/superpowers/plans/2026-06-06-model-store-cleanup-hardening.md.
"""

from __future__ import annotations

from pathlib import Path

from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry


def _model(tmp_path: Path, mid: str = "m1", **kw) -> Model:
    return Model(
        id=mid,
        name=mid,
        path=str(tmp_path / f"{mid}.gguf"),
        size_bytes=1,
        capabilities=["chat"],
        **kw,
    )


def test_on_change_fires_after_add(tmp_path):
    reg = ModelRegistry(tmp_path)
    calls: list[str] = []
    reg.on_change = lambda: calls.append("x")
    reg.add(_model(tmp_path))
    assert calls == ["x"]


def test_on_change_fires_after_update_and_remove(tmp_path):
    reg = ModelRegistry(tmp_path)
    reg.add(_model(tmp_path))
    calls: list[str] = []
    reg.on_change = lambda: calls.append("x")
    reg.update("m1", {"size_bytes": 2})
    reg.remove("m1")
    assert calls == ["x", "x"]


def test_on_change_failure_does_not_break_write(tmp_path):
    reg = ModelRegistry(tmp_path)

    def boom() -> None:
        raise RuntimeError("regen failed")

    reg.on_change = boom
    # The add must still succeed and persist even though the hook raises.
    reg.add(_model(tmp_path))
    assert reg.has("m1")


def test_no_hook_is_a_noop(tmp_path):
    reg = ModelRegistry(tmp_path)
    reg.add(_model(tmp_path))  # must not raise with on_change unset
    assert reg.has("m1")
