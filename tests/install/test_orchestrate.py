import pytest

from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install.orchestrate import (
    Selections,
    SetupResult,
    SlotOutcome,
    SlotSelection,
)

# pytest-asyncio is not installed in this venv; anyio (present) provides an
# equivalent marker that works with the anyio pytest plugin.
pytestmark = pytest.mark.anyio


def test_selections_roundtrip():
    sel = Selections(
        storage_dir="/var/lib/hal0/models",
        slots=[SlotSelection(capability="chat", slot_name="chat", port=8081, model_id="qwen3-4b")],
        extensions={"openwebui": True, "hermes": True, "pi": False},
        npu_opt_in=False,
    )
    assert sel.slots[0].model_id == "qwen3-4b"
    assert sel.slots[0].device is None  # derived later
    assert sel.extensions["pi"] is False


def test_setup_result_shape():
    res = SetupResult(
        slots=[SlotOutcome(slot="chat", model_id="qwen3-4b")], extensions=[], model_ids=[], pulls=[]
    )
    assert res.slots[0].created is False
    assert res.slots[0].skipped is None


class _FakeSlotManager:
    def __init__(self):
        self.created = {}

    async def create(self, name, cfg):
        self.created[name] = cfg
        return object()


def _strix_hw():
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=98304,
        ram_available_mb=90000,
        unified_memory_mb=98304,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True, vulkan_capable=True)],
        npu=NPUInfo(present=True),
    )


async def test_apply_setup_creates_chat_slot_and_plans_pull():
    from hal0.install import orchestrate

    sm = _FakeSlotManager()
    jobs: dict = {}
    sel = Selections(
        storage_dir="/var/lib/hal0/models",
        slots=[SlotSelection(capability="chat", slot_name="chat", port=8081, model_id="qwen3-4b")],
        extensions={},
        npu_opt_in=False,
    )
    res = await orchestrate.apply_setup(
        sel,
        hardware=_strix_hw(),
        slot_manager=sm,
        registry={},
        jobs=jobs,
        write_sentinel=False,
    )
    assert sm.created["chat"]["device"] == "gpu-rocm"
    assert sm.created["chat"]["profile"] == "rocm-dnse"
    out = res.slots[0]
    assert out.created is True and out.skipped is None
    assert "qwen3-4b" in res.model_ids
    assert len(res.pulls) == 1 and res.pulls[0].model_id == "qwen3-4b"


async def test_apply_setup_skips_uncurated_model():
    from hal0.install import orchestrate

    sel = Selections(
        storage_dir="/x",
        slots=[SlotSelection("chat", "chat", 8081, "does-not-exist")],
        extensions={},
        npu_opt_in=False,
    )
    res = await orchestrate.apply_setup(
        sel,
        hardware=_strix_hw(),
        slot_manager=_FakeSlotManager(),
        registry={},
        jobs={},
        write_sentinel=False,
    )
    assert res.slots[0].skipped == "needs_upstream_routing"
    assert res.slots[0].created is False


def test_mark_first_run_done_writes_sentinel(tmp_path, monkeypatch):
    from hal0.install import orchestrate

    sentinel = tmp_path / ".first_run_done"
    monkeypatch.setattr(orchestrate, "_sentinel_path", lambda: sentinel)
    orchestrate.mark_first_run_done()
    assert sentinel.exists()


def test_install_extensions_dispatches(monkeypatch):
    from hal0.install import orchestrate

    calls = []
    monkeypatch.setattr(
        orchestrate,
        "install_extension",
        lambda ext_id: (
            calls.append(ext_id) or orchestrate.ExtensionOutcome(ext_id=ext_id, installed=True)
        ),
    )
    outs = orchestrate._install_extensions({"openwebui": True, "pi": False, "hermes": True})
    assert set(calls) == {"openwebui", "hermes"}  # only enabled
    assert all(o.installed for o in outs)
