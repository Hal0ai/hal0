"""v2 — the profile-catalog reset watermark.

The transform is the identity; the *version bump* is the payload. That makes the
contract worth pinning explicitly, because everything about "the destructive
profile wipe fires exactly once" hangs off it:

  * v2 is registered, so ``latest_version()`` is 2 and the updater's schema
    runner will stamp a converged box forward;
  * the transform loses nothing from ``hal0.toml``;
  * the constant the updater gates on and the version the runner stamps are the
    same number — if those two ever drift, the wipe either never fires or fires
    on every update.
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
