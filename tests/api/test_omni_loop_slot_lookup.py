"""``_maybe_run_omni_loop`` slot lookup (#2779).

An FLM slot's configured id is the ``-FLM`` catalog id while the request body
carries the native colon tag by the time the omni branch runs; the lookup must
compare through the same translation and read every config shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hal0.api.routes import v1


class _SlotManager:
    def __init__(self, configs: list[dict[str, Any]]) -> None:
        self._configs = configs

    async def iter_configs(self) -> list[dict[str, Any]]:
        return self._configs


class _Omni:
    def __init__(self) -> None:
        self.caller: str | None = None

    async def run_loop(self, *, caller_slot_name: str, body: dict[str, Any]) -> dict[str, Any]:
        self.caller = caller_slot_name
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


def _request(configs: list[dict[str, Any]], omni: _Omni) -> Any:
    state = SimpleNamespace(slot_manager=_SlotManager(configs), omni_router=omni)
    return SimpleNamespace(app=SimpleNamespace(state=state), headers={}, state=SimpleNamespace())


@pytest.fixture(autouse=True)
def _patch_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_tag(mid: str) -> str:
        return "gemma4-it:e2b" if mid == "gemma4-it-e2b-FLM" else mid

    monkeypatch.setattr("hal0.providers.flm.flm_id_to_tag_async", fake_tag)
    monkeypatch.setattr(
        v1, "preferred_slot", lambda _sm, cands, lane_slot=None: cands[0] if cands else None
    )
    monkeypatch.setattr(v1, "lane_slot_pin", lambda _r: None)


@pytest.mark.parametrize(
    "model_cfg",
    [
        {"model": {"default": "gemma4-it-e2b-FLM"}},
        {"model_default": "gemma4-it-e2b-FLM"},
        {"model_id": "gemma4-it-e2b-FLM"},
        {"defaults": {"model": "gemma4-it-e2b-FLM"}},
    ],
)
@pytest.mark.parametrize("requested", ["gemma4-it:e2b", "gemma4-it-e2b-FLM"])
async def test_flm_slot_enters_omni_loop(model_cfg: dict[str, Any], requested: str) -> None:
    omni = _Omni()
    req = _request([{"name": "npu", "type": "llm", **model_cfg}], omni)
    resp = await v1._maybe_run_omni_loop(req, {"model": requested, "omni": True, "messages": []})
    assert resp is not None
    assert omni.caller == "npu"
