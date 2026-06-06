"""NPU Phase 2 — end-to-end smoke for the trio-driven embed path.

Drives ``CapabilityOrchestrator.apply()`` through the full reconcile +
trio fork against on-disk capabilities.toml / slot TOML, asserting the
whole Phase 2 contract in one pass:

  - lemond ``flm_args`` recomposed to enable the modality;
  - a ``device=npu``, ``type=embedding`` slot RECORD exists (so
    ``v1._is_npu_trio_request`` gates dispatch on);
  - ZERO ``load``/``swap``/``unload`` on the embed slot (the FLM anchor
    serves it coresident — no standalone process);
  - the anchor is never eagerly restarted; ``pending_reload`` is True;
  - the persisted capabilities.toml reflects device=npu + enabled.

The on-disk layout is the "drift" fixture: capabilities.toml says
npu/flm/enabled while the embed slot TOML still says vulkan — exactly the
mismatch Phase 1 left when the NPU section couldn't drive the trio.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.config import load_capabilities_config
from hal0.capabilities.orchestrator import CapabilityOrchestrator


@pytest.fixture(autouse=True)
def _no_spawn_context_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)


class _StubSlot:
    def __init__(self, state: str = "ready") -> None:
        class _S:
            value = state

        self.state = _S()


class FakeSlotManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._configs: list[dict[str, Any]] = []

    def set_configs(self, configs: list[dict[str, Any]]) -> None:
        self._configs = list(configs)

    async def iter_configs(self) -> list[dict[str, Any]]:
        self.calls.append(("iter_configs", "", {}))
        return list(self._configs)

    async def status(self, slot_name: str) -> _StubSlot:
        self.calls.append(("status", slot_name, {}))
        return _StubSlot("ready")

    async def load(self, slot_name: str, model_id: str | None = None) -> _StubSlot:
        self.calls.append(("load", slot_name, {"model_id": model_id}))
        return _StubSlot("ready")

    async def unload(self, slot_name: str) -> _StubSlot:
        self.calls.append(("unload", slot_name, {}))
        return _StubSlot("offline")

    async def swap(self, slot_name: str, new_model_id: str) -> _StubSlot:
        self.calls.append(("swap", slot_name, {"model_id": new_model_id}))
        return _StubSlot("ready")

    async def restart(self, slot_name: str) -> _StubSlot:
        self.calls.append(("restart", slot_name, {}))
        return _StubSlot("ready")

    async def create(self, slot_name: str, cfg: dict[str, Any]) -> _StubSlot:
        self.calls.append(("create", slot_name, {"cfg": cfg}))
        return _StubSlot("offline")

    async def update_config(self, slot_name: str, updates: dict[str, Any]) -> _StubSlot:
        self.calls.append(("update_config", slot_name, {"updates": updates}))
        return _StubSlot("ready")


class FakeLemonadeClient:
    def __init__(self, initial_flm_args: str = "") -> None:
        self._config: dict[str, Any] = {"flm_args": initial_flm_args}
        self.set_calls: list[dict[str, Any]] = []

    async def internal_config(self) -> dict[str, Any]:
        return dict(self._config)

    async def internal_set(self, values: dict[str, Any]) -> dict[str, Any]:
        self.set_calls.append(dict(values))
        self._config.update(values)
        return dict(self._config)


async def test_npu_phase2_embed_enable_end_to_end(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        CapabilityOrchestrator,
        "_validate_model_in_catalog",
        lambda self, slot, child, model_id, backend_id: None,
    )
    home = Path(tmp_hal0_home)
    slots_dir = home / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    # Drift: slot TOML still vulkan/llama-server while caps wants npu/flm.
    # NB: NO ``type`` on disk — the real production drift shape. The apply
    # must stamp ``type=embedding`` itself, else trio dispatch never gates.
    (slots_dir / "embed.toml").write_text(
        "\n".join(
            [
                'name = "embed"',
                "port = 8082",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "enabled = false",
                "[model]",
                'default = "nomic-embed-text-v1.5-q8_0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    caps_path = home / "etc" / "hal0" / "capabilities.toml"
    caps_path.write_text(
        "\n".join(
            [
                "[selections.embed.embed]",
                'backend = "npu"',
                'provider = "flm"',
                'model = "nomic-embed-text-v1.5-q8_0"',
                "enabled = false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    client = FakeLemonadeClient(initial_flm_args="--asr 1 --embed 0")
    fake = FakeSlotManager()
    fake.set_configs([{"name": "agent", "type": "llm", "device": "npu", "enabled": True}])
    orch = CapabilityOrchestrator(slot_manager=fake, lemonade_provider=lambda: client)

    result = await orch.apply(
        "embed",
        "embed",
        {
            "enabled": True,
            "backend": "npu",
            "provider": "flm",
            "model": "nomic-embed-text-v1.5-q8_0",
        },
    )

    # 1. flm_args recomposed to enable embed on the anchor.
    assert client.set_calls, "anchor flm_args was never set"
    assert client.set_calls[-1] == {"flm_args": "--asr 1 --embed 1"}, client.set_calls

    # 2. A device=npu, type=embedding slot record is in force. The slot TOML
    #    existed with NO type (drift shape), so the apply must stamp
    #    type=embedding alongside enabled — WITHOUT a nested model (Decision 4).
    enabled_writes = [
        c
        for c in fake.calls
        if c[0] == "update_config"
        and c[1] == "embed"
        and c[2]["updates"].get("enabled") is True
        and c[2]["updates"].get("type") == "embedding"
    ]
    assert enabled_writes, f"no enabled+type write on embed slot: {fake.calls}"
    # That write must NOT carry the nested model (Decision 4).
    assert "model" not in enabled_writes[-1][2]["updates"]

    # 3. ZERO standalone spawn on the embed slot.
    assert not [c for c in fake.calls if c[0] in ("load", "swap", "unload")], (
        f"NPU embed path must not bounce the modality slot: {fake.calls}"
    )

    # 4. Anchor never eagerly restarted; pending_reload surfaced.
    assert not [c for c in fake.calls if c[0] == "restart"], fake.calls
    assert result.get("pending_reload") is True

    # 5. Persisted selection reflects device=npu + enabled.
    persisted = load_capabilities_config(caps_path)
    sel = persisted.selections["embed"]["embed"]
    assert sel.device == "npu"
    assert sel.enabled is True
