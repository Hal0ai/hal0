"""The ``hal0.toml`` schema-migration writer (``_maybe_run_config_migrations``).

#1652: it used to dump the validated model, and ``None`` (the default of
``SecurityConfig.require_auth`` / ``trust_forwarded_for``) has no TOML
representation. It now round-trips the raw file, which never holds ``None``,
never writes model defaults into the file, and keeps keys the validator does
not know. Since the retired profile-catalog reset no longer stamps v2 itself,
a fresh install's v1 file reaches this writer on its first migration run.
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


def test_a_fresh_v1_file_is_stamped_without_writing_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fresh-install case: install.sh writes a minimal v1 hal0.toml and the
    first migration run stamps v2. The round-trip must not expand the file with
    every model default (which would pin them) or drop an unknown key."""
    import tomllib

    toml_path = tmp_path / "hal0.toml"
    toml_path.write_text(
        "[meta]\nschema_version = 1\n\n[slots]\nport_range_start = 8081\n\n"
        "[operator_notes]\nkept = true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(updater_module.paths, "hal0_toml", lambda: toml_path)

    assert updater_module._maybe_run_config_migrations(1) == (1, 2)
    raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert raw == {
        "meta": {"schema_version": 2},
        "slots": {"port_range_start": 8081},
        "operator_notes": {"kept": True},
    }
