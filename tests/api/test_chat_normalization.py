from types import SimpleNamespace

import pytest

from hal0.api.routes import v1


class _Health:
    def __init__(self, loaded):
        self.loaded_models = tuple(loaded)


class _Shim:
    def __init__(self, loaded):
        self._health = _Health(loaded)


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
    state = SimpleNamespace(
        slot_manager=_SlotManager(cfgs or []),
        lemonade_metrics_shim=_Shim(loaded or set()),
        upstreams=_Upstreams(upstreams or []),
        upstream_models=upstream_models or {},
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
async def test_caller_opted_thinking_preserved():
    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    out = await v1._normalize_chat_body(req, {"model": "hal0/primary", "enable_thinking": True})
    assert out["enable_thinking"] is True


@pytest.mark.asyncio
async def test_request_body_rewritten_for_proxy_fallthrough():
    import json

    req = _make_request(cfgs=_PRIMARY, loaded={"big"})
    await v1._normalize_chat_body(req, {"model": "hal0/primary", "messages": []})
    # the proxy fall-through reads request._body, so it must carry the normalized body
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
        state = SimpleNamespace(
            slot_manager=_SlotManager(_PRIMARY),
            lemonade_metrics_shim=_Shim({"big"}),
            upstreams=_Upstreams([]),
            upstream_models={},
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
    class _RaisingShim:
        def __init__(self, loaded):
            self._health = _Health(loaded)

        async def health(self):  # if this were called, the test would fail
            raise AssertionError("must not poll lemond")

    from types import SimpleNamespace

    req = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(lemonade_metrics_shim=_RaisingShim({"big"})))
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
