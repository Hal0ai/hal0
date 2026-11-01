"""components.json state store: atomic write, tolerant read."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from hal0.components import state
from hal0.security import nofollow


@pytest.fixture(autouse=True)
def _isolated_var_lib(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))


def test_record_then_load_roundtrip() -> None:
    state.record_component_result(
        "openwebui", {"status": "converged", "installed": "sha256:" + "a" * 64}
    )
    loaded = state.load_component_state()
    assert loaded["openwebui"]["status"] == "converged"
    assert "ts" in loaded["openwebui"]


def test_record_preserves_other_components() -> None:
    state.record_component_result("openwebui", {"status": "converged"})
    state.record_component_result("hermes", {"status": "build_failed", "error": "pip died"})
    loaded = state.load_component_state()
    assert set(loaded) == {"openwebui", "hermes"}


def test_load_missing_file_returns_empty() -> None:
    assert state.load_component_state() == {}


def test_load_corrupt_file_returns_empty() -> None:
    path = state.components_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert state.load_component_state() == {}


def test_record_over_corrupt_file_recovers() -> None:
    path = state.components_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    state.record_component_result("hindsight", {"status": "upgraded"})
    assert state.load_component_state()["hindsight"]["status"] == "upgraded"


def test_record_never_raises_on_unwritable_dir(monkeypatch) -> None:
    # Fail-soft posture: bookkeeping failure must not fail an update.
    monkeypatch.setattr(
        state, "components_state_path", lambda: state.Path("/proc/nope/components.json")
    )
    state.record_component_result("openwebui", {"status": "converged"})  # must not raise


@pytest.mark.parametrize("existing", [False, True])
def test_root_record_adopts_owner_before_replace(
    monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    path = state.components_state_path()
    if existing:
        path.parent.mkdir(parents=True)
        path.parent.chmod(0o755)
        path.write_text('{"hermes": {"status": "converged"}}')
        path.chmod(0o600)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(nofollow, "dir_owner", lambda p: (1234, 5678))
    adopted: dict[int, tuple[int, int]] = {}

    def chown(fd: int, uid: int, gid: int) -> None:
        adopted[os.fstat(fd).st_ino] = (uid, gid)

    replace = os.replace

    def checked_replace(src: str, dst: Path) -> None:
        assert dst == path
        assert adopted[path.parent.stat().st_ino] == (1234, 5678)
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o2775
        assert adopted[Path(src).stat().st_ino] == (1234, 5678)
        assert stat.S_IMODE(Path(src).stat().st_mode) == 0o600
        replace(src, dst)

    monkeypatch.setattr(os, "fchown", chown)
    monkeypatch.setattr(os, "replace", checked_replace)
    old_umask = os.umask(0o077)
    try:
        state.record_component_result("openwebui", {"status": "converged"})
    finally:
        os.umask(old_umask)
    loaded = state.load_component_state()
    assert loaded["openwebui"]["status"] == "converged"
    if existing:
        assert loaded["hermes"]["status"] == "converged"


def test_root_record_chown_failure_preserves_previous_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = state.components_state_path()
    path.parent.mkdir(parents=True)
    previous = '{"hermes": {"status": "converged"}}'
    path.write_text(previous)
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    def chown(fd: int, uid: int, gid: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise PermissionError("ownership refused")

    monkeypatch.setattr(os, "fchown", chown)
    state.record_component_result("openwebui", {"status": "converged"})
    assert path.read_text() == previous
    assert list(path.parent.iterdir()) == [path]


def test_nonroot_record_needs_no_chown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1234)

    def unexpected_chown(*args: object) -> None:
        pytest.fail("an unprivileged writer must not chown")

    monkeypatch.setattr(os, "fchown", unexpected_chown)
    state.record_component_result("hermes", {"status": "converged"})
    path = state.components_state_path()
    assert state.load_component_state()["hermes"]["status"] == "converged"
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o2775
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
