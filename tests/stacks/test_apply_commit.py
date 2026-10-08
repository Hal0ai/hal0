"""Unit tests for StackApplyEngine.apply_config() — atomic commit + rollback.

Targeted file run:
    uv run pytest tests/stacks/test_apply_commit.py -q
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.stacks.apply import StackApplyEngine


def _slots_dir(home: str) -> Path:
    d = Path(home) / "etc" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_slot(home: str, name: str, model: str) -> Path:
    path = _slots_dir(home) / f"{name}.toml"
    path.write_text(
        "\n".join(
            [
                f'name = "{name}"',
                "port = 8087",
                'device = "gpu-vulkan"',
                "[model]",
                f'default = "{model}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _read(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _stack(*entries: StackSlotEntry) -> StackConfig:
    return StackConfig(name="S", slots=list(entries))


class TestCommit:
    def test_commit_writes_after(self, tmp_hal0_home: str) -> None:
        path = _write_slot(tmp_hal0_home, "agent", "old")
        engine = StackApplyEngine()
        plan = engine.plan("s", _stack(StackSlotEntry(slot="agent", model="new")))
        engine.apply_config(plan)
        assert _read(path)["model"]["default"] == "new"

    def test_commit_is_idempotent(self, tmp_hal0_home: str) -> None:
        _write_slot(tmp_hal0_home, "agent", "old")
        engine = StackApplyEngine()
        engine.apply_config(engine.plan("s", _stack(StackSlotEntry(slot="agent", model="new"))))
        # Re-planning against the now-applied disk yields no change.
        assert (
            engine.plan("s", _stack(StackSlotEntry(slot="agent", model="new"))).change_set.changed
            is False
        )


class TestRollback:
    def test_failed_commit_rolls_back(
        self, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hal0.slot_config as slot_config_mod

        a_path = _write_slot(tmp_hal0_home, "agent", "old-a")
        c_path = _write_slot(tmp_hal0_home, "chat", "old-c")
        a_before, c_before = _read(a_path), _read(c_path)

        engine = StackApplyEngine()
        plan = engine.plan(
            "s",
            _stack(
                StackSlotEntry(slot="agent", model="new-a"),
                StackSlotEntry(slot="chat", model="new-c"),
            ),
        )
        real_write = slot_config_mod.write_toml_atomic

        def _boom_on_chat(path: Path | str, data: dict[str, Any]) -> None:
            if Path(path).name == "chat.toml":
                raise OSError("disk full")
            real_write(path, data)

        monkeypatch.setattr(slot_config_mod, "write_toml_atomic", _boom_on_chat)
        with pytest.raises(OSError):
            engine.apply_config(plan)
        monkeypatch.setattr(slot_config_mod, "write_toml_atomic", real_write)

        assert _read(a_path) == a_before, "agent.toml must roll back to before"
        assert _read(c_path) == c_before, "chat.toml never written"
