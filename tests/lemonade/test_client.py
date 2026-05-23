"""Unit tests for ``hal0.lemonade.client.LemonadeClient``.

Covers the control-plane wrapper's shape — every method routes through
the same ``_request`` chokepoint, so the test surface is:

  * each method hits the right HTTP verb + path + body shape
  * 2xx responses parse + return JSON body
  * non-2xx responses raise the right exception subclass
    (``LemonadeLoadError`` for /v1/load specifically)
  * httpx network errors raise ``LemonadeUnavailableError``
  * httpx timeouts raise ``LemonadeTimeoutError``
  * Bearer header is set when api_key is present, absent otherwise
  * ``live()`` swallows errors and returns bool, never raises
  * ``aclose()`` closes the owned client; double-close is a no-op
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.errors import (
    LemonadeHTTPError,
    LemonadeLoadError,
    LemonadeTimeoutError,
    LemonadeUnavailableError,
)


def _mock_transport(handler):
    """httpx MockTransport convenience wrapper — handler signature
    ``(request) -> httpx.Response``."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


# ── /live ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_returns_true_on_2xx() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        assert await client.live() is True


@pytest.mark.asyncio
async def test_live_returns_false_on_5xx_without_raising() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"err": "down"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        # /live is the healthcheck path — must not raise so a down
        # daemon doesn't crash hal0-api's poll loop.
        assert await client.live() is False


@pytest.mark.asyncio
async def test_live_returns_false_on_connect_error_without_raising() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        assert await client.live() is False


# ── /v1/health ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_returns_parsed_body() -> None:
    payload = {
        "loaded": [{"model_name": "hermes-4-14b", "backend_url": "http://127.0.0.1:9101"}],
        "ready": True,
    }

    def h(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        assert req.url.path == "/v1/health"
        return httpx.Response(200, json=payload)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        assert await client.health() == payload


@pytest.mark.asyncio
async def test_health_raises_lemonade_http_error_on_500() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "internal"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        with pytest.raises(LemonadeHTTPError) as exc:
            await client.health()
        assert exc.value.status_code == 500
        assert exc.value.body == {"detail": "internal"}


# ── /v1/stats ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_returns_parsed_body() -> None:
    payload = {"last_request": {"prompt_t_per_s": 287.3, "decode_t_per_s": 21.4}}

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/stats"
        return httpx.Response(200, json=payload)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        assert await client.stats() == payload


# ── /v1/load ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_posts_minimal_body() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/v1/load"
        import json as _json

        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "loaded"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        await client.load("hermes-4-14b")
        # Only model_name is required (v1_load_schema memory + ADR-0006 §3).
        assert captured["body"] == {"model_name": "hermes-4-14b"}


@pytest.mark.asyncio
async def test_load_includes_optional_fields_when_provided() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "loaded"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        await client.load(
            "hermes-4-14b",
            recipe="llamacpp:rocm",
            ctx_size=8192,
            llamacpp_backend="rocm",
            llamacpp_args=["--parallel", "1", "--threads", "8"],
        )
        # llamacpp_args is wire-serialised as a single space-separated
        # string — Lemonade's nlohmann::json parser raises
        # "type must be string, but is array" on a list.
        assert captured["body"] == {
            "model_name": "hermes-4-14b",
            "recipe": "llamacpp:rocm",
            "ctx_size": 8192,
            "llamacpp_backend": "rocm",
            "llamacpp_args": "--parallel 1 --threads 8",
        }


# ── llamacpp_args serialization (ADR-0008 §4 + spike #2) ────────────


@pytest.mark.asyncio
async def test_load_omits_llamacpp_args_when_none() -> None:
    """``None`` → key absent from the JSON body. Never send JSON ``null``
    (nlohmann's unconditional accessor raises "type must be string, but
    is null"). See ``hal0_lemonade_v1_load_schema`` memory."""

    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "loaded"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        await client.load("hermes-4-14b", llamacpp_args=None)
        assert "llamacpp_args" not in captured["body"]


@pytest.mark.asyncio
async def test_load_forwards_llamacpp_args_string_verbatim() -> None:
    """A pre-joined string is the canonical wire shape — pass it
    through unchanged so callers that already hold the config-file
    representation don't pay a needless split/join round-trip."""

    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "loaded"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        await client.load("hermes-4-14b", llamacpp_args="--threads 8")
        assert captured["body"]["llamacpp_args"] == "--threads 8"


@pytest.mark.asyncio
async def test_load_joins_llamacpp_args_list_with_single_spaces() -> None:
    """List input is joined with single spaces — the shape recommended
    in ADR-0008 §4 (``"--parallel 1 --threads N"``)."""

    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "loaded"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        await client.load(
            "hermes-4-14b",
            llamacpp_args=["--parallel", "1", "--threads", "8"],
        )
        assert captured["body"]["llamacpp_args"] == "--parallel 1 --threads 8"


@pytest.mark.asyncio
async def test_load_empty_list_becomes_empty_string() -> None:
    """An empty list is forwarded as the empty string, which Lemonade
    treats as a "use default" sentinel via ``is_empty_option`` — distinct
    from omitting the key entirely."""

    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "loaded"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        await client.load("hermes-4-14b", llamacpp_args=[])
        assert captured["body"]["llamacpp_args"] == ""


@pytest.mark.asyncio
async def test_load_raises_lemonade_load_error_not_generic_http_error() -> None:
    """Critical for ADR-0007: SlotManager must distinguish /v1/load
    failures (evict-all triggered) from other HTTP errors."""

    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "load failed"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        with pytest.raises(LemonadeLoadError) as exc:
            await client.load("hermes-4-14b")
        # LemonadeLoadError extends LemonadeHTTPError; carries status + body.
        assert isinstance(exc.value, LemonadeHTTPError)
        assert exc.value.status_code == 500
        assert exc.value.body == {"detail": "load failed"}
        # And the message mentions the model name for debuggability.
        assert "hermes-4-14b" in str(exc.value)


# ── /v1/unload ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unload_posts_model_name() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(req.content.decode())
        assert req.url.path == "/v1/unload"
        return httpx.Response(200, json={"status": "unloaded"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        await client.unload("hermes-4-14b")
        assert captured["body"] == {"model_name": "hermes-4-14b"}


# ── /v1/pull ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pull_posts_model_name_without_overwrite_by_default() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"job_id": "abc"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        await client.pull("hermes-4-14b")
        assert captured["body"] == {"model_name": "hermes-4-14b"}


@pytest.mark.asyncio
async def test_pull_includes_allow_overwrite_when_true() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"job_id": "abc"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        await client.pull("hermes-4-14b", allow_overwrite=True)
        assert captured["body"] == {"model_name": "hermes-4-14b", "allow_overwrite": True}


# ── network / timeout error mapping ──────────────────────────────────


@pytest.mark.asyncio
async def test_connect_error_maps_to_unavailable() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        with pytest.raises(LemonadeUnavailableError):
            await client.health()


@pytest.mark.asyncio
async def test_timeout_maps_to_lemonade_timeout() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        with pytest.raises(LemonadeTimeoutError):
            await client.health()


# ── auth headers ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bearer_header_set_when_api_key_present() -> None:
    captured: dict[str, str] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport, api_key="hal0-internal-token")
        await client.health()
    assert captured["auth"] == "Bearer hal0-internal-token"


@pytest.mark.asyncio
async def test_no_auth_header_when_api_key_absent() -> None:
    captured: dict[str, str] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        await client.health()
    assert captured["auth"] == ""


# ── lifecycle ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aclose_is_idempotent_when_client_is_owned() -> None:
    """Double-aclose on an owned client should not raise."""

    client = LemonadeClient(base_url="http://test")  # owns the http_client
    await client.aclose()
    await client.aclose()  # noop


@pytest.mark.asyncio
async def test_aclose_does_not_close_borrowed_client() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = _mock_transport(h)
    client = LemonadeClient(http_client=transport)  # client is borrowed
    await client.aclose()
    # Borrowed client should remain usable
    assert not transport.is_closed
    await transport.aclose()


# ── /internal/* — loopback-only control endpoints (plan §2.2) ────────


@pytest.mark.asyncio
async def test_shutdown_posts_to_internal_shutdown_with_auth() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["method"] = req.method
        captured["path"] = req.url.path
        captured["auth"] = req.headers.get("authorization", "")
        captured["body"] = req.content.decode() if req.content else ""
        return httpx.Response(200, json={"status": "shutting_down"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport, api_key="hal0-internal-token")
        assert await client.shutdown() == {"status": "shutting_down"}

    assert captured["method"] == "POST"
    assert captured["path"] == "/internal/shutdown"
    assert captured["auth"] == "Bearer hal0-internal-token"
    # No body required for shutdown — plan §2.2.
    assert captured["body"] == ""


@pytest.mark.asyncio
async def test_internal_config_gets_runtime_snapshot_with_auth() -> None:
    snapshot = {
        "host": "127.0.0.1",
        "port": 13305,
        "ctx_size": 4096,
        "llamacpp": {"args": "--parallel 1 --threads 8", "backend": "rocm"},
    }
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["method"] = req.method
        captured["path"] = req.url.path
        captured["auth"] = req.headers.get("authorization", "")
        return httpx.Response(200, json=snapshot)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport, api_key="hal0-internal-token")
        assert await client.internal_config() == snapshot

    assert captured["method"] == "GET"
    assert captured["path"] == "/internal/config"
    assert captured["auth"] == "Bearer hal0-internal-token"


@pytest.mark.asyncio
async def test_internal_set_posts_atomic_key_value_body() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["method"] = req.method
        captured["path"] = req.url.path
        captured["auth"] = req.headers.get("authorization", "")
        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"applied": list(captured["body"])})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport, api_key="hal0-internal-token")
        result = await client.internal_set({"log_level": "debug", "max_loaded_models": 4})
        assert result == {"applied": ["log_level", "max_loaded_models"]}

    assert captured["method"] == "POST"
    assert captured["path"] == "/internal/set"
    assert captured["auth"] == "Bearer hal0-internal-token"
    assert captured["body"] == {"log_level": "debug", "max_loaded_models": 4}


@pytest.mark.asyncio
async def test_internal_cleanup_cache_posts_with_empty_body() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["method"] = req.method
        captured["path"] = req.url.path
        captured["auth"] = req.headers.get("authorization", "")
        captured["body"] = req.content.decode() if req.content else ""
        return httpx.Response(200, json={"removed_bytes": 0})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport, api_key="hal0-internal-token")
        assert await client.internal_cleanup_cache() == {"removed_bytes": 0}

    assert captured["method"] == "POST"
    assert captured["path"] == "/internal/cleanup-cache"
    assert captured["auth"] == "Bearer hal0-internal-token"
    assert captured["body"] == ""


@pytest.mark.asyncio
async def test_internal_endpoints_raise_lemonade_http_error_on_non_2xx() -> None:
    """The four ``/internal/*`` endpoints route through the generic
    ``_raise_for_status`` chokepoint — non-2xx must surface as
    ``LemonadeHTTPError``, NOT ``LemonadeLoadError`` (which is reserved
    for ``/v1/load``'s evict-all blast radius per ADR-0008 §3)."""

    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "loopback only"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        for coro in (
            client.shutdown(),
            client.internal_config(),
            client.internal_set({"log_level": "info"}),
            client.internal_cleanup_cache(),
        ):
            with pytest.raises(LemonadeHTTPError) as exc:
                await coro
            assert exc.value.status_code == 403
