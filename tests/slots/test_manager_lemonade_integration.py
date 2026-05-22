"""SlotManager ↔ Lemonade integration tests (ADR-0007).

This module tests the integration *contract* between SlotManager and
the Lemonade pre-load + load-timeout path. The contract is enforced
today via ``hal0.lemonade.preload.safe_load`` — the function the
future ``LemonadeProvider`` (separate PR per the migration plan) will
call instead of bare ``LemonadeClient.load``. This file pins the
behaviour the provider PR must honour:

  * PreloadError on validation failure → /v1/load is NOT called
  * Other loaded models stay loaded across a pre-validation failure
    (blast-radius assertion — the whole point of ADR-0007)
  * /v1/load timeout converts to ``PreloadError.LoadTimeout`` so
    SlotManager can route it through the same error path
  * No retry on timeout (ADR-0007 §4)

When the ``LemonadeProvider`` lands, this file becomes the regression
fence — any code path inside the provider that bypasses ``safe_load``
will break these tests.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.errors import LemonadeTimeoutError
from hal0.lemonade.preload import (
    GGUF_MAGIC,
    ChecksumMismatch,
    FileNotFound,
    LoadTimeout,
    PreloadError,
    safe_load,
)
from hal0.registry.model import Model

# Placeholder for the SlotConfig argument. ``safe_load`` doesn't read
# the SlotConfig today (reserved for future tunables), so any sentinel
# works. The LemonadeProvider PR will pass a real one.
_SLOT_CFG = object()


def _mock_transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


def _write_gguf(path: Path, body: bytes = b"\x00" * 1024) -> bytes:
    """Write a minimal GGUF file and return the payload."""
    payload = GGUF_MAGIC + body
    path.write_bytes(payload)
    return payload


def _model(path: Path, payload: bytes, *, model_id: str = "primary") -> Model:
    return Model(
        id=model_id,
        path=str(path),
        size_bytes=len(payload),
        backends=["vulkan"],
        capabilities=["chat"],
        metadata={"sha256": hashlib.sha256(payload).hexdigest()},
    )


# ── ADR-0007 §1 + §2: pre-validation failure → /v1/load NOT called ────


@pytest.mark.asyncio
async def test_preload_failure_does_not_call_v1_load(tmp_path: Path) -> None:
    """The core ADR-0007 invariant.

    If safe_load lets a corrupt model through to /v1/load, Lemonade's
    nuclear-evict-all policy will blast every loaded model on the
    failure. This test pins the short-circuit.
    """
    p = tmp_path / "primary.gguf"
    payload = _write_gguf(p)
    entry = _model(p, payload)
    entry.metadata["sha256"] = "0" * 64  # poison the hash

    v1_load_hits = {"count": 0}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/load":
            v1_load_hits["count"] += 1
        return httpx.Response(200, json={"status": "loaded"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        with pytest.raises(PreloadError) as exc_info:
            await safe_load(client, _SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]
        # Critical: /v1/load was never touched. No evict-all triggered.
        assert v1_load_hits["count"] == 0
        # Specific subclass surfaces for the dashboard.
        assert isinstance(exc_info.value, ChecksumMismatch)


@pytest.mark.asyncio
async def test_missing_file_short_circuits_before_load(tmp_path: Path) -> None:
    """FileNotFound is the only failure mode Lemonade itself handles
    safely, but we STILL intercept here so the dashboard gets a typed
    error instead of a naked HTTP 4xx — and so the surface is uniform
    with the other PreloadError variants."""
    missing = tmp_path / "ghost.gguf"
    entry = Model(
        id="ghost",
        path=str(missing),
        size_bytes=1024,
        backends=["vulkan"],
        capabilities=["chat"],
        metadata={"sha256": "0" * 64},
    )

    v1_load_hits = {"count": 0}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/load":
            v1_load_hits["count"] += 1
        return httpx.Response(200, json={"status": "loaded"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        with pytest.raises(FileNotFound):
            await safe_load(client, _SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]
        assert v1_load_hits["count"] == 0


# ── ADR-0007: other slots stay loaded across a failure ────────────────


@pytest.mark.asyncio
async def test_other_loaded_models_stay_loaded_when_pre_validation_fails(
    tmp_path: Path,
) -> None:
    """Blast-radius assertion.

    Simulates the prod scenario: slot ``alpha`` is loaded and serving;
    slot ``beta`` is asked to load a corrupt model. Without ADR-0007,
    /v1/load on beta would 5xx and evict alpha. With ADR-0007's
    pre-validation, /v1/load is never called for beta → alpha
    untouched.
    """
    p = tmp_path / "beta.gguf"
    payload = _write_gguf(p)
    beta = _model(p, payload, model_id="beta")
    beta.metadata["sha256"] = "deadbeef" * 8  # corrupt

    # Track lemond pool state. /v1/load is the only call that would
    # cause alpha to drop out of `all_models_loaded`.
    pool: dict[str, dict[str, Any]] = {"alpha": {"model_name": "alpha", "last_use": 0}}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"all_models_loaded": list(pool.values())})
        if req.url.path == "/v1/load":
            # If we ever reach here, simulate the nuclear-evict-all:
            pool.clear()
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        # Confirm baseline: alpha is loaded.
        assert any(m["model_name"] == "alpha" for m in (await client.health())["all_models_loaded"])

        with pytest.raises(ChecksumMismatch):
            await safe_load(client, _SLOT_CFG, beta, registry=None)  # type: ignore[arg-type]

        # ADR-0007 invariant: alpha STILL loaded. No evict-all triggered
        # because /v1/load was never called on beta.
        after = await client.health()
        assert any(m["model_name"] == "alpha" for m in after["all_models_loaded"])


# ── ADR-0007 §5: timeout converts to PreloadError.LoadTimeout ─────────


@pytest.mark.asyncio
async def test_load_timeout_surfaces_as_preload_load_timeout(tmp_path: Path) -> None:
    """ADR-0007 §5 + brief item 4.

    SlotManager's pre-load failure path is a single ``except PreloadError``
    branch. ``LemonadeTimeoutError`` from ``client.load()`` must be
    wrapped into ``PreloadError.LoadTimeout`` so SlotManager doesn't
    have to know about httpx/lemonade timeout types.
    """
    p = tmp_path / "primary.gguf"
    payload = _write_gguf(p)
    entry = _model(p, payload)

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/load":
            raise httpx.ReadTimeout("simulated hang")
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        with pytest.raises(LoadTimeout) as exc_info:
            await safe_load(client, _SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]
        # Underlying cause is preserved for debug tooling.
        assert isinstance(exc_info.value.__cause__, LemonadeTimeoutError)


# ── ADR-0007 §4: NO retry on timeout ──────────────────────────────────


@pytest.mark.asyncio
async def test_safe_load_does_not_retry_on_timeout(tmp_path: Path) -> None:
    """Per ADR-0007 §4: retry would risk another evict-all if the cause
    flips from a timeout to a non-file-not-found failure mid-retry.
    safe_load must call /v1/load exactly once."""
    p = tmp_path / "primary.gguf"
    payload = _write_gguf(p)
    entry = _model(p, payload)

    attempts = {"count": 0}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/load":
            attempts["count"] += 1
            raise httpx.ReadTimeout("simulated hang")
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        with pytest.raises(LoadTimeout):
            await safe_load(client, _SLOT_CFG, entry, registry=None)  # type: ignore[arg-type]
        # Single attempt, no retry.
        assert attempts["count"] == 1


# ── happy path: validation passes → /v1/load called once ──────────────


@pytest.mark.asyncio
async def test_safe_load_happy_path_calls_v1_load_once(tmp_path: Path) -> None:
    p = tmp_path / "primary.gguf"
    payload = _write_gguf(p)
    entry = _model(p, payload, model_id="hermes-4-14b")

    bodies: list[dict[str, Any]] = []

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/load":
            import json as _json

            bodies.append(_json.loads(req.content.decode()))
            return httpx.Response(200, json={"status": "loaded"})
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        result = await safe_load(
            client,
            _SLOT_CFG,  # type: ignore[arg-type]
            entry,
            registry=None,
            llamacpp_backend="rocm",
            ctx_size=8192,
        )
        assert result == {"status": "loaded"}
        assert len(bodies) == 1
        assert bodies[0]["model_name"] == "hermes-4-14b"
        assert bodies[0]["llamacpp_backend"] == "rocm"
        assert bodies[0]["ctx_size"] == 8192
