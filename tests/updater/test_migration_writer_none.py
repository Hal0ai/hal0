"""#1652: ``_maybe_run_config_migrations`` dumps ``None`` into ``tomli_w``.

``_maybe_run_config_migrations`` (``src/hal0/updater/updater.py``) does
``cfg.model_dump(mode="python")`` -- unlike ``save_hal0_config``, which uses
``exclude_none=True`` precisely because ``None`` has no TOML representation
and ``tomli_w`` raises ``TypeError`` on it. ``SecurityConfig.require_auth``
and ``trust_forwarded_for`` both default to ``None``, so any config that
reaches this writer with a migration actually pending raises
``TypeError: Object of type 'NoneType' is not TOML serializable``.

Today this is masked by arithmetic: the only registered migration is v2,
and the profile-catalog gate either stamps v2 first (source==target, a
no-op) or caps the ceiling at 1 (target==source, also a no-op), so the
real writer is never reached. The moment a v3 migration is registered --
simulated here by monkeypatching the migrations registry -- this becomes
a hard failure of every ``hal0 update``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config import migrations as migrations_module
from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.config.schema import Hal0Config
from hal0.updater import updater as updater_module


@pytest.fixture
def _hal0_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real, on-disk v1 hal0.toml, with paths.hal0_toml() redirected to it."""
    toml_path = tmp_path / "hal0.toml"
    save_hal0_config(Hal0Config(), toml_path)
    monkeypatch.setattr(updater_module.paths, "hal0_toml", lambda: toml_path)
    return toml_path


@pytest.fixture
def _with_v3_migration(monkeypatch: pytest.MonkeyPatch):
    """Register a throwaway identity v3 migration so a real (non-v2) step
    is actually walked -- reproducing the world after a v3 migration ships,
    which today's v2-ceiling arithmetic otherwise masks."""

    def _identity(data: dict) -> dict:
        return dict(data)

    monkeypatch.setitem(migrations_module.MIGRATIONS, 3, _identity)


def test_a_pending_migration_does_not_typeerror_on_none_fields(
    _hal0_toml: Path, _with_v3_migration
) -> None:
    """The regression itself: SecurityConfig's None-default fields must not
    reach tomli_w unserialized once a real migration step runs."""
    source, target = updater_module._maybe_run_config_migrations(min_data_version=3)
    assert (source, target) == (1, 3)

    # And the write actually landed and is re-loadable.
    reloaded = load_hal0_config(_hal0_toml)
    assert reloaded.meta.schema_version == 3
    # The None-default security fields survived the round trip as None, not
    # some stringified crash artifact.
    assert reloaded.security.require_auth is None
    assert reloaded.security.trust_forwarded_for is None
