"""``hal0 slot migrate-caps`` — the operator-run deploy-window model-ownership
fold (spec-hw-slot-ownership §1). Offline / filesystem-direct: it rewrites the
on-disk slot layout AND the registry DB, and is never wired into any automatic
boot/update path.

Covers the command's own wiring — the dry-run-by-default gate, the ``--apply``
backup, and delegation to
:func:`hal0.config.migrations.model_owned_caps.run_migration` — against a real
fixture tree. The fold LOGIC itself is unit-tested in
``tests/config/test_model_owned_caps_migration.py``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.cli.slot_commands import slot_migrate_caps


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
        'name = "chat"\nport = 8081\nmtp = true\n[model]\ndefault = "m"\n',
    )
    before = slot.read_text(encoding="utf-8")

    # apply defaults to False → dry-run: prints the plan, writes nothing.
    slot_migrate_caps(apply=False, yes=True, stop_services=False)

    assert slot.read_text(encoding="utf-8") == before  # untouched
    assert not (paths.var_lib() / "backups").exists()  # no backup on a dry-run


def test_apply_folds_slot_mtp_to_model_and_backs_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths
    from hal0.registry.model import Model
    from hal0.registry.store import ModelRegistry

    slot = _write_slot(
        paths.slots_config_dir(),
        "chat",
        'name = "chat"\nport = 8081\nmtp = true\n[model]\ndefault = "m"\n',
    )
    registry = ModelRegistry()
    registry.add(Model(id="m", path="/models/m.gguf"))

    # Exercise the filesystem fold independently of host systemd state. The
    # safety gate itself retains dedicated coverage in the id-keying tests.
    monkeypatch.setattr("hal0.cli.slot_commands._active_hal0_units", lambda: [])
    slot_migrate_caps(apply=True, yes=True, stop_services=False)

    raw = tomllib.loads(slot.read_text(encoding="utf-8"))
    assert "mtp" not in raw  # slot debris dropped
    assert raw["model"]["default"] == "m"  # untouched sibling key

    got = registry.get("m")
    assert got.defaults is not None
    assert got.defaults.mtp is True  # folded onto the model

    backups = list((paths.var_lib() / "backups").glob("*.tar.gz"))
    assert len(backups) == 1  # a backup is taken before the write


def test_apply_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    from hal0.config import paths
    from hal0.registry.model import Model
    from hal0.registry.store import ModelRegistry

    _write_slot(
        paths.slots_config_dir(),
        "chat",
        'name = "chat"\nport = 8081\nmtp = true\n[model]\ndefault = "m"\n',
    )
    registry = ModelRegistry()
    registry.add(Model(id="m", path="/models/m.gguf"))
    monkeypatch.setattr("hal0.cli.slot_commands._active_hal0_units", lambda: [])

    slot_migrate_caps(apply=True, yes=True, stop_services=False)
    first_defaults = registry.get("m").defaults

    # Second pass: nothing left to fold or drop.
    slot_migrate_caps(apply=True, yes=True, stop_services=False)
    assert registry.get("m").defaults == first_defaults

    # A fresh backup is attempted on each --apply run; the timestamp is
    # second-granularity so two runs within the same wall-clock second can
    # collide onto the same filename (pre-existing property of the shared
    # ``_backup_slot_state`` helper — same as ``migrate-hw``) — assert at
    # least one exists rather than pin the exact count.
    backups = list((paths.var_lib() / "backups").glob("*.tar.gz"))
    assert len(backups) >= 1
