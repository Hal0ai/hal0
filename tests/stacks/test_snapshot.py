"""Tests for snapshot-from-live: read slots + capabilities → a StackConfig.

Targeted file run:
    cd /home/halo/dev/wt/stacks-export && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_snapshot.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.registry.store import ModelRegistry
from hal0.stacks.portable import snapshot_live_stack


@pytest.fixture
def reg(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_dir=tmp_path / "registry")


def _write_slot(home: str, name: str, body: list[str]) -> None:
    d = Path(home) / "etc" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    # Wrap slot-level properties in [slot] section for loader compatibility
    lines: list[str] = ["[slot]"]
    for line in body:
        if line.startswith("["):
            # Section header—write accumulated slot lines, then the new section
            lines.append("")
            lines.append(line)
        else:
            lines.append(line)
    (d / f"{name}.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_caps(home: str, text: str) -> None:
    p = Path(home) / "etc" / "hal0" / "capabilities.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class TestSnapshot:
    def test_captures_primary_slot(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        _write_slot(
            tmp_hal0_home,
            "agent",
            [
                'name = "agent"',
                "port = 8087",
                'device = "gpu-rocm"',
                'provider = "llama-server"',
                "[model]",
                'default = "ace-saber"',
            ],
        )
        stack = snapshot_live_stack(registry=reg, name="Live")
        agent = next(e for e in stack.slots if e.slot == "agent")
        assert agent.model == "ace-saber"
        assert agent.device == "gpu-rocm"
        assert stack.name == "Live"

    def test_captures_flat_shape_slot(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        # The live runtime writes the FLAT slot shape (no [slot] section). The
        # strict load_slot_config mishandles it (drops port, raises) which
        # silently emptied every live snapshot — regression guard for that.
        from pathlib import Path

        d = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
        d.mkdir(parents=True, exist_ok=True)
        (d / "agent.toml").write_text(
            'name = "agent"\n'
            'type = "llm"\n'
            'device = "gpu-rocm"\n'
            'backend = "rocm"\n'
            'profile = "rocm"\n'
            "port = 8082\n"
            "mtp = true\n\n"
            "[model]\n"
            'default = "ace-saber"\n',
            encoding="utf-8",
        )
        stack = snapshot_live_stack(registry=reg, name="Live")
        agent = next(e for e in stack.slots if e.slot == "agent")
        assert agent.model == "ace-saber"
        assert agent.device == "gpu-rocm"
        assert agent.profile == "rocm"
        assert agent.mtp is True

    def test_legacy_backend_device_dropped(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        # A non-canonical device (legacy "rocm" backend) would fail
        # StackSlotEntry validation → it's read as None, not raised.
        _write_slot(
            tmp_hal0_home,
            "old",
            ['name = "old"', "port = 8088", 'device = "rocm"', "[model]", 'default = "m"'],
        )
        stack = snapshot_live_stack(registry=reg)
        entry = next(e for e in stack.slots if e.slot == "old")
        assert entry.model == "m"
        assert entry.device is None

    def test_captures_capability_rows(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        _write_slot(
            tmp_hal0_home, "embed", ['name = "embed"', "port = 8082", "[model]", 'default = ""']
        )
        _write_caps(
            tmp_hal0_home,
            "\n".join(
                [
                    "schema_version = 2",
                    "[selections.embed.embed]",
                    'device = "npu"',
                    'provider = "flm"',
                    'model = "bge-m3"',
                    "enabled = true",
                    "",
                ]
            ),
        )
        stack = snapshot_live_stack(registry=reg)
        embed = next(e for e in stack.slots if e.slot == "embed")
        assert any(
            r.child == "embed" and r.model == "bge-m3" and r.device == "npu"
            for r in embed.capabilities
        )

    def test_empty_slot_is_skipped(self, reg: ModelRegistry, tmp_hal0_home: str) -> None:
        # A seeded slot with no model and no capabilities should not bloat the snapshot.
        _write_slot(
            tmp_hal0_home, "tts", ['name = "tts"', "port = 8085", "[model]", 'default = ""']
        )
        stack = snapshot_live_stack(registry=reg)
        assert not any(e.slot == "tts" for e in stack.slots)

    def test_unset_capability_device_is_skipped(
        self, reg: ModelRegistry, tmp_hal0_home: str
    ) -> None:
        # A blank-picker selection (device == "") must not produce an invalid row.
        _write_slot(
            tmp_hal0_home, "vision", ['name = "vision"', "port = 8086", "[model]", 'default = "v"']
        )
        _write_caps(
            tmp_hal0_home,
            "\n".join(
                [
                    "schema_version = 2",
                    "[selections.vision.vision]",
                    'device = ""',
                    'provider = ""',
                    'model = ""',
                    "enabled = false",
                    "",
                ]
            ),
        )
        stack = snapshot_live_stack(registry=reg)
        vision = next(e for e in stack.slots if e.slot == "vision")
        assert vision.capabilities == []
