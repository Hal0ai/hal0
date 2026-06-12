"""#733: capability status for npu-device selections served by the FLM trio.

The embed/stt shadow slots never run a process of their own — the npu
anchor's FLM child serves them coresident. ``get_state`` used to read each
selection's own slot status, so trio-served selections always reported
``offline`` even while the modality answered live traffic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.orchestrator import CapabilityOrchestrator


@pytest.fixture(autouse=True)
def _no_spawn_context_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _stub_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep get_state hermetic — no hardware probe, no registry scan."""
    import hal0.capabilities.catalog as _cat

    monkeypatch.setattr(_cat, "available_backends", lambda: [])
    monkeypatch.setattr(_cat, "catalogs_by_slot", lambda registry=None: {})


class _StubSlot:
    def __init__(self, state: str) -> None:
        class _S:
            value = state

        self.state = _S()


class StatusMapSlotManager:
    """status() answers from a per-slot map; unknown slots are offline."""

    def __init__(
        self,
        configs: list[dict[str, Any]] | None = None,
        status_map: dict[str, str] | None = None,
    ) -> None:
        self._configs = list(configs or [])
        self._status_map = dict(status_map or {})

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self._configs)

    async def status(self, slot_name: str) -> _StubSlot:
        return _StubSlot(self._status_map.get(slot_name, "offline"))


NPU_SELECTIONS_TOML = """
[selections.embed.embed]
device = "npu"
model = "embed-gemma:300m"
enabled = true

[selections.voice.stt]
device = "npu"
model = "whisper-v3:turbo"
enabled = true
"""


def _anchor_cfg(**over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "name": "npu",
        "type": "llm",
        "device": "npu",
        "profile": "flm-npu",
        "enabled": True,
    }
    cfg.update(over)
    return cfg


def _orch(
    tmp_path: Path,
    caps_toml: str,
    configs: list[dict[str, Any]],
    status_map: dict[str, str],
) -> CapabilityOrchestrator:
    caps_path = tmp_path / "capabilities.toml"
    caps_path.write_text(caps_toml, encoding="utf-8")
    return CapabilityOrchestrator(
        slot_manager=StatusMapSlotManager(configs, status_map),  # type: ignore[arg-type]
        config_path=caps_path,
    )


async def test_npu_selection_inherits_anchor_status(tmp_path: Path) -> None:
    orch = _orch(tmp_path, NPU_SELECTIONS_TOML, [_anchor_cfg()], {"npu": "ready"})
    state = await orch.get_state()
    assert state["selections"]["embed"]["embed"]["status"] == "ready"
    assert state["selections"]["voice"]["stt"]["status"] == "ready"


async def test_npu_selection_without_anchor_stays_offline(tmp_path: Path) -> None:
    orch = _orch(tmp_path, NPU_SELECTIONS_TOML, [], {})
    state = await orch.get_state()
    assert state["selections"]["embed"]["embed"]["status"] == "offline"


async def test_offline_anchor_propagates_offline(tmp_path: Path) -> None:
    orch = _orch(tmp_path, NPU_SELECTIONS_TOML, [_anchor_cfg()], {"npu": "offline"})
    state = await orch.get_state()
    assert state["selections"]["embed"]["embed"]["status"] == "offline"


async def test_non_npu_selection_keeps_own_status(tmp_path: Path) -> None:
    caps = """
[selections.embed.embed]
device = "gpu-vulkan"
model = "nomic-embed-text-v1.5"
enabled = true
"""
    orch = _orch(tmp_path, caps, [_anchor_cfg()], {"npu": "ready"})
    state = await orch.get_state()
    assert state["selections"]["embed"]["embed"]["status"] == "offline"


async def test_disabled_npu_selection_not_inherited(tmp_path: Path) -> None:
    caps = """
[selections.embed.embed]
device = "npu"
model = "embed-gemma:300m"
enabled = false
"""
    orch = _orch(tmp_path, caps, [_anchor_cfg()], {"npu": "ready"})
    state = await orch.get_state()
    assert state["selections"]["embed"]["embed"]["status"] == "offline"
