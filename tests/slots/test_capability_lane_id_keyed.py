"""The capability lane must resolve slots bilingually (issues #1643 / #1664).

After the supported ``hal0 slot migrate-id-keying`` migration a slot's TOML
lives at ``slots/<id>.toml`` with its display name in the body. Every lane that
addressed the file as ``slots/<name>.toml`` silently missed it:

  * :meth:`hal0.slot_config.SlotConfigStore._slot_path` — a capability
    **disable** read ``None`` for the slot, so ``_reconciled_slot`` returned
    ``None``, ``[model].default`` was never cleared, and the slot stayed bound
    and routable while the dashboard rendered the capability as off;
  * :meth:`CapabilityOrchestrator._ensure_slot_exists` /
    ``_ensure_slot_exists_npu`` — the "does the slot already exist?" probe
    missed, so an **enable** fell through to ``SlotManager.create``, whose
    bilingual clobber guard raised ``slot 'x' already exists`` → 503;
  * :func:`hal0.slots.npu.trio.reconcile_trio_slots` — the shadow probe missed,
    so step 2 (structural normalization) was skipped on every boot and step 3
    re-attempted ``create`` into the same guard (#1664).

All four resolve through the ONE canonical seam,
:func:`hal0.slots.layout.resolve_slot_stem`, which the stacks lane (#1510)
already uses. These tests build a genuinely id-keyed HAL0_HOME — via the real
:func:`hal0.slots.migrate_id_keying.migrate_slot_id_keying` where a lifecycle is
involved — and drive the production classes, no mocks.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.config import CapabilitySelection
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.ports.authority import PortAuthority
from hal0.slot_config import SlotConfigStore, SlotSelection
from hal0.slots.identity import SlotIdentityStore
from hal0.slots.manager import SlotManager
from hal0.slots.migrate_id_keying import RecordingSlotArtifactOps, migrate_slot_id_keying
from hal0.slots.routing import loaded_slot_from_config

# ── helpers ──────────────────────────────────────────────────────────────────


def _etc(home: str | Path) -> Path:
    return Path(home) / "etc" / "hal0"


def _slots_dir(home: str | Path) -> Path:
    d = _etc(home) / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _data_dir(home: str | Path) -> Path:
    d = Path(home) / "var" / "lib" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _read(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _id_keyed_embed(home: str | Path, *, slot_id: int = 12, model: str = "nomic-embed:v1") -> Path:
    """An id-keyed ``embed`` slot: ``12.toml`` carrying ``name = "embed"``."""
    return _write(
        _slots_dir(home) / f"{slot_id}.toml",
        [
            f"id = {slot_id}",
            'name = "embed"',
            'type = "embedding"',
            "port = 8082",
            'device = "gpu-vulkan"',
            'provider = "llama-server"',
            "[model]",
            f'default = "{model}"',
        ],
    )


def _caps(home: str | Path, *, enabled: bool = True) -> Path:
    return _write(
        _etc(home) / "capabilities.toml",
        [
            "schema_version = 2",
            "[selections.embed.embed]",
            'device = "gpu-vulkan"',
            'provider = "llama-server"',
            'model = "nomic-embed:v1"',
            f"enabled = {str(enabled).lower()}",
        ],
    )


def _selection(*, enabled: bool, model: str = "nomic-embed:v1") -> SlotSelection:
    return SlotSelection(
        slot="embed",
        child="embed",
        slot_name="embed",
        selection=CapabilitySelection(
            device="gpu-vulkan",
            provider="llama-server",
            model=model,
            enabled=enabled,
        ),
    )


def _manager(home: str | Path) -> SlotManager:
    db = Path(home) / "hal0.db"
    return SlotManager(
        identity_store=SlotIdentityStore(db_path=db),
        port_authority=PortAuthority(pool=(8081, 8200), db_path=db),
    )


# ── (a) SlotConfigStore: disable must actually unbind the id-keyed slot ──────


def test_disable_clears_model_on_id_keyed_slot(tmp_hal0_home: str) -> None:
    id_path = _id_keyed_embed(tmp_hal0_home)
    _caps(tmp_hal0_home)

    store = SlotConfigStore()
    store.apply_and_commit(_selection(enabled=False))

    raw = _read(id_path)
    assert raw["model"]["default"] == "", "disable must clear [model].default on <id>.toml"
    # …and must NOT fabricate a name-keyed sibling (that would duplicate the slot).
    assert not (_slots_dir(tmp_hal0_home) / "embed.toml").exists()


def test_disabled_id_keyed_slot_is_not_routable(tmp_hal0_home: str) -> None:
    """The operator-visible consequence: the router stops resolving the slot."""
    id_path = _id_keyed_embed(tmp_hal0_home)
    _caps(tmp_hal0_home)
    assert loaded_slot_from_config(_read(id_path)) is not None  # bound before

    SlotConfigStore().apply_and_commit(_selection(enabled=False))

    assert loaded_slot_from_config(_read(id_path)) is None


def test_enable_rebinds_model_on_id_keyed_slot(tmp_hal0_home: str) -> None:
    id_path = _id_keyed_embed(tmp_hal0_home, model="")
    _caps(tmp_hal0_home, enabled=False)

    SlotConfigStore().apply_and_commit(_selection(enabled=True, model="nomic-embed:v2"))

    raw = _read(id_path)
    assert raw["model"]["default"] == "nomic-embed:v2"
    assert loaded_slot_from_config(raw) is not None
    assert not (_slots_dir(tmp_hal0_home) / "embed.toml").exists()


def test_name_keyed_box_is_unchanged(tmp_hal0_home: str) -> None:
    """HARD INVARIANT: the name-keyed layout resolves exactly as it did."""
    name_path = _write(
        _slots_dir(tmp_hal0_home) / "embed.toml",
        [
            'name = "embed"',
            'type = "embedding"',
            "port = 8082",
            "[model]",
            'default = "nomic-embed:v1"',
        ],
    )
    _caps(tmp_hal0_home)

    SlotConfigStore().apply_and_commit(_selection(enabled=False))

    assert _read(name_path)["model"]["default"] == ""


# ── (b) orchestrator: enable must not 5xx on an id-keyed slot ────────────────


async def test_ensure_slot_exists_is_a_noop_after_id_keying(tmp_hal0_home: str) -> None:
    home = Path(tmp_hal0_home)
    sm = _manager(home)
    await sm.create(
        "tts",
        {
            "name": "tts",
            "type": "tts",
            "device": "cpu",
            "provider": "kokoro",
            "port": 8090,
            "model": {"default": "kokoro:82m"},
        },
    )
    report = migrate_slot_id_keying(
        identity=sm._identity,
        config_dir=_slots_dir(home),
        data_dir=_data_dir(home),
        ops=RecordingSlotArtifactOps(),
    )
    assert report.migrations, "fixture must actually migrate the slot to id-keyed"
    assert not (_slots_dir(home) / "tts.toml").exists()

    orch = CapabilityOrchestrator(_manager(home), config_path=_etc(home) / "capabilities.toml")
    # Pre-fix this raised CapabilityApplyFailed (503) — the name-keyed probe
    # missed, so create() ran and hit its bilingual clobber guard.
    await orch._ensure_slot_exists(
        "tts",
        CapabilitySelection(device="cpu", provider="kokoro", model="kokoro:82m", enabled=True),
    )
    # No duplicate name-keyed record left behind.
    assert not (_slots_dir(home) / "tts.toml").exists()


async def test_ensure_slot_exists_npu_is_a_noop_after_id_keying(tmp_hal0_home: str) -> None:
    home = Path(tmp_hal0_home)
    sm = _manager(home)
    await sm.create(
        "flm-embed",
        {
            "name": "flm-embed",
            "type": "embedding",
            "device": "npu",
            "provider": "flm",
            "port": 8088,
            "model": {"default": "embed-gemma:300m"},
        },
    )
    migrate_slot_id_keying(
        identity=sm._identity,
        config_dir=_slots_dir(home),
        data_dir=_data_dir(home),
        ops=RecordingSlotArtifactOps(),
    )
    assert not (_slots_dir(home) / "flm-embed.toml").exists()

    orch = CapabilityOrchestrator(_manager(home), config_path=_etc(home) / "capabilities.toml")
    await orch._ensure_slot_exists_npu(
        "flm-embed",
        "embed",
        CapabilitySelection(device="npu", provider="flm", model="embed-gemma:300m", enabled=True),
    )
    assert not (_slots_dir(home) / "flm-embed.toml").exists()


async def test_ensure_slot_exists_still_creates_a_genuinely_missing_slot(
    tmp_hal0_home: str,
) -> None:
    """The resolver must not turn creation into a silent no-op."""
    home = Path(tmp_hal0_home)
    _slots_dir(home)
    orch = CapabilityOrchestrator(_manager(home), config_path=_etc(home) / "capabilities.toml")

    await orch._ensure_slot_exists(
        "embed-rerank",
        CapabilitySelection(
            device="cpu", provider="llama-server", model="bge-rerank:v2", enabled=True
        ),
    )
    assert (_slots_dir(home) / "embed-rerank.toml").exists()


# ── (c) trio reconciler: normalize the id-keyed shadow (#1664) ───────────────


async def _npu_box(home: Path, *, shadow: str | None) -> tuple[SlotManager, dict[str, int]]:
    """A migrated, id-keyed box: FLM container anchor + (optionally) one shadow.

    Returns the (fresh, post-migration) manager and the ``name -> slot id`` map,
    exactly the shape ``hal0 slot migrate-id-keying`` leaves behind.
    """
    sm = _manager(home)
    await sm.create(
        "flm",
        {
            "name": "flm",
            "type": "llm",
            "device": "npu",
            "provider": "flm",
            "runtime": "container",
            "profile": "flm",
            "port": 8088,
            "model": {"default": "qwen3:4b"},
            "npu": {"chat": True, "asr": True, "embed": True},
        },
    )
    if shadow is not None:
        await sm.create(
            shadow,
            {
                "name": shadow,
                "device": "npu",
                "provider": "flm",
                # Drifted: wrong port, and no ``type`` gate — exactly what
                # step 2 of the reconciler exists to repair.
                "port": 9999,
                "model": {"default": "operator-choice:v1"},
            },
        )
    report = migrate_slot_id_keying(
        identity=sm._identity,
        config_dir=_slots_dir(home),
        data_dir=_data_dir(home),
        ops=RecordingSlotArtifactOps(),
    )
    ids = {m.name: m.slot_id for m in report.migrations}
    assert "flm" in ids, "fixture must actually migrate the anchor to id-keyed"
    return _manager(home), ids


@pytest.mark.usefixtures("container_stub")
async def test_trio_reconcile_normalizes_id_keyed_shadow(tmp_hal0_home: str) -> None:
    home = Path(tmp_hal0_home)
    sm, ids = await _npu_box(home, shadow="flm-stt")
    shadow_path = _slots_dir(home) / f"{ids['flm-stt']}.toml"

    changed = await sm.reconcile_npu_trio_slots()

    raw = _read(shadow_path)
    assert raw["type"] == "transcription", "step 2 normalization must repair the drifted shadow"
    assert raw["port"] == 8088
    assert raw["served_by"] == "flm"
    assert raw["profile"] == "flm"
    # Repaired in place — no duplicate name-keyed shadow record.
    assert not (_slots_dir(home) / "flm-stt.toml").exists()
    # The missing embed shadow is still created (name-keyed: it has no id yet).
    assert (_slots_dir(home) / "flm-embed.toml").exists()
    assert changed == 2


@pytest.mark.usefixtures("container_stub")
async def test_trio_reconcile_renames_id_keyed_legacy_shadow_in_place(tmp_hal0_home: str) -> None:
    home = Path(tmp_hal0_home)
    sm, ids = await _npu_box(home, shadow="stt-npu")
    legacy_path = _slots_dir(home) / f"{ids['stt-npu']}.toml"

    await sm.reconcile_npu_trio_slots()

    # The legacy → canon rename must keep the id-keyed stem (renaming a slot
    # is a relabel, not a re-key) and preserve the operator's model.
    raw = _read(legacy_path)
    assert raw["name"] == "flm-stt"
    assert raw["type"] == "transcription"
    assert raw["model"]["default"] == "operator-choice:v1"
    assert not (_slots_dir(home) / "flm-stt.toml").exists()
    assert not (_slots_dir(home) / "stt-npu.toml").exists()
    # …and the identity row must move WITH the label, or the shadow is
    # unresolvable by its new name (a bare file move would strand the row).
    assert sm._identity.get_by_name("stt-npu") is None
    row = sm._identity.get_by_name("flm-stt")
    assert row is not None and row.id == ids["stt-npu"]
    assert await sm.status("flm-stt") is not None
