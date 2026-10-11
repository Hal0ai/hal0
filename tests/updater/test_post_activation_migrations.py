"""``run_post_activation_migrations`` (GH #1475) — the single sequence both
``Updater.commit()`` (self-update) and ``install.sh``'s repair/upgrade-in-
place re-run path call, so the two upgrade paths converge on the same
on-disk state.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.errors import Hal0Error
from hal0.updater.updater import check_outstanding_migrations, run_post_activation_migrations


@pytest.fixture(autouse=True)
def _stub_every_pass(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Replace every pass with recording stubs so tests assert call
    order/count without touching real config/registry state."""
    calls: dict[str, list[Any]] = {
        "runner_system": [],
        "config_migrations": [],
        "registry_toml": [],
        "mtp": [],
        "vulkan_migration": [],
        "extra_args": [],
        "reasoning_format_none": [],
        "hermes_venv": [],
        "converge_components": [],
    }

    def _runner_system(*, dry_run=False, job_id=None, honour_restore_marker=False):
        calls["runner_system"].append(job_id)
        calls.setdefault("restore_marker", []).append(honour_restore_marker)

    def _config_migrations(min_data_version, *, job_id=None, ceiling=None):
        calls["config_migrations"].append((min_data_version, job_id))
        return (1, 2)

    def _registry_toml(*, job_id=None):
        calls["registry_toml"].append(job_id)
        return {"outcome": "absent"}

    def _mtp(*, job_id=None, registry=None):
        calls["mtp"].append(job_id)
        return 0

    def _vulkan_migration(*, job_id=None, rocm_lane=None, amd_host=None):
        calls["vulkan_migration"].append(job_id)
        return 0

    def _extra_args(*, job_id=None, registry=None):
        calls["extra_args"].append(job_id)
        return 0

    def _reasoning_format_none(*, registry=None):
        calls["reasoning_format_none"].append(registry)
        return {"profiles": [], "slots": [], "models": []}

    def _hermes_venv(*, job_id=None, install=True, venv=None, restart_gateway=True):
        calls["hermes_venv"].append((job_id, install))
        return False

    def _converge_components(
        *, job_id=None, apply=True, catalogue_sync=True, engine=True, hermes_install=True
    ):
        calls["converge_components"].append(
            {
                "job_id": job_id,
                "apply": apply,
                "catalogue_sync": catalogue_sync,
                "engine": engine,
                "hermes_install": hermes_install,
            }
        )
        return {}

    monkeypatch.setattr(
        "hal0.config.migrations.runner_system.migrate_runner_system", _runner_system
    )
    monkeypatch.setattr("hal0.updater.updater._maybe_run_config_migrations", _config_migrations)
    monkeypatch.setattr("hal0.registry.toml_retirement.retire_registry_toml", _registry_toml)
    monkeypatch.setattr("hal0.updater.updater.clear_stale_mtp_overrides", _mtp)
    monkeypatch.setattr("hal0.updater.updater.relabel_stale_vulkan_slots", _vulkan_migration)
    monkeypatch.setattr("hal0.updater.updater.sanitize_model_extra_args", _extra_args)
    monkeypatch.setattr(
        "hal0.config.migrations.reasoning_format_none.run_migration", _reasoning_format_none
    )
    monkeypatch.setattr("hal0.updater.updater.repair_hermes_mcp_client", _hermes_venv)
    monkeypatch.setattr("hal0.components.runner.converge_components", _converge_components)
    return calls


def test_runner_conversion_runs_before_schema_and_registry_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0028: the runner conversion rewrites raw TOML that config
    validation would reject, so it must run before the schema migration and
    before the registry.toml retirement (both load config)."""
    order: list[str] = []
    monkeypatch.setattr(
        "hal0.config.migrations.runner_system.migrate_runner_system",
        lambda *, dry_run=False, job_id=None, honour_restore_marker=False: order.append(
            "runner_system"
        ),
    )
    monkeypatch.setattr(
        "hal0.updater.updater._maybe_run_config_migrations",
        lambda min_data_version, *, job_id=None, ceiling=None: order.append("schema") or (1, 2),
    )
    monkeypatch.setattr(
        "hal0.registry.toml_retirement.retire_registry_toml",
        lambda *, job_id=None: order.append("registry_toml") or {"outcome": "absent"},
    )
    run_post_activation_migrations(job_id="j0")
    assert order == ["runner_system", "schema", "registry_toml"]


def test_an_update_converts_even_after_a_restore(_stub_every_pass: dict[str, list[Any]]) -> None:
    """An actual update ignores the restore marker; only boot honours it."""
    run_post_activation_migrations(job_id="j1")
    run_post_activation_migrations(job_id="j2", honour_restore_marker=True)
    assert _stub_every_pass["restore_marker"] == [False, True]


def test_runs_every_pass(_stub_every_pass: dict[str, list[Any]]) -> None:
    result = run_post_activation_migrations(job_id="j1")
    assert result == (1, 2)
    assert _stub_every_pass["runner_system"] == ["j1"]
    assert _stub_every_pass["config_migrations"] == [(1, "j1")]
    assert _stub_every_pass["registry_toml"] == ["j1"]
    assert _stub_every_pass["mtp"] == ["j1"]
    assert _stub_every_pass["vulkan_migration"] == ["j1"]
    assert _stub_every_pass["extra_args"] == ["j1"]
    assert _stub_every_pass["reasoning_format_none"] == [None]
    assert _stub_every_pass["converge_components"] == [
        {
            "job_id": "j1",
            "apply": True,
            "catalogue_sync": True,
            "engine": True,
            "hermes_install": True,
        }
    ]


def test_skip_catalogue_sync_omits_only_that_pass(_stub_every_pass: dict[str, list[Any]]) -> None:
    """check_outstanding_migrations passes skip_catalogue_sync=True so the
    boot-time safety net never runs the runner-image catalogue sync (the
    runner-images arm inside converge_components) — every OTHER pass must
    still run unaffected."""
    result = run_post_activation_migrations(job_id="j1", skip_catalogue_sync=True)
    assert result == (1, 2)
    assert _stub_every_pass["mtp"] == ["j1"]
    assert _stub_every_pass["vulkan_migration"] == ["j1"]
    assert _stub_every_pass["extra_args"] == ["j1"]
    assert _stub_every_pass["converge_components"] == [
        {
            "job_id": "j1",
            "apply": True,
            "catalogue_sync": False,
            "engine": True,
            "hermes_install": True,
        }
    ]


def test_runner_system_conversion_runs_before_the_schema_migration(
    monkeypatch: pytest.MonkeyPatch, _stub_every_pass: dict[str, list[Any]]
) -> None:
    """ADR-0028: the runner-system conversion rewrites raw TOML before config
    is validated (a removed runner key fails config load), so it is the first
    pass, ahead of the schema migration."""
    order: list[str] = []
    monkeypatch.setattr(
        "hal0.config.migrations.runner_system.migrate_runner_system",
        lambda *, dry_run=False, job_id=None, honour_restore_marker=False: order.append(
            "runner_system"
        ),
    )
    monkeypatch.setattr(
        "hal0.updater.updater._maybe_run_config_migrations",
        lambda min_data_version, *, job_id=None, ceiling=None: order.append("schema") or (1, 2),
    )
    run_post_activation_migrations(job_id="j1")
    assert order == ["runner_system", "schema"]


def test_runner_system_failure_is_fatal(
    monkeypatch: pytest.MonkeyPatch, _stub_every_pass: dict[str, list[Any]]
) -> None:
    """The conversion restores its own writes before raising; the update must
    see the failure and nothing after it may run on a half-converted box."""

    def _boom(*, dry_run=False, job_id=None, honour_restore_marker=False):
        raise Hal0Error("runner conversion failed", code="test.boom")

    monkeypatch.setattr("hal0.config.migrations.runner_system.migrate_runner_system", _boom)

    with pytest.raises(Hal0Error):
        run_post_activation_migrations(job_id="j9")

    assert _stub_every_pass["config_migrations"] == []
    assert _stub_every_pass["registry_toml"] == []
    assert _stub_every_pass["converge_components"] == []


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
    assert _stub_every_pass["vulkan_migration"] == ["j2"]  # ran despite mtp's failure
    assert _stub_every_pass["extra_args"] == ["j2"]
    assert [c["job_id"] for c in _stub_every_pass["converge_components"]] == [
        "j2"
    ]  # ran despite mtp's failure


def test_schema_migration_failure_propagates(
    monkeypatch: pytest.MonkeyPatch, _stub_every_pass: dict[str, list[Any]]
) -> None:
    """Unlike the data-cleanup passes, a schema-migration failure is
    NOT swallowed — the caller (commit()'s install_dir cleanup, or
    install.sh's `set -euo pipefail`) must see it and abort the
    activation rather than proceed on an unmigrated schema."""

    def _boom(min_data_version, *, job_id=None, ceiling=None):
        raise Hal0Error("schema migration exploded", code="test.boom")

    monkeypatch.setattr("hal0.updater.updater._maybe_run_config_migrations", _boom)

    with pytest.raises(Hal0Error):
        run_post_activation_migrations(job_id="j3")

    # None of the data-cleanup passes ran — the schema must land first.
    assert _stub_every_pass["mtp"] == []
    assert _stub_every_pass["vulkan_migration"] == []
    assert _stub_every_pass["extra_args"] == []
    assert _stub_every_pass["converge_components"] == []


@pytest.mark.parametrize("mode", ["raises", "reports_failed"])
def test_registry_toml_retirement_failure_never_blocks(
    monkeypatch: pytest.MonkeyPatch,
    _stub_every_pass: dict[str, list[Any]],
    caplog: pytest.LogCaptureFixture,
    mode: str,
) -> None:
    """registry.toml is inert, so failing to retire it must never block API
    boot or `hal0 update`: every later pass still runs, and the failure is
    logged under the event commit() scans for pass warnings."""

    def _retire(*, job_id=None):
        if mode == "raises":
            raise PermissionError("root-owned")
        return {"outcome": "failed", "reason": "cannot back it up: root-owned"}

    monkeypatch.setattr("hal0.registry.toml_retirement.retire_registry_toml", _retire)

    assert run_post_activation_migrations(job_id="j4") == (1, 2)

    assert _stub_every_pass["mtp"] == ["j4"]
    assert _stub_every_pass["extra_args"] == ["j4"]
    assert [c["job_id"] for c in _stub_every_pass["converge_components"]] == ["j4"]


def test_registry_toml_retire_event_is_a_reported_pass_warning() -> None:
    from hal0.cli.update_commands import _MIGRATION_PASS_LABELS
    from hal0.updater.updater import _NON_FATAL_MIGRATION_FAILURE_EVENTS

    assert "updater.registry_toml_retire_failed" in _NON_FATAL_MIGRATION_FAILURE_EVENTS
    assert set(_MIGRATION_PASS_LABELS) == set(_NON_FATAL_MIGRATION_FAILURE_EVENTS)


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

    check_outstanding_migrations(job_id="boot")

    assert _stub_every_pass["hermes_venv"] == [("boot", False)]
    assert _stub_every_pass["converge_components"] == [
        {
            "job_id": "boot",
            "apply": False,
            "catalogue_sync": False,
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
            "catalogue_sync": True,
            "engine": True,
            "hermes_install": True,
        }
    ]
