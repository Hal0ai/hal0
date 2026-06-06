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


class FakeLemonadeClient:
    """Records ``internal_config`` reads + ``internal_set`` writes.

    Mirrors :class:`hal0.lemonade.client.LemonadeClient` for the two
    methods the orchestrator's NPU-trio path uses. ``flm_args`` starts at
    whatever ``initial_flm_args`` is passed (the trio anchor's current
    args); ``internal_set`` merges the patch so a later ``internal_config``
    reflects it.
    """

    def __init__(self, initial_flm_args: str = "") -> None:
        self._config: dict[str, Any] = {"flm_args": initial_flm_args}
        self.set_calls: list[dict[str, Any]] = []

    async def internal_config(self) -> dict[str, Any]:
        return dict(self._config)

    async def internal_set(self, values: dict[str, Any]) -> dict[str, Any]:
        self.set_calls.append(dict(values))
        self._config.update(values)
        return dict(self._config)


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


# ── Step 2: _recompose_flm_args (Case 6) ──────────────────────────────────────


def test_recompose_flm_args_empty_embed_enable() -> None:
    """Empty current args + (embed, True) → both trio flags explicit, embed=1."""
    from hal0.capabilities.orchestrator import _recompose_flm_args

    out = _recompose_flm_args("", "embed", True)
    assert "--embed 1" in out
    assert "--asr 0" in out  # absent → appended explicit 0


def test_recompose_flm_args_preserves_unrecognized_flags() -> None:
    """Decision 2: only the targeted trio flag is touched; others verbatim."""
    from hal0.capabilities.orchestrator import _recompose_flm_args

    out = _recompose_flm_args("--threads 8 --asr 1 --embed 0", "embed", True)
    assert "--threads 8" in out, f"unrecognized flag dropped: {out!r}"
    assert "--embed 1" in out, f"embed not flipped on: {out!r}"
    assert "--asr 1" in out, f"asr clobbered: {out!r}"


def test_recompose_flm_args_stt_sets_asr() -> None:
    """child=stt maps to --asr (not --embed)."""
    from hal0.capabilities.orchestrator import _recompose_flm_args

    out = _recompose_flm_args("--embed 1", "stt", True)
    assert "--asr 1" in out
    assert "--embed 1" in out  # preserved


def test_recompose_flm_args_disable_emits_explicit_zero() -> None:
    """Disabling a modality emits the explicit 0 form, keeps the other flag."""
    from hal0.capabilities.orchestrator import _recompose_flm_args

    out = _recompose_flm_args("--asr 1 --embed 1", "embed", False)
    assert "--embed 0" in out
    assert "--asr 1" in out


# ── Step 4: test stubs (FakeSlotManager.iter_configs/restart, FakeLemonadeClient)


async def test_fake_slot_manager_iter_configs_roundtrip() -> None:
    fake = FakeSlotManager()
    fake.set_configs([{"name": "agent", "type": "llm", "device": "npu", "enabled": True}])
    configs = await fake.iter_configs()
    assert configs == [{"name": "agent", "type": "llm", "device": "npu", "enabled": True}]


async def test_fake_lemonade_client_records_set() -> None:
    client = FakeLemonadeClient(initial_flm_args="--asr 1 --embed 1")
    cfg = await client.internal_config()
    assert cfg["flm_args"] == "--asr 1 --embed 1"
    await client.internal_set({"flm_args": "--asr 1 --embed 0"})
    assert client.set_calls[-1] == {"flm_args": "--asr 1 --embed 0"}
    assert (await client.internal_config())["flm_args"] == "--asr 1 --embed 0"


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
) -> tuple[CapabilityOrchestrator, FakeSlotManager, FakeLemonadeClient]:
    """Orchestrator wired with a FakeLemonadeClient — engages the trio fork.

    Bypasses catalog validation (downstream of the path under test). The
    anchor is seeded live (type=llm,device=npu) with the FLM child already
    serving chat+asr (``--asr 1 --embed 0``) so a Case-1 embed-enable
    yields the exact ``--asr 1 --embed 1``.
    """
    monkeypatch.setattr(
        CapabilityOrchestrator,
        "_validate_model_in_catalog",
        lambda self, slot, child, model_id, backend_id: None,
    )
    client = FakeLemonadeClient(initial_flm_args="--asr 1 --embed 0")
    fake = FakeSlotManager()
    fake.set_configs([{"name": "agent", "type": "llm", "device": "npu", "enabled": True}])
    orch = CapabilityOrchestrator(slot_manager=fake, lemonade_provider=lambda: client)
    return orch, fake, client


async def test_npu_embed_enable_sets_flm_args_no_load(
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager, FakeLemonadeClient],
    tmp_hal0_home: str,
) -> None:
    """Case 1: NPU embed enable → flm_args embed=1, update_config enabled, NO load."""
    orch, fake, client = npu_orchestrator
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

    assert client.set_calls, "flm_args was never set"
    assert client.set_calls[-1] == {"flm_args": "--asr 1 --embed 1"}, client.set_calls
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


async def test_npu_embed_disable_zeroes_flm_args_no_unload(
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager, FakeLemonadeClient],
    tmp_hal0_home: str,
) -> None:
    """Case 2: NPU embed disable → flm_args embed=0, slot enabled=False, NO unload."""
    orch, fake, client = npu_orchestrator
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

    assert client.set_calls[-1] == {"flm_args": "--asr 1 --embed 0"}, client.set_calls
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
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager, FakeLemonadeClient],
    tmp_hal0_home: str,
) -> None:
    """Case 3: NPU stt enable → flm_args asr=1, no standalone load."""
    orch, fake, client = npu_orchestrator
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

    assert client.set_calls, "flm_args was never set for stt"
    last = client.set_calls[-1]["flm_args"]
    assert "--asr 1" in last, f"stt enable did not set asr=1: {last!r}"
    assert not [c for c in fake.calls if c[0] in ("load", "swap", "unload")], (
        f"NPU stt path must not bounce a slot: {fake.calls}"
    )
    # Created stt slot stamps type=transcription.
    create_calls = [c for c in fake.calls if c[0] == "create" and c[1] == "stt"]
    assert create_calls, f"stt slot not created: {fake.calls}"
    assert create_calls[-1][2]["cfg"].get("type") == "transcription"


async def test_embed_gpu_to_npu_no_load(
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager, FakeLemonadeClient],
    tmp_hal0_home: str,
) -> None:
    """Case 4: embed gpu-vulkan→npu → flm_args embed=1, device=npu, NO load/swap."""
    orch, fake, client = npu_orchestrator
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

    assert "--embed 1" in client.set_calls[-1]["flm_args"], client.set_calls
    # device rewritten to npu on the slot TOML.
    dev_writes = [
        c
        for c in fake.calls
        if c[0] == "update_config" and c[1] == "embed" and c[2]["updates"].get("device") == "npu"
    ]
    assert dev_writes, f"device not rewritten to npu: {fake.calls}"
    assert not [c for c in fake.calls if c[0] in ("load", "swap", "unload")], (
        f"gpu->npu must not bounce the embed slot: {fake.calls}"
    )


async def test_embed_npu_to_gpu_zeroes_flm_and_loads(
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager, FakeLemonadeClient],
    tmp_hal0_home: str,
) -> None:
    """Case 5: embed npu→gpu-vulkan → flm_args embed=0 AND gpu path DOES load/swap."""
    orch, fake, client = npu_orchestrator
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

    # Leaving NPU must drop embed from the anchor's flm_args.
    assert client.set_calls, "leaving npu did not touch flm_args"
    assert "--embed 0" in client.set_calls[-1]["flm_args"], client.set_calls
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
    client = FakeLemonadeClient(initial_flm_args="--asr 1 --embed 0")
    fake = FakeSlotManager()
    # No anchor record at all → "offline".
    fake.set_configs([])
    orch = CapabilityOrchestrator(slot_manager=fake, lemonade_provider=lambda: client)
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
    npu_orchestrator: tuple[CapabilityOrchestrator, FakeSlotManager, FakeLemonadeClient],
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
    orch, fake, _client = npu_orchestrator
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
