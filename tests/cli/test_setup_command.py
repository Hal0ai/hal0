from typer.testing import CliRunner

from hal0.cli.setup_command import _api_reachable, app, build_auto_selections
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo


def test_headless_interactive_prints_stage2_command(monkeypatch):
    """Two-stage handoff (issue #1112): a piped / non-TTY `hal0 setup` (no
    --auto) must NOT launch rich prompts — it prints the command to run and
    exits 0 without probing hardware or applying anything."""
    monkeypatch.delenv("HAL0_FORCE_INTERACTIVE", raising=False)

    def boom(*a, **k):  # would blow up if the probe were reached
        raise AssertionError("hardware probe should not run on the headless guard path")

    monkeypatch.setattr("hal0.cli.setup_command.HardwareProbe", boom)
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0
    assert "hal0 setup" in result.output
    assert "--auto" in result.output


def _hw(ram_gb=96):
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=ram_gb * 1024,
        ram_available_mb=ram_gb * 1024,
        unified_memory_mb=ram_gb * 1024,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True, vulkan_capable=True)],
        npu=NPUInfo(present=True),
    )


def test_api_reachable_false_on_connection_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("refused")

    monkeypatch.setattr("hal0.cli.setup_command.httpx.get", boom)
    assert _api_reachable(timeout=0.01) is False


def test_auto_selections_scaffolds_capability_slots_empty():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models")
    names = {s.slot_name for s in sel.slots}
    # the capability slot STRUCTURE is scaffolded (device/profile/port), but no
    # model is chosen — pick-free: slots yes, models no. The chat capability's
    # slot is NAMED `agent` (ADR-0023 LLM anchor) and the steward's `brain`
    # scaffold rides along.
    # vision is retired — a model property served by any llm slot, no
    # dedicated scaffold.
    assert {"agent", "brain", "embed", "rerank", "stt", "tts"} <= names
    assert "vision" not in names
    assert all(s.model_id is None for s in sel.slots)  # every slot empty
    assert sel.extensions["openwebui"] is True
    assert sel.extensions["hermes"] is True
    # an agent extension is enabled by default → coder slot is scaffolded too
    assert "coder" in names


def test_auto_selections_brain_scaffold_is_chat_capability():
    """`brain` rides the chat-capability device/profile derivation and, like
    every scaffold, ships without a model (hal0/brain falls back to `agent`
    until the operator binds one)."""
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models")
    brain = next(s for s in sel.slots if s.slot_name == "brain")
    assert brain.capability == "chat"
    assert brain.model_id is None
    agent = next(s for s in sel.slots if s.slot_name == "agent")
    assert agent.capability == "chat"
    assert brain.port != agent.port


def test_auto_selections_no_slots_seeds_nothing():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models", with_slots=False)
    assert sel.slots == []
    assert sel.comfyui_defaults == ()


def test_auto_selections_no_extensions_disables_all_and_skips_agent_slot():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models", with_extensions=False)
    assert all(v is False for v in sel.extensions.values())
    # agent (Main) slot still seeded; coder slot NOT seeded (no agent ext on)
    assert any(s.slot_name == "agent" for s in sel.slots)
    assert not any(s.slot_name == "coder" for s in sel.slots)


def test_auto_selections_default_keeps_extensions_and_agent_slot():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models")
    assert sel.extensions["hermes"] is True
    assert any(s.slot_name == "coder" for s in sel.slots)


def test_auto_selections_skips_existing_slots():
    sel = build_auto_selections(
        _hw(96),
        storage_dir="/var/lib/hal0/models",
        existing_slots=frozenset({"agent", "brain"}),
    )
    # agent + brain already exist on disk → not re-seeded; coder (agent
    # extension default on) still seeded
    assert not any(s.slot_name == "agent" for s in sel.slots)
    assert not any(s.slot_name == "brain" for s in sel.slots)
    assert any(s.slot_name == "coder" for s in sel.slots)


def test_auto_selections_no_existing_seeds_all_default():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models")
    assert any(s.slot_name == "agent" for s in sel.slots)
