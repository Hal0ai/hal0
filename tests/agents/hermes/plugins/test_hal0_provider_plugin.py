"""hal0-provider Hermes plugin (canonical src copy) — hermetic unit coverage.

``src/hal0/agents/hermes/plugins/provider_hal0/`` is the canonical source for
the shipped hal0-provider plugin (its installer seed at
``installer/agents/hermes/plugins/hal0-provider/`` is pinned byte-identical in
``test_hal0_provider_parity.py``). This file imports the package the normal way
(``hal0.agents.hermes...``) so coverage attributes to the real source file.

Everything here is pure / mocked — no sockets:

* ``fetch_models`` runs against a duck-typed fake HTTP client that records the
  request URL + headers and returns a canned ``/v1/models`` body.
* ``register`` / ``register(ctx)`` / the module-level seam are exercised with
  stubs and the FROZEN contract fixture (``register_provider`` /
  ``get_provider_profile``) to prove round-trip registration.
* ``profile.py`` falls back to a vendored ``ProviderProfile`` dataclass when
  the real ``providers.base`` (Hermes-venv-only) is absent, so no
  ``importorskip`` is needed here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from hal0.agents.hermes.plugins.provider_hal0 import (
    PROFILE,
    Hal0ProviderProfile,
    register,
)
from hal0.agents.hermes.plugins.provider_hal0 import profile as profile_mod
from hal0.agents.hermes.plugins.provider_hal0.profile import (
    DEFAULT_AUX_MODEL,
    DEFAULT_BASE_URL,
    assemble_stream,
    is_alias,
    iter_sse_data,
    parse_sse_chunks,
)
from hal0.agents.hermes.plugins.provider_hal0.profile import (
    Hal0ProviderProfile as ProfileClass,
)

# The FROZEN contract fixture — the module-level registration seam Hermes pins.
from tests.fixtures.hermes.contracts.provider_profile import (
    ProviderProfile as FrozenProviderProfile,
)
from tests.fixtures.hermes.contracts.provider_profile import (
    get_provider_profile,
    register_provider,
)

# ── fakes ──────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeHttpClient:
    """Duck-typed ``httpx.Client`` stand-in — records calls, never sockets."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.calls.append({"url": url, "headers": dict(headers or {})})
        return self._responses.pop(0)

    def close(self) -> None:  # pragma: no cover — injected clients aren't closed
        raise AssertionError("injected client must not be closed by fetch_models")


def _models_body(ids: list[str], owned_by: str = "hal0") -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": mid, "object": "model", "owned_by": owned_by} for mid in ids],
    }


# ── identity / declarative fields ──────────────────────────────────────────


def test_profile_declares_hal0_chat_completions_contract() -> None:
    p = PROFILE
    assert p.name == "hal0"
    assert p.api_mode == "chat_completions"
    assert p.base_url == DEFAULT_BASE_URL
    assert p.models_url == f"{DEFAULT_BASE_URL}/models"
    assert p.default_aux_model == DEFAULT_AUX_MODEL == "hal0/agent"
    assert p.supports_vision is True
    assert p.auth_type == "none"


def test_profile_is_a_providerprofile_subclass() -> None:
    # Subclass of the (vendored-or-real) ProviderProfile so Hermes registers it.
    assert isinstance(PROFILE, ProfileClass)
    assert type(PROFILE).__mro__[1].__name__ == "ProviderProfile"


def test_profile_constructs_arg_free_for_subclass_discovery() -> None:
    # Hermes' "find a top-level ProviderProfile subclass" path constructs it
    # with no args — must not raise.
    p = Hal0ProviderProfile()
    assert p.name == "hal0"


def test_base_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_PROVIDER_BASE", "http://10.0.0.9:9000/v1/")
    p = Hal0ProviderProfile()
    assert p.base_url == "http://10.0.0.9:9000/v1"
    assert p.models_url == "http://10.0.0.9:9000/v1/models"


# ── live model discovery ───────────────────────────────────────────────────


def test_fetch_models_curates_and_filters_aliases() -> None:
    fake = _FakeHttpClient(
        [_FakeResponse(_models_body(["qwen3-30b", "primary", "haloai:x", "hal0/agent"]))]
    )
    p = Hal0ProviderProfile(http_client=fake)
    models = p.fetch_models()
    # Real models kept; routing aliases (primary, haloai:*) dropped.
    assert models == ["qwen3-30b", "hal0/agent"]


def test_fetch_models_sends_owner_filter_and_agent_headers() -> None:
    fake = _FakeHttpClient([_FakeResponse(_models_body(["m1"]))])
    Hal0ProviderProfile(http_client=fake).fetch_models()
    call = fake.calls[0]
    assert call["url"] == f"{DEFAULT_BASE_URL}/models"
    assert call["headers"]["X-hal0-Model-Filter"] == "hal0"
    assert call["headers"]["X-hal0-Agent"] == "hermes"


def test_fetch_models_agent_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_AGENT_ID", "hermes-nightly")
    fake = _FakeHttpClient([_FakeResponse(_models_body(["m1"]))])
    Hal0ProviderProfile(http_client=fake).fetch_models()
    assert fake.calls[0]["headers"]["X-hal0-Agent"] == "hermes-nightly"


def test_fetch_models_includes_aliases_when_requested() -> None:
    fake = _FakeHttpClient([_FakeResponse(_models_body(["real", "primary"]))])
    p = Hal0ProviderProfile(http_client=fake, include_aliases=True)
    assert p.fetch_models() == ["real", "primary"]


def test_fetch_models_transport_error_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        def get(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("connection refused")

    p = Hal0ProviderProfile(http_client=_Boom())
    assert p.fetch_models() is None


def test_fetch_models_http_error_status_falls_back() -> None:
    fake = _FakeHttpClient([_FakeResponse({"error": "nope"}, status_code=503)])
    assert Hal0ProviderProfile(http_client=fake).fetch_models() is None


def test_fetch_models_bad_json_falls_back() -> None:
    fake = _FakeHttpClient([_FakeResponse(ValueError("bad json"))])
    assert Hal0ProviderProfile(http_client=fake).fetch_models() is None


def test_fetch_models_role_alias_hot_swap() -> None:
    """Restart-free hot-swap: re-reading inventory reflects a role retarget with
    no re-instantiation of the profile and no cache to invalidate."""
    fake = _FakeHttpClient(
        [
            _FakeResponse(_models_body(["qwen3-30b", "hal0/agent"])),
            _FakeResponse(_models_body(["llama3-70b", "hal0/agent"])),
        ]
    )
    p = Hal0ProviderProfile(http_client=fake)
    first = p.fetch_models()
    second = p.fetch_models()
    assert first == ["qwen3-30b", "hal0/agent"]
    assert second == ["llama3-70b", "hal0/agent"]
    # The stable role alias survives the swap; only the concrete model changed.
    assert "hal0/agent" in first and "hal0/agent" in second
    assert p.default_aux_model == "hal0/agent"


def test_is_alias_matches_hal0_core_semantics() -> None:
    assert is_alias("primary") is True
    assert is_alias("haloai:foo") is True
    assert is_alias("qwen3-30b") is False


# ── message passthrough (tool-calling / reasoning / vision) ────────────────


def test_prepare_messages_passes_tool_calls_through_verbatim() -> None:
    messages = [
        {"role": "user", "content": "weather?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"SF"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
    ]
    assert PROFILE.prepare_messages(messages) == messages


def test_prepare_messages_passes_vision_content_through() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            ],
        }
    ]
    out = PROFILE.prepare_messages(messages)
    assert out == messages
    assert out[0]["content"][1]["type"] == "image_url"


def test_profile_advertises_vision_capability() -> None:
    assert PROFILE.supports_vision is True
    assert PROFILE.supports_vision_tool_messages is True


# ── SSE streaming / backfill / gap handling ────────────────────────────────


def _sse(chunk: dict[str, Any]) -> str:
    return f"data: {json.dumps(chunk)}"


def _delta(**delta: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta}]}


def test_iter_sse_data_skips_heartbeats_blanks_and_done() -> None:
    lines = [
        ": keep-alive",  # heartbeat comment (gap)
        "",  # blank keep-alive (gap)
        "event: message",  # non-data field ignored
        'data: {"a": 1}',
        b'data: {"b": 2}',  # bytes line
        "data: [DONE]",
        'data: {"never": true}',  # after DONE — must not appear
    ]
    assert list(iter_sse_data(lines)) == ['{"a": 1}', '{"b": 2}']


def test_parse_sse_chunks_drops_malformed_chunks() -> None:
    lines = [
        _sse(_delta(content="hi")),
        "data: {not valid json",  # gap: garbled chunk tolerated
        _sse(_delta(content="!")),
    ]
    chunks = list(parse_sse_chunks(lines))
    assert [c["choices"][0]["delta"]["content"] for c in chunks] == ["hi", "!"]


def test_assemble_stream_backfills_content_and_role() -> None:
    lines = [
        _sse(_delta(role="assistant", content="Hel")),
        _sse(_delta(content="lo, ")),
        _sse(_delta(content="world")),
        _sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    result = assemble_stream(parse_sse_chunks(lines))
    assert result["message"]["role"] == "assistant"
    assert result["message"]["content"] == "Hello, world"
    assert result["finish_reason"] == "stop"


def test_assemble_stream_reassembles_tool_calls_across_gaps() -> None:
    # Streamed tool call: name in one delta, arguments across two more; the
    # index appears non-contiguously and repeats — all folded by index.
    lines = [
        _sse(
            _delta(
                tool_calls=[
                    {"index": 0, "id": "c1", "type": "function", "function": {"name": "search"}}
                ]
            )
        ),
        _sse(_delta(tool_calls=[{"index": 0, "function": {"arguments": '{"q":'}}])),
        _sse(_delta(tool_calls=[{"index": 0, "function": {"arguments": '"cats"}'}}])),
        _sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
    ]
    result = assemble_stream(parse_sse_chunks(lines))
    tcs = result["message"]["tool_calls"]
    assert len(tcs) == 1
    assert tcs[0]["id"] == "c1"
    assert tcs[0]["function"]["name"] == "search"
    assert json.loads(tcs[0]["function"]["arguments"]) == {"q": "cats"}
    assert result["finish_reason"] == "tool_calls"


def test_assemble_stream_orders_sparse_tool_call_indices() -> None:
    # Two tool calls whose indices arrive out of order and non-contiguously.
    lines = [
        _sse(_delta(tool_calls=[{"index": 2, "id": "b", "function": {"name": "beta"}}])),
        _sse(_delta(tool_calls=[{"index": 0, "id": "a", "function": {"name": "alpha"}}])),
    ]
    result = assemble_stream(parse_sse_chunks(lines))
    names = [tc["function"]["name"] for tc in result["message"]["tool_calls"]]
    assert names == ["alpha", "beta"]  # emitted in index order despite the gap


def test_assemble_stream_preserves_reasoning_content() -> None:
    lines = [
        _sse(_delta(role="assistant", reasoning_content="Let me think. ")),
        _sse(_delta(reasoning_content="Step 2.")),
        _sse(_delta(content="The answer is 42.")),
    ]
    result = assemble_stream(parse_sse_chunks(lines))
    assert result["message"]["reasoning_content"] == "Let me think. Step 2."
    assert result["message"]["content"] == "The answer is 42."


# ── registration seam ──────────────────────────────────────────────────────


def test_register_uses_ctx_seam_when_present() -> None:
    class _Ctx:
        def __init__(self) -> None:
            self.registered: list[object] = []

        def register_provider_profile(self, profile: object) -> None:
            self.registered.append(profile)

    ctx = _Ctx()
    register(ctx)
    assert ctx.registered == [PROFILE]


def test_register_falls_back_to_module_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []
    # In hal0's venv the real seam is None; simulate the Hermes-venv seam.
    monkeypatch.setattr(
        "hal0.agents.hermes.plugins.provider_hal0.register_provider",
        lambda profile: captured.append(profile),
        raising=False,
    )
    register(ctx=None)
    assert captured == [PROFILE]


def test_register_is_a_noop_without_any_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    # No ctx and no module seam (the real hal0-venv state) → silent no-op.
    monkeypatch.setattr(
        "hal0.agents.hermes.plugins.provider_hal0.register_provider", None, raising=False
    )
    register(ctx=None)  # must not raise


def test_profile_round_trips_through_frozen_registration_seam() -> None:
    # Prove the profile satisfies the FROZEN module-level seam contract:
    # register under its name, then resolve it back.
    register_provider(PROFILE)  # PROFILE is a ProviderProfile subclass instance
    resolved = get_provider_profile("hal0")
    assert resolved is PROFILE
    assert resolved.api_mode == "chat_completions"


def test_frozen_provider_profile_shape_matches_our_fields() -> None:
    # Guardrail: our subclass only sets fields the frozen dataclass declares.
    frozen = FrozenProviderProfile(name="hal0")
    for fieldname in (
        "api_mode",
        "base_url",
        "models_url",
        "supports_vision",
        "default_aux_model",
        "auth_type",
    ):
        assert hasattr(frozen, fieldname)


# ── import-fallback (no Hermes venv) ───────────────────────────────────────


def test_import_fallback_vendored_providerprofile_active() -> None:
    # In hal0's venv the real ``providers.base`` is absent, so the module used
    # its vendored frozen ProviderProfile — the parent class name is stable and
    # the module imported without a Hermes dependency.
    assert profile_mod.ProviderProfile.__mro__[0].__name__ == "ProviderProfile"
    # The module-level Hermes seam is absent here (import-fallback to None).
    import hal0.agents.hermes.plugins.provider_hal0 as pkg

    assert pkg.register_provider is None
