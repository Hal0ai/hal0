"""Pure builders behind the agent overview route."""

from __future__ import annotations

from pathlib import Path

from hal0.agents.hermes_release import Release
from hal0.agents.overview import (
    degraded_reasons,
    doctor_rows,
    hermes_phase,
    hermes_version,
    pi_phase,
    provision_summary,
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
    out = hermes_version(
        live,
        None,
        pin_ref="4d043038" + "0" * 32,
        component={"installed": "d923a5eb", "pinned": "4d043038", "status": "pending"},
    )
    assert out["live"]["describe"] == "v0.22.2"
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


def test_unit_states_maps_probe_results():
    assert unit_states(True, "active") == {"agent": "active", "gateway": "active"}
    assert unit_states(False, "failed") == {"agent": "inactive", "gateway": "failed"}
    assert unit_states(None, None) == {"agent": "unknown", "gateway": "unknown"}


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
        out[3]["fix"] == "cli" and out[3]["command"] == "sudo hal0 agent bootstrap hermes --repair"
    )
    assert out[4]["fix"] == "none" and "overrides" in out[4]["detail"]


def test_gateway_down_suppresses_api_unhealthy():
    out = degraded_reasons(
        **{**BASE, "units": {"agent": "active", "gateway": "inactive"}, "api_healthy": False}
    )
    assert [r["reason"] for r in out] == ["gateway_down"]
    assert out[0]["fix"] == "restart_gateway"


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


def test_non_ownership_doctor_failure_offers_repair():
    out = degraded_reasons(
        **{**BASE, "doctor": [{"id": "gateway_unit", "status": "fail", "detail": "stale"}]}
    )
    assert [(r["reason"], r["fix"], r["command"]) for r in out] == [
        ("doctor_drift", "repair", None)
    ]


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
