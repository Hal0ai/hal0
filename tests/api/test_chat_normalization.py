from types import SimpleNamespace

import pytest

from hal0.api.routes import v1


class _SlotManager:
    def __init__(self, cfgs):
        self._cfgs = cfgs

    async def iter_configs(self):
        return self._cfgs


class _Upstreams:
    def __init__(self, ups):
        self._ups = ups

    def list(self):
        return self._ups


def _make_request(*, cfgs=None, loaded=None, upstreams=None, upstream_models=None, registry=None):
    """Build a minimal request stand-in.

    ``loaded`` materialises as a container-backed remote upstream
    (kind="remote" + slot_name) whose cached catalog carries the given
    model ids — that is how `_normalize_loaded_models` derives the loaded
    set post-container-cutover (#662): there is no separate health probe.
    """
    ups = list(upstreams or [])
    models = dict(upstream_models or {})
    if loaded:
        ups.append(SimpleNamespace(name="_container", kind="remote", slot_name="_container"))
        models["_container"] = sorted(loaded)
    state = SimpleNamespace(
        slot_manager=_SlotManager(cfgs or []),
        upstreams=_Upstreams(ups),
        upstream_models=models,
        model_registry=registry,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), _body=b"")


_PRIMARY = [
    {
        # ADR-0023: the canonical default/anchor slot is `agent` (was `chat`).
        "name": "agent",
        "type": "llm",
        "enabled": True,
        "device": "gpu-vulkan",
        "role": None,
        "model": {"default": "big", "context_size": 4096},
    }
]


@pytest.mark.asyncio
async def test_virtual_name_resolved_and_thinking_injected():
    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    out = await v1._normalize_chat_body(req, {"model": "hal0/agent", "messages": []})
    assert out["model"] == "big"
    assert out["chat_template_kwargs"]["enable_thinking"] is False


@pytest.mark.asyncio
async def test_physical_model_passthrough_still_gets_thinking():
    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    out = await v1._normalize_chat_body(req, {"model": "big", "messages": []})
    assert out["model"] == "big"  # non-virtual -> not rewritten
    assert out["chat_template_kwargs"]["enable_thinking"] is False


@pytest.mark.asyncio
async def test_remote_model_not_thinking_injected():
    req = _make_request(
        upstreams=[SimpleNamespace(name="or", kind="remote")],
        upstream_models={"or": ["gpt-x"]},
    )
    out = await v1._normalize_chat_body(req, {"model": "gpt-x", "messages": []})
    assert "enable_thinking" not in out


@pytest.mark.asyncio
async def test_caller_top_level_thinking_translated_to_kwarg():
    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    out = await v1._normalize_chat_body(req, {"model": "hal0/agent", "enable_thinking": True})
    # #487: top-level enable_thinking is translated to the chat-template lever
    # (a bare /no_think marker is ineffective on abliterated Qwen3), not passed through.
    assert out["chat_template_kwargs"]["enable_thinking"] is True
    assert "enable_thinking" not in out


class _ThinkingRegistry:
    """Registry stub: model 'big' carries defaults.enable_thinking=True
    (spec-hw-slot-ownership §1 — reasoning is MODEL-owned tuning; the former
    per-slot enable_thinking override is gone from SlotConfig)."""

    def get(self, model_id):
        if model_id != "big":
            raise KeyError(model_id)
        return SimpleNamespace(defaults=SimpleNamespace(enable_thinking=True))


@pytest.mark.asyncio
async def test_model_enable_thinking_default_applied():
    # Model defaults enable_thinking=true → requests to it default to ON.
    req = _make_request(cfgs=_PRIMARY, loaded={"big"}, registry=_ThinkingRegistry())
    out = await v1._normalize_chat_body(req, {"model": "big", "messages": []})
    assert out["chat_template_kwargs"]["enable_thinking"] is True


@pytest.mark.asyncio
async def test_model_default_overridden_by_request():
    req = _make_request(cfgs=_PRIMARY, loaded={"big"}, registry=_ThinkingRegistry())
    out = await v1._normalize_chat_body(req, {"model": "big", "enable_thinking": False})
    assert out["chat_template_kwargs"]["enable_thinking"] is False


@pytest.mark.asyncio
async def test_request_body_rewritten_for_downstream_consumers():
    import json

    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    await v1._normalize_chat_body(req, {"model": "hal0/agent", "messages": []})
    # any downstream consumer re-reading request.body() must observe the
    # normalized body, so request._body carries the rewrite
    assert json.loads(req._body)["model"] == "big"
    assert json.loads(req._body)["chat_template_kwargs"]["enable_thinking"] is False


class _Headers:
    def get(self, key, default=None):
        # No content-type → _read_json_body takes the JSON path.
        return default


class _OmniRequest:
    """Request that flows through chat_completions -> _read_json_body -> omni branch."""

    def __init__(self, raw: bytes):
        self._raw = raw
        self.headers = _Headers()
        # "big" is loaded via a container-backed remote's cached catalog.
        state = SimpleNamespace(
            slot_manager=_SlotManager(_PRIMARY),
            upstreams=_Upstreams(
                [SimpleNamespace(name="_container", kind="remote", slot_name="_container")]
            ),
            upstream_models={"_container": ["big"]},
        )
        self.app = SimpleNamespace(state=state)

    async def body(self):
        return self._raw


@pytest.mark.asyncio
async def test_omni_path_receives_normalized_body(monkeypatch):
    """The omni branch returns before _dispatch_and_forward, so chat_completions
    must normalize BEFORE the omni gate. Prove the body handed to the OmniRouter
    has the virtual name resolved + thinking injected."""
    import json

    from starlette.responses import JSONResponse

    from hal0.normalize.resolver import SlotView

    # Pin the resolver inputs so resolution is deterministic regardless of
    # alias-map internals: hal0/agent -> "big" (loaded on gpu-vulkan).
    async def _fake_views(request):
        return [
            SlotView(
                name="agent",
                device="gpu-vulkan",
                model_id="big",
                context_length=4096,
            )
        ]

    monkeypatch.setattr(v1, "_normalize_slot_views", _fake_views)
    monkeypatch.setattr(v1, "_normalize_loaded_models", lambda request: {"big"})

    seen = {}

    async def _fake_omni(request, body):
        seen["body"] = body
        return JSONResponse({"ok": True})

    monkeypatch.setattr(v1, "_maybe_run_omni_loop", _fake_omni)

    raw = json.dumps({"model": "hal0/agent", "omni": True, "messages": []}).encode("utf-8")
    req = _OmniRequest(raw)

    resp = await v1.chat_completions(req, dispatcher=None)

    assert resp.status_code == 200
    assert seen["body"]["model"] == "big"
    assert seen["body"]["chat_template_kwargs"]["enable_thinking"] is False


# Single-gate invariant: _normalize_chat_body is called in exactly ONE place
# (chat_completions, before the omni branch) and NOT in _dispatch_and_forward.
# _dispatch_and_forward also serves the non-chat endpoints — /v1/completions,
# /v1/embeddings, /v1/rerankings, and the multipart /v1/audio/transcriptions —
# where an unconditional request._body = json(body) rewrite would corrupt the
# multipart upload or inject a meaningless enable_thinking. Normalization is a
# chat-only concern (virtual hal0/* names + thinking policy), so it must stay
# out of the shared dispatch helper.


class _NonChatRequest:
    def __init__(self):
        self.headers = _Headers()
        self.url = SimpleNamespace(path="/v1/embeddings")
        state = SimpleNamespace(
            slot_manager=None,
            last_used_model={},
            tps_events=None,
            ttft_events=None,
        )
        self.app = SimpleNamespace(state=state)

    async def body(self):
        return b""


class _FakeDispatcher:
    async def dispatch(self, request, body=None):
        return SimpleNamespace(upstream_name="embed", resolved_model="bge")

    async def forward(self, call):
        from fastapi.responses import Response as _Resp

        return _Resp(content=b"", media_type="application/json")


@pytest.mark.asyncio
async def test_chat_template_kwargs_opt_out_through_seam():
    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    out = await v1._normalize_chat_body(
        req, {"model": "hal0/agent", "chat_template_kwargs": {"enable_thinking": True}}
    )
    assert "enable_thinking" not in out
    assert out["chat_template_kwargs"] == {"enable_thinking": True}


def test_normalize_loaded_models_uses_cache_no_rpc():
    """The loaded set is read from the cached upstream catalogs only —
    no live /v1/models fetch on the request hot path."""

    class _NoFetchUpstreams:
        def list(self):
            return [SimpleNamespace(name="chat", kind="remote", slot_name="chat")]

        async def fetch_models(self, name):  # if this were called, the test would fail
            raise AssertionError("must not fetch live catalogs")

    req = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                upstreams=_NoFetchUpstreams(),
                upstream_models={"chat": ["big"]},
            )
        )
    )
    assert v1._normalize_loaded_models(req) == {"big"}


@pytest.mark.asyncio
async def test_dispatch_and_forward_does_not_normalize_non_chat(monkeypatch):
    """_dispatch_and_forward must NOT invoke _normalize_chat_body — that would
    rewrite request._body and break embeddings/rerank/multipart-audio."""
    called = {"flag": False}

    async def _spy(request, body):
        called["flag"] = True
        return body

    monkeypatch.setattr(v1, "_normalize_chat_body", _spy)

    req = _NonChatRequest()
    body = {"model": "bge", "input": "hello"}
    await v1._dispatch_and_forward(req, _FakeDispatcher(), body=body)

    assert called["flag"] is False, "_dispatch_and_forward must not normalize non-chat requests"


def test_loaded_models_includes_ready_container_slots():
    """The loaded set derives from container-backed upstreams (``slot_name``
    set): those advertise their served model only while up, so their cached
    catalog IS the loaded set (cutover #662). Genuine external remotes
    (slot_name=None) are not local slots and never count."""
    req = _make_request(
        upstreams=[
            SimpleNamespace(name="agent", kind="remote", slot_name="agent"),
            SimpleNamespace(name="or", kind="remote", slot_name=None),  # real remote
        ],
        upstream_models={"agent": ["chadrock-35b-ace-saber"], "or": ["gpt-x"]},
    )
    loaded = v1._normalize_loaded_models(req)
    assert "chadrock-35b-ace-saber" in loaded
    # A genuine external remote (no slot_name) is NOT a local container slot.
    assert "gpt-x" not in loaded


def test_loaded_models_includes_kind_slot_upstreams_o21():
    """O21 regression: SlotManager registers LOCAL container slots as
    kind="slot" (upstreams/registry guards that kind as SlotManager-owned),
    but the loaded-set collector filtered kind=="remote" only — starving the
    hal0/<slot> alias resolver of every local slot's model and 404ing all
    virtual ids on real boxes. The container-backed marker is ``slot_name``,
    regardless of kind."""
    req = _make_request(
        upstreams=[
            SimpleNamespace(name="brain", kind="slot", slot_name="brain"),
        ],
        upstream_models={"brain": ["hal0-brain-fpx8-agent"]},
    )
    loaded = v1._normalize_loaded_models(req)
    assert "hal0-brain-fpx8-agent" in loaded


@pytest.mark.asyncio
async def test_hal0_brain_alias_resolves_via_kind_slot_upstream_o21():
    """End-to-end O21 repro: [brain_chat] model="hal0/brain" with the brain
    slot loaded (registered the way SlotManager actually registers it —
    kind="slot") must rewrite to the slot's model id, not fall through to
    legacy capability routing."""
    cfgs = [
        {
            "name": "brain",
            "type": "llm",
            "enabled": True,
            "device": "gpu-rocm",
            "role": None,
            "model": {"default": "hal0-brain-fpx8-agent", "context_size": 16384},
        }
    ]
    req = _make_request(
        cfgs=cfgs,
        upstreams=[SimpleNamespace(name="brain", kind="slot", slot_name="brain")],
        upstream_models={"brain": ["hal0-brain-fpx8-agent"]},
    )
    out = await v1._normalize_chat_body(req, {"model": "hal0/brain", "messages": []})
    assert out["model"] == "hal0-brain-fpx8-agent"


def test_container_slot_not_treated_as_remote_for_thinking():
    """Container slots register as kind='remote' (with slot_name) but are LOCAL —
    the thinking policy must apply to them. Only genuine external remotes
    (slot_name=None) skip thinking injection. (cutover #662: chat reasoned by
    default because it looked remote.)"""
    req = _make_request(
        upstreams=[
            SimpleNamespace(name="chat", kind="remote", slot_name="chat"),
            SimpleNamespace(name="or", kind="remote", slot_name=None),
        ],
        upstream_models={"chat": ["qwopus3.6-27b-v2"], "or": ["gpt-x"]},
    )
    # Container-backed remote → NOT remote-for-thinking (policy should apply).
    assert v1._is_remote_model(req, "qwopus3.6-27b-v2") is False
    # Genuine external remote → remote (skip thinking injection).
    assert v1._is_remote_model(req, "gpt-x") is True


# ── system-message canonicalisation integration tests ─────────────────────
#
# These exercise the call site in `_normalize_chat_body` itself rather than
# the pure helper — they pin the contract that every body reaching the
# upstream is canonical (system at index 0, at most one system), so the
# Qwen3.6-35B-A3B Jinja template never sees a config that triggers its
# `raise_exception('System message must be at the beginning')`.


@pytest.mark.asyncio
async def test_normalize_chat_body_hoists_mid_array_system_to_position_zero():
    """The dashboard bug repro: a system message mid-array used to leave
    `_normalize_chat_body` untouched and reach llama-server, which 500'd."""
    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    body = {
        "model": "hal0/agent",
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "you are the hal0 operator"},
        ],
    }
    out = await v1._normalize_chat_body(req, body)
    msgs = out["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "you are the hal0 operator"
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "Hi"


@pytest.mark.asyncio
async def test_normalize_chat_body_collapses_stacked_systems():
    """Two system entries collapse into one leading system joined with
    blank lines; non-system turns retain their original order."""
    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    body = {
        "model": "hal0/agent",
        "messages": [
            {"role": "user", "content": "u1"},
            {"role": "system", "content": "s1"},
            {"role": "assistant", "content": "a1"},
            {"role": "system", "content": "s2"},
        ],
    }
    out = await v1._normalize_chat_body(req, body)
    msgs = out["messages"]
    sys_entries = [m for m in msgs if m["role"] == "system"]
    assert len(sys_entries) == 1
    assert sys_entries[0] == {"role": "system", "content": "s1\n\ns2"}
    # non-system order preserved
    assert msgs[1:] == [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]


@pytest.mark.asyncio
async def test_normalize_chat_body_user_only_body_is_passthrough():
    """No-system payloads must round-trip through normalise without
    rewriting the message array (the SPA's hot path)."""
    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    out = await v1._normalize_chat_body(req, {"model": "hal0/agent", "messages": list(msgs)})
    assert out["messages"] == msgs


@pytest.mark.asyncio
async def test_normalize_chat_body_caches_canonical_messages_in_request_body():
    """Once normalised, request._body must reflect the canonical array so
    downstream consumers reading the request body verbatim see the same
    shape (Dispatcher re-serialises it)."""
    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    req._body = b""  # the helper writes here; assert it's rewritten
    body = {
        "model": "hal0/agent",
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "ops"},
        ],
    }
    await v1._normalize_chat_body(req, body)
    import json as _json

    cached = _json.loads(req._body)
    assert cached["messages"][0]["role"] == "system"
    assert cached["messages"][-1]["role"] == "user"
