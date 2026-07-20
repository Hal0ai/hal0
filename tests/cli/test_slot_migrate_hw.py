"""``hal0 slot migrate-hw`` — the operator-run deploy-window HW-ownership fold
(spec-hw-slot-ownership §6). Offline / filesystem-direct: it rewrites the on-disk
slot layout the *stopped* API reads next boot, and is never wired into any
automatic boot/update path.

Covers the command's own wiring — the dry-run-by-default gate, the ``--apply``
backup, and delegation to
:func:`hal0.config.migrations.hw_slot_ownership.run_migration` — against a real
fixture tree. The fold LOGIC itself is unit-tested in
``tests/config/test_hw_slot_ownership_migration.py``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.cli.slot_commands import slot_migrate_hw


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
        'name = "chat"\nport = 8081\nimage = "ghcr.io/test/custom:v1"\n',
    )
    before = slot.read_text(encoding="utf-8")

    # apply defaults to False → dry-run: prints the plan, writes nothing.
    slot_migrate_hw(apply=False, yes=True, stop_services=False)

    assert slot.read_text(encoding="utf-8") == before  # untouched
    assert not (paths.var_lib() / "backups").exists()  # no backup on a dry-run


def test_apply_folds_slot_image_to_image_pin_and_backs_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths

    slot = _write_slot(
        paths.slots_config_dir(),
        "chat",
        'name = "chat"\nport = 8081\nimage = "ghcr.io/test/custom:v1"\n',
    )

    # No live systemd in the test env — _active_hal0_units reports nothing, so
    # the safety gate passes; --apply performs the real (filesystem) fold.
    slot_migrate_hw(apply=True, yes=True, stop_services=False)

    raw = tomllib.loads(slot.read_text(encoding="utf-8"))
    # A slot's own deliberate image collapses onto the image_pin escape hatch.
    assert raw.get("image_pin") == "ghcr.io/test/custom:v1"
    assert "image" not in raw  # raw key collapsed away

    backups = list((paths.var_lib() / "backups").glob("*.tar.gz"))
    assert len(backups) == 1  # a backup is taken before the write
