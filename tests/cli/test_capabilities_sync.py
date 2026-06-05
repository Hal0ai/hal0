"""Tests for ``hal0 capabilities sync`` — runtime entry point for #141.

The install hook calls ``python -m hal0.lemonade.server_models_gen``
directly (tested under tests/lemonade/test_server_models_gen.py); this
file covers the Typer-mounted equivalent operators reach for after a
``hal0 model pull`` or registry edit.
"""

from __future__ import annotations

import json
from pathlib import Path

import tomli_w
from typer.testing import CliRunner

from hal0.cli.capabilities_commands import app as capabilities_app

runner = CliRunner()


def _write_registry(path: Path, models: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump({"models": models}, f)


def test_sync_writes_server_models_file(tmp_path: Path) -> None:
    registry = tmp_path / "registry.toml"
    output = tmp_path / "server_models.json"
    _write_registry(
        registry,
        {
            "bge-reranker-v2-m3-q4_k_m": {
                "path": "/mnt/ai-models/local/bge-reranker-v2-m3-Q4_K_M.gguf",
                "capabilities": ["rerank"],
                "backends": ["llamacpp"],
                "hf_repo": "gpustack/bge-reranker-v2-m3-GGUF",
                "hf_filename": "bge-reranker-v2-m3-Q4_K_M.gguf",
            }
        },
    )

    result = runner.invoke(
        capabilities_app,
        ["sync", "--registry", str(registry), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert output.exists()

    with open(output) as f:
        parsed = json.load(f)
    assert parsed["bge-reranker-v2-m3-q4_k_m"]["labels"] == ["reranking"]


def test_sync_dry_run_does_not_write(tmp_path: Path) -> None:
    registry = tmp_path / "registry.toml"
    output = tmp_path / "server_models.json"
    _write_registry(
        registry,
        {
            "m": {
                "path": "/x.gguf",
                "capabilities": ["chat"],
                "backends": ["vulkan"],
            }
        },
    )

    result = runner.invoke(
        capabilities_app,
        [
            "sync",
            "--registry",
            str(registry),
            "--output",
            str(output),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not output.exists()
    # The summary table is printed even in dry-run.
    assert "m" in result.output


def test_sync_empty_registry_emits_stock_fallback(tmp_path: Path) -> None:
    registry = tmp_path / "registry.toml"
    output = tmp_path / "server_models.json"
    # No registry file present at all -> #210: instead of a blank catalog,
    # the generator now emits a curated STOCK fallback so a fresh install
    # still has loadable models. The sync output reflects that non-empty set.

    result = runner.invoke(
        capabilities_app,
        ["sync", "--registry", str(registry), "--output", str(output), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    # Non-empty stock fallback rendered: a short canonical id appears
    # untruncated and the summary reports the 6-entry fallback count
    # (long ids are Rich-table-truncated, so assert on stable substrings).
    assert "qwen3.5-9b" in result.output
    assert "6 entries" in result.output
