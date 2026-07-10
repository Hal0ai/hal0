"""registry.updates — HF sha probe + update-available comparison."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from hal0.registry.model import Model
from hal0.registry.updates import (
    check_for_updates,
    clear_check_cache,
    is_checkable,
    local_sha256,
)

LOCAL_SHA = hashlib.sha256(b"old bytes").hexdigest()
REMOTE_SHA = hashlib.sha256(b"new bytes").hexdigest()


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_check_cache()
    yield
    clear_check_cache()


def _model(
    mid: str = "m1",
    *,
    repo: str = "org/repo",
    filename: str = "model.gguf",
    sha: str | None = LOCAL_SHA,
) -> Model:
    meta = {"sha256": sha} if sha else {}
    return Model(
        id=mid,
        path=f"/tmp/{mid}/model.gguf",
        hf_repo=repo,
        hf_filename=filename,
        metadata=meta,
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _head_handler(remote_sha: str | None, status: int = 200, calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        assert request.method == "HEAD"
        headers = {}
        if remote_sha is not None:
            headers["X-Linked-ETag"] = f'"{remote_sha}"'
        return httpx.Response(status, headers=headers)

    return handler


# ── field helpers ─────────────────────────────────────────────────────────────


def test_is_checkable_requires_both_coords():
    assert is_checkable(_model())
    assert not is_checkable(_model(repo=""))
    assert not is_checkable(_model(filename=""))


def test_local_sha256_missing_and_normalised():
    assert local_sha256(_model(sha=None)) is None
    assert local_sha256(_model(sha=LOCAL_SHA.upper())) == LOCAL_SHA


# ── check_for_updates ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_available_when_remote_sha_differs():
    async with _client(_head_handler(REMOTE_SHA)) as client:
        out = await check_for_updates([_model()], client=client)
    assert len(out) == 1
    row = out[0]
    assert row["model_id"] == "m1"
    assert row["status"] == "update_available"
    assert row["update_available"] is True
    assert row["local_sha256"] == LOCAL_SHA
    assert row["remote_sha256"] == REMOTE_SHA


@pytest.mark.asyncio
async def test_up_to_date_when_shas_match():
    async with _client(_head_handler(LOCAL_SHA)) as client:
        out = await check_for_updates([_model()], client=client)
    assert out[0]["status"] == "up_to_date"
    assert out[0]["update_available"] is False


@pytest.mark.asyncio
async def test_unknown_without_local_sha():
    async with _client(_head_handler(REMOTE_SHA)) as client:
        out = await check_for_updates([_model(sha=None)], client=client)
    assert out[0]["status"] == "unknown"
    assert out[0]["update_available"] is False


@pytest.mark.asyncio
async def test_unknown_without_remote_sha():
    # Non-LFS file: HEAD succeeds but exposes no sha256-shaped linked etag.
    async with _client(_head_handler(None)) as client:
        out = await check_for_updates([_model()], client=client)
    assert out[0]["status"] == "unknown"
    assert out[0]["update_available"] is False


@pytest.mark.asyncio
async def test_error_status_on_http_failure():
    async with _client(_head_handler(REMOTE_SHA, status=500)) as client:
        out = await check_for_updates([_model()], client=client)
    assert out[0]["status"] == "error"
    assert out[0]["update_available"] is False
    assert "500" in out[0]["error"]


@pytest.mark.asyncio
async def test_transport_error_is_captured_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with _client(handler) as client:
        out = await check_for_updates([_model()], client=client)
    assert out[0]["status"] == "error"
    assert "ConnectError" in out[0]["error"]


@pytest.mark.asyncio
async def test_rows_without_hf_coords_are_omitted():
    async with _client(_head_handler(REMOTE_SHA)) as client:
        out = await check_for_updates([_model(), _model("scanned", repo="")], client=client)
    assert [r["model_id"] for r in out] == ["m1"]


@pytest.mark.asyncio
async def test_probe_cached_within_ttl_and_shared_across_rows():
    calls: list[str] = []
    async with _client(_head_handler(REMOTE_SHA, calls=calls)) as client:
        # Two rows pointing at the same (repo, filename) → one probe.
        out = await check_for_updates([_model("a"), _model("b")], client=client)
        assert len(calls) == 1
        assert all(r["update_available"] for r in out)
        # Second sweep inside the TTL reuses the cache — no new HEAD.
        await check_for_updates([_model("a")], client=client)
        assert len(calls) == 1
        # refresh=True re-probes.
        await check_for_updates([_model("a")], refresh=True, client=client)
        assert len(calls) == 2


@pytest.mark.asyncio
async def test_cached_remote_sha_recomputed_against_current_local_sha():
    """A re-pull flips the row to up_to_date without a cache refresh."""
    async with _client(_head_handler(REMOTE_SHA)) as client:
        out = await check_for_updates([_model()], client=client)
        assert out[0]["update_available"] is True
        # Simulate the pull completing: local sha now matches remote.
        updated = _model(sha=REMOTE_SHA)
        out = await check_for_updates([updated], client=client)
    assert out[0]["status"] == "up_to_date"
    assert out[0]["update_available"] is False
