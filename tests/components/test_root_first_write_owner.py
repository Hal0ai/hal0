"""#3260: a root-run first write of a service-read state file adopts the state root's owner."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hal0.agents import hermes_provision, hermes_release
from hal0.components import hermes_arm, openwebui_arm
from hal0.config.paths import var_lib
from hal0.install import perms
from hal0.security import nofollow

OWNER = (1234, 5678)


@pytest.fixture
def as_root(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, int]]:
    """Pretend to be root over a service-owned state root; record fchown targets."""
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(nofollow, "dir_owner", lambda p: OWNER)

    def fchown(fd: int, uid: int, gid: int) -> None:
        calls.append((os.readlink(f"/proc/self/fd/{fd}"), uid, gid))

    monkeypatch.setattr(os, "fchown", fchown)
    return calls


def _adopted(calls: list[tuple[str, int, int]], name: str) -> None:
    assert [c for c in calls if Path(c[0]).name.startswith(f".{name}.")] or [
        c for c in calls if c[0].endswith(name)
    ]
    assert all((u, g) == OWNER for _, u, g in calls)


def test_state_root_owner_unprivileged_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1234)
    assert perms.state_root_owner() is None


def test_state_root_owner_root_adopts_state_root(as_root: list[tuple[str, int, int]]) -> None:
    assert perms.state_root_owner() == OWNER


def test_state_root_owner_unreadable_root_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    def boom(p: object) -> tuple[int, int]:
        raise OSError("nope")

    monkeypatch.setattr(nofollow, "dir_owner", boom)
    assert perms.state_root_owner() is None


def test_hermes_override_first_write_adopts_owner(as_root: list[tuple[str, int, int]]) -> None:
    hermes_arm._settle_override({}, "abc123", False, job_id=None)
    assert hermes_arm.override_path().read_text() == "abc123\n"
    _adopted(as_root, "hermes.pin-override")


def test_openwebui_override_first_write_adopts_owner(
    as_root: list[tuple[str, int, int]],
) -> None:
    openwebui_arm._write_override("sha256:" + "a" * 64)
    assert openwebui_arm.override_path().read_text().startswith("sha256:")
    _adopted(as_root, "openwebui.pin-override")


def test_venv_pin_stamp_first_write_adopts_owner(as_root: list[tuple[str, int, int]]) -> None:
    stamp = var_lib() / "state" / "agents" / "hermes" / "venv.pin"

    class Rel:
        ref = "0123456789abcdef"

    assert hermes_release.write_stamp(Rel(), stamp) is None  # type: ignore[arg-type]
    assert stamp.read_text() == "0123456789ab\n"
    _adopted(as_root, "venv.pin")


def test_provision_report_first_write_adopts_owner(
    as_root: list[tuple[str, int, int]], tmp_path: Path
) -> None:
    root = var_lib() / "state" / "agents" / "hermes"
    hermes_provision._atomic_write(root / "provision.json", "{}\n", owner=perms.state_root_owner())
    assert (root / "provision.json").read_text() == "{}\n"
    _adopted(as_root, "provision.json")


def test_unprivileged_writes_never_chown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1234)

    def unexpected(*a: object) -> None:
        pytest.fail("an unprivileged writer must not chown")

    monkeypatch.setattr(os, "fchown", unexpected)
    openwebui_arm._write_override("sha256:" + "b" * 64)
    assert openwebui_arm.override_path().exists()
