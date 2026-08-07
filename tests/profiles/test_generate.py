"""Tests for hal0.profiles.generate — draft profile generation, compute-only.

No network: every HTTP call generate.py can make (HF repo fetch, HF
``?expand[]=gguf``, the ``use_llm`` self-call) is intercepted via
``httpx.MockTransport``, mirroring tests/api/test_hf_routes.py's pattern.

Targeted file run only (full suite hangs):
    ~/dev/hal0/.venv/bin/python -m pytest tests/profiles/test_generate.py -q
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.config.schema import GPUInfo, HardwareInfo
from hal0.errors import BadRequest
from hal0.profiles.generate import LlmCallContext, generate_draft_profile
from hal0.registry.model import Model, ModelDefaults
from hal0.registry.store import ModelNotFound, ModelRegistry

_EXPORTED_AT = "2026-08-07T00:00:00+00:00"


# ── hardware fixtures ────────────────────────────────────────────────────────


def _hw_rocm() -> HardwareInfo:
    return HardwareInfo(gpus=[GPUInfo(vendor="amd", compute_capable=True)])


def _hw_cpu() -> HardwareInfo:
    return HardwareInfo()


# ── httpx transport patching (mirrors tests/api/test_hf_routes.py) ─────────


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, target: str, handler) -> None:
    real_cls = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(target, factory)


def _patch_both(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """generate.py and upstreams.huggingface each build their own AsyncClient."""
    _patch_httpx(monkeypatch, "hal0.upstreams.huggingface.httpx.AsyncClient", handler)
    _patch_httpx(monkeypatch, "hal0.profiles.generate.httpx.AsyncClient", handler)


def _hf_handler(
    *,
    meta: dict[str, Any] | None = None,
    tree: list[dict[str, Any]] | None = None,
    gguf: dict[str, Any] | None = None,
    llm_content: str | None = None,
    llm_status: int = 200,
    meta_status: int = 200,
    capture: list[str] | None = None,
):
    """One handler covering fetch_repo's meta+tree calls, the gguf-expand
    call, and the use_llm self-call — routed by URL/path shape."""

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if capture is not None:
            capture.append(url)
        if req.url.path.endswith("/v1/chat/completions"):
            if llm_content is None:
                return httpx.Response(llm_status, json={"error": "no utility slot"})
            return httpx.Response(
                llm_status,
                json={"choices": [{"message": {"content": llm_content}}]},
            )
        if "expand" in url:
            return httpx.Response(200, json={"gguf": gguf} if gguf is not None else {})
        if "/tree/main" in url:
            return httpx.Response(200, json=tree or [])
        return httpx.Response(meta_status, json=meta or {})

    return handler


# ── model_id path ────────────────────────────────────────────────────────────


def test_generate_from_model_id_chat_gpu_clones_chat_seed(tmp_hal0_home: str) -> None:
    reg = ModelRegistry(registry_dir=tmp_hal0_home + "/registry")
    reg.add(
        Model(
            id="qwen3-4b-q4_k_m",
            name="Qwen3 4B",
            path="/models/qwen3-4b.gguf",
            capabilities=["chat"],
            quant="Q4_K_M",
            size_bytes=4_000_000_000,
            architecture="qwen3",
            metadata={"context_length": 32768},
        )
    )

    import asyncio

    result = asyncio.run(
        generate_draft_profile(
            model_id="qwen3-4b-q4_k_m",
            exported_at=_EXPORTED_AT,
            hw=_hw_rocm(),
            registry=reg,
        )
    )

    assert result.profile["kind"] == "hal0.profile"
    assert result.profile["name"] == "qwen3-4b-q4_k_m"
    body = result.profile["profile"]
    assert body["cloned_from"] == "chat"
    assert body.get("device_class") is None  # seed "chat" is device-agnostic; excluded when None
    assert body["quant"] == "Q4_K_M"
    assert "Qwen3 4B" in body["intent"]
    assert "registry:qwen3-4b-q4_k_m" in result.sources
    assert result.warnings == ()


def test_generate_from_model_id_not_found_raises(tmp_hal0_home: str) -> None:
    reg = ModelRegistry(registry_dir=tmp_hal0_home + "/registry")

    import asyncio

    with pytest.raises(ModelNotFound):
        asyncio.run(
            generate_draft_profile(
                model_id="does-not-exist",
                exported_at=_EXPORTED_AT,
                hw=_hw_cpu(),
                registry=reg,
            )
        )


def test_generate_moe_architecture_upgrades_to_moe_seed(tmp_hal0_home: str) -> None:
    reg = ModelRegistry(registry_dir=tmp_hal0_home + "/registry")
    reg.add(
        Model(
            id="chadrock-moe",
            path="/models/chadrock.gguf",
            capabilities=["chat"],
            architecture="qwen3next",  # in hardware.recommend._MOE_ARCHITECTURES
        )
    )

    import asyncio

    result = asyncio.run(
        generate_draft_profile(
            model_id="chadrock-moe",
            exported_at=_EXPORTED_AT,
            hw=_hw_rocm(),
            registry=reg,
        )
    )

    assert result.profile["profile"]["cloned_from"] == "moe"


def test_generate_embed_capability_clones_embedding_seed(tmp_hal0_home: str) -> None:
    reg = ModelRegistry(registry_dir=tmp_hal0_home + "/registry")
    reg.add(
        Model(
            id="bge-large",
            path="/models/bge.gguf",
            capabilities=["embed"],
        )
    )

    import asyncio

    result = asyncio.run(
        generate_draft_profile(
            model_id="bge-large",
            exported_at=_EXPORTED_AT,
            hw=_hw_rocm(),
            registry=reg,
        )
    )

    assert result.profile["profile"]["cloned_from"] == "embedding"


def test_generate_cpu_only_host_clones_cpu_chat_seed(tmp_hal0_home: str) -> None:
    reg = ModelRegistry(registry_dir=tmp_hal0_home + "/registry")
    reg.add(Model(id="llama32-3b", path="/models/llama32.gguf", capabilities=["chat"]))

    import asyncio

    result = asyncio.run(
        generate_draft_profile(
            model_id="llama32-3b",
            exported_at=_EXPORTED_AT,
            hw=_hw_cpu(),
            registry=reg,
        )
    )

    assert result.profile["profile"]["cloned_from"] == "cpu-chat"


def test_generate_carries_context_size_default_when_no_metadata_context(
    tmp_hal0_home: str,
) -> None:
    reg = ModelRegistry(registry_dir=tmp_hal0_home + "/registry")
    reg.add(
        Model(
            id="ctx-fallback",
            path="/models/x.gguf",
            capabilities=["chat"],
            defaults=ModelDefaults(context_size=16384),
        )
    )

    import asyncio

    result = asyncio.run(
        generate_draft_profile(
            model_id="ctx-fallback",
            exported_at=_EXPORTED_AT,
            hw=_hw_cpu(),
            registry=reg,
        )
    )

    assert "16K ctx" in result.profile["profile"]["intent"]


# ── validation ───────────────────────────────────────────────────────────────


def test_generate_requires_exactly_one_source(tmp_hal0_home: str) -> None:
    import asyncio

    with pytest.raises(BadRequest):
        asyncio.run(generate_draft_profile(exported_at=_EXPORTED_AT, hw=_hw_cpu()))

    with pytest.raises(BadRequest):
        asyncio.run(
            generate_draft_profile(
                model_id="a",
                hf_repo="org/b",
                exported_at=_EXPORTED_AT,
                hw=_hw_cpu(),
            )
        )


def test_generate_sanitizes_invalid_requested_name(tmp_hal0_home: str) -> None:
    reg = ModelRegistry(registry_dir=tmp_hal0_home + "/registry")
    reg.add(Model(id="m1", path="/models/m1.gguf", capabilities=["chat"]))

    import asyncio

    result = asyncio.run(
        generate_draft_profile(
            model_id="m1",
            name="Not A Valid Name!!",
            exported_at=_EXPORTED_AT,
            hw=_hw_cpu(),
            registry=reg,
        )
    )

    assert result.profile["name"] != "Not A Valid Name!!"
    import re

    assert re.match(r"^[a-z0-9][a-z0-9_-]{0,31}$", result.profile["name"])
    assert any("sanitized" in w for w in result.warnings)


# ── hf_repo path ─────────────────────────────────────────────────────────────


def test_generate_from_hf_repo_happy_path(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = _hf_handler(
        meta={"tags": ["text-generation", "gguf"], "cardData": {"license": "apache-2.0"}},
        tree=[
            {"path": "qwen3-8b-q4_k_m.gguf", "size": 5_000_000_000},
        ],
        gguf={"architecture": "qwen3", "context_length": 40960, "total": 8_190_735_360},
    )
    _patch_both(monkeypatch, handler)

    import asyncio

    result = asyncio.run(
        generate_draft_profile(
            hf_repo="unsloth/Qwen3-8B-GGUF",
            exported_at=_EXPORTED_AT,
            hw=_hw_rocm(),
        )
    )

    body = result.profile["profile"]
    assert body["cloned_from"] == "chat"
    assert body["quant"] == "Q4_K_M"
    assert "qwen3" in body["intent"]
    assert "40K ctx" in body["intent"]
    assert "huggingface:unsloth/Qwen3-8B-GGUF" in result.sources
    assert "huggingface:unsloth/Qwen3-8B-GGUF#gguf" in result.sources


def test_generate_from_hf_repo_bad_coordinate_raises_bad_request(tmp_hal0_home: str) -> None:
    import asyncio

    with pytest.raises(BadRequest):
        asyncio.run(
            generate_draft_profile(
                hf_repo="not-a-valid-repo",
                exported_at=_EXPORTED_AT,
                hw=_hw_cpu(),
            )
        )


def test_generate_from_hf_repo_unreachable_degrades_to_502(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.upstreams.huggingface import HFUpstreamError

    def failing_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")

    _patch_both(monkeypatch, failing_handler)

    import asyncio

    with pytest.raises(HFUpstreamError):
        asyncio.run(
            generate_draft_profile(
                hf_repo="org/does-not-matter",
                exported_at=_EXPORTED_AT,
                hw=_hw_cpu(),
            )
        )


def test_generate_from_hf_repo_no_gguf_metadata_warns_and_falls_back(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = _hf_handler(
        meta={"tags": ["text-generation"]},
        tree=[{"path": "model-q4_k_m.gguf", "size": 1_000_000}],
        gguf=None,
    )
    _patch_both(monkeypatch, handler)

    import asyncio

    result = asyncio.run(
        generate_draft_profile(
            hf_repo="org/generic-repo",
            exported_at=_EXPORTED_AT,
            hw=_hw_cpu(),
        )
    )

    assert any("did not report parsed GGUF metadata" in w for w in result.warnings)
    assert result.profile["profile"]["quant"] == "Q4_K_M"  # filename fallback


# ── use_llm path ─────────────────────────────────────────────────────────────


def test_generate_use_llm_success_overrides_intent(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = _hf_handler(
        meta={"tags": ["text-generation"], "cardData": {"description": "A great chat model."}},
        tree=[{"path": "model-q4_k_m.gguf", "size": 1_000_000}],
        gguf={"architecture": "qwen3", "context_length": 8192},
        llm_content="Great for fast local chat with tool use.",
    )
    _patch_both(monkeypatch, handler)

    import asyncio

    result = asyncio.run(
        generate_draft_profile(
            hf_repo="org/repo",
            use_llm=True,
            llm=LlmCallContext(base_url="http://127.0.0.1:8080", headers={}),
            exported_at=_EXPORTED_AT,
            hw=_hw_cpu(),
        )
    )

    assert result.profile["profile"]["intent"] == "Great for fast local chat with tool use."
    assert "llm:hal0/utility" in result.sources
    assert result.warnings == ()


def test_generate_use_llm_unavailable_degrades_to_heuristic(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = _hf_handler(
        meta={"tags": ["text-generation"]},
        tree=[{"path": "model-q4_k_m.gguf", "size": 1_000_000}],
        gguf={"architecture": "qwen3", "context_length": 8192},
        llm_status=404,
    )
    _patch_both(monkeypatch, handler)

    import asyncio

    result = asyncio.run(
        generate_draft_profile(
            hf_repo="org/repo",
            use_llm=True,
            llm=LlmCallContext(base_url="http://127.0.0.1:8080", headers={}),
            exported_at=_EXPORTED_AT,
            hw=_hw_cpu(),
        )
    )

    # Falls back to the heuristic intent (contains the repo id), never raises.
    assert "org/repo" in result.profile["profile"]["intent"]
    assert any("LLM summarization unavailable" in w for w in result.warnings)


def test_generate_use_llm_without_context_warns_and_skips_call(tmp_hal0_home: str) -> None:
    reg = ModelRegistry(registry_dir=tmp_hal0_home + "/registry")
    reg.add(Model(id="m1", path="/models/m1.gguf", capabilities=["chat"]))

    import asyncio

    result = asyncio.run(
        generate_draft_profile(
            model_id="m1",
            use_llm=True,
            llm=None,
            exported_at=_EXPORTED_AT,
            hw=_hw_cpu(),
            registry=reg,
        )
    )

    assert any("no LLM call context was provided" in w for w in result.warnings)
