"""#2741 — a ``hal0/<slot>`` request with that slot cold loads THAT slot.

Before the fix, ``resolve_chain`` counted a chain entry only when its model was
already loaded, so a cold-but-model-bound ``utility`` fell through to the
``agent`` anchor: the body was rewritten to the agent's model id and
``_ensure_backend_for_model`` cold-loaded ``agent``. Hindsight's extraction LLM
is ``hal0/utility``, so every retain call woke the biggest model on the box.

These route tests drive ``POST /v1/chat/completions`` with nothing loaded and
assert the backend-aware load targets the named slot and never ``agent``.
"""

from __future__ import annotations

from typing import Any

import pytest

import hal0.api as hal0_api
from hal0.slots.state import SlotState


class _ColdSlotManager:
    """Every slot is offline; ``load`` calls are recorded."""

    def __init__(self) -> None:
        self.loaded: list[str] = []

    async def load(self, slot_name: str, model_id: str | None = None) -> None:
        self.loaded.append(slot_name)

    def state(self, slot_name: str) -> SlotState:
        return SlotState.OFFLINE

    async def iter_configs(self) -> list[dict[str, Any]]:
        return []


def _run(
    monkeypatch: pytest.MonkeyPatch,
    slots: dict[str, str],
    model: str,
) -> tuple[Any, _ColdSlotManager]:
    from fastapi.testclient import TestClient

    from hal0.api import create_app

    async def _alias_map(_sm: Any) -> dict[str, str]:
        return dict(slots)

    async def _views(_sm: Any, _registry: Any = None) -> list[dict[str, Any]]:
        return [
            {"name": n, "device": "gpu-vulkan", "model_id": m, "context_length": 8192}
            for n, m in slots.items()
        ]

    monkeypatch.setattr(hal0_api, "hal0_chat_slot_alias_map", _alias_map)
    monkeypatch.setattr(hal0_api, "hal0_llm_slot_views", _views)

    sm = _ColdSlotManager()
    app = create_app()
    with TestClient(app) as client:
        app.state.slot_manager = sm
        r = client.post(
            "/v1/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        )
    return r, sm


def test_cold_utility_loads_utility_never_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    _r, sm = _run(
        monkeypatch,
        {"agent": "qwen3.8-27b-ud-q4-k-xl", "utility": "gemma4-v2-q4-k-m"},
        "hal0/utility",
    )
    assert sm.loaded == ["utility"]
    assert "agent" not in sm.loaded


def test_cold_utility_sharing_agent_model_still_loads_utility(
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    """Two slots bind one model id: the lane pin (now set for a cold match too)
    makes the backend-aware load pick ``utility``, not the first-declared
    ``agent``."""
    _r, sm = _run(
        monkeypatch,
        {"agent": "shared-model", "utility": "shared-model"},
        "hal0/utility",
    )
    assert sm.loaded == ["utility"]


def test_cold_operator_slot_loads_that_slot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    """Generalized chain: ``hal0/<operator-slot>`` cold -> that slot loads."""
    _r, sm = _run(
        monkeypatch,
        {"agent": "big", "extract": "small"},
        "hal0/extract",
    )
    assert sm.loaded == ["extract"]


class _RecordingDispatcher:
    """Stands in for the dispatcher: records the routed model and answers 200."""

    def __init__(self) -> None:
        self.models: list[str] = []

    async def dispatch(self, request: Any, body: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        self.models.append(body.get("model"))
        return SimpleNamespace(upstream_name="flm", resolved_model=body.get("model"))

    async def forward(self, call: Any) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse({"choices": []})


@pytest.mark.parametrize("npu_slot", ["npu", "flm"])
def test_cold_flm_npu_slot_loads_for_hal0_npu(
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
    npu_slot: str,
) -> None:
    """A cold npu-device slot bound to a ``-FLM`` id loads for ``hal0/npu``.

    The slot views carry the native colon tag (as ``hal0_llm_slot_views`` does),
    so the body is rewritten to ``gemma4-it:e2b`` while the alias map keeps the
    configured ``gemma4-it-e2b-FLM``. The backend-aware load must still find the
    slot (by its own name, or the trio name ``flm``), and the request must route
    with the served tag.
    """
    from fastapi.testclient import TestClient

    import hal0.providers.flm as flm
    from hal0.api import create_app

    slots = {"agent": "big", "utility": "small", npu_slot: "gemma4-it-e2b-FLM"}

    async def _alias_map(_sm: Any) -> dict[str, str]:
        return dict(slots)

    async def _views(_sm: Any, _registry: Any = None) -> list[dict[str, Any]]:
        return [
            {"name": "agent", "device": "gpu-vulkan", "model_id": "big", "context_length": 8192},
            {
                "name": "utility",
                "device": "gpu-vulkan",
                "model_id": "small",
                "context_length": 8192,
            },
            {
                "name": npu_slot,
                "device": "npu",
                "model_id": "gemma4-it:e2b",
                "context_length": 8192,
            },
        ]

    async def _to_tag(model_id: str) -> str | None:
        return "gemma4-it:e2b" if model_id == "gemma4-it-e2b-FLM" else None

    monkeypatch.setattr(hal0_api, "hal0_chat_slot_alias_map", _alias_map)
    monkeypatch.setattr(hal0_api, "hal0_llm_slot_views", _views)
    monkeypatch.setattr(flm, "flm_id_to_tag_async", _to_tag)

    sm = _ColdSlotManager()
    dispatcher = _RecordingDispatcher()
    app = create_app()
    with TestClient(app) as client:
        app.state.slot_manager = sm
        app.state.dispatcher = dispatcher
        r = client.post(
            "/v1/chat/completions",
            json={"model": "hal0/npu", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert sm.loaded == [npu_slot]
    assert r.status_code == 200
    assert dispatcher.models == ["gemma4-it:e2b"]
