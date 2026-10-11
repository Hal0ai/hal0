"""v2 — an identity step, once the watermark of the retired profile reset.

* v2 is registered, so ``latest_version()`` is 2 and the updater's schema
  runner stamps a v1 box forward;
* the transform loses nothing from ``hal0.toml``;
* ``PROFILE_CATALOG_SCHEMA_VERSION`` stays importable from the updater for a
  1.3.x updater's post-swap child script.
"""

from __future__ import annotations

from hal0.config.migrations import MIGRATIONS, latest_version, run_migrations
from hal0.config.migrations.v2 import PROFILE_CATALOG_SCHEMA_VERSION


def test_v2_is_registered() -> None:
    assert 2 in MIGRATIONS
    assert latest_version() >= 2


def test_watermark_matches_the_registered_version() -> None:
    """The gate constant and the stamped version must never drift apart."""
    assert PROFILE_CATALOG_SCHEMA_VERSION == 2
    assert PROFILE_CATALOG_SCHEMA_VERSION in MIGRATIONS


def test_updater_gate_imports_the_same_constant() -> None:
    from hal0.updater import updater as U

    assert U.PROFILE_CATALOG_SCHEMA_VERSION is PROFILE_CATALOG_SCHEMA_VERSION


def test_v1_to_v2_is_lossless_and_stamps() -> None:
    data = {
        "meta": {"schema_version": 1},
        "telemetry": {"enabled": True, "channel": "preview"},
        "slots": {"port_range_start": 8081},
    }
    out, version = run_migrations(data, target_version=2)
    assert version == 2
    assert out["meta"]["schema_version"] == 2
    assert out["telemetry"] == {"enabled": True, "channel": "preview"}
    assert out["slots"] == {"port_range_start": 8081}


def test_unversioned_config_walks_all_the_way_to_v2() -> None:
    """A legacy hal0.toml with no [meta] table is a v1 box, not a v2 one."""
    out, version = run_migrations({"telemetry": {"enabled": False}}, target_version=2)
    assert version == 2
    assert out["meta"]["schema_version"] == 2
    assert out["telemetry"]["enabled"] is False


def test_v2_to_v2_is_a_noop() -> None:
    out, version = run_migrations({"meta": {"schema_version": 2}, "x": {"y": 1}})
    assert version == 2
    assert out["x"] == {"y": 1}
