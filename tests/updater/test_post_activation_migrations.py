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
from hal0.updater.updater import check_outstanding_migrations, run_post_activation_migrations


@pytest.fixture(autouse=True)
def _stub_every_pass(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Replace all five passes with recording stubs so tests assert call
    order/count without touching real config/registry state."""
    calls: dict[str, list[Any]] = {
        "config_migrations": [],
        "seed_profiles": [],
        "mtp": [],
        "vulkan_migration": [],
        "extra_args": [],
        "hermes_venv": [],
        "converge_components": [],
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

    def _vulkan_migration(*, job_id=None, kfd_present=None, amd_host=None):
        calls["vulkan_migration"].append(job_id)
        return 0

    def _extra_args(*, job_id=None, registry=None):
        calls["extra_args"].append(job_id)
        return 0

    def _hermes_venv(*, job_id=None, install=True, venv=None, restart_gateway=True):
        calls["hermes_venv"].append((job_id, install))
        return False

    def _converge_components(*, job_id=None, apply=True, image_retag=True, engine=True, hermes_install=True):
        calls["converge_components"].append(
            {
                "job_id": job_id,
                "apply": apply,
                "image_retag": image_retag,
                "engine": engine,
                "hermes_install": hermes_install,
            }
        )
        return {}

    monkeypatch.setattr("hal0.updater.updater._maybe_run_config_migrations", _config_migrations)
    monkeypatch.setattr("hal0.updater.updater.ensure_seed_profiles", _seed_profiles)
    monkeypatch.setattr("hal0.updater.updater.clear_stale_mtp_overrides", _mtp)
    monkeypatch.setattr("hal0.updater.updater.relabel_stale_vulkan_slots", _vulkan_migration)
    monkeypatch.setattr("hal0.updater.updater.sanitize_model_extra_args", _extra_args)
    monkeypatch.setattr("hal0.updater.updater.repair_hermes_mcp_client", _hermes_venv)
    monkeypatch.setattr("hal0.components.runner.converge_components", _converge_components)
    return calls


def test_runs_all_six_passes(_stub_every_pass: dict[str, list[Any]]) -> None:
    result = run_post_activation_migrations(job_id="j1")
    assert result == (1, 2)
    assert _stub_every_pass["config_migrations"] == [(1, "j1")]
    assert _stub_every_pass["seed_profiles"] == ["j1"]
    assert _stub_every_pass["mtp"] == ["j1"]
    assert _stub_every_pass["vulkan_migration"] == ["j1"]
    assert _stub_every_pass["extra_args"] == ["j1"]
    assert _stub_every_pass["converge_components"] == [
        {
            "job_id": "j1",
            "apply": True,
            "image_retag": True,
            "engine": True,
            "hermes_install": True,
        }
    ]


def test_skip_image_retag_omits_only_that_pass(_stub_every_pass: dict[str, list[Any]]) -> None:
    """#1960 N2: check_outstanding_migrations passes skip_image_retag=True
    so the boot-time safety net never runs retag_stale_slot_images (now the
    runner-images arm inside converge_components) — every OTHER pass must
    still run unaffected."""
    result = run_post_activation_migrations(job_id="j1", skip_image_retag=True)
    assert result == (1, 2)
    assert _stub_every_pass["seed_profiles"] == ["j1"]
    assert _stub_every_pass["mtp"] == ["j1"]
    assert _stub_every_pass["vulkan_migration"] == ["j1"]
    assert _stub_every_pass["extra_args"] == ["j1"]
    assert _stub_every_pass["converge_components"] == [
        {
            "job_id": "j1",
            "apply": True,
            "image_retag": False,
            "engine": True,
            "hermes_install": True,
        }
    ]


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
    assert _stub_every_pass["vulkan_migration"] == ["j2"]  # ran despite mtp's failure
    assert _stub_every_pass["extra_args"] == ["j2"]
    assert [c["job_id"] for c in _stub_every_pass["converge_components"]] == ["j2"]  # ran despite mtp's failure


def test_schema_migration_failure_propagates(
    monkeypatch: pytest.MonkeyPatch, _stub_every_pass: dict[str, list[Any]]
) -> None:
    """Unlike the five data-cleanup passes, a schema-migration failure is
    NOT swallowed — the caller (commit()'s install_dir cleanup, or
    install.sh's `set -euo pipefail`) must see it and abort the
    activation rather than proceed on an unmigrated schema."""

    def _boom(min_data_version, *, job_id=None, ceiling=None):
        raise Hal0Error("schema migration exploded", code="test.boom")

    monkeypatch.setattr("hal0.updater.updater._maybe_run_config_migrations", _boom)

    with pytest.raises(Hal0Error):
        run_post_activation_migrations(job_id="j3")

    # None of the five data-cleanup passes ran — the schema must land first.
    assert _stub_every_pass["seed_profiles"] == []
    assert _stub_every_pass["mtp"] == []
    assert _stub_every_pass["vulkan_migration"] == []
    assert _stub_every_pass["extra_args"] == []
    assert _stub_every_pass["converge_components"] == []


def test_update_path_repairs_a_drifted_hermes_venv(
    _stub_every_pass: dict[str, list[Any]],
) -> None:
    """#2102: an upgrade heals a venv whose MCP client cannot import.

    rc.12's #2090 pass repairs the config header only, so a box whose venv
    drifted off the vetted pin (the residual of #2021) came out of a
    successful ``hal0 update`` still holding zero memory tools.
    """
    run_post_activation_migrations(job_id="j1")
    assert _stub_every_pass["hermes_venv"] == [("j1", True)]


def test_boot_safety_net_diagnoses_the_venv_without_installing(
    _stub_every_pass: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boot path must not put a pip install in ``hal0-api``'s startup.

    ``check_outstanding_migrations`` runs on every start, crash-restarts
    included. It still probes — the fault and its remedy get logged — but the
    repair itself waits for a real update.
    """
    monkeypatch.setattr("hal0.updater.updater.profile_reset_status", lambda: {"due": False})

    check_outstanding_migrations(job_id="boot")

    assert _stub_every_pass["hermes_venv"] == [("boot", False)]
    assert _stub_every_pass["converge_components"] == [
        {
            "job_id": "boot",
            "apply": False,
            "image_retag": False,
            "engine": False,
            "hermes_install": False,
        }
    ]


def test_converge_companions_flag_gates_the_apply_kwarg(
    _stub_every_pass: dict[str, list[Any]],
) -> None:
    """``converge_companions=False`` is the explicit, non-inferred way to
    make the whole component catalog diagnose-only — see the controller
    ruling in the component-updates spec: inferring "diagnose-only" from
    the other three flags being off was rejected as fragile."""
    run_post_activation_migrations(job_id="j4", converge_companions=False)
    assert _stub_every_pass["converge_components"] == [
        {
            "job_id": "j4",
            "apply": False,
            "image_retag": True,
            "engine": True,
            "hermes_install": True,
        }
    ]
