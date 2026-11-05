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

from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.config.migrations import latest_version
from hal0.config.schema import Hal0Config
from hal0.updater import updater as updater_module


@pytest.fixture
def _hal0_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real, on-disk v1 hal0.toml, with paths.hal0_toml() redirected to it."""
    toml_path = tmp_path / "hal0.toml"
    cfg = Hal0Config()
    cfg.meta.schema_version = 1
    save_hal0_config(cfg, toml_path)
    monkeypatch.setattr(updater_module.paths, "hal0_toml", lambda: toml_path)
    return toml_path


def test_a_pending_migration_does_not_typeerror_on_none_fields(_hal0_toml: Path) -> None:
    """The regression itself: SecurityConfig's None-default fields must not
    reach tomli_w unserialized once a real migration step runs."""
    head = latest_version()
    source, target = updater_module._maybe_run_config_migrations(min_data_version=head)
    assert (source, target) == (1, head)

    # And the write actually landed and is re-loadable.
    reloaded = load_hal0_config(_hal0_toml)
    assert reloaded.meta.schema_version == head
    # The None-default security fields survived the round trip as None, not
    # some stringified crash artifact.
    assert reloaded.security.require_auth is None
    assert reloaded.security.trust_forwarded_for is None


def test_a_fresh_v1_file_is_stamped_without_writing_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fresh-install case: install.sh writes a minimal v1 hal0.toml and the
    first migration run stamps the head version. The round-trip must not expand the file with
    every model default (which would pin them) or drop an unknown key."""
    import tomllib

    toml_path = tmp_path / "hal0.toml"
    toml_path.write_text(
        "[meta]\nschema_version = 1\n\n[slots]\nport_range_start = 8081\n\n"
        "[operator_notes]\nkept = true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(updater_module.paths, "hal0_toml", lambda: toml_path)

    head = latest_version()
    assert updater_module._maybe_run_config_migrations(1) == (1, head)
    raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert raw == {
        "meta": {"schema_version": head},
        "slots": {"port_range_start": 8081},
        "operator_notes": {"kept": True},
        "memory": {"graph": {"extraction_slot": "utility"}, "priority": {"enabled": False}},
    }
