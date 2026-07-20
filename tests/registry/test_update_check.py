"""Tests for HF update detection (registry/update_check.py).

Mirrors the pull-engine test style: ``httpx.MockTransport`` stands in for
huggingface.co's tree API, and the pure verdict helper is exercised
directly against pre-baked repo trees.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.registry.model import Model
from hal0.registry.pull import make_job, run_pull
from hal0.registry.store import ModelRegistry
from hal0.registry.update_check import evaluate_model_update, fetch_remote_lfs_shas

# ── helpers ──────────────────────────────────────────────────────────────────

SHA_A = "a" * 64
SHA_B = "b" * 64


def _model(**overrides: Any) -> Model:
    base: dict[str, Any] = {
        "id": "qwen3-4b",
        "path": "/var/lib/hal0/models/qwen3-4b/qwen3-4b.gguf",
        "hf_repo": "Qwen/Qwen3-4B-GGUF",
        "hf_filename": "qwen3-4b.gguf",
        "metadata": {"sha256": SHA_A},
    }
    base.update(overrides)
    return Model(**base)


def _tree_transport(trees: dict[str, list[dict[str, Any]] | int]) -> httpx.MockTransport:
    """Serve ``/api/models/{repo}/tree/main`` from a repo→entries map.

    An int value is returned as that HTTP status instead of a body.
    """

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path  # /api/models/<org>/<repo>/tree/main
        repo = path.removeprefix("/api/models/").removesuffix("/tree/main")
        entries = trees.get(repo)
        if entries is None:
            return httpx.Response(404, json={"error": "not found"})
        if isinstance(entries, int):
            return httpx.Response(entries, content=b"")
        return httpx.Response(200, json=entries)

    return httpx.MockTransport(handler)


# ── evaluate_model_update (pure) ─────────────────────────────────────────────


def test_evaluate_none_for_rows_without_hf_coords() -> None:
    """Hand-registered rows (no repo/filename) are not HF-updatable at all."""
    assert evaluate_model_update(_model(hf_repo=""), {}) is None
    assert evaluate_model_update(_model(hf_filename=""), {}) is None


def test_evaluate_update_available_when_shas_differ() -> None:
    verdict = evaluate_model_update(_model(), {"Qwen/Qwen3-4B-GGUF": {"qwen3-4b.gguf": SHA_B}})
    assert verdict is not None
    assert verdict["update_available"] is True
    assert verdict["remote_sha256"] == SHA_B
    assert verdict["local_sha256"] == SHA_A
    assert verdict["reason"] is None


def test_evaluate_up_to_date_when_shas_match_case_insensitively() -> None:
    verdict = evaluate_model_update(
        _model(metadata={"sha256": SHA_A.upper()}),
        {"Qwen/Qwen3-4B-GGUF": {"qwen3-4b.gguf": SHA_A}},
    )
    assert verdict is not None
    assert verdict["update_available"] is False


def test_evaluate_repo_unreachable_never_flags_update() -> None:
    """A failed tree fetch (mapped to None) is 'couldn't check', not 'update'."""
    verdict = evaluate_model_update(_model(), {"Qwen/Qwen3-4B-GGUF": None})
    assert verdict is not None
    assert verdict["update_available"] is False
    assert verdict["reason"] == "repo_unreachable"


def test_evaluate_missing_remote_file_never_flags_update() -> None:
    """File renamed/removed upstream (or non-LFS) → no phantom update."""
    verdict = evaluate_model_update(_model(), {"Qwen/Qwen3-4B-GGUF": {}})
    assert verdict is not None
    assert verdict["update_available"] is False
    assert verdict["reason"] == "file_missing_or_not_lfs"


def test_evaluate_no_local_sha_never_flags_update() -> None:
    """Rows registered before sha recording can't be compared — no badge."""
    verdict = evaluate_model_update(
        _model(metadata={}), {"Qwen/Qwen3-4B-GGUF": {"qwen3-4b.gguf": SHA_B}}
    )
    assert verdict is not None
    assert verdict["update_available"] is False
    assert verdict["reason"] == "no_local_sha256"
    assert verdict["remote_sha256"] == SHA_B


def test_evaluate_basename_fallback_resolves_subdir_hosted_file() -> None:
    """A row whose stored hf_filename dropped the upstream subdir prefix
    still resolves via a unique basename match, so it isn't a false negative."""
    verdict = evaluate_model_update(
        _model(),  # hf_filename="qwen3-4b.gguf" (bare)
        {"Qwen/Qwen3-4B-GGUF": {"UD-Q4_K_XL/qwen3-4b.gguf": SHA_B}},
    )
    assert verdict is not None
    assert verdict["update_available"] is True
    assert verdict["remote_sha256"] == SHA_B


def test_evaluate_basename_fallback_skips_ambiguous_match() -> None:
    """When two tree files share the basename with DIFFERENT shas, the
    fallback must not guess — leave it unresolved rather than compare
    against the wrong quant."""
    verdict = evaluate_model_update(
        _model(),
        {"Qwen/Qwen3-4B-GGUF": {"a/qwen3-4b.gguf": SHA_A, "b/qwen3-4b.gguf": SHA_B}},
    )
    assert verdict is not None
    assert verdict["update_available"] is False
    assert verdict["reason"] == "file_missing_or_not_lfs"


# ── fetch_remote_lfs_shas ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_surfaces_lfs_oids_and_skips_non_lfs() -> None:
    transport = _tree_transport(
        {
            "Qwen/Qwen3-4B-GGUF": [
                {"path": "qwen3-4b.gguf", "lfs": {"oid": f"sha256:{SHA_A.upper()}", "size": 9}},
                {"path": "README.md", "size": 100},  # non-LFS: git blob only
                {"path": "sub/dir/part2.gguf", "lfs": {"oid": SHA_B}},
                "garbage-entry",
            ]
        }
    )
    client = httpx.AsyncClient(transport=transport)
    try:
        out = await fetch_remote_lfs_shas({"Qwen/Qwen3-4B-GGUF"}, client=client)
    finally:
        await client.aclose()
    files = out["Qwen/Qwen3-4B-GGUF"]
    assert files == {"qwen3-4b.gguf": SHA_A, "sub/dir/part2.gguf": SHA_B}


@pytest.mark.asyncio
async def test_fetch_maps_failed_repos_to_none_without_raising() -> None:
    transport = _tree_transport({"ok/repo": [{"path": "m.gguf", "lfs": {"oid": SHA_A}}]})
    client = httpx.AsyncClient(transport=transport)
    try:
        out = await fetch_remote_lfs_shas({"ok/repo", "gone/repo"}, client=client)
    finally:
        await client.aclose()
    assert out["ok/repo"] == {"m.gguf": SHA_A}
    assert out["gone/repo"] is None


@pytest.mark.asyncio
async def test_fetch_sends_bearer_token_when_provided() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization", "")
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await fetch_remote_lfs_shas({"a/b"}, hf_token="tok123", client=client)
    finally:
        await client.aclose()
    assert seen["auth"] == "Bearer tok123"


# ── run_pull dest_override (the in-place update path) ────────────────────────


@pytest.mark.asyncio
async def test_run_pull_dest_override_replaces_in_place(tmp_hal0_home: str, tmp_path: Path) -> None:
    """An update re-pull must land at the row's EXISTING path, not the
    layout-derived one — and refresh sha256 + pulled_at provenance."""
    old_body = b"GGUF" + b"o" * 60
    new_body = b"GGUF" + b"n" * 1024
    dest = tmp_path / "store" / "qwen3-4b" / "qwen3-4b.gguf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(old_body)

    registry = ModelRegistry()
    registry.add(
        Model(
            id="qwen3-4b",
            path=str(dest),
            hf_repo="Qwen/Qwen3-4B-GGUF",
            hf_filename="qwen3-4b.gguf",
            metadata={"sha256": hashlib.sha256(old_body).hexdigest()},
        )
    )

    job = make_job("qwen3-4b")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=new_body))
    )
    try:
        await run_pull(
            job,
            hf_repo="Qwen/Qwen3-4B-GGUF",
            hf_file="qwen3-4b.gguf",
            registry=registry,
            client=client,
            dest_override=str(dest),
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert dest.read_bytes() == new_body
    row = registry.get("qwen3-4b")
    assert row.path == str(dest)
    assert row.metadata["sha256"] == hashlib.sha256(new_body).hexdigest()
    assert isinstance(row.metadata.get("pulled_at"), int)
