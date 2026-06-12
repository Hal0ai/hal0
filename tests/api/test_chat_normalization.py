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


def _make_request(*, cfgs=None, loaded=None, upstreams=None, upstream_models=None):
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
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), _body=b"")


_PRIMARY = [
    {
        "name": "primary",
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
    out = await v1._normalize_chat_body(req, {"model": "hal0/primary", "messages": []})
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
    out = await v1._normalize_chat_body(req, {"model": "hal0/primary", "enable_thinking": True})
    # #487: top-level enable_thinking is translated to the chat-template lever
    # (a bare /no_think marker is ineffective on abliterated Qwen3), not passed through.
    assert out["chat_template_kwargs"]["enable_thinking"] is True
    assert "enable_thinking" not in out


_PRIMARY_THINKING = [
    {
        "name": "primary",
        "type": "llm",
        "enabled": True,
        "device": "gpu-rocm",
        "role": None,
        "enable_thinking": True,
        "model": {"default": "big", "context_size": 4096},
    }
]


@pytest.mark.asyncio
async def test_per_slot_enable_thinking_default_applied():
    # Slot configured enable_thinking=true → requests to its model default to ON.
    req = _make_request(cfgs=_PRIMARY_THINKING, loaded={"big"})
    out = await v1._normalize_chat_body(req, {"model": "big", "messages": []})
    assert out["chat_template_kwargs"]["enable_thinking"] is True


@pytest.mark.asyncio
async def test_per_slot_default_overridden_by_request():
    req = _make_request(cfgs=_PRIMARY_THINKING, loaded={"big"})
    out = await v1._normalize_chat_body(req, {"model": "big", "enable_thinking": False})
    assert out["chat_template_kwargs"]["enable_thinking"] is False


@pytest.mark.asyncio
async def test_request_body_rewritten_for_downstream_consumers():
    import json

    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    await v1._normalize_chat_body(req, {"model": "hal0/primary", "messages": []})
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
    # alias-map internals: hal0/primary -> "big" (loaded on gpu-vulkan).
    async def _fake_views(request):
        return [
            SlotView(
                name="primary",
                role=None,
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

    raw = json.dumps({"model": "hal0/primary", "omni": True, "messages": []}).encode("utf-8")
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
        req, {"model": "hal0/primary", "chat_template_kwargs": {"enable_thinking": True}}
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
    """The loaded set derives from container-backed remotes (kind='remote' +
    slot_name): those upstreams advertise their served model only while up,
    so their cached catalog IS the loaded set (cutover #662). Genuine
    external remotes (slot_name=None) are not local slots and never count."""
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
