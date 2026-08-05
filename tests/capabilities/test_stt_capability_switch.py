"""voice.stt capability switch — Moonshine (CPU) vs the FLM trio (NPU).

Mirrors ``tests/capabilities/test_tts_capability_switch.py``: STT is a
device-keyed engine switch WITHIN the single ``stt`` slot, exactly like
voice.tts (cpu -> kokoro / gpu -> qwen3-tts). Three surfaces are exercised
here:

  - the catalog enumerates the CPU Moonshine engine for voice.stt
    (``_stt_rows_for_capability``, wired into ``_flat_rows_for_capability``
    the same way ``_tts_rows_for_capability`` is),
  - the apply path (``SlotConfigStore._reconciled_slot`` /
    ``_engine_profile_for``) rewrites the single ``stt`` slot's ``profile``
    + ``provider`` to match the picked engine — the SAME mechanism the tts
    engine switch uses, generalised off ``child in {"tts", "stt"}``,
  - selecting ``npu`` drives the FLM trio (no standalone slot spawn) exactly
    like voice.embed's NPU Phase 2 path — see
    ``tests/capabilities/test_npu_phase2_integration.py`` and
    ``tests/capabilities/test_catalog_npu_stt.py`` for the pre-existing NPU
    fan-out/apply contracts this must not contradict.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.catalog import models_for_capability
from hal0.capabilities.config import load_capabilities_config
from hal0.capabilities.orchestrator import CapabilityOrchestrator

# ── catalog enumeration ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_flm_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the stt catalog's NPU fan-out deterministic (empty) in these tests.

    ``stt`` is in ``_NPU_FANOUT_CAPS`` so ``models_for_capability("stt")``
    always calls ``flm_served_models()``, which shells a container runtime
    probe absent a mock. Real NPU/FLM fan-out behaviour is covered by
    ``test_catalog_npu_stt.py``; here we only care about the Moonshine
    CPU row, so pin the FLM side to empty for isolation and speed.
    """
    monkeypatch.setattr("hal0.providers.flm.flm_served_models", lambda: [])


def test_voice_stt_catalog_includes_moonshine_engine(tmp_hal0_home: str) -> None:
    rows = models_for_capability("stt", registry=None)
    moonshine = next((r for r in rows if r["id"] == "moonshine-base-en"), None)
    assert moonshine is not None, f"moonshine engine missing from voice.stt catalog: {rows!r}"
    backends = {b["id"]: b for b in moonshine["backends"]}
    assert "cpu" in backends, f"moonshine row carries no cpu backend: {moonshine!r}"
    cpu = backends["cpu"]
    assert cpu["provider"] == "moonshine"
    # The CPU engine resolves the Moonshine profile, not a llama profile.
    assert cpu["runtime_family"] == "moonshine"
    assert cpu["profile"] == "moonshine"
    assert cpu["pullable"] is False, "moonshine weights are operator-staged, not pullable"


def test_voice_stt_catalog_moonshine_not_offered_on_gpu(tmp_hal0_home: str) -> None:
    """Regression: hal0 ships no GPU STT engine — moonshine must not fan out there."""
    rows = models_for_capability("stt", registry=None)
    moonshine = next((r for r in rows if r["id"] == "moonshine-base-en"), None)
    assert moonshine is not None
    backends = {b["id"] for b in moonshine["backends"]}
    assert "gpu-vulkan" not in backends
    assert "gpu-rocm" not in backends


# ── apply path: the engine swap lands in the single ``stt`` slot ─────────────


def _read_stt_slot(home: str) -> dict[str, Any]:
    with open(Path(home) / "etc" / "hal0" / "slots" / "stt.toml", "rb") as f:
        return tomllib.load(f)


def _write_stt_slot(home: str, *, device: str, profile: str, provider: str = "moonshine") -> None:
    """Write the canonical single ``stt`` slot in a known engine state."""
    slots_dir = Path(home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "stt.toml").write_text(
        "\n".join(
            [
                'name = "stt"',
                'type = "transcription"',
                f'device = "{device}"',
                'runtime = "container"',
                f'profile = "{profile}"',
                f'provider = "{provider}"',
                "port = 8084",
                "[model]",
                'default = "moonshine-base-en"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_apply_selecting_cpu_moonshine_writes_profile_and_provider(
    tmp_hal0_home: str,
) -> None:
    """Picking cpu/moonshine writes profile=moonshine + provider=moonshine
    into the SAME ``stt`` slot TOML — no move, no new slot file.
    """
    from hal0.capabilities.config import CapabilitySelection
    from hal0.slot_config import SlotConfigStore, SlotSelection

    _write_stt_slot(tmp_hal0_home, device="cpu", profile="", provider="")
    store = SlotConfigStore()
    selection = CapabilitySelection.model_validate(
        {
            "device": "cpu",
            "provider": "moonshine",
            "model": "moonshine-base-en",
            "enabled": True,
        }
    )
    cs = store.apply(SlotSelection(slot="voice", child="stt", slot_name="stt", selection=selection))
    store.commit(cs)

    on_disk = _read_stt_slot(tmp_hal0_home)
    assert on_disk["profile"] == "moonshine", f"engine profile not stamped: {on_disk!r}"
    assert on_disk["provider"] == "moonshine", on_disk
    assert on_disk["device"] == "cpu", on_disk
    assert on_disk["model"]["default"] == "moonshine-base-en", on_disk
    assert on_disk["name"] == "stt", "slot must not move/rename"
    # Only one slot file exists — the selection reconciled in place.
    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    assert [p.name for p in slots_dir.glob("*.toml")] == ["stt.toml"]


def test_apply_disabled_stt_selection_does_not_rewrite_engine(tmp_hal0_home: str) -> None:
    """A disabled selection clears the model but never rewrites profile/device/provider."""
    from hal0.capabilities.config import CapabilitySelection
    from hal0.slot_config import SlotConfigStore, SlotSelection

    _write_stt_slot(tmp_hal0_home, device="cpu", profile="moonshine")
    before = _read_stt_slot(tmp_hal0_home)
    store = SlotConfigStore()
    selection = CapabilitySelection.model_validate(
        {
            "device": "cpu",
            "provider": "moonshine",
            "model": "moonshine-base-en",
            "enabled": False,
        }
    )
    cs = store.apply(SlotSelection(slot="voice", child="stt", slot_name="stt", selection=selection))
    store.commit(cs)
    after = _read_stt_slot(tmp_hal0_home)
    assert after["model"]["default"] == "", "disable must clear [model].default"
    assert {k: v for k, v in after.items() if k != "model"} == {
        k: v for k, v in before.items() if k != "model"
    }, "disabled selection rewrote an engine field"


def test_apply_non_stt_child_never_gets_moonshine_profile(tmp_hal0_home: str) -> None:
    """Regression: the stt engine-profile derivation must be gated on the stt
    child only — an embed apply must never pick up the moonshine profile,
    and a tts apply must keep resolving its own (kokoro/qwen3-tts) engine,
    not moonshine.
    """
    from hal0.capabilities.config import CapabilitySelection
    from hal0.slot_config import SlotConfigStore, SlotSelection

    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "embed.toml").write_text(
        "\n".join(
            [
                'name = "embed"',
                "port = 8082",
                'device = "gpu-vulkan"',
                'provider = "llama-server"',
                "[model]",
                'default = "nomic-embed-text-v1.5-q8_0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    store = SlotConfigStore()
    selection = CapabilitySelection.model_validate(
        {
            "device": "cpu",
            "provider": "llama-server",
            "model": "nomic-embed-text-v1.5-q8_0",
            "enabled": True,
        }
    )
    cs = store.apply(
        SlotSelection(slot="embed", child="embed", slot_name="embed", selection=selection)
    )
    store.commit(cs)
    with open(slots_dir / "embed.toml", "rb") as f:
        on_disk = tomllib.load(f)
    assert "profile" not in on_disk, f"embed slot must not gain a profile: {on_disk!r}"
    assert on_disk.get("provider") != "moonshine"

    # tts must still resolve its OWN engine, never moonshine leaking across
    # capabilities.
    (slots_dir / "tts.toml").write_text(
        "\n".join(
            [
                'name = "tts"',
                'type = "tts"',
                'device = "cpu"',
                'runtime = "container"',
                'profile = "kokoro"',
                "port = 8085",
                "[model]",
                'default = "kokoro-v1"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    tts_selection = CapabilitySelection.model_validate(
        {"device": "cpu", "provider": "kokoro", "model": "kokoro-v1", "enabled": True}
    )
    cs = store.apply(
        SlotSelection(slot="voice", child="tts", slot_name="tts", selection=tts_selection)
    )
    store.commit(cs)
    with open(slots_dir / "tts.toml", "rb") as f:
        tts_on_disk = tomllib.load(f)
    assert tts_on_disk["profile"] == "kokoro", tts_on_disk
    assert tts_on_disk["provider"] != "moonshine"


# ── NPU trio (NPU Phase 2): device=npu never spawns the shadow stt slot ──────


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


@pytest.fixture(autouse=True)
def _no_spawn_context_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)


async def test_apply_npu_stt_drives_flm_trio_without_slot_spawn(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting npu for voice.stt rewrites to the FLM trio behaviour: an
    anchor [npu] asr=True toggle, a device=npu/type=transcription slot
    RECORD, ZERO load/swap/unload of the stt slot, and pending_reload=True —
    mirrors the embed NPU Phase 2 contract in
    ``test_npu_phase2_integration.py`` adapted for voice.stt.
    """
    monkeypatch.setattr(
        CapabilityOrchestrator,
        "_validate_model_in_catalog",
        lambda self, slot, child, model_id, backend_id: None,
    )
    home = Path(tmp_hal0_home)
    slots_dir = home / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    # Prior state: CPU Moonshine engine already selected and running.
    _write_stt_slot(str(home), device="cpu", profile="moonshine")
    caps_path = home / "etc" / "hal0" / "capabilities.toml"
    caps_path.write_text(
        "\n".join(
            [
                "[selections.voice.stt]",
                'device = "cpu"',
                'provider = "moonshine"',
                'model = "moonshine-base-en"',
                "enabled = true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    fake = FakeSlotManager()
    fake.set_configs(
        [
            {
                "name": "npu",
                "type": "llm",
                "device": "npu",
                "profile": "flm",
                "model": {"default": "gemma3:1b"},
            }
        ]
    )
    orch = CapabilityOrchestrator(slot_manager=fake)

    result = await orch.apply(
        "voice",
        "stt",
        {
            "enabled": True,
            "device": "npu",
            "provider": "flm",
            "model": "whisper-large-v3",
        },
    )

    # 1. Anchor's [npu] asr toggle written.
    npu_writes = [
        c
        for c in fake.calls
        if c[0] == "update_config" and c[1] == "npu" and c[2]["updates"] == {"npu": {"asr": True}}
    ]
    assert npu_writes, f"anchor [npu] asr toggle was never written: {fake.calls}"

    # 2. The stt slot RECORD is stamped type=transcription (no nested model).
    type_writes = [
        c
        for c in fake.calls
        if c[0] == "update_config"
        and c[1] == "stt"
        and c[2]["updates"].get("type") == "transcription"
    ]
    assert type_writes, f"no type write on stt slot: {fake.calls}"
    assert type_writes[-1][2]["updates"] == {"type": "transcription"}, type_writes[-1]

    # 3. ZERO standalone spawn on the stt slot — same slot, no move/recreate
    #    via the lifecycle path.
    assert not [c for c in fake.calls if c[0] in ("load", "swap", "unload")], (
        f"NPU stt path must not bounce the modality slot: {fake.calls}"
    )
    assert not [c for c in fake.calls if c[0] == "create"], (
        f"stt.toml already existed — must reconcile in place, not recreate: {fake.calls}"
    )

    # 4. Anchor never eagerly restarted; pending_reload surfaced.
    assert not [c for c in fake.calls if c[0] == "restart"], fake.calls
    assert result.get("pending_reload") is True

    # 5. Persisted selection reflects device=npu + enabled; slot name unchanged.
    assert result["slot"] == "stt"
    persisted = load_capabilities_config(caps_path)
    sel = persisted.selections["voice"]["stt"]
    assert sel.device == "npu"
    assert sel.enabled is True

    # 6. Still exactly one stt slot file — no new/renamed file appeared.
    assert [p.name for p in slots_dir.glob("*.toml")] == ["stt.toml"]


async def test_apply_gpu_device_for_stt_is_a_typed_error(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hal0 ships no GPU STT engine — a gpu device selection for voice.stt
    must fail with a typed BadRequest (capability.no_engine_for_device), not
    fall through to writing a llama profile into the stt slot."""
    from hal0.errors import BadRequest

    _write_stt_slot(tmp_hal0_home, device="cpu", profile="moonshine")
    fake = FakeSlotManager()
    orch = CapabilityOrchestrator(slot_manager=fake)

    for device in ("gpu-vulkan", "gpu-rocm"):
        with pytest.raises(BadRequest) as exc_info:
            await orch.apply("voice", "stt", {"device": device, "enabled": True})
        assert exc_info.value.code == "capability.no_engine_for_device"
        assert device in str(exc_info.value)

    # The slot on disk is untouched — the reject happened before any write.
    on_disk = _read_stt_slot(tmp_hal0_home)
    assert on_disk["profile"] == "moonshine"
    assert on_disk["device"] == "cpu"


def test_stt_engine_id_is_the_served_non_streaming_id(tmp_hal0_home: str) -> None:
    """The picker must advertise the id the server actually serves.

    ``moonshine-small-streaming-en`` (the HaloaiModel seed id) names a
    streaming bundle the toolbox image cannot load — offering it would be a
    pick that never serves a request. The server advertises
    ``moonshine-<arch>-en`` for the loadable non-streaming bundle.
    """
    rows = models_for_capability("stt", registry=None)
    ids = {r["id"] for r in rows}
    assert "moonshine-base-en" in ids
    assert "moonshine-small-streaming-en" not in ids
