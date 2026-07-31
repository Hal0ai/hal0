"""``run_post_activation_migrations`` (GH #1475) — the single sequence both
``Updater.commit()`` (self-update) and ``install.sh``'s repair/upgrade-in-
place re-run path call, so the two upgrade paths converge on the same
on-disk state.

Before this, install.sh's venv-python block called only two of the five
post-swap passes ``Updater.commit()`` runs (``ensure_seed_profiles``,
``clear_stale_mtp_overrides``) — a box upgraded by re-running install.sh
kept a stale ``meta.schema_version``, stale runner-image pins, and
unsanitised ``defaults.extra_args``, while a box upgraded via ``hal0
update`` did not.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.errors import Hal0Error
from hal0.updater.updater import run_post_activation_migrations


@pytest.fixture(autouse=True)
def _stub_every_pass(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Replace all five passes with recording stubs so tests assert call
    order/count without touching real config/registry state."""
    calls: dict[str, list[Any]] = {
        "config_migrations": [],
        "seed_profiles": [],
        "mtp": [],
        "image_retag": [],
        "extra_args": [],
    }

    def _config_migrations(min_data_version, *, job_id=None, ceiling=None):
        calls["config_migrations"].append((min_data_version, job_id))
        return (1, 2)

    def _seed_profiles(*, job_id=None):
        calls["seed_profiles"].append(job_id)
        return 0

    def _mtp(*, job_id=None, registry=None):
        calls["mtp"].append(job_id)
        return 0

    def _image_retag(*, job_id=None):
        calls["image_retag"].append(job_id)
        return 0

    def _extra_args(*, job_id=None, registry=None):
        calls["extra_args"].append(job_id)
        return 0

    monkeypatch.setattr("hal0.updater.updater._maybe_run_config_migrations", _config_migrations)
    monkeypatch.setattr("hal0.updater.updater.ensure_seed_profiles", _seed_profiles)
    monkeypatch.setattr("hal0.updater.updater.clear_stale_mtp_overrides", _mtp)
    monkeypatch.setattr("hal0.updater.updater.retag_stale_slot_images", _image_retag)
    monkeypatch.setattr("hal0.updater.updater.sanitize_model_extra_args", _extra_args)
    return calls


def test_runs_all_five_passes(_stub_every_pass: dict[str, list[Any]]) -> None:
    result = run_post_activation_migrations(job_id="j1")
    assert result == (1, 2)
    assert _stub_every_pass["config_migrations"] == [(1, "j1")]
    assert _stub_every_pass["seed_profiles"] == ["j1"]
    assert _stub_every_pass["mtp"] == ["j1"]
    assert _stub_every_pass["image_retag"] == ["j1"]
    assert _stub_every_pass["extra_args"] == ["j1"]


def test_min_data_version_defaults_to_1(_stub_every_pass: dict[str, list[Any]]) -> None:
    """install.sh has no release manifest to read min_data_version from —
    the default must resolve to "migrate to whatever this code's latest
    schema version is", matching _maybe_run_config_migrations's own
    ``max(min_data_version or 1, latest_version())`` floor."""
    run_post_activation_migrations()
    assert _stub_every_pass["config_migrations"] == [(1, None)]


def test_a_failing_non_fatal_pass_does_not_block_the_others(
    monkeypatch: pytest.MonkeyPatch, _stub_every_pass: dict[str, list[Any]]
) -> None:
    def _boom(*, job_id=None, registry=None):
        raise RuntimeError("registry read failed")

    monkeypatch.setattr("hal0.updater.updater.clear_stale_mtp_overrides", _boom)

    result = run_post_activation_migrations(job_id="j2")

    assert result == (1, 2)  # schema migration still reported
    assert _stub_every_pass["seed_profiles"] == ["j2"]
    assert _stub_every_pass["image_retag"] == ["j2"]  # ran despite mtp's failure
    assert _stub_every_pass["extra_args"] == ["j2"]


def test_schema_migration_failure_propagates(
    monkeypatch: pytest.MonkeyPatch, _stub_every_pass: dict[str, list[Any]]
) -> None:
    """Unlike the four data-cleanup passes, a schema-migration failure is
    NOT swallowed — the caller (commit()'s install_dir cleanup, or
    install.sh's `set -euo pipefail`) must see it and abort the
    activation rather than proceed on an unmigrated schema."""

    def _boom(min_data_version, *, job_id=None, ceiling=None):
        raise Hal0Error("schema migration exploded", code="test.boom")

    monkeypatch.setattr("hal0.updater.updater._maybe_run_config_migrations", _boom)

    with pytest.raises(Hal0Error):
        run_post_activation_migrations(job_id="j3")

    # None of the four data-cleanup passes ran — the schema must land first.
    assert _stub_every_pass["seed_profiles"] == []
    assert _stub_every_pass["mtp"] == []
    assert _stub_every_pass["image_retag"] == []
    assert _stub_every_pass["extra_args"] == []
