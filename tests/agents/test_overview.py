"""Pure builders behind the agent overview route."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hal0.agents.hermes_release import Release
from hal0.agents.overview import (
    degraded_reasons,
    doctor_rows,
    hermes_phase,
    hermes_version,
    pi_installed_versions,
    pi_phase,
    pi_pins,
    pi_profile_checks,
    pi_update_available,
    provision_summary,
    unit_file_enabled,
    unit_states,
)


def test_hermes_version_marks_update_when_installed_differs_from_pinned():
    live = Release(
        id="d923a5eb487a-20261022T021933Z",
        path=Path("/x"),
        data={
            "ref": "d923a5eb" + "0" * 32,
            "describe": "v0.22.2",
            "built_at": "2026-10-22T02:19:58Z",
            "python_minor": "3.14",
            "extras": ["web", "mcp"],
            "platform_extras": ["telegram"],
            "hal0_version": "1.5.0",
        },
    )
    previous = Release(
        id="3ef6bbd2c1a0-20261001T000000Z",
        path=Path("/y"),
        data={
            "ref": "3ef6bbd2" + "0" * 32,
            "describe": "v0.19.0",
            "built_at": "2026-10-01T00:00:00Z",
        },
    )
    out = hermes_version(
        live,
        previous,
        pin_ref="4d043038" + "0" * 32,
        component={"installed": "d923a5eb", "pinned": "4d043038", "status": "pending"},
    )
    assert out["live"] == {
        "id": "d923a5eb487a-20261022T021933Z",
        "ref": "d923a5eb" + "0" * 32,
        "describe": "v0.22.2",
        "built_at": "2026-10-22T02:19:58Z",
        "python_minor": "3.14",
        "extras": ["web", "mcp"],
        "platform_extras": ["telegram"],
        "hal0_version": "1.5.0",
    }
    assert out["previous"] == {
        "id": "3ef6bbd2c1a0-20261001T000000Z",
        "ref": "3ef6bbd2" + "0" * 32,
        "describe": "v0.19.0",
    }
    assert out["pin"] == {"ref": "4d043038" + "0" * 32, "describe": None}
    assert out["component"] == {"installed": "d923a5eb", "pinned": "4d043038", "status": "pending"}
    assert out["update_available"] is True


def test_hermes_version_converged_is_not_update():
    out = hermes_version(
        None,
        None,
        pin_ref="a" * 40,
        component={"installed": "x", "pinned": "x", "status": "converged"},
    )
    assert out["update_available"] is False and out["live"] is None


def test_hermes_version_held_at_an_override_is_not_an_update():
    out = hermes_version(
        None,
        None,
        pin_ref="a" * 40,
        component={"installed": "d923a5eb", "pinned": "4d043038", "status": "override"},
    )
    assert out["update_available"] is False


def test_pending_statuses_have_one_owner():
    from hal0.agents import overview
    from hal0.components import status

    assert overview._PENDING_STATUSES is status._PENDING_STATUSES


def test_unit_states_maps_probe_results():
    assert unit_states(True, "active") == {
        "agent": "active",
        "gateway": "active",
        "gateway_enabled": None,
    }
    assert unit_states(False, "failed", gateway_enabled=False) == {
        "agent": "inactive",
        "gateway": "failed",
        "gateway_enabled": False,
    }
    assert unit_states(None, None) == {
        "agent": "unknown",
        "gateway": "unknown",
        "gateway_enabled": None,
    }


@pytest.mark.parametrize(
    ("unit_file_state", "expected"),
    [
        ("disabled", False),
        ("masked", False),
        ("masked-runtime", False),
        ("enabled", True),
        ("enabled-runtime", True),
        ("static", True),
        ("linked", True),
        ("generated", True),
        ("transient", True),
        ("linked-runtime", None),
        ("alias", None),
        ("unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_unit_file_enabled(unit_file_state, expected):
    assert unit_file_enabled(unit_file_state) is expected


def test_provision_summary_counts_and_lifts_config_write_details():
    data = {
        "hal0_version": "1.5.0",
        "hermes_version": "d923a5eb487a",
        "completed_at": None,
        "phases": {
            "install": {"status": "fail", "at": "t1"},
            "smoke_tests": {"status": "warn", "failure_count": 3, "at": "t2"},
            "config_write": {
                "status": "ok",
                "at": "t3",
                "details": {
                    "primary_model": "m1",
                    "delegation_model": "m2",
                    "chat_slot_count": 6,
                    "mcp_server_count": 2,
                    "memory_provider": "hal0-memory",
                    "persona": "Hermes",
                    "terminal_tool": "on",
                    "approvals_mode": "smart",
                },
            },
        },
    }
    out = provision_summary(data)
    assert out["present"] is True and (out["ok"], out["warn"], out["fail"]) == (1, 1, 1)
    assert out["phases"][0] == {
        "name": "install",
        "status": "fail",
        "at": "t1",
        "failure_count": None,
        "reason": None,
    }
    assert (
        out["terminal_tool"] == "on"
        and out["primary_model"] == "m1"
        and out["approvals_mode"] == "smart"
    )


def test_provision_summary_reports_newest_phase_time():
    data = {
        "phases": {
            "install": {"status": "fail", "at": "2026-10-21T23:49:54Z"},
            "smoke_tests": {"status": "ok", "at": "2026-10-22T01:00:00+00:00"},
            "config_write": {"status": "ok", "at": "2026-10-21T12:00:00Z"},
            "mcp_wire": {"status": "ok"},
        }
    }
    assert provision_summary(data)["reported_at"] == "2026-10-22T01:00:00+00:00"
    assert provision_summary(None)["reported_at"] is None
    assert (
        provision_summary({"phases": {"install": {"status": "ok", "at": "t1"}}})["reported_at"]
        is None
    )


def test_provision_summary_absent():
    out = provision_summary(None)
    assert out["present"] is False and out["phases"] == [] and out["terminal_tool"] is None


def test_doctor_rows_map_statuses():
    rows = doctor_rows(
        {"status": "ok", "detail": "py"},
        {"status": "absent", "detail": "no home"},
        [{"path": "/a", "label": "HERMES_HOME tree", "status": "drift", "detail": "root:root"}],
        None,
    )
    assert rows == [
        {"id": "interpreter", "status": "ok", "detail": "py"},
        {"id": "plugins", "status": "absent", "detail": "no home"},
        {"id": "ownership", "status": "fail", "detail": "HERMES_HOME tree: root:root"},
    ]


BASE = dict(
    units={"agent": "active", "gateway": "active"},
    api_healthy=True,
    provision={"present": True, "fail": 0, "warn": 0, "phases": []},
    doctor=[],
    approvals={"in_sync": True, "source": "setting", "mode": "smart"},
)


def test_no_reasons_when_healthy():
    assert degraded_reasons(**BASE) == []


def test_smoke_skips_alone_raise_no_provision_warn(tmp_path: Path):
    """A repair on a box whose model slots are cold: the gated smoke probes
    skip (so the smoke test does not cold-load a slot), the phase stays ``ok``,
    and the report the provisioner writes carries no ``failure_count`` — so a
    healthy box shows no "provision warnings"."""
    from hal0.agents import hermes_provision as hp

    skipped = [
        "chat_completions: skipped: chat model not loaded (slot agent is offline; "
        "skipped so the smoke test does not cold-load it)",
        "memory_roundtrip: skipped: memory engine's model not loaded (hal0/utility: "
        "slot utility is offline; skipped so the smoke test does not cold-load it)",
    ]
    report = hp.InstallReport(hermes_home="/h", venv="/v", agent_id="hermes")
    report.steps.append(hp.InstallStep(name="config_write", status="ok"))
    report.steps.append(
        hp.InstallStep(
            name="smoke_tests",
            status=hp.PhaseStatus.OK.value,
            details={"results": {}, "failures": [], "skipped": skipped},
        )
    )
    hp._write_run_report(report, tmp_path)
    data = json.loads((tmp_path / hp._STATE_FILE_NAME).read_text())

    smoke = data["phases"]["smoke_tests"]
    assert "failure_count" not in smoke and smoke["skipped_count"] == 2
    summary = provision_summary(data)
    assert (summary["warn"], summary["fail"]) == (0, 0)
    assert [p["failure_count"] for p in summary["phases"]] == [None, None]
    assert degraded_reasons(**{**BASE, "provision": summary}) == []


def test_stale_provision_report_does_not_degrade():
    stale = {
        "present": True,
        "fail": 1,
        "warn": 1,
        "stale": True,
        "phases": [{"name": "install", "status": "fail", "failure_count": 2}],
    }
    assert degraded_reasons(**{**BASE, "provision": stale}) == []
    # Only the two provision reasons go quiet; every other reason still fires.
    out = degraded_reasons(**{**BASE, "provision": stale, "api_healthy": False})
    assert [r["reason"] for r in out] == ["api_unhealthy"]


def test_phases_older_than_the_live_release_do_not_degrade():
    """#2914: a converge refreshes only ``install``; older phases stay quiet, newer ones count."""
    provision = {
        "present": True,
        "fail": 2,
        "warn": 1,
        "stale": False,
        "phases": [
            {"name": "install", "status": "ok", "stale": False},
            {"name": "mcp_wire", "status": "fail", "stale": True},
            {"name": "smoke_tests", "status": "warn", "failure_count": 1, "stale": True},
        ],
    }
    assert degraded_reasons(**{**BASE, "provision": provision}) == []
    provision["phases"].append({"name": "config_write", "status": "fail", "stale": False})
    out = degraded_reasons(**{**BASE, "provision": provision})
    assert [(r["reason"], r["detail"]) for r in out] == [
        ("provision_failed", "1 provision phase(s) failed: config_write")
    ]


def test_reasons_in_display_order_with_fixes():
    out = degraded_reasons(
        **{
            **BASE,
            "api_healthy": False,
            "provision": {"present": True, "fail": 1, "warn": 1, "phases": []},
            "doctor": [{"id": "ownership", "status": "fail", "detail": "d"}],
            "approvals": {"in_sync": False, "source": "overrides", "mode": "smart"},
        }
    )
    assert [r["reason"] for r in out] == [
        "api_unhealthy",
        "provision_failed",
        "provision_warn",
        "doctor_drift",
        "approvals_drift",
    ]
    assert out[0]["fix"] == "restart_gateway"
    assert (
        out[3]["fix"] == "cli"
        and out[3]["command"] == "sudo hal0 agent reprovision hermes --repair"
    )
    assert out[4]["fix"] == "none" and "overrides" in out[4]["detail"]


def test_gateway_down_suppresses_api_unhealthy():
    out = degraded_reasons(
        **{**BASE, "units": {"agent": "active", "gateway": "inactive"}, "api_healthy": False}
    )
    assert [r["reason"] for r in out] == ["gateway_down"]
    assert out[0]["fix"] == "restart_gateway"


def test_gateway_absent_raises_no_gateway_down_the_doctor_row_covers_it():
    assert degraded_reasons(**{**BASE, "units": {"agent": "active", "gateway": "absent"}}) == []
    out = degraded_reasons(
        **{
            **BASE,
            "units": {"agent": "active", "gateway": "absent"},
            "doctor": [{"id": "gateway_unit", "status": "fail", "detail": "unit missing"}],
        }
    )
    assert [(r["reason"], r["fix"], r["command"]) for r in out] == [
        ("doctor_drift", "cli", "sudo hal0 agent install hermes")
    ]


def test_gateway_down_offers_a_restart_for_inactive_and_failed():
    for state in ("inactive", "failed"):
        (reason,) = degraded_reasons(**{**BASE, "units": {"agent": "active", "gateway": state}})
        assert reason["reason"] == "gateway_down" and reason["fix"] == "restart_gateway"


def test_gateway_the_operator_turned_off_is_not_degraded():
    for state in ("inactive", "failed"):
        units = {"agent": "active", "gateway": state, "gateway_enabled": False}
        assert degraded_reasons(**{**BASE, "units": units}) == []
    for enabled in (True, None):
        units = {"agent": "active", "gateway": "inactive", "gateway_enabled": enabled}
        out = degraded_reasons(**{**BASE, "units": units})
        assert [r["reason"] for r in out] == ["gateway_down"]


def test_api_unhealthy_waits_while_the_gateway_settles():
    unhealthy = {**BASE, "api_healthy": False}
    assert degraded_reasons(**unhealthy, gateway_settling=True) == []
    out = degraded_reasons(**unhealthy, gateway_settling=False)
    assert [r["reason"] for r in out] == ["api_unhealthy"]


def test_gateway_down_is_listed_before_the_other_reasons():
    out = degraded_reasons(
        **{
            **BASE,
            "units": {"agent": "active", "gateway": "inactive"},
            "provision": {"present": True, "fail": 1, "warn": 0, "phases": []},
        }
    )
    assert [r["reason"] for r in out] == ["gateway_down", "provision_failed"]
    assert out[1]["fix"] == "repair"


def test_unknown_gateway_state_is_not_a_fault():
    out = degraded_reasons(**{**BASE, "units": {"agent": "active", "gateway": "unknown"}})
    assert out == []


def test_doctor_drift_fires_on_any_not_ok_row():
    warn_only = degraded_reasons(
        **{**BASE, "doctor": [{"id": "plugins", "status": "warn", "detail": "trees differ"}]}
    )
    assert warn_only == [
        {
            "reason": "doctor_drift",
            "detail": "plugins: trees differ",
            "fix": "repair",
            "command": None,
        }
    ]

    mixed = degraded_reasons(
        **{
            **BASE,
            "doctor": [
                {"id": "interpreter", "status": "absent", "detail": "no Hermes venv"},
                {"id": "ownership", "status": "fail", "detail": "HERMES_HOME tree: root:root"},
            ],
        }
    )
    assert len(mixed) == 1 and mixed[0]["reason"] == "doctor_drift"
    assert mixed[0]["fix"] == "cli"
    assert mixed[0]["command"] == "sudo hal0 agent reprovision hermes --repair"
    assert mixed[0]["detail"] == "ownership: HERMES_HOME tree: root:root"


def test_interpreter_and_plugin_drift_offer_repair():
    for row_id in ("interpreter", "plugins"):
        out = degraded_reasons(
            **{**BASE, "doctor": [{"id": row_id, "status": "warn", "detail": "stale"}]}
        )
        assert [(r["reason"], r["fix"], r["command"]) for r in out] == [
            ("doctor_drift", "repair", None)
        ]


def test_gateway_unit_drift_needs_the_root_install_command():
    out = degraded_reasons(
        **{**BASE, "doctor": [{"id": "gateway_unit", "status": "fail", "detail": "unit missing"}]}
    )
    assert out == [
        {
            "reason": "doctor_drift",
            "detail": "gateway_unit: unit missing",
            "fix": "cli",
            "command": "sudo hal0 agent install hermes",
        }
    ]


def test_unreadable_gateway_unit_row_has_no_fix():
    """A read error is not a missing unit: the install command would not help."""
    out = degraded_reasons(
        **{
            **BASE,
            "doctor": [{"id": "gateway_unit", "status": "unreadable", "detail": "cannot read"}],
        }
    )
    assert out == [
        {
            "reason": "doctor_drift",
            "detail": "gateway_unit: cannot read",
            "fix": "none",
            "command": None,
        }
    ]


def test_gateway_down_detail_can_name_a_crash_loop():
    (reason,) = degraded_reasons(
        **{**BASE, "units": {"agent": "active", "gateway": "failed"}},
        gateway_note="crash-looping (auto-restart)",
    )
    assert reason["detail"] == "hermes-gateway.service is crash-looping (auto-restart)"
    assert reason["fix"] == "restart_gateway"


def test_doctor_drift_is_one_reason_per_distinct_fix():
    out = degraded_reasons(
        **{
            **BASE,
            "doctor": [
                {"id": "interpreter", "status": "warn", "detail": "old sqlite"},
                {"id": "plugins", "status": "warn", "detail": "trees differ"},
                {"id": "ownership", "status": "fail", "detail": "root:root"},
                {"id": "gateway_unit", "status": "fail", "detail": "unit missing"},
            ],
        }
    )
    assert [(r["fix"], r["command"], r["detail"]) for r in out] == [
        ("repair", None, "interpreter: old sqlite; plugins: trees differ"),
        ("cli", "sudo hal0 agent reprovision hermes --repair", "ownership: root:root"),
        ("cli", "sudo hal0 agent install hermes", "gateway_unit: unit missing"),
    ]
    assert {r["reason"] for r in out} == {"doctor_drift"}


def test_approvals_drift_resync_when_setting_owned():
    out = degraded_reasons(
        **{**BASE, "approvals": {"in_sync": False, "source": "setting", "mode": "manual"}}
    )
    assert out == [
        {
            "reason": "approvals_drift",
            "detail": "config.yaml does not match the setting (manual)",
            "fix": "resync_approvals",
            "command": None,
        }
    ]


def test_hermes_phase_precedence():
    d = [{"reason": "gateway_down"}]
    assert (
        hermes_phase(installed=False, status="", unit_active=None, degraded=[], job=None)
        == "not_installed"
    )
    assert (
        hermes_phase(
            installed=True,
            status="installed",
            unit_active=True,
            degraded=d,
            job={"state": "running"},
        )
        == "working"
    )
    assert (
        hermes_phase(installed=True, status="broken", unit_active=True, degraded=d, job=None)
        == "broken"
    )
    assert (
        hermes_phase(installed=True, status="installed", unit_active=False, degraded=d, job=None)
        == "down"
    )
    assert (
        hermes_phase(installed=True, status="installed", unit_active=True, degraded=d, job=None)
        == "degraded"
    )
    assert (
        hermes_phase(installed=True, status="installed", unit_active=True, degraded=[], job=None)
        == "ready"
    )
    assert (
        hermes_phase(installed=True, status="installed", unit_active=None, degraded=[], job=None)
        == "unknown"
    )


def test_pi_phase():
    assert pi_phase(installed=False, status="") == "not_installed"
    assert pi_phase(installed=True, status="broken") == "broken"
    assert pi_phase(installed=True, status="installed") == "installed"


def _fake_npm_tree(tmp_path, pi_v="0.84.4", adapter_v="2.31.0"):
    root = tmp_path / "lib" / "node_modules"
    pkg = root / "@earendil-works" / "pi-coding-agent"
    (pkg / "dist" / "bundle").mkdir(parents=True)
    (pkg / "package.json").write_text(
        json.dumps({"name": "@earendil-works/pi-coding-agent", "version": pi_v})
    )
    (pkg / "dist" / "bundle" / "cli.js").write_text("")
    (root / "pi-mcp-adapter").mkdir()
    (root / "pi-mcp-adapter" / "package.json").write_text(
        json.dumps({"name": "pi-mcp-adapter", "version": adapter_v})
    )
    link = tmp_path / "bin" / "pi"
    link.parent.mkdir()
    link.symlink_to(pkg / "dist" / "bundle" / "cli.js")
    return link


def test_pi_installed_versions_walks_up_from_the_symlink(tmp_path):
    assert pi_installed_versions(_fake_npm_tree(tmp_path)) == {"pi": "0.84.4", "adapter": "2.31.0"}


def test_pi_installed_versions_none_without_binary():
    assert pi_installed_versions(None) is None


def test_pi_installed_versions_adapter_absent(tmp_path):
    link = _fake_npm_tree(tmp_path)
    (tmp_path / "lib" / "node_modules" / "pi-mcp-adapter" / "package.json").unlink()
    assert pi_installed_versions(link) == {"pi": "0.84.4", "adapter": None}


def test_pi_installed_versions_none_for_a_non_npm_binary(tmp_path):
    binary = tmp_path / "pi"
    binary.write_text("#!/bin/sh\n")
    assert pi_installed_versions(binary) is None


def test_pi_pins_from_installer_script(tmp_path):
    s = tmp_path / "pi.sh"
    s.write_text(
        'PI_PKG="@earendil-works/pi-coding-agent@1.2.0"\nADAPTER_PKG="pi-mcp-adapter@5.3.0"\n'
    )
    assert pi_pins(s) == {"pi": "1.2.0", "adapter": "5.3.0"}


def test_pi_pins_raises_when_a_pin_is_missing(tmp_path):
    s = tmp_path / "pi.sh"
    s.write_text('PI_PKG="@earendil-works/pi-coding-agent@1.2.0"\n')
    with pytest.raises(ValueError, match="ADAPTER_PKG"):
        pi_pins(s)


def test_pi_update_available_compares_versions():
    assert (
        pi_update_available(
            {"pi": "0.84.4", "adapter": "2.31.0"}, {"pi": "1.2.0", "adapter": "5.3.0"}
        )
        is True
    )
    assert (
        pi_update_available(
            {"pi": "1.2.0", "adapter": "5.3.0"}, {"pi": "1.2.0", "adapter": "5.3.0"}
        )
        is False
    )
    assert pi_update_available(None, {"pi": "1.2.0", "adapter": "5.3.0"}) is False


def test_pi_update_available_when_only_the_adapter_lags_or_is_missing():
    pins = {"pi": "1.2.0", "adapter": "5.3.0"}
    assert pi_update_available({"pi": "1.2.0", "adapter": "5.2.9"}, pins) is True
    assert pi_update_available({"pi": "1.2.0", "adapter": None}, pins) is True
    assert pi_update_available({"pi": "1.3.0", "adapter": "5.4.0"}, pins) is False


def test_pi_checks_null_when_home_unreadable():
    out = pi_profile_checks(
        marker={"home": "/root"},
        versions={"pi": "0.84.4", "adapter": "2.31.0"},
        pins={"pi": "1.2.0", "adapter": "5.3.0"},
        home_readable=False,
        home=Path("/root"),
    )
    by = {c["id"]: c for c in out}
    assert by["binary"]["ok"] is True
    assert by["provider_ext"]["ok"] is None
    assert by["provider_ext"]["detail"] == "home not readable from the daemon"
    assert by["adapter_gate"]["ok"] is False and "5.0" in by["adapter_gate"]["detail"]


def test_pi_checks_read_home_when_readable(tmp_path):
    agent = tmp_path / ".pi" / "agent"
    (agent / "extensions" / "hal0-provider").mkdir(parents=True)
    (agent / "extensions" / "hal0-provider" / "index.ts").write_text("")
    (agent / "themes").mkdir()
    (agent / "themes" / "hal0.json").write_text("{}")
    (agent / "mcp.json").write_text(json.dumps({"mcpServers": {"hal0-memory": {"url": "u"}}}))
    out = {
        c["id"]: c["ok"]
        for c in pi_profile_checks(
            marker={"home": str(tmp_path)},
            versions={"pi": "1.2.0", "adapter": "5.3.0"},
            pins={"pi": "1.2.0", "adapter": "5.3.0"},
            home_readable=True,
            home=tmp_path,
        )
    }
    assert out == {
        "binary": True,
        "provider_ext": True,
        "hindsight_ext": False,
        "theme": True,
        "mcp_entry": True,
        "hindsight_config": False,
        "adapter_gate": True,
    }


def test_pi_checks_in_display_order_and_without_binary():
    out = pi_profile_checks(
        marker=None,
        versions=None,
        pins={"pi": "1.2.0", "adapter": "5.3.0"},
        home_readable=False,
        home=None,
    )
    assert [c["id"] for c in out] == [
        "binary",
        "provider_ext",
        "hindsight_ext",
        "theme",
        "mcp_entry",
        "hindsight_config",
        "adapter_gate",
    ]
    assert out[0]["ok"] is False and out[-1]["ok"] is False
    assert out[1]["ok"] is None and out[1]["detail"] == "home not readable from the daemon"


def test_pi_checks_do_not_read_the_marker_home_when_home_is_none(tmp_path):
    (tmp_path / ".pi" / "agent").mkdir(parents=True)
    (tmp_path / ".pi" / "agent" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"hal0-memory": {}}})
    )
    out = {
        c["id"]: c["ok"]
        for c in pi_profile_checks(
            marker={"home": str(tmp_path)},
            versions={"pi": "1.2.0", "adapter": "5.3.0"},
            pins={"pi": "1.2.0", "adapter": "5.3.0"},
            home_readable=True,
            home=None,
        )
    }
    assert [out[k] for k in ("provider_ext", "hindsight_ext", "theme", "mcp_entry")] == [None] * 4
    assert out["hindsight_config"] is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_pi_checks_answer_none_for_untraversable_paths_under_a_readable_home(tmp_path):
    (tmp_path / ".pi" / "agent").mkdir(parents=True)
    (tmp_path / ".pi" / "agent" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"hal0-memory": {}}})
    )
    (tmp_path / ".hindsight").mkdir()
    locked = [tmp_path / ".pi", tmp_path / ".hindsight"]
    for path in locked:
        path.chmod(0o000)
    try:
        checks = {
            c["id"]: c
            for c in pi_profile_checks(
                marker={"home": str(tmp_path)},
                versions={"pi": "1.2.0", "adapter": "5.3.0"},
                pins={"pi": "1.2.0", "adapter": "5.3.0"},
                home_readable=True,
                home=tmp_path,
            )
        }
    finally:
        for path in locked:
            path.chmod(0o755)
    for check_id in ("provider_ext", "hindsight_ext", "theme", "mcp_entry", "hindsight_config"):
        assert checks[check_id]["ok"] is None, check_id
        assert checks[check_id]["detail"].endswith("not readable from the daemon"), check_id
    assert checks["binary"]["ok"] is True and checks["adapter_gate"]["ok"] is True


def test_missing_unit_keys_are_unknown_not_a_fault():
    assert degraded_reasons(**{**BASE, "units": {}}) == []
    assert degraded_reasons(**{**BASE, "units": {"agent": "active", "gateway": None}}) == []
