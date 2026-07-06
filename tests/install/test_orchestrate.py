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


async def test_apply_setup_creates_chat_slot_and_plans_pull(tmp_hal0_home):
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
    assert sm.created["chat"]["profile"] == "rocm"
    out = res.slots[0]
    assert out.created is True and out.skipped is None
    assert "qwen3-4b" in res.model_ids
    assert len(res.pulls) == 1 and res.pulls[0].model_id == "qwen3-4b"


async def test_apply_setup_scaffolds_modelless_slot_without_pull(tmp_hal0_home):
    """A SlotSelection with model_id=None creates an empty slot (device/profile
    wired, model.default unset) and plans NO pull."""
    from hal0.install import orchestrate

    sm = _FakeSlotManager()
    sel = Selections(
        storage_dir="/var/lib/hal0/models",
        slots=[SlotSelection(capability="embed", slot_name="embed", port=8083, model_id=None)],
        extensions={},
        npu_opt_in=False,
    )
    res = await orchestrate.apply_setup(
        sel,
        hardware=_strix_hw(),
        slot_manager=sm,
        registry={},
        jobs={},
        write_sentinel=False,
    )
    assert sm.created["embed"]["device"] == "gpu-rocm"
    assert sm.created["embed"]["profile"] == "embed"
    assert sm.created["embed"]["model"]["default"] == ""  # empty scaffold
    out = res.slots[0]
    assert out.created is True and out.skipped is None
    assert res.pulls == [] and res.model_ids == []  # pick-free: no downloads


async def test_apply_setup_skips_uncurated_model(tmp_hal0_home):
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


async def test_apply_setup_persists_custom_store_dir(tmp_hal0_home, tmp_path):
    """A non-empty absolute ``storage_dir`` is written to ``[models].store`` so
    the pull engine (which reads it lazily at pull time) lands pulls there
    (issue #1095 threading decision)."""
    from hal0.config.loader import load_hal0_config
    from hal0.install import orchestrate
    from hal0.registry import pull as pull_mod

    custom = tmp_path / "custom-store"
    sel = Selections(
        storage_dir=str(custom),
        # scaffold slot — exercises the store write without needing a curated
        # model or a network pull.
        slots=[SlotSelection(capability="embed", slot_name="embed", port=8083, model_id=None)],
        extensions={},
        npu_opt_in=False,
    )
    await orchestrate.apply_setup(
        sel,
        hardware=_strix_hw(),
        slot_manager=_FakeSlotManager(),
        registry={},
        jobs={},
        write_sentinel=False,
    )
    # Persisted to config …
    assert load_hal0_config().models.store == str(custom)
    # … and the pull engine now resolves its destination root to it.
    assert pull_mod._pull_root() == custom


async def test_apply_setup_colocates_flm_store(tmp_hal0_home, tmp_path):
    """A custom ``storage_dir`` also seeds ``[models].flm_store`` co-located
    under it (``<store>/flm/models``), so NPU/FLM weights don't strand on the
    root FS (issue #1100, decision Q4 — extends #1095's WS-A store thread)."""
    from hal0.config.loader import load_hal0_config
    from hal0.install import orchestrate

    custom = tmp_path / "custom-store"
    sel = Selections(
        storage_dir=str(custom),
        slots=[SlotSelection(capability="embed", slot_name="embed", port=8083, model_id=None)],
        extensions={},
        npu_opt_in=False,
    )
    await orchestrate.apply_setup(
        sel,
        hardware=_strix_hw(),
        slot_manager=_FakeSlotManager(),
        registry={},
        jobs={},
        write_sentinel=False,
    )
    cfg = load_hal0_config()
    assert cfg.models.store == str(custom)
    assert cfg.models.flm_store == str(custom / "flm" / "models")


def test_colocated_flm_store_path():
    from hal0.install import orchestrate

    assert orchestrate._colocated_flm_store("/mnt/ai-models") == "/mnt/ai-models/flm/models"


def test_persist_store_dir_idempotent_when_both_already_set(tmp_hal0_home, tmp_path, monkeypatch):
    """No rewrite when both ``store`` and the co-located ``flm_store`` already
    match the chosen dir (the existing #1095 idempotency guard extended to
    cover the new field)."""
    from hal0.config.loader import load_hal0_config, save_hal0_config
    from hal0.install import orchestrate

    custom = tmp_path / "custom-store"
    cfg = load_hal0_config()
    cfg.models.store = str(custom)
    cfg.models.flm_store = str(custom / "flm" / "models")
    save_hal0_config(cfg)

    calls = []
    monkeypatch.setattr("hal0.config.loader.save_hal0_config", lambda c, path=None: calls.append(c))
    orchestrate._persist_store_dir(str(custom))
    assert calls == []  # save_hal0_config never invoked — already up to date


def test_persist_store_dir_rewrites_flm_store_when_store_already_set(tmp_hal0_home, tmp_path):
    """A pre-#1100 config with ``store`` set but no ``flm_store`` still gets
    the co-located ``flm_store`` seeded on the next persist (store already
    matching must not short-circuit the flm_store write)."""
    from hal0.config.loader import load_hal0_config, save_hal0_config
    from hal0.install import orchestrate

    custom = tmp_path / "custom-store"
    cfg = load_hal0_config()
    cfg.models.store = str(custom)  # pre-existing, store already correct
    save_hal0_config(cfg)
    assert load_hal0_config().models.flm_store == ""  # sanity: unset before

    orchestrate._persist_store_dir(str(custom))

    assert load_hal0_config().models.flm_store == str(custom / "flm" / "models")


async def test_apply_setup_ignores_relative_storage_dir_for_flm_store_too(tmp_hal0_home):
    """A relative ``storage_dir`` leaves ``flm_store`` untouched too (mirrors
    the existing relative-store guard)."""
    from hal0.config.loader import load_hal0_config
    from hal0.install import orchestrate

    sel = Selections(
        storage_dir="relative/models",
        slots=[SlotSelection(capability="embed", slot_name="embed", port=8083, model_id=None)],
        extensions={},
        npu_opt_in=False,
    )
    await orchestrate.apply_setup(
        sel,
        hardware=_strix_hw(),
        slot_manager=_FakeSlotManager(),
        registry={},
        jobs={},
        write_sentinel=False,
    )
    assert load_hal0_config().models.flm_store == ""


def test_validate_store_mount_warns_on_unwritable(tmp_path, caplog, monkeypatch):
    from hal0.install import orchestrate

    monkeypatch.setattr(orchestrate, "_is_writable", lambda ancestor: False)
    monkeypatch.setattr(orchestrate, "_is_root_fs", lambda ancestor: False)
    monkeypatch.setattr(orchestrate, "_free_space_gib", lambda path: 999.0)
    with caplog.at_level("WARNING", logger="hal0.install.orchestrate"):
        orchestrate._validate_store_mount(str(tmp_path / "store"))
    assert "not writable" in caplog.text


def test_validate_store_mount_warns_on_root_fs(tmp_path, caplog, monkeypatch):
    from hal0.install import orchestrate

    monkeypatch.setattr(orchestrate, "_is_writable", lambda ancestor: True)
    monkeypatch.setattr(orchestrate, "_is_root_fs", lambda ancestor: True)
    monkeypatch.setattr(orchestrate, "_free_space_gib", lambda path: 999.0)
    with caplog.at_level("WARNING", logger="hal0.install.orchestrate"):
        orchestrate._validate_store_mount(str(tmp_path / "store"))
    assert "root filesystem" in caplog.text


def test_validate_store_mount_warns_on_low_free_space(tmp_path, caplog, monkeypatch):
    from hal0.install import orchestrate

    monkeypatch.setattr(orchestrate, "_is_writable", lambda ancestor: True)
    monkeypatch.setattr(orchestrate, "_is_root_fs", lambda ancestor: False)
    monkeypatch.setattr(orchestrate, "_free_space_gib", lambda path: 1.0)
    with caplog.at_level("WARNING", logger="hal0.install.orchestrate"):
        orchestrate._validate_store_mount(str(tmp_path / "store"))
    assert "GiB free" in caplog.text


def test_validate_store_mount_silent_when_healthy(tmp_path, caplog, monkeypatch):
    from hal0.install import orchestrate

    monkeypatch.setattr(orchestrate, "_is_writable", lambda ancestor: True)
    monkeypatch.setattr(orchestrate, "_is_root_fs", lambda ancestor: False)
    monkeypatch.setattr(orchestrate, "_free_space_gib", lambda path: 999.0)
    with caplog.at_level("WARNING", logger="hal0.install.orchestrate"):
        orchestrate._validate_store_mount(str(tmp_path / "store"))
    assert caplog.text == ""


def test_free_space_gib_walks_to_existing_ancestor(tmp_path):
    from hal0.install import orchestrate

    missing = tmp_path / "does" / "not" / "exist" / "yet"
    free = orchestrate._free_space_gib(str(missing))
    assert free is not None and free > 0


def test_is_root_fs_false_for_tmp_path(tmp_path):
    """A pytest tmp_path is essentially never the same device as ``/`` in CI
    sandboxes/containers; this pins the comparison direction (not a hard
    filesystem-topology assertion)."""
    from pathlib import Path

    from hal0.install import orchestrate

    if orchestrate._is_root_fs(Path("/")) and orchestrate._is_root_fs(tmp_path):
        pytest.skip("test root and / share a device in this sandbox — inconsistent host")
    assert orchestrate._is_root_fs(tmp_path) == (tmp_path.stat().st_dev == Path("/").stat().st_dev)


async def test_apply_setup_ignores_relative_storage_dir(tmp_hal0_home):
    """A relative ``storage_dir`` is not persisted (best-effort guard); the
    store stays at its default so a bad pick never corrupts config."""
    from hal0.config.loader import load_hal0_config
    from hal0.install import orchestrate

    sel = Selections(
        storage_dir="relative/models",
        slots=[SlotSelection(capability="embed", slot_name="embed", port=8083, model_id=None)],
        extensions={},
        npu_opt_in=False,
    )
    await orchestrate.apply_setup(
        sel,
        hardware=_strix_hw(),
        slot_manager=_FakeSlotManager(),
        registry={},
        jobs={},
        write_sentinel=False,
    )
    assert load_hal0_config().models.store == ""  # untouched


async def test_apply_setup_empty_storage_dir_is_noop(tmp_hal0_home):
    """An empty ``storage_dir`` leaves ``[models].store`` unset (the default
    store keeps applying)."""
    from hal0.config.loader import load_hal0_config
    from hal0.install import orchestrate

    sel = Selections(
        storage_dir="",
        slots=[SlotSelection(capability="embed", slot_name="embed", port=8083, model_id=None)],
        extensions={},
        npu_opt_in=False,
    )
    await orchestrate.apply_setup(
        sel,
        hardware=_strix_hw(),
        slot_manager=_FakeSlotManager(),
        registry={},
        jobs={},
        write_sentinel=False,
    )
    assert load_hal0_config().models.store == ""


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


# ── WS-E (#1108): safe slot activation ──────────────────────────────────────────


class _RecordingSlotManager:
    """Fake slot manager that records create() cfgs + update_config() flips."""

    def __init__(self):
        self.created = {}
        self.enabled = {}
        self.updates = []  # list of (name, updates_dict)

    async def create(self, name, cfg):
        self.created[name] = cfg
        self.enabled[name] = cfg.get("enabled")
        return object()

    async def update_config(self, name, updates):
        self.updates.append((name, updates))
        if "enabled" in updates:
            self.enabled[name] = updates["enabled"]
        return object()


class _Job:
    def __init__(self, job_id="job-1"):
        self.job_id = job_id
        self.state = "queued"


async def test_apply_setup_creates_slot_disabled_and_plan_carries_slot():
    """The guided path seeds the slot DISABLED and the plan remembers which
    slot to enable once the pull lands (no start-before-model race)."""
    from hal0.install import orchestrate

    sm = _RecordingSlotManager()
    sel = Selections(
        storage_dir="",
        slots=[SlotSelection(capability="chat", slot_name="chat", port=8081, model_id="qwen3-4b")],
        extensions={},
        npu_opt_in=False,
    )
    res = await orchestrate.apply_setup(
        sel,
        hardware=_strix_hw(),
        slot_manager=sm,
        registry={},
        jobs={},
        write_sentinel=False,
    )
    # Born disabled — never enabled at create time.
    assert sm.created["chat"]["enabled"] is False
    # The plan carries the slot so the pull driver can enable it on success.
    assert res.pulls[0].slot_names == ["chat"]


async def test_apply_setup_shared_model_enables_both_slots():
    """Two slots on the SAME model_id ride one pull — both get enabled."""
    from hal0.install import orchestrate

    sm = _RecordingSlotManager()
    sel = Selections(
        storage_dir="",
        slots=[
            SlotSelection(capability="chat", slot_name="chat", port=8081, model_id="qwen3-4b"),
            SlotSelection(capability="coder", slot_name="coder", port=8082, model_id="qwen3-4b"),
        ],
        extensions={},
        npu_opt_in=False,
    )
    res = await orchestrate.apply_setup(
        sel,
        hardware=_strix_hw(),
        slot_manager=sm,
        registry={},
        jobs={},
        write_sentinel=False,
    )
    # One pull, but both slots attached to it.
    assert len(res.pulls) == 1
    assert set(res.pulls[0].slot_names) == {"chat", "coder"}


async def test_run_pull_and_activate_enables_slot_on_success(monkeypatch):
    from hal0.install import orchestrate
    from hal0.registry import pull as pull_mod

    job = _Job()

    async def _fake_run_pull(job, **kw):
        job.state = "completed"

    monkeypatch.setattr(pull_mod, "run_pull", _fake_run_pull)

    sm = _RecordingSlotManager()
    sm.enabled["chat"] = False
    plan = orchestrate.PullPlan(model_id="qwen3-4b", job=job, kwargs={}, slot_names=["chat"])
    await orchestrate.run_pull_and_activate(plan, slot_manager=sm)

    assert sm.enabled["chat"] is True
    assert ("chat", {"enabled": True}) in sm.updates


async def test_run_pull_and_activate_marks_disabled_on_failed_job(monkeypatch):
    """A pull that settles state=='failed' leaves the slot disabled + marked."""
    from hal0.install import orchestrate
    from hal0.registry import pull as pull_mod

    job = _Job()

    async def _fake_run_pull(job, **kw):
        job.state = "failed"

    monkeypatch.setattr(pull_mod, "run_pull", _fake_run_pull)

    sm = _RecordingSlotManager()
    sm.enabled["chat"] = False
    plan = orchestrate.PullPlan(model_id="qwen3-4b", job=job, kwargs={}, slot_names=["chat"])
    await orchestrate.run_pull_and_activate(plan, slot_manager=sm)

    assert sm.enabled["chat"] is False
    name, updates = sm.updates[-1]
    assert name == "chat"
    assert updates["enabled"] is False
    assert updates["meta"] == {"pull_failed": True}


async def test_run_pull_and_activate_marks_and_reraises_on_exception(monkeypatch):
    """run_pull raising leaves the slot disabled+marked and re-raises so the
    caller's gather sees the failure."""
    import pytest as _pytest

    from hal0.install import orchestrate
    from hal0.registry import pull as pull_mod

    job = _Job()

    async def _boom(job, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(pull_mod, "run_pull", _boom)

    sm = _RecordingSlotManager()
    sm.enabled["chat"] = False
    plan = orchestrate.PullPlan(model_id="qwen3-4b", job=job, kwargs={}, slot_names=["chat"])
    with _pytest.raises(RuntimeError, match="network down"):
        await orchestrate.run_pull_and_activate(plan, slot_manager=sm)

    assert sm.enabled["chat"] is False
    assert sm.updates[-1][1]["meta"] == {"pull_failed": True}


def test_clamp_context_size_passes_through_on_ample_hw():
    """A big unified pool funds the model's native window unchanged."""
    from hal0.install.orchestrate import _clamp_context_size

    # 96 GB unified Strix box → half-pool budget dwarfs a 32K KV cache.
    assert _clamp_context_size(32768, _strix_hw(), weights_gb=2.5) == 32768


def test_clamp_context_size_clamps_on_tight_cpu_host():
    """A memory-tight CPU-only host clamps a huge native window down to a
    budget the KV cache actually fits — the first-warm OOM fix (#1108)."""
    from hal0.install.orchestrate import _clamp_context_size

    hw = HardwareInfo(ram_mb=8192, ram_available_mb=8192)  # no GPU
    clamped = _clamp_context_size(131072, hw, weights_gb=2.0)
    assert clamped < 131072
    # budget = 0.5*8 = 4 GB; kv = 4 - 2(weights) - 1(reserve) = 1 GB;
    # 1 GiB / 128 KiB ≈ 8192 tokens.
    assert clamped == 8192


def test_clamp_context_size_honors_cgroup_cap():
    """An LXC/container memory cap binds the UMA budget so a capped box clamps
    even though the raw unified pool looks huge (the `oom` artifact box)."""
    from hal0.install.orchestrate import _clamp_context_size

    hw = HardwareInfo(
        platform="strix-halo",
        ram_mb=98304,
        ram_available_mb=90000,
        unified_memory_mb=98304,
        cgroup_max_mb=8192,  # LXC-capped to 8 GB
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True, vulkan_capable=True)],
        npu=NPUInfo(present=True),
    )
    clamped = _clamp_context_size(131072, hw, weights_gb=2.0)
    assert clamped < 131072


def test_clamp_context_size_never_below_floor():
    """Even a starved host keeps a minimally usable window."""
    from hal0.install.orchestrate import _MIN_CONTEXT, _clamp_context_size

    hw = HardwareInfo(ram_mb=2048, ram_available_mb=1024)  # ~0.5 GB budget
    assert _clamp_context_size(131072, hw, weights_gb=8.0) == _MIN_CONTEXT
