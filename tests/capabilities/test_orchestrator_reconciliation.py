"""Regression tests for CapabilityOrchestrator slot-toml reconciliation.

Covers the drift case caught in production on hal0 (2026-05-20):

    capabilities.toml says embed.embed = npu / flm / enabled=true
    /etc/hal0/slots/embed.toml says backend=vulkan / provider=llama-server
    -> POST /api/capabilities/embed/embed {enabled: true, ...} returns 200,
      slot reports "ready", but the spawned process is still the Vulkan
      toolbox because the slot TOML never got rewritten.

The pre-fix orchestrator gated _rewrite_underlying_slot on
``backend_changed or provider_changed`` -- diffs computed against the
capabilities.toml value, not against the slot TOML. When the two
already disagreed (e.g. a previous failed apply, a manual edit, or a
seed/migration bug), no rewrite fired and the next load() spawned
against the stale slot TOML.

The fix reconciles unconditionally whenever the slot is going to be
enabled. These tests pin that contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.orchestrator import CapabilityOrchestrator


@pytest.fixture(autouse=True)
def _no_spawn_context_refresh(monkeypatch):
    # The runtime writers (swap/apply) fire a detached hal0-agent
    # render-context; stub it so tests never launch real subprocesses.
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)


class _StubSlot:
    """Minimal stand-in for the Slot dataclass that ``status()`` returns."""

    def __init__(self, state: str = "ready") -> None:
        class _S:
            value = state

        self.state = _S()


class FakeSlotManager:
    """Records lifecycle calls without touching systemd or the filesystem."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

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

    async def create(self, slot_name: str, cfg: dict[str, Any]) -> _StubSlot:
        self.calls.append(("create", slot_name, {"cfg": cfg}))
        return _StubSlot("offline")

    async def update_config(self, slot_name: str, updates: dict[str, Any]) -> _StubSlot:
        self.calls.append(("update_config", slot_name, {"updates": updates}))
        return _StubSlot("ready")


@pytest.fixture
def drifted_state(tmp_hal0_home: str) -> Path:
    """Lay out the on-disk state that triggers the prod bug.

    - ``etc/hal0/slots/embed.toml`` says vulkan / llama-server (stale).
    - ``etc/hal0/capabilities.toml`` says npu / flm / enabled=true.

    The 'drift' is the disagreement between the two -- exactly what was
    observed on hal0 when the NPU rollup card showed empty while the
    embed capability card claimed NPU was selected.
    """
    home = Path(tmp_hal0_home)
    slots_dir = home / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "embed.toml").write_text(
        "\n".join(
            [
                'name = "embed"',
                "port = 8082",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "enabled = true",
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
                "enabled = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return home


@pytest.fixture
def orchestrator(
    drifted_state: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[CapabilityOrchestrator, FakeSlotManager]:
    """Build the orchestrator with a fake SlotManager + the catalog bypassed.

    The catalog validator (``_validate_model_in_catalog``) would otherwise
    require the registry to know about the model id. We bypass it for the
    unit test because the bug we care about lives downstream of validation.
    """
    monkeypatch.setattr(
        CapabilityOrchestrator,
        "_validate_model_in_catalog",
        lambda self, slot, child, model_id, backend_id: None,
    )
    fake = FakeSlotManager()
    orch = CapabilityOrchestrator(slot_manager=fake)
    return orch, fake


async def test_apply_rewrites_slot_toml_when_drift_present(
    orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager],
) -> None:
    """Re-enabling embed with the *same* selection still rewrites the slot TOML.

    The selection on disk already says npu/flm. A POST that flips
    enabled false->true (without changing backend/provider) does not
    introduce a selection diff -- but the slot TOML still says vulkan.

    Regression: the orchestrator must call ``update_config`` with
    ``backend="flm"`` (the slot-toml form of catalog id "npu") so the
    next ``load()`` reads the correct backend.
    """
    orch, fake = orchestrator

    await orch.apply("embed", "embed", {"enabled": False})
    await orch.apply(
        "embed",
        "embed",
        {
            "enabled": True,
            "backend": "npu",
            "provider": "flm",
            "model": "nomic-embed-text-v1.5-q8_0",
        },
    )

    update_calls = [c for c in fake.calls if c[0] == "update_config"]
    assert update_calls, (
        "update_config was never invoked -- slot TOML drift was not reconciled. "
        f"All calls: {fake.calls}"
    )

    last_updates = update_calls[-1][2]["updates"]
    assert last_updates.get("backend") == "flm", (
        f"slot TOML backend was not reconciled to FLM: {last_updates!r}"
    )
    assert last_updates.get("provider") == "flm", (
        f"slot TOML provider was not reconciled to FLM: {last_updates!r}"
    )


async def test_apply_reconciles_before_load(
    orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager],
) -> None:
    """The rewrite must happen *before* load() so the spawn reads fresh config."""
    orch, fake = orchestrator

    await orch.apply("embed", "embed", {"enabled": False})
    fake.calls.clear()

    await orch.apply(
        "embed",
        "embed",
        {
            "enabled": True,
            "backend": "npu",
            "provider": "flm",
            "model": "nomic-embed-text-v1.5-q8_0",
        },
    )

    methods = [c[0] for c in fake.calls]
    assert "update_config" in methods, f"no rewrite happened: {methods}"
    assert "load" in methods, f"no load happened: {methods}"
    assert methods.index("update_config") < methods.index("load"), (
        f"update_config must precede load so the spawn reads the new TOML; "
        f"observed order: {methods}"
    )


async def test_apply_no_rewrite_on_pure_disable(
    orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager],
) -> None:
    """Disabling the slot does not require rewriting the TOML."""
    orch, fake = orchestrator

    await orch.apply("embed", "embed", {"enabled": False})

    update_calls = [c for c in fake.calls if c[0] == "update_config"]
    assert update_calls == [], f"unexpected update_config on disable transition: {update_calls}"
