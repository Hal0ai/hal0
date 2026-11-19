"""``list_all`` must not report an FLM registry row installed once FLM says it is gone (#3810)."""

from __future__ import annotations

import types
from typing import Any

import pytest

import hal0.providers.flm as flm_mod
from hal0.registry.model import Model
from hal0.services import models_service


def _flm_row(tag: str) -> Model:
    return Model(
        id=tag,
        path=f"/models/flm/models/{tag}",
        backends=["npu"],
        metadata={"runtime": "flm"},
    )


async def _installed(
    monkeypatch: pytest.MonkeyPatch, rows: list[Model], catalog: list[dict[str, Any]] | None
) -> dict[str, bool]:
    monkeypatch.setattr(flm_mod, "flm_catalog", lambda: catalog)
    monkeypatch.setattr(flm_mod, "flm_served_models", lambda: catalog or [])
    registry = types.SimpleNamespace(list=lambda: rows)
    upstreams = types.SimpleNamespace(list=lambda: [])
    out = await models_service.list_all(
        registry=registry, upstreams=upstreams, cache={}, update_state=None
    )
    return {m["id"]: m["installed"] for m in out["models"]}


async def test_flm_row_missing_from_disk_reads_not_installed(monkeypatch) -> None:
    got = await _installed(
        monkeypatch,
        [_flm_row("qwen3:0.6b"), _flm_row("llama3.2:1b")],
        [
            {"tag": "qwen3:0.6b", "installed": False, "capabilities": ["chat"]},
            {"tag": "llama3.2:1b", "installed": True, "capabilities": ["chat"]},
        ],
    )
    assert got["qwen3:0.6b"] is False
    assert got["llama3.2:1b"] is True


async def test_unanswered_probe_keeps_flm_rows_installed(monkeypatch) -> None:
    got = await _installed(monkeypatch, [_flm_row("qwen3:0.6b")], None)
    assert got["qwen3:0.6b"] is True


async def test_gguf_row_is_untouched_by_the_flm_catalogue(monkeypatch) -> None:
    gguf = Model(id="qwen3:0.6b", path="/models/q.gguf", backends=["llamacpp"])
    got = await _installed(
        monkeypatch, [gguf], [{"tag": "qwen3:0.6b", "installed": False, "capabilities": ["chat"]}]
    )
    assert got["qwen3:0.6b"] is True
