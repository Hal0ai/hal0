"""Test that ``_pull_root`` honours [models].store with pull_root fallback."""

from __future__ import annotations

from pathlib import Path

from hal0.config import paths
from hal0.config.loader import save_hal0_config
from hal0.config.schema import Hal0Config, ModelsConfig


def test_pull_root_defaults_to_pull_root_when_store_unset(
    tmp_hal0_home: str,
) -> None:
    cfg = Hal0Config()
    save_hal0_config(cfg)
    from hal0.registry.pull import _pull_root

    assert _pull_root() == Path(cfg.models.pull_root)


def test_pull_root_uses_store_when_set(tmp_hal0_home: str, tmp_path: Path) -> None:
    ext = tmp_path / "mnt-ai"
    ext.mkdir()
    cfg = Hal0Config(
        models=ModelsConfig(
            roots=[str(paths.models_dir())],
            pull_root=str(paths.models_dir()),
            store=str(ext),
        ),
    )
    save_hal0_config(cfg)
    from hal0.registry.pull import _pull_root

    assert _pull_root() == ext


def test_effective_store_picks_pull_root_fallback() -> None:
    """Backward compat — PR-#313 installs without `store` keep working."""
    models = ModelsConfig(
        roots=["/some/root"],
        pull_root="/legacy/path",
    )
    assert models.effective_store() == "/legacy/path"


def test_effective_store_prefers_explicit_store() -> None:
    models = ModelsConfig(
        roots=["/some/root"],
        pull_root="/legacy/path",
        store="/new/store",
    )
    assert models.effective_store() == "/new/store"
