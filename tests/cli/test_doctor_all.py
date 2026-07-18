"""Tests for ``hal0 doctor all`` — the read-only evidence roll-up (§21.4).

The extra classifiers are pure (parsed JSON in, ``Check`` out). The
orchestration + exit-code roll-up is driven through the module's seams so no
live API is needed.
"""

from __future__ import annotations

import io
import json as jsonlib

import pytest
import typer
from rich.console import Console

from hal0.cli import doctor_all as da
from hal0.cli.doctor_verify import Check

# ── check_auth_posture ────────────────────────────────────────────────────────


def test_auth_unreachable_warns() -> None:
    c = da.check_auth_posture(None)
    assert c.status == "warn" and c.key == "auth"


def test_auth_open_passes() -> None:
    c = da.check_auth_posture({"auth_required": False, "has_admin_key": False})
    assert c.status == "pass"
    assert "open" in c.detail


def test_auth_required_no_key_warns() -> None:
    c = da.check_auth_posture({"auth_required": True, "has_admin_key": False})
    assert c.status == "warn"
    assert "HAL0_ADMIN_KEY" in c.detail


def test_auth_required_with_key_passes() -> None:
    c = da.check_auth_posture({"auth_required": True, "has_admin_key": True})
    assert c.status == "pass"


# ── check_model_store ─────────────────────────────────────────────────────────


def test_model_store_unreachable_warns() -> None:
    assert da.check_model_store(None).status == "warn"


def test_model_store_clean_when_all_present() -> None:
    models = {"models": [{"id": "a", "path": "/m/a.gguf"}, {"id": "b", "path": "/m/b.gguf"}]}
    c = da.check_model_store(models, exists=lambda _p: True)
    assert c.status == "pass"
    assert "2 registered" in c.detail


def test_model_store_fails_on_dangling() -> None:
    models = [{"id": "a", "path": "/m/a.gguf"}, {"id": "b", "path": "/m/gone.gguf"}]
    c = da.check_model_store(models, exists=lambda p: p == "/m/a.gguf")
    assert c.status == "fail"
    assert not c.critical  # actionable but non-blocking
    assert "gone.gguf" in c.detail or "b" in c.detail


def test_model_store_bad_payload_warns() -> None:
    assert da.check_model_store(12345).status == "warn"


# ── check_migrations ──────────────────────────────────────────────────────────


def test_migrations_planner_unavailable_passes() -> None:
    assert da.check_migrations(None).status == "pass"


def test_migrations_current_passes() -> None:
    assert da.check_migrations((0, 0)).status == "pass"


def test_migrations_pending_warns() -> None:
    c = da.check_migrations((5, 2))
    assert c.status == "warn"
    assert "5 link" in c.detail


# ── check_ports ───────────────────────────────────────────────────────────────


def test_ports_unreachable_warns() -> None:
    assert da.check_ports(None).status == "warn"


def test_ports_none_bound_passes() -> None:
    c = da.check_ports([])
    assert c.status == "pass"


def test_ports_lists_bound() -> None:
    c = da.check_ports([{"port": 8081}, {"port": 8082}, {"name": "x"}])
    assert c.status == "pass"
    assert "8081" in c.detail and "8082" in c.detail


# ── overall_verdict + exit codes ──────────────────────────────────────────────


def _c(status: str, *, critical: bool = False) -> Check:
    return Check("k", "L", status, "d", critical=critical)


def test_verdict_ok_when_only_warn() -> None:
    assert da.overall_verdict([_c("pass"), _c("warn")]) == "ok"


def test_verdict_fail_on_noncritical_fail() -> None:
    assert da.overall_verdict([_c("pass"), _c("fail")]) == "fail"


def test_verdict_critical_on_critical_fail() -> None:
    assert da.overall_verdict([_c("fail", critical=True), _c("fail")]) == "critical"


def test_exit_code_mapping() -> None:
    assert da._exit_code([_c("pass")]) == 0
    assert da._exit_code([_c("fail")]) == 1
    assert da._exit_code([_c("fail", critical=True)]) == 2


# ── build_all_checks orchestration ────────────────────────────────────────────


def test_build_all_checks_composes_verify_plus_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        da,
        "gather_payloads",
        lambda base=None: {
            "health": {"version": "1"},
            "urls": {"api": "http://halo.local:8080"},
            "system": {"checks": {"slot_manager": {"slots": 1, "errored": []}}},
            "capabilities": {"selections": {"embed": "x"}},
            "memory": {"engine": None},
            "services": {"services": []},
        },
    )

    def _fake_get(path: str, base=None):
        return {
            "/api/auth/status": {"auth_required": False, "has_admin_key": False},
            "/api/models": {"models": [{"id": "a", "path": "/m/a"}]},
            "/api/slots": [{"port": 8081}],
        }.get(path)

    monkeypatch.setattr(da, "_get_any", _fake_get)
    monkeypatch.setattr(
        "hal0.cli.doctor_commands.pending_layout_migration", lambda: (0, 0)
    )
    # model file existence: pretend present so no spurious fail.
    monkeypatch.setattr(da.Path, "exists", lambda self: True)

    checks = da.build_all_checks()
    keys = [c.key for c in checks]
    # 7 verify rows + 4 extras.
    assert keys[-4:] == ["auth", "models", "migrations", "ports"]
    assert "api" in keys and "runners" in keys
    assert da.overall_verdict(checks) == "ok"


# ── command ───────────────────────────────────────────────────────────────────


def test_command_json_emits_rows_and_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        da, "build_all_checks", lambda: [_c("pass"), _c("fail")]
    )
    buf = io.StringIO()
    monkeypatch.setattr(da, "console", Console(file=buf))
    with pytest.raises(typer.Exit) as exc:
        da.doctor_all_cmd(json_output=True)
    assert exc.value.exit_code == 1
    rows = jsonlib.loads(buf.getvalue())
    assert len(rows) == 2
    assert rows[0]["status"] == "pass"


def test_command_human_exit_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(da, "build_all_checks", lambda: [_c("pass"), _c("warn")])
    monkeypatch.setattr(da, "console", Console(file=io.StringIO()))
    with pytest.raises(typer.Exit) as exc:
        da.doctor_all_cmd(json_output=False)
    assert exc.value.exit_code == 0
