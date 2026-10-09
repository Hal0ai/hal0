"""#2446 repair path: ``hal0 doctor perms --fix`` relinks flm's cache onto the store.

On a box that already hit #2446, flm's ``$HOME/.config/flm/models`` is a real
directory under a root-owned ``.config/flm``, holding the models the user
pulled, while the NPU slot mounts the empty ``[models].flm_store``. The root-run
repair hands the HOME-side parents to the service user, runs the move and the
link as that user, and gives the store to the container uid. It never deletes
user data: a name present on both sides stops the repair and is reported.

The repair runs as root, but the store path (``hal0.toml``) and HOME are the
service user's to change, so it follows no symlink and chowns nothing that the
service user or the container did not already own.

``fchown`` and the service-user link step are injected and the service-user
ids are patched, so the tests run unprivileged.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.providers import flm


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Relocated store + flm default under a fake service HOME; return (default, store).

    The test user plays both the service user and the container uid.
    """
    home = tmp_path / "var-lib-hal0"
    default = home / ".config" / "flm" / "models"
    store = home / "models" / "flm" / "models"
    home.mkdir()
    monkeypatch.setattr("hal0.config.paths.default_flm_models_dir", lambda: str(default))
    monkeypatch.setattr("hal0.config.paths.flm_models_dir", lambda: str(store))
    monkeypatch.setattr(flm, "_service_ids", lambda: (os.getuid(), os.getgid()))
    monkeypatch.setattr(flm, "_FLM_CONTAINER_UID", os.getuid())
    return default, store


class _FChown:
    """Record ``fchown`` calls as ``(path, uid, gid)`` without changing anything."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def __call__(self, fd: int, uid: int, gid: int) -> None:
        self.calls.append((os.readlink(f"/proc/self/fd/{fd}"), uid, gid))


def _link_in_process(uid: int, gid: int) -> None:
    flm.ensure_host_flm_store_link()


def _drift(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [r for r in rows if r["status"] == "drift"]


def _drift_paths(rows: list[dict[str, str]]) -> set[str]:
    return {r["path"] for r in _drift(rows)}


def test_stranded_models_are_migrated_and_relinked(layout: tuple[Path, Path]) -> None:
    default, store = layout
    model = default / "Gemma3-1B-NPU2"
    model.mkdir(parents=True)
    (model / "model.q4nx").write_text("weights", encoding="utf-8")

    assert str(default) in _drift_paths(flm.audit_host_flm_store_link())

    fchown = _FChown()
    actions = flm.repair_host_flm_store_link(fchown=fchown, link=_link_in_process)

    assert default.is_symlink()
    assert default.resolve() == store.resolve()
    assert (store / "Gemma3-1B-NPU2" / "model.q4nx").read_text(encoding="utf-8") == "weights"
    uid, gid = os.getuid(), os.getgid()
    # HOME-side parents go to the service user so the runtime can relink later.
    assert (str(default.parent.parent), uid, gid) in fchown.calls
    assert (str(default.parent), uid, gid) in fchown.calls
    # The RESOLVED store gets the container uid + service group, mode 2775.
    assert (str(store), flm._FLM_CONTAINER_UID, gid) in fchown.calls
    assert stat.S_IMODE(store.stat().st_mode) == 0o2775
    assert actions
    assert _drift(flm.audit_host_flm_store_link()) == []


def test_conflict_is_reported_and_nothing_is_deleted(layout: tuple[Path, Path]) -> None:
    default, store = layout
    (default / "Model-NPU2").mkdir(parents=True)
    (default / "Model-NPU2" / "w.bin").write_text("home-copy", encoding="utf-8")
    (store / "Model-NPU2").mkdir(parents=True)
    (store / "Model-NPU2" / "w.bin").write_text("store-copy", encoding="utf-8")

    with pytest.raises(flm.FLMStoreLinkError) as exc_info:
        flm.repair_host_flm_store_link(fchown=_FChown(), link=_link_in_process)

    assert "Model-NPU2" in exc_info.value.message
    assert (default / "Model-NPU2" / "w.bin").read_text(encoding="utf-8") == "home-copy"
    assert (store / "Model-NPU2" / "w.bin").read_text(encoding="utf-8") == "store-copy"
    assert not default.is_symlink()


def test_nothing_to_repair_on_a_box_without_flm(layout: tuple[Path, Path]) -> None:
    default, store = layout
    assert _drift(flm.audit_host_flm_store_link()) == []
    fchown = _FChown()
    assert flm.repair_host_flm_store_link(fchown=fchown, link=_link_in_process) == []
    assert fchown.calls == []
    assert not os.path.lexists(default)
    assert not store.exists()


def test_symlinked_home_parent_is_refused_not_followed(
    layout: tuple[Path, Path], tmp_path: Path
) -> None:
    """The service user owns HOME and can plant ``.config`` → ``/etc``.

    The root repair must not create ``flm`` inside the link's target or chown it.
    """
    default, _store = layout
    target = tmp_path / "victim"
    (target / "flm").mkdir(parents=True)
    (default.parent.parent).symlink_to(target)

    assert str(default.parent.parent) in _drift_paths(flm.audit_host_flm_store_link())

    fchown = _FChown()
    with pytest.raises(flm.FLMStoreLinkError):
        flm.repair_host_flm_store_link(fchown=fchown, link=_link_in_process)
    assert list((target / "flm").iterdir()) == []
    assert all(not p.startswith(str(target)) for p, _u, _g in fchown.calls)


def test_store_owned_by_someone_else_is_left_alone(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store path pointed at a dir neither hal0 nor uid 1000 owns is never chowned."""
    default, store = layout
    store.mkdir(parents=True)
    store.chmod(0o755)
    default.parent.mkdir(parents=True)
    default.symlink_to(store)
    monkeypatch.setattr(flm, "_service_ids", lambda: (os.getuid() + 1, os.getgid()))
    monkeypatch.setattr(flm, "_FLM_CONTAINER_UID", os.getuid() + 2)

    fchown = _FChown()
    actions = flm.repair_host_flm_store_link(fchown=fchown, link=lambda u, g: None)

    assert all(p != str(store) for p, _u, _g in fchown.calls)
    assert stat.S_IMODE(store.stat().st_mode) == 0o755
    assert any("left alone" in a for a in actions)


def test_empty_default_dir_is_drift(layout: tuple[Path, Path]) -> None:
    """``flm list`` reads the default dir too: empty, it hides the store's models."""
    default, store = layout
    default.mkdir(parents=True)
    store.mkdir(parents=True)
    assert str(default) in _drift_paths(flm.audit_host_flm_store_link())


def test_parent_not_owned_by_service_user_is_drift(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fresh-install shape: ``.config/flm`` exists, root-owned."""
    default, _store = layout
    default.parent.mkdir(parents=True)
    monkeypatch.setattr(flm, "_service_ids", lambda: (os.getuid() + 1, os.getgid() + 1))
    assert str(default.parent) in _drift_paths(flm.audit_host_flm_store_link())


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory write bits")
def test_store_the_service_user_cannot_create_is_drift(layout: tuple[Path, Path]) -> None:
    """Otherwise every pull says "run doctor perms --fix" and doctor says clean."""
    default, store = layout
    default.parent.mkdir(parents=True)
    store.parent.parent.mkdir(parents=True)
    store.parent.parent.chmod(0o555)
    try:
        rows = flm.audit_host_flm_store_link()
    finally:
        store.parent.parent.chmod(0o755)
    assert str(store) in _drift_paths(rows)


def test_group_writable_store_not_owned_by_container_is_drift(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The container uid is not in the hal0 group: g+w does not let it write."""
    _default, store = layout
    store.mkdir(parents=True)
    store.chmod(0o2775)
    monkeypatch.setattr(flm, "_FLM_CONTAINER_UID", os.getuid() + 5)
    assert str(store) in _drift_paths(flm.audit_host_flm_store_link())


def test_correct_link_audits_clean(layout: tuple[Path, Path]) -> None:
    default, store = layout
    store.mkdir(parents=True)
    store.chmod(0o2775)
    default.parent.mkdir(parents=True)
    default.symlink_to(store)
    rows = flm.audit_host_flm_store_link()
    assert _drift(rows) == [], rows


def test_link_step_runs_as_the_service_user() -> None:
    seen: dict[str, Any] = {}

    def _run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kw, argv=argv)
        return subprocess.CompletedProcess(argv, 3, "", "both hold Model-NPU2")

    with pytest.raises(flm.FLMStoreLinkError) as exc_info:
        flm._link_as_service_user(990, 991, run=_run)

    assert (seen["user"], seen["group"], seen["extra_groups"]) == (990, 991, [])
    assert "ensure_host_flm_store_link" in seen["argv"][-1]
    assert exc_info.value.message == "both hold Model-NPU2"
