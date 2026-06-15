from hal0.cli.setup_command import _api_reachable, build_auto_selections
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo


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


def test_auto_selections_pick_recommended_and_default_extensions():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models")
    chat = next(s for s in sel.slots if s.slot_name == "chat")
    assert chat.model_id  # a recommended model id was chosen
    assert sel.extensions["openwebui"] is True
    assert sel.extensions["hermes"] is True
    assert sel.extensions["pi"] is False
    # an agent is enabled by default → agent slot is seeded
    assert any(s.slot_name == "coder" for s in sel.slots)
