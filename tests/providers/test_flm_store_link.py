"""Tests for ensure_host_flm_store_link (host flm pull → configured store).

flm hardcodes ``$HOME/.config/flm/models`` and has no dir flag, so a host
``flm pull`` writes there — not the (possibly relocated) ``flm_store`` the
progress poller + serving container use. ``ensure_host_flm_store_link``
symlinks flm's default path onto the resolved store (migrating any legacy
content first) so the two agree.
"""

from __future__ import annotations

from pathlib import Path

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

    flm.ensure_host_flm_store_link()

    # Store copy preserved; default dir left in place (not symlinked over a
    # collision), so nothing is orphaned or lost.
    assert _read(store / "Model-NPU2" / "keep.bin") == "store-copy"
    assert not default.is_symlink()
    assert (default / "Model-NPU2" / "keep.bin").exists()


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
