"""A7: NPU modality toggles write [npu] TOML for container slots.

Contract:
  - container anchor (profile/runtime=container, device=npu, type=llm):
      update_config called with {"npu": {field: bool}}; the anchor is NEVER
      restarted (Decision 1 — pending_reload, operator drives the reload).
  - non-container (legacy) anchor (no profile/runtime, device=npu, type=llm):
      _set_flm_modality is a NO-OP — no update_config, no restart, no raise.
  - sibling preservation: one-level deep merge keeps untouched [npu] fields.
  - field mapping: "stt" → "asr", "embed" → "embed".
  - caller flow: _apply_npu_trio_modality returns pending_reload=True on the
    container path so the dashboard reload affordance fires.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.config import CapabilitySelection
from hal0.capabilities.orchestrator import CapabilityOrchestrator


@pytest.fixture(autouse=True)
def _no_spawn_context_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)


# ── Fakes ────────────────────────────────────────────────────────────────────


class _StubSlot:
    def __init__(self, state: str = "ready") -> None:
        class _S:
            value = state

        self.state = _S()


class FakeSlotManager:
    """Records SlotManager calls; returns stub slots."""

    def __init__(self, configs: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._configs: list[dict[str, Any]] = list(configs or [])

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


def _container_anchor(name: str = "npu") -> dict[str, Any]:
    """Return a slot config dict that is_container_npu_cfg considers container."""
    return {
        "name": name,
        "type": "llm",
        "device": "npu",
        "profile": "flm-npu",
        "enabled": True,
    }


def _legacy_anchor(name: str = "npu") -> dict[str, Any]:
    """Return a slot config dict that is_container_npu_cfg considers NOT container."""
    return {
        "name": name,
        "type": "llm",
        "device": "npu",
        "enabled": True,
        # no profile, no runtime="container"
    }


def _make_orch(
    configs: list[dict[str, Any]],
    tmp_path: Path,
) -> tuple[CapabilityOrchestrator, FakeSlotManager]:
    """Build orchestrator + matching FakeSlotManager with minimal capabilities.toml."""
    caps_path = tmp_path / "capabilities.toml"
    caps_path.write_text("", encoding="utf-8")
    fake = FakeSlotManager(configs)
    orch = CapabilityOrchestrator(
        slot_manager=fake,  # type: ignore[arg-type]
        config_path=caps_path,
    )
    return orch, fake


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_set_flm_modality_writes_npu_table_for_container_slot(
    tmp_path: Path,
) -> None:
    """Container anchor: update_config with {"npu": {"asr": True}}; NO restart."""
    orch, fake = _make_orch([_container_anchor("npu")], tmp_path)

    await orch._set_flm_modality("stt", enable=True)

    # update_config called with {"npu": {"asr": True}}
    uc_calls = [c for c in fake.calls if c[0] == "update_config"]
    assert len(uc_calls) == 1, f"expected 1 update_config, got: {fake.calls}"
    assert uc_calls[0][1] == "npu"
    assert uc_calls[0][2]["updates"] == {"npu": {"asr": True}}

    # Decision 1: the anchor is NEVER restarted — change is pending_reload.
    restart_calls = [c for c in fake.calls if c[0] == "restart"]
    assert not restart_calls, f"anchor must never be auto-restarted: {fake.calls}"


@pytest.mark.anyio
async def test_set_flm_modality_embed_field_mapping(tmp_path: Path) -> None:
    """child 'embed' maps to npu field 'embed'; disable → False."""
    orch, fake = _make_orch([_container_anchor("npu")], tmp_path)

    await orch._set_flm_modality("embed", enable=False)

    uc_calls = [c for c in fake.calls if c[0] == "update_config"]
    assert len(uc_calls) == 1
    assert uc_calls[0][2]["updates"] == {"npu": {"embed": False}}

    # Decision 1: never auto-restart.
    restart_calls = [c for c in fake.calls if c[0] == "restart"]
    assert not restart_calls


@pytest.mark.anyio
async def test_set_flm_modality_non_container_anchor_is_noop(
    tmp_path: Path,
) -> None:
    """Non-container (legacy) anchor: _set_flm_modality is a NO-OP and does not raise."""
    orch, fake = _make_orch([_legacy_anchor("npu")], tmp_path)

    await orch._set_flm_modality("stt", enable=True)

    # no TOML write via update_config
    uc_calls = [c for c in fake.calls if c[0] == "update_config"]
    assert not uc_calls, (
        f"update_config must not be called for a non-container anchor: {fake.calls}"
    )

    # no restart
    restart_calls = [c for c in fake.calls if c[0] == "restart"]
    assert not restart_calls


@pytest.mark.anyio
async def test_sibling_toggle_preserved(tmp_path: Path) -> None:
    """Writing {"npu": {"asr": True}} must not overwrite sibling "embed" key.

    The one-level deep merge in SlotManager.update_config preserves siblings,
    so the payload we pass must be the partial dict {"npu": {"asr": True}}
    rather than a wholesale replacement.  Assert the payload carries only the
    target field (sibling preservation is then guaranteed by manager merge).
    """
    orch, fake = _make_orch([_container_anchor("npu")], tmp_path)

    await orch._set_flm_modality("stt", enable=True)

    uc_calls = [c for c in fake.calls if c[0] == "update_config"]
    assert len(uc_calls) == 1
    npu_payload = uc_calls[0][2]["updates"].get("npu", {})
    # Only the target field is in the payload — manager merge handles the rest.
    assert set(npu_payload.keys()) == {"asr"}, (
        f"payload must carry only 'asr', not wholesale npu table: {npu_payload}"
    )
    assert npu_payload["asr"] is True


@pytest.mark.anyio
async def test_container_path_propagates_pending_reload(tmp_hal0_home: str, tmp_path: Path) -> None:
    """Caller flow: _apply_npu_trio_modality returns pending_reload=True on the
    container path (and never restarts the anchor), so apply() surfaces the
    dashboard reload affordance."""
    orch, fake = _make_orch([_container_anchor("npu")], tmp_path)

    selection = CapabilitySelection(
        device="npu",
        provider="flm",
        model="nomic-embed-text-v1.5-q8_0",
        enabled=True,
    )
    pending_reload = await orch._apply_npu_trio_modality("embed", "embed", selection)

    # pending_reload flows back to apply() → dashboard reload affordance.
    assert pending_reload is True

    # The [npu] toggle was written on the container anchor.
    npu_writes = [
        c
        for c in fake.calls
        if c[0] == "update_config" and c[1] == "npu" and "npu" in c[2]["updates"]
    ]
    assert npu_writes, f"no [npu] TOML write on the anchor: {fake.calls}"
    assert npu_writes[-1][2]["updates"]["npu"] == {"embed": True}

    # Decision 1: anchor never bounced; no standalone lifecycle on the slot.
    assert not [c for c in fake.calls if c[0] in ("restart", "load", "swap", "unload")], fake.calls
