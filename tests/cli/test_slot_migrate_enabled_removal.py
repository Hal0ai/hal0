"""``hal0 slot migrate-enabled-removal`` — the on-demand #1369 sweep.

Unlike the other ``slot migrate-*`` commands this one is NOT a deploy-window
operation: the same sweep runs on every API boot, it is idempotent, and it only
touches slots that still carry the removed ``enabled`` key. The command exists
so an operator can run it (or preview it) without restarting the API.

The transform itself is unit-tested in
``tests/config/test_slot_enabled_removal_migration.py``; this covers the
command's wiring — dry-run by default, ``--apply`` writes.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.cli.slot_commands import slot_migrate_enabled_removal


def _write_slot(config_dir: Path, name: str, body: str) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    p = config_dir / f"{name}.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_dry_run_by_default_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths

    slot = _write_slot(
        paths.slots_config_dir(),
        "chat",
        'name = "chat"\nport = 8081\nenabled = false\n[model]\ndefault = "m"\n',
    )
    before = slot.read_text(encoding="utf-8")

    slot_migrate_enabled_removal(apply=False)

    assert slot.read_text(encoding="utf-8") == before


def test_apply_sweeps_the_key_and_clears_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths

    slot = _write_slot(
        paths.slots_config_dir(),
        "chat",
        'name = "chat"\nport = 8081\nenabled = false\n[model]\ndefault = "m"\n',
    )

    slot_migrate_enabled_removal(apply=True)

    raw = tomllib.loads(slot.read_text(encoding="utf-8"))
    assert "enabled" not in raw
    assert raw["model"]["default"] == ""


def test_apply_on_a_clean_tree_is_a_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths

    slot = _write_slot(
        paths.slots_config_dir(), "chat", 'name = "chat"\nport = 8081\n[model]\ndefault = "m"\n'
    )
    before = slot.read_bytes()

    slot_migrate_enabled_removal(apply=True)

    assert slot.read_bytes() == before
