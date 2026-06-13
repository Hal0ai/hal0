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

The fix reconciles whenever the slot is going to be enabled. Since
issue #697 the reconciliation runs through ``hal0.slot_config``'s
``SlotConfigStore`` (compute-only ``apply`` + atomic ``commit``), so
these tests pin the contract against the on-disk slot TOML — the
reconciled truth — rather than against SlotManager call recording.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.orchestrator import CapabilityOrchestrator


def _read_slot_toml(home: Path, slot: str = "embed") -> dict[str, Any]:
    path = home / "etc" / "hal0" / "slots" / f"{slot}.toml"
    with open(path, "rb") as f:
        return tomllib.load(f)


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
        # Slot config records the orchestrator scans for the NPU anchor
        # (type==llm && device==npu). Seed via :meth:`set_configs`.
        self._configs: list[dict[str, Any]] = []

    def set_configs(self, configs: list[dict[str, Any]]) -> None:
        """Test helper — seed the records returned by ``iter_configs()``."""
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
        # Recorded so tests can assert the NPU path NEVER eagerly restarts
        # the anchor (Decision 1: return pending_reload, don't bounce it).
        self.calls.append(("restart", slot_name, {}))
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
    drifted_state: Path,
) -> None:
    """Re-enabling embed with the *same* selection still rewrites the slot TOML.

    The selection on disk already says npu/flm. A POST that flips
    enabled false->true (without changing backend/provider) does not
    introduce a selection diff -- but the slot TOML still says vulkan.

    Regression: the slot TOML on disk must end up with ``backend="flm"``
    (the slot-toml form of catalog id "npu") so the next ``load()``
    reads the correct backend.
    """
    orch, _fake = orchestrator

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

    on_disk = _read_slot_toml(drifted_state)
    assert on_disk.get("backend") == "flm", (
        f"slot TOML backend was not reconciled to FLM: {on_disk!r}"
    )
    assert on_disk.get("provider") == "flm", (
        f"slot TOML provider was not reconciled to FLM: {on_disk!r}"
    )
    assert on_disk.get("device") == "npu", f"slot TOML device not reconciled: {on_disk!r}"


async def test_apply_reconciles_before_load(
    drifted_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rewrite must hit disk *before* load() so the spawn reads fresh config."""
    monkeypatch.setattr(
        CapabilityOrchestrator,
        "_validate_model_in_catalog",
        lambda self, slot, child, model_id, backend_id: None,
    )

    class DiskPeekSlotManager(FakeSlotManager):
        """Snapshots the slot TOML at load() time — what the spawn would read."""

        def __init__(self, home: Path) -> None:
            super().__init__()
            self._home = home
            self.seen_at_load: list[dict[str, Any]] = []

        async def load(self, slot_name: str, model_id: str | None = None) -> _StubSlot:
            self.seen_at_load.append(_read_slot_toml(self._home, slot_name))
            return await super().load(slot_name, model_id=model_id)

    fake = DiskPeekSlotManager(drifted_state)
    orch = CapabilityOrchestrator(slot_manager=fake)

    await orch.apply("embed", "embed", {"enabled": False})
    fake.calls.clear()

    # gpu-rocm (not npu): device=npu + embed always forks to the NPU-trio
    # path which never load()s — this test pins the STANDARD lifecycle, so
    # target a backend whose slot-toml form ("rocm") differs from the
    # stale on-disk "vulkan" and still drives load().
    await orch.apply(
        "embed",
        "embed",
        {
            "enabled": True,
            "backend": "gpu-rocm",
            "provider": "llama-server",
            "model": "nomic-embed-text-v1.5-q8_0",
        },
    )

    assert fake.seen_at_load, f"no load happened: {fake.calls}"
    assert fake.seen_at_load[0].get("backend") == "rocm", (
        "load() observed a stale slot TOML — reconciliation must be committed "
        f"to disk before the spawn: {fake.seen_at_load[0]!r}"
    )


async def test_apply_no_rewrite_on_pure_disable(
    orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager],
    drifted_state: Path,
) -> None:
    """Disabling the slot does not require rewriting the TOML."""
    orch, fake = orchestrator
    # Pin the persisted selection to a non-NPU backend first: this test is
    # about the STANDARD lifecycle's pure-disable transition, and a
    # device=npu embed selection now always forks to the NPU-trio path
    # (which legitimately writes update_config on the modality slot).
    caps_path = drifted_state / "etc" / "hal0" / "capabilities.toml"
    caps_path.write_text(
        "\n".join(
            [
                "[selections.embed.embed]",
                'backend = "gpu-vulkan"',
                'provider = "llama-server"',
                'model = "nomic-embed-text-v1.5-q8_0"',
                "enabled = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    before = _read_slot_toml(drifted_state)

    await orch.apply("embed", "embed", {"enabled": False})

    update_calls = [c for c in fake.calls if c[0] == "update_config"]
    assert update_calls == [], f"unexpected update_config on disable transition: {update_calls}"
    assert _read_slot_toml(drifted_state) == before, "slot TOML changed on pure disable"


async def test_apply_commit_failure_leaves_both_files_at_before(
    orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager],
    drifted_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant (#697): a failed mid-apply leaves disk at ``before``.

    When the store's commit blows up partway, NEITHER capabilities.toml
    nor the slot TOML may be left changed — the half-reconciled state is
    exactly the drift this module exists to prevent.
    """
    import hal0.slot_config as slot_config_mod

    orch, _fake = orchestrator
    home = drifted_state
    caps_path = home / "etc" / "hal0" / "capabilities.toml"
    slot_before = _read_slot_toml(home)
    caps_before = caps_path.read_bytes()

    real_write = slot_config_mod.write_toml_atomic

    def _boom_on_slot(path, data):  # type: ignore[no-untyped-def]
        if Path(path).name == "embed.toml":
            raise OSError("disk full")
        real_write(path, data)

    monkeypatch.setattr(slot_config_mod, "write_toml_atomic", _boom_on_slot)

    from hal0.errors import Hal0Error

    with pytest.raises(Hal0Error):
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

    monkeypatch.setattr(slot_config_mod, "write_toml_atomic", real_write)
    assert _read_slot_toml(home) == slot_before
    assert caps_path.read_bytes() == caps_before


async def test_apply_lifecycle_failure_still_persists_intent(
    drifted_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A load() failure AFTER commit keeps the persisted selection (the
    pre-#697 'persist the user's intent even if the slot bounce failed'
    behaviour) — and both files stay mutually consistent."""
    from hal0.capabilities.config import load_capabilities_config
    from hal0.capabilities.orchestrator import CapabilityApplyFailed

    monkeypatch.setattr(
        CapabilityOrchestrator,
        "_validate_model_in_catalog",
        lambda self, slot, child, model_id, backend_id: None,
    )

    class ExplodingSlotManager(FakeSlotManager):
        async def load(self, slot_name: str, model_id: str | None = None) -> _StubSlot:
            raise RuntimeError("slot runtime is down")

    orch = CapabilityOrchestrator(slot_manager=ExplodingSlotManager())
    await orch.apply("embed", "embed", {"enabled": False})

    # gpu-rocm (not npu): device=npu + embed always forks to the NPU-trio
    # path which never load()s — this test pins the STANDARD lifecycle's
    # commit-then-load ordering, so it must target a backend that loads.
    with pytest.raises(CapabilityApplyFailed):
        await orch.apply(
            "embed",
            "embed",
            {
                "enabled": True,
                "backend": "gpu-rocm",
                "provider": "llama-server",
                "model": "nomic-embed-text-v1.5-q8_0",
            },
        )

    caps_path = drifted_state / "etc" / "hal0" / "capabilities.toml"
    sel = load_capabilities_config(caps_path).selections["embed"]["embed"]
    assert sel.enabled is True, "user intent must persist past a lifecycle failure"
    on_disk = _read_slot_toml(drifted_state)
    assert on_disk.get("backend") == "rocm", "slot TOML must match the persisted selection"


# ── Step 1: _CHILD_TO_SLOT_TYPE + type written by _ensure_slot_exists ──────────


def test_child_to_slot_type_mapping() -> None:
    """The trio-modality children carry their dispatch ``type`` discriminator."""
    from hal0.capabilities.orchestrator import _CHILD_TO_SLOT_TYPE

    assert _CHILD_TO_SLOT_TYPE["embed"] == "embedding"
    assert _CHILD_TO_SLOT_TYPE["stt"] == "transcription"


async def test_ensure_slot_exists_writes_type_for_embed(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-creating the embed slot stamps ``type=embedding`` into the cfg.

    The dispatch gate (`v1._is_npu_trio_request`) keys on ``type`` first;
    a created slot that omits it never activates trio dispatch.
    """
    from hal0.capabilities.config import CapabilitySelection

    fake = FakeSlotManager()
    orch = CapabilityOrchestrator(slot_manager=fake)
    selection = CapabilitySelection.model_validate(
        {"device": "npu", "provider": "flm", "model": "some-embed", "enabled": True}
    )
    # No embed.toml on disk under tmp_hal0_home → create path fires.
    await orch._ensure_slot_exists("embed", selection)

    create_calls = [c for c in fake.calls if c[0] == "create"]
    assert create_calls, f"create was never invoked: {fake.calls}"
    cfg = create_calls[-1][2]["cfg"]
    assert cfg.get("type") == "embedding", f"created cfg missing type: {cfg!r}"


def test_ensure_slot_exists_omits_type_for_rerank() -> None:
    """Non-trio children (rerank/tts/img) get no ``type`` key."""
    from hal0.capabilities.orchestrator import _CHILD_TO_SLOT_TYPE

    # embed-rerank → child "rerank", not in the trio-type map.
    assert "rerank" not in _CHILD_TO_SLOT_TYPE
    assert "tts" not in _CHILD_TO_SLOT_TYPE


# ── Step 4: test stubs (FakeSlotManager.iter_configs/restart) ─────────────────


async def test_fake_slot_manager_iter_configs_roundtrip() -> None:
    fake = FakeSlotManager()
    fake.set_configs([{"name": "agent", "type": "llm", "device": "npu", "enabled": True}])
    configs = await fake.iter_configs()
    assert configs == [{"name": "agent", "type": "llm", "device": "npu", "enabled": True}]


# ── Step 5: NPU-trio fork (Cases 1-5, 9) ──────────────────────────────────────


def _write_embed_slot(home: Path, *, device: str = "vulkan", slot_type: str | None = None) -> None:
    """(Re)write etc/hal0/slots/embed.toml under ``home``."""
    slots_dir = home / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        'name = "embed"',
        "port = 8082",
        f'backend = "{device}"',
        'provider = "llama-server"',
        "enabled = true",
    ]
    if slot_type:
        lines.append(f'type = "{slot_type}"')
    lines += ["[model]", 'default = "nomic-embed-text-v1.5-q8_0"', ""]
    (slots_dir / "embed.toml").write_text("\n".join(lines), encoding="utf-8")


def _write_caps(home: Path, *, slot: str, child: str, fields: dict[str, Any]) -> None:
    """Write a single-selection capabilities.toml under ``home``."""
    caps_path = home / "etc" / "hal0" / "capabilities.toml"
    caps_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"[selections.{slot}.{child}]"]
    for k, v in fields.items():
        if isinstance(v, bool):
            lines.append(f"{k} = {str(v).lower()}")
        else:
            lines.append(f'{k} = "{v}"')
    lines.append("")
    caps_path.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def npu_orchestrator(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[CapabilityOrchestrator, FakeSlotManager]:
    """Orchestrator with a CONTAINER NPU anchor — engages the trio fork.

    Bypasses catalog validation (downstream of the path under test). The
    anchor is seeded as a container slot (type=llm, device=npu,
    profile=flm — the ``profile`` key is what makes
    ``is_container_npu_cfg`` true), so trio toggles land as ``[npu]``
    TOML writes via ``update_config`` on the anchor.
    """
    monkeypatch.setattr(
        CapabilityOrchestrator,
        "_validate_model_in_catalog",
        lambda self, slot, child, model_id, backend_id: None,
    )
    fake = FakeSlotManager()
    fake.set_configs(
        [
            {
                "name": "npu",
                "type": "llm",
                "device": "npu",
                "profile": "flm",
                "enabled": True,
            }
        ]
    )
    orch = CapabilityOrchestrator(slot_manager=fake)
    return orch, fake


def _anchor_npu_writes(fake: FakeSlotManager, expected: dict[str, bool]) -> list[Any]:
    """Filter ``update_config`` calls on the anchor carrying the [npu] toggle."""
    return [
        c
        for c in fake.calls
        if c[0] == "update_config" and c[1] == "npu" and c[2]["updates"] == {"npu": expected}
    ]


async def test_npu_embed_enable_writes_anchor_toggle_no_load(
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager],
    tmp_hal0_home: str,
) -> None:
    """Case 1: NPU embed enable → anchor [npu] embed=true, update_config enabled, NO load."""
    orch, fake = npu_orchestrator
    home = Path(tmp_hal0_home)
    _write_embed_slot(home, device="flm", slot_type="embedding")
    _write_caps(
        home,
        slot="embed",
        child="embed",
        fields={
            "backend": "npu",
            "provider": "flm",
            "model": "nomic-embed-text-v1.5-q8_0",
            "enabled": False,
        },
    )

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

    assert _anchor_npu_writes(fake, {"embed": True}), (
        f"anchor [npu] embed toggle never written: {fake.calls}"
    )
    # The embed slot must be flipped enabled=true + stamped type=embedding,
    # WITHOUT a nested model in that write (Decision 4).
    enabled_writes = [
        c
        for c in fake.calls
        if c[0] == "update_config"
        and c[1] == "embed"
        and c[2]["updates"].get("enabled") is True
        and c[2]["updates"].get("type") == "embedding"
    ]
    assert enabled_writes, f"no enabled+type update_config on embed slot: {fake.calls}"
    assert "model" not in enabled_writes[-1][2]["updates"], enabled_writes[-1]
    # ZERO load/swap/unload on the embed slot.
    assert not [c for c in fake.calls if c[0] in ("load", "swap", "unload")], (
        f"NPU embed path must not bounce the slot: {fake.calls}"
    )
    assert result.get("pending_reload") is True, result


async def test_npu_embed_disable_writes_anchor_toggle_off_no_unload(
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager],
    tmp_hal0_home: str,
) -> None:
    """Case 2: NPU embed disable → anchor [npu] embed=false, slot enabled=False, NO unload."""
    orch, fake = npu_orchestrator
    home = Path(tmp_hal0_home)
    _write_embed_slot(home, device="flm", slot_type="embedding")
    _write_caps(
        home,
        slot="embed",
        child="embed",
        fields={
            "backend": "npu",
            "provider": "flm",
            "model": "nomic-embed-text-v1.5-q8_0",
            "enabled": True,
        },
    )

    result = await orch.apply("embed", "embed", {"enabled": False})

    assert _anchor_npu_writes(fake, {"embed": False}), (
        f"anchor [npu] embed=false toggle never written: {fake.calls}"
    )
    disabled_writes = [
        c
        for c in fake.calls
        if c[0] == "update_config"
        and c[1] == "embed"
        and c[2]["updates"].get("enabled") is False
        and c[2]["updates"].get("type") == "embedding"
    ]
    assert disabled_writes, f"no enabled=False+type write on embed slot: {fake.calls}"
    assert "model" not in disabled_writes[-1][2]["updates"], disabled_writes[-1]
    assert not [c for c in fake.calls if c[0] in ("load", "swap", "unload")], (
        f"NPU embed disable must not unload the slot: {fake.calls}"
    )
    assert result.get("pending_reload") is True


async def test_npu_stt_enable_sets_asr(
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager],
    tmp_hal0_home: str,
) -> None:
    """Case 3: NPU stt enable → anchor [npu] asr=true, no standalone load."""
    orch, fake = npu_orchestrator
    home = Path(tmp_hal0_home)
    # stt slot does not exist on disk → create path writes type=transcription.
    _write_caps(
        home,
        slot="voice",
        child="stt",
        fields={
            "backend": "npu",
            "provider": "flm",
            "model": "whisper-large-v3",
            "enabled": False,
        },
    )

    await orch.apply(
        "voice",
        "stt",
        {
            "enabled": True,
            "backend": "npu",
            "provider": "flm",
            "model": "whisper-large-v3",
        },
    )

    assert _anchor_npu_writes(fake, {"asr": True}), (
        f"stt enable did not write anchor [npu] asr=true: {fake.calls}"
    )
    assert not [c for c in fake.calls if c[0] in ("load", "swap", "unload")], (
        f"NPU stt path must not bounce a slot: {fake.calls}"
    )
    # Created stt slot stamps type=transcription.
    create_calls = [c for c in fake.calls if c[0] == "create" and c[1] == "stt"]
    assert create_calls, f"stt slot not created: {fake.calls}"
    assert create_calls[-1][2]["cfg"].get("type") == "transcription"


async def test_embed_gpu_to_npu_no_load(
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager],
    tmp_hal0_home: str,
) -> None:
    """Case 4: embed gpu-vulkan→npu → anchor [npu] embed=true, device=npu, NO load/swap."""
    orch, fake = npu_orchestrator
    home = Path(tmp_hal0_home)
    _write_embed_slot(home, device="vulkan", slot_type="embedding")
    _write_caps(
        home,
        slot="embed",
        child="embed",
        fields={
            "backend": "gpu-vulkan",
            "provider": "llama-server",
            "model": "nomic-embed-text-v1.5-q8_0",
            "enabled": True,
        },
    )

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

    assert _anchor_npu_writes(fake, {"embed": True}), (
        f"gpu->npu did not write anchor [npu] embed=true: {fake.calls}"
    )
    # device rewritten to npu on the slot TOML (committed by the store).
    on_disk = _read_slot_toml(home)
    assert on_disk.get("device") == "npu", f"device not rewritten to npu: {on_disk!r}"
    assert not [c for c in fake.calls if c[0] in ("load", "swap", "unload")], (
        f"gpu->npu must not bounce the embed slot: {fake.calls}"
    )


async def test_embed_npu_to_gpu_zeroes_anchor_toggle_and_loads(
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager],
    tmp_hal0_home: str,
) -> None:
    """Case 5: embed npu→gpu-vulkan → anchor [npu] embed=false AND gpu path DOES load/swap."""
    orch, fake = npu_orchestrator
    home = Path(tmp_hal0_home)
    _write_embed_slot(home, device="flm", slot_type="embedding")
    _write_caps(
        home,
        slot="embed",
        child="embed",
        fields={
            "backend": "npu",
            "provider": "flm",
            "model": "nomic-embed-text-v1.5-q8_0",
            "enabled": True,
        },
    )

    await orch.apply(
        "embed",
        "embed",
        {
            "enabled": True,
            "backend": "gpu-vulkan",
            "provider": "llama-server",
            "model": "nomic-embed-text-v1.5-q8_0",
        },
    )

    # Leaving NPU must drop embed from the anchor's [npu] toggle.
    assert _anchor_npu_writes(fake, {"embed": False}), (
        f"leaving npu did not write anchor [npu] embed=false: {fake.calls}"
    )
    # The gpu path runs the standard lifecycle (device/model changed → swap, or load).
    assert [c for c in fake.calls if c[0] in ("load", "swap")], (
        f"gpu-vulkan target must run the standard load/swap path: {fake.calls}"
    )


async def test_npu_embed_anchor_offline_still_pending(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 9: anchor offline → pending_reload True, NO restart."""
    monkeypatch.setattr(
        CapabilityOrchestrator,
        "_validate_model_in_catalog",
        lambda self, slot, child, model_id, backend_id: None,
    )
    fake = FakeSlotManager()
    # No anchor record at all → "offline".
    fake.set_configs([])
    orch = CapabilityOrchestrator(slot_manager=fake)
    home = Path(tmp_hal0_home)
    _write_embed_slot(home, device="flm", slot_type="embedding")
    _write_caps(
        home,
        slot="embed",
        child="embed",
        fields={
            "backend": "npu",
            "provider": "flm",
            "model": "nomic-embed-text-v1.5-q8_0",
            "enabled": False,
        },
    )

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

    assert result.get("pending_reload") is True
    assert not [c for c in fake.calls if c[0] == "restart"], (
        f"anchor must never be eagerly restarted: {fake.calls}"
    )


async def test_npu_embed_existing_slot_without_type_gets_typed(
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager],
    tmp_hal0_home: str,
) -> None:
    """A pre-existing embed slot with NO ``type`` must be stamped on the npu path.

    The real production drift shape (the original ``drifted_state`` fixture)
    has an ``embed.toml`` with no ``type`` key. ``_ensure_slot_exists_npu``
    early-returns for an existing TOML, so the ``type`` discriminator must be
    written some other way — otherwise ``v1._is_npu_trio_request`` never
    matches (``cfg.get("type") != "embedding"``) and trio dispatch silently
    no-ops, violating the hard constraint that the npu path leaves a
    ``device=npu, type=embedding`` record in force.
    """
    orch, fake = npu_orchestrator
    home = Path(tmp_hal0_home)
    # Existing slot, NO type (matches the production drift shape).
    _write_embed_slot(home, device="vulkan", slot_type=None)
    _write_caps(
        home,
        slot="embed",
        child="embed",
        fields={
            "backend": "npu",
            "provider": "flm",
            "model": "nomic-embed-text-v1.5-q8_0",
            "enabled": False,
        },
    )

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

    # The embed slot record must carry type=embedding after the apply, via
    # one of the update_config writes on the slot.
    type_writes = [
        c
        for c in fake.calls
        if c[0] == "update_config"
        and c[1] == "embed"
        and c[2]["updates"].get("type") == "embedding"
    ]
    assert type_writes, (
        "embed slot was never stamped with type=embedding on the npu path; "
        f"trio dispatch would silently no-op. calls: {fake.calls}"
    )
    # Decision 4: the write that carries type must NOT carry a nested model
    # (nested dicts are replaced wholesale by the shallow merge).
    for c in type_writes:
        assert "model" not in c[2]["updates"], (
            f"type write must not carry model (Decision 4): {c[2]['updates']!r}"
        )


async def test_npu_embed_enable_container_anchor_without_external_runtime(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase E (#687): the trio fork engages with NO external runtime client.

    A container NPU anchor (profile=flm) plus a device=npu embed enable
    must take the trio path — ``[npu]`` TOML toggle via update_config on the
    anchor, pending_reload=True, zero load/swap/unload on the embed slot —
    with the container path as the only wiring.
    """
    monkeypatch.setattr(
        CapabilityOrchestrator,
        "_validate_model_in_catalog",
        lambda self, slot, child, model_id, backend_id: None,
    )
    fake = FakeSlotManager()
    fake.set_configs(
        [
            {
                "name": "npu",
                "type": "llm",
                "device": "npu",
                "profile": "flm",
                "enabled": True,
            }
        ]
    )
    orch = CapabilityOrchestrator(slot_manager=fake)
    home = Path(tmp_hal0_home)
    _write_embed_slot(home, device="flm", slot_type="embedding")
    _write_caps(
        home,
        slot="embed",
        child="embed",
        fields={
            "backend": "npu",
            "provider": "flm",
            "model": "nomic-embed-text-v1.5-q8_0",
            "enabled": False,
        },
    )

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

    npu_writes = [
        c
        for c in fake.calls
        if c[0] == "update_config" and c[1] == "npu" and c[2]["updates"] == {"npu": {"embed": True}}
    ]
    assert npu_writes, (
        "container anchor [npu] toggle was never written — the #687 gate "
        f"is still active. calls: {fake.calls}"
    )
    assert result.get("pending_reload") is True, result
    assert not [c for c in fake.calls if c[0] in ("load", "swap", "unload")], (
        f"NPU embed path must not bounce the slot: {fake.calls}"
    )
