"""Tests for SlotManager.reconcile_npu_trio_slots (FLM-trio shadow canon).

The reconcile pass keeps the ``flm-stt`` / ``flm-embed`` shadow records
coherent on every API start: it renames legacy ``stt-npu`` / ``embed-npu``
TOMLs to ``{anchor}-stt`` / ``{anchor}-embed``, normalizes the coresident
structural fields (device=npu, profile=flm, served_by=<anchor>, port=<anchor
port>, type), and seeds any missing shadow with ``enabled`` mirroring the
anchor's ``[npu]`` toggle.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager

pytestmark = pytest.mark.usefixtures("container_stub")


def _slots_dir(tmp_hal0_home: str) -> Path:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write(root: Path, name: str, lines: list[str]) -> Path:
    path = root / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _anchor(root: Path, *, asr: bool = True, embed: bool = True, name: str = "flm") -> None:
    _write(
        root,
        name,
        [
            f'name = "{name}"',
            'type = "llm"',
            'device = "npu"',
            'backend = "flm"',
            'runtime = "container"',
            'profile = "flm"',
            "port = 8088",
            "[model]",
            'default = "qwen3:4b"',
            "[npu]",
            "chat = true",
            f"asr = {str(asr).lower()}",
            f"embed = {str(embed).lower()}",
        ],
    )


async def test_creates_missing_shadows_mirroring_anchor_toggles(tmp_hal0_home: str) -> None:
    root = _slots_dir(tmp_hal0_home)
    _anchor(root, asr=True, embed=False)

    sm = SlotManager()
    changed = await sm.reconcile_npu_trio_slots()

    assert changed == 2
    stt = tomllib.loads((root / "flm-stt.toml").read_text())
    embed = tomllib.loads((root / "flm-embed.toml").read_text())
    # Structural coresident shape.
    for cfg, slot_type in ((stt, "transcription"), (embed, "embedding")):
        assert cfg["device"] == "npu"
        assert cfg["profile"] == "flm"
        assert cfg["served_by"] == "flm"
        assert cfg["port"] == 8088
        assert cfg["type"] == slot_type
    # enabled mirrors the anchor's [npu] toggle (asr on, embed off).
    assert stt["enabled"] is True
    assert embed["enabled"] is False


async def test_renames_and_normalizes_legacy_shadow(tmp_hal0_home: str) -> None:
    root = _slots_dir(tmp_hal0_home)
    _anchor(root)
    # Legacy stt-npu with a distinct (wrong) port and no profile/served_by,
    # plus an operator-set enabled + model that must survive the rename.
    _write(
        root,
        "stt-npu",
        [
            'name = "stt-npu"',
            'device = "npu"',
            'type = "transcription"',
            "port = 8084",
            "enabled = true",
            "[model]",
            'default = "whisper-v3:turbo"',
        ],
    )

    sm = SlotManager()
    await sm.reconcile_npu_trio_slots()

    assert not (root / "stt-npu.toml").exists()
    stt = tomllib.loads((root / "flm-stt.toml").read_text())
    assert stt["name"] == "flm-stt"
    assert stt["device"] == "npu"
    assert stt["profile"] == "flm"
    assert stt["served_by"] == "flm"
    assert stt["port"] == 8088  # normalized to the anchor port
    assert stt["type"] == "transcription"
    # Operator state preserved across the rename.
    assert stt["enabled"] is True
    assert stt["model"]["default"] == "whisper-v3:turbo"


async def test_rename_skipped_when_canon_exists(tmp_hal0_home: str) -> None:
    root = _slots_dir(tmp_hal0_home)
    _anchor(root)
    _write(
        root,
        "stt-npu",
        ['name = "stt-npu"', 'device = "npu"', 'type = "transcription"', "port = 8084"],
    )
    _write(
        root,
        "flm-stt",
        [
            'name = "flm-stt"',
            'device = "npu"',
            'type = "transcription"',
            'profile = "flm"',
            'served_by = "flm"',
            "port = 8088",
            "enabled = false",
            "[model]",
            'default = "whisper-v3:turbo"',
        ],
    )

    sm = SlotManager()
    await sm.reconcile_npu_trio_slots()

    # Both remain — the legacy file is left in place, never clobbering canon.
    assert (root / "stt-npu.toml").exists()
    assert (root / "flm-stt.toml").exists()


async def test_noop_without_container_npu_anchor(tmp_hal0_home: str) -> None:
    root = _slots_dir(tmp_hal0_home)
    # device=npu type=llm but NO profile / runtime=container → not an FLM
    # container anchor (is_container_npu_cfg False).
    _write(
        root,
        "agent",
        ['name = "agent"', 'device = "npu"', 'type = "llm"', "port = 8082", "enabled = true"],
    )

    sm = SlotManager()
    changed = await sm.reconcile_npu_trio_slots()

    assert changed == 0
    assert not (root / "flm-stt.toml").exists()
    assert not (root / "flm-embed.toml").exists()


async def test_idempotent_on_second_run(tmp_hal0_home: str) -> None:
    root = _slots_dir(tmp_hal0_home)
    _anchor(root)

    sm = SlotManager()
    first = await sm.reconcile_npu_trio_slots()
    second = await sm.reconcile_npu_trio_slots()

    assert first == 2
    assert second == 0  # nothing left to change
