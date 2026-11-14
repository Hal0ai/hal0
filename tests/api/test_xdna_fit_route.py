"""``GET /api/runners/xdna/fit`` -- fused vs per-op for every GGUF chat model."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.registry.model import Model
from hal0.slots.model_fit import xdna_fit_cache_clear
from tests.registry.gguf_fixtures import write_gguf


@pytest.fixture
def fit_client(client: TestClient, tmp_path: Path) -> TestClient:
    xdna_fit_cache_clear()
    fused = write_gguf(
        tmp_path / "qwen35-fused.gguf",
        "qwen35",
        uints={"qwen35.embedding_length": 1024},
        tensor_names=["blk.0.ssm_out.weight"],
        tensor_types={"blk.0.ssm_out.weight": 13},
    )
    perop = write_gguf(tmp_path / "lfm2-chat.gguf", "lfm2")
    embed = write_gguf(tmp_path / "nomic-embed-text.gguf", "nomic-bert")
    reg = client.app.state.model_registry
    for mid, path, caps in (
        ("qwen35-fused", fused, ["chat"]),
        ("lfm2-chat", perop, ["chat"]),
        ("missing-chat", tmp_path / "gone.gguf", ["chat"]),
        ("nomic-embed-text", embed, ["embed"]),
    ):
        reg.add(Model(id=mid, name=mid, path=str(path), capabilities=caps))
    yield client
    xdna_fit_cache_clear()


def test_fit_maps_every_gguf_llm_model(fit_client: TestClient) -> None:
    r = fit_client.get("/api/runners/xdna/fit")
    assert r.status_code == 200, r.text
    models = r.json()["models"]
    assert models["qwen35-fused"]["xdna_path"] == "fused"
    assert models["lfm2-chat"]["xdna_path"] == "per-op"
    assert "nomic-embed-text" not in models


def test_fit_reports_an_unreadable_model_without_failing(fit_client: TestClient) -> None:
    r = fit_client.get("/api/runners/xdna/fit")
    assert r.status_code == 200
    row = r.json()["models"]["missing-chat"]
    assert row["xdna_path"] is None
    assert row["reason"]


def test_fit_filters_to_one_model(fit_client: TestClient) -> None:
    r = fit_client.get("/api/runners/xdna/fit", params={"model": "lfm2-chat"})
    assert r.status_code == 200
    assert list(r.json()["models"]) == ["lfm2-chat"]


def test_fit_unknown_model_is_404(fit_client: TestClient) -> None:
    r = fit_client.get("/api/runners/xdna/fit", params={"model": "nope"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "runners.model_not_found"
