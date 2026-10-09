"""Tests for ensure_host_flm_store_link (host flm pull → configured store).

flm hardcodes ``$HOME/.config/flm/models`` and has no dir flag, so a host
``flm pull`` writes there — not the (possibly relocated) ``flm_store`` the
progress poller + serving container use. ``ensure_host_flm_store_link``
symlinks flm's default path onto the resolved store (migrating any legacy
content first) so the two agree.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from hal0.providers import flm


@pytest.fixture
def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Patch flm's default path + resolved store to tmp dirs; return (default, store)."""
    default = tmp_path / "home" / ".config" / "flm" / "models"
    store = tmp_path / "mnt" / "flm-store"
    monkeypatch.setattr("hal0.config.paths.default_flm_models_dir", lambda: str(default))
    monkeypatch.setattr("hal0.config.paths.flm_models_dir", lambda: str(store))
    return default, store


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_noop_when_store_is_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    same = tmp_path / ".config" / "flm" / "models"
    monkeypatch.setattr("hal0.config.paths.default_flm_models_dir", lambda: str(same))
    monkeypatch.setattr("hal0.config.paths.flm_models_dir", lambda: str(same))
    out = flm.ensure_host_flm_store_link()
    assert out == str(same)
    # No symlink is fabricated when there is nothing to reconcile.
    assert not same.is_symlink()


def test_symlinks_default_to_store_when_absent(stores: tuple[Path, Path]) -> None:
    default, store = stores
    out = flm.ensure_host_flm_store_link()
    assert out == str(store)
    assert default.is_symlink()
    assert Path(default).resolve() == store.resolve()


def test_replaces_empty_default_dir_with_symlink(stores: tuple[Path, Path]) -> None:
    default, store = stores
    default.mkdir(parents=True)
    assert default.is_dir() and not default.is_symlink()
    flm.ensure_host_flm_store_link()
    assert default.is_symlink()
    assert Path(default).resolve() == store.resolve()


def test_migrates_legacy_content_then_symlinks(stores: tuple[Path, Path]) -> None:
    default, store = stores
    # A previously-mispulled model dir sitting in flm's default path.
    model = default / "Phi4-mini-Instruct-NPU2"
    model.mkdir(parents=True)
    (model / "model.q4nx").write_text("weights", encoding="utf-8")
    (model / "config.json").write_text("{}", encoding="utf-8")

    flm.ensure_host_flm_store_link()

    # Default path is now a symlink; the weights moved into the store.
    assert default.is_symlink()
    assert Path(default).resolve() == store.resolve()
    moved = store / "Phi4-mini-Instruct-NPU2" / "model.q4nx"
    assert moved.exists()
    assert _read(moved) == "weights"


def test_migration_skips_collisions_and_leaves_dir(stores: tuple[Path, Path]) -> None:
    default, store = stores
    # Same-named model already in the store — must not be clobbered.
    (store / "Model-NPU2").mkdir(parents=True)
    (store / "Model-NPU2" / "keep.bin").write_text("store-copy", encoding="utf-8")
    (default / "Model-NPU2").mkdir(parents=True)
    (default / "Model-NPU2" / "keep.bin").write_text("home-copy", encoding="utf-8")

    # #2446: a collision leaves flm's default a real dir, so a pull now would
    # land outside the store. That must stop the caller, not log and continue.
    with pytest.raises(flm.FLMStoreLinkError) as exc_info:
        flm.ensure_host_flm_store_link()
    assert "Model-NPU2" in exc_info.value.message

    # Store copy preserved; default dir left in place (not symlinked over a
    # collision), so nothing is orphaned or lost.
    assert _read(store / "Model-NPU2" / "keep.bin") == "store-copy"
    assert not default.is_symlink()
    assert (default / "Model-NPU2" / "keep.bin").exists()


def test_file_at_default_path_raises(stores: tuple[Path, Path]) -> None:
    default, _store = stores
    default.parent.mkdir(parents=True)
    default.write_text("not a dir", encoding="utf-8")

    with pytest.raises(flm.FLMStoreLinkError):
        flm.ensure_host_flm_store_link()
    assert default.read_text(encoding="utf-8") == "not a dir"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory write bits")
def test_root_owned_parent_fails_loud_as_service_user(stores: tuple[Path, Path]) -> None:
    """#2446: hal0-api runs User=hal0; the installer made ``.config/flm`` root-owned.

    Simulated with a parent the test user cannot write. The rmdir of flm's
    default dir fails; before the fix that was a logged warning and the pull
    went on into the default dir, outside the store the slot mounts.
    """
    default, store = stores
    default.mkdir(parents=True)
    parent = default.parent
    parent.chmod(0o555)
    try:
        with pytest.raises(flm.FLMStoreLinkError) as exc_info:
            flm.ensure_host_flm_store_link()
    finally:
        parent.chmod(0o755)
    msg = exc_info.value.message
    assert "hal0 doctor perms --fix" in msg
    assert str(default) in msg and str(store) in msg
    assert default.is_dir() and not default.is_symlink()


def test_service_user_store_dir_is_group_writable_despite_umask(tmp_path: Path) -> None:
    """Non-root (User=hal0, UMask=0022) still births the store 2775, not 2755.

    The host pull writes through the hal0 group; a 2755 dir only works for the
    exact owner.
    """
    store = tmp_path / "models" / "flm" / "models"
    old = os.umask(0o022)
    try:
        if os.geteuid() == 0:
            pytest.skip("covers the unprivileged branch")
        flm._ensure_flm_models_dir(str(store))
    finally:
        os.umask(old)
    assert stat.S_IMODE(store.stat().st_mode) == 0o2775


def test_root_chowns_the_resolved_store_to_container_uid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As root, the RESOLVED store (not only the default dir) gets uid 1000 + hal0 group."""
    store = tmp_path / "models" / "flm" / "models"
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(flm.os, "geteuid", lambda: 0)
    monkeypatch.setattr(flm.os, "chown", lambda p, u, g: calls.append((p, u, g)))
    monkeypatch.setattr(
        "grp.getgrnam", lambda name: SimpleNamespace(gr_gid=4242) if name == "hal0" else None
    )
    flm._ensure_flm_models_dir(str(store))
    assert calls == [(str(store), 1000, 4242)]
    assert stat.S_IMODE(store.stat().st_mode) == 0o2775


def test_repoints_stale_symlink(stores: tuple[Path, Path]) -> None:
    default, store = stores
    stale = store.parent / "old-store"
    stale.mkdir(parents=True)
    default.parent.mkdir(parents=True)
    default.symlink_to(stale)

    flm.ensure_host_flm_store_link()

    assert default.is_symlink()
    assert Path(default).resolve() == store.resolve()


def test_idempotent_when_symlink_already_correct(stores: tuple[Path, Path]) -> None:
    default, store = stores
    store.mkdir(parents=True)
    default.parent.mkdir(parents=True)
    default.symlink_to(store)

    # Second run is a clean no-op — no exception, link unchanged.
    flm.ensure_host_flm_store_link()
    assert default.is_symlink()
    assert Path(default).resolve() == store.resolve()
