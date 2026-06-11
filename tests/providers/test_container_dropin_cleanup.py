"""Tests for stale lemonade-era drop-in cleanup in _write_and_start_unit (closes #694).

When a slot converts from lemonade to container the old drop-in dir
(hal0-slot@<name>.service.d/override.conf) carries dead EnvironmentFile refs
that make systemd fail with "Failed to load environment files".
ContainerProvider._write_and_start_unit must remove the drop-in dir before
writing the unit file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from hal0.providers.container import ContainerProvider


def _make_provider_with_tmp_unit(
    tmp_path: Path, slot_name: str = "tts"
) -> tuple[ContainerProvider, Path, list[list[str]]]:
    """Return (provider, unit_path, calls_made) with _unit_path redirected into tmp_path."""
    provider = ContainerProvider()
    unit_path = tmp_path / f"hal0-slot@{slot_name}.service"
    calls_made: list[list[str]] = []

    def fake_run(*args: str, check: bool = True) -> MagicMock:
        calls_made.append(list(args))
        m = MagicMock()
        m.returncode = 0
        return m

    return provider, unit_path, calls_made, fake_run  # type: ignore[return-value]


def test_stale_dropin_dir_removed_before_start(tmp_path: Path) -> None:
    """A pre-existing lemonade drop-in dir is removed before the unit is written."""
    slot_name = "tts"
    provider = ContainerProvider()
    unit_path = tmp_path / f"hal0-slot@{slot_name}.service"

    # Create stale drop-in dir with an override.conf file.
    dropin_dir = tmp_path / f"hal0-slot@{slot_name}.service.d"
    dropin_dir.mkdir()
    (dropin_dir / "override.conf").write_text(
        "[Service]\nEnvironmentFile=/var/lib/hal0/slots/tts/env\n"
    )

    calls_made: list[list[str]] = []

    def fake_run(*args: str, check: bool = True) -> MagicMock:
        calls_made.append(list(args))
        m = MagicMock()
        m.returncode = 0
        return m

    with (
        patch.object(provider, "_run", side_effect=fake_run),
        patch.object(provider, "_unit_path", return_value=unit_path),
    ):
        provider._write_and_start_unit(slot_name, "[Unit]\nDescription=test\n")

    # Drop-in dir must be gone.
    assert not dropin_dir.exists(), "stale drop-in dir should have been removed"

    # Unit file must be written.
    assert unit_path.exists(), "unit file should have been written"

    # daemon-reload must appear in the _run calls AFTER the drop-in removal
    # (it runs as part of the existing systemctl sequence in _write_and_start_unit,
    # which follows the shutil.rmtree call).
    cmds = [" ".join(c) for c in calls_made]
    assert any("daemon-reload" in c for c in cmds), f"daemon-reload missing in {cmds}"
    assert any("restart" in c for c in cmds), f"restart missing in {cmds}"


def test_no_dropin_clean_start(tmp_path: Path) -> None:
    """When no drop-in dir exists, _write_and_start_unit proceeds without error."""
    slot_name = "chat"
    provider = ContainerProvider()
    unit_path = tmp_path / f"hal0-slot@{slot_name}.service"

    calls_made: list[list[str]] = []

    def fake_run(*args: str, check: bool = True) -> MagicMock:
        calls_made.append(list(args))
        m = MagicMock()
        m.returncode = 0
        return m

    with (
        patch.object(provider, "_run", side_effect=fake_run),
        patch.object(provider, "_unit_path", return_value=unit_path),
    ):
        provider._write_and_start_unit(slot_name, "[Unit]\nDescription=test\n")

    # Unit file written.
    assert unit_path.exists()

    # Full systemd sequence present.
    cmds = [" ".join(c) for c in calls_made]
    assert any("daemon-reload" in c for c in cmds)
    assert any("restart" in c for c in cmds)


def test_dropin_removal_logged(tmp_path: Path, caplog) -> None:
    """Removing a stale drop-in emits a container.stale_dropin_removed log record."""
    slot_name = "tts"
    provider = ContainerProvider()
    unit_path = tmp_path / f"hal0-slot@{slot_name}.service"

    dropin_dir = tmp_path / f"hal0-slot@{slot_name}.service.d"
    dropin_dir.mkdir()
    (dropin_dir / "override.conf").write_text("[Service]\nEnvironmentFile=/dead/path\n")

    def fake_run(*args: str, check: bool = True) -> MagicMock:
        m = MagicMock()
        m.returncode = 0
        return m

    with (
        caplog.at_level(logging.INFO, logger="hal0.providers.container"),
        patch.object(provider, "_run", side_effect=fake_run),
        patch.object(provider, "_unit_path", return_value=unit_path),
    ):
        provider._write_and_start_unit(slot_name, "[Unit]\nDescription=test\n")

    log_messages = [r.getMessage() for r in caplog.records]
    assert any("container.stale_dropin_removed" in m for m in log_messages), (
        f"expected container.stale_dropin_removed log; got: {log_messages}"
    )
