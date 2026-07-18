"""Tests for ``hal0 doctor --verify`` — the WS-K report card (issue #1114).

The classifiers are pure functions over parsed API payloads, so we exercise
each check + the roll-up + the render + the tolerant orchestration without a
live API (the fetch seam is monkeypatched).
"""

from __future__ import annotations

import io

import pytest
import typer
from rich.console import Console

from hal0.cli import doctor_verify as dv
from hal0.cli.doctor_commands import doctor

# ── individual classifiers ─────────────────────────────────────────────────────


def test_check_api_pass_and_critical_fail() -> None:
    ok = dv.check_api({"status": "ok", "version": "0.9.1"})
    assert ok.status == "pass" and not ok.critical and "0.9.1" in ok.detail

    down = dv.check_api(None)
    assert down.status == "fail" and down.critical is True


def test_check_runners_zero_healthy_is_critical() -> None:
    # slots present but ALL errored → zero healthy → critical fail
    payload = {"checks": {"slot_manager": {"ok": False, "slots": 2, "errored": ["a", "b"]}}}
    c = dv.check_runners(payload)
    assert c.status == "fail" and c.critical is True

    # no slots configured at all → still critical (zero healthy)
    c0 = dv.check_runners({"checks": {"slot_manager": {"ok": True, "slots": 0, "errored": []}}})
    assert c0.status == "fail" and c0.critical is True


def test_check_runners_partial_errored_is_warn_not_critical() -> None:
    payload = {"checks": {"slot_manager": {"ok": False, "slots": 3, "errored": ["b"]}}}
    c = dv.check_runners(payload)
    assert c.status == "warn" and not c.critical and "2/3" in c.detail


def test_check_runners_all_healthy_pass() -> None:
    payload = {"checks": {"slot_manager": {"ok": True, "slots": 2, "errored": []}}}
    c = dv.check_runners(payload)
    assert c.status == "pass" and "2/2" in c.detail


def test_check_runners_unreachable_is_critical() -> None:
    assert dv.check_runners(None).critical is True


def test_check_dns_local_resolves_and_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dv.socket, "gethostbyname", lambda h: "192.0.2.1")
    ok = dv.check_dns({"api": "http://hal0.local:8080"})
    assert ok.status == "pass" and not ok.critical

    def _boom(_h: str) -> str:
        raise OSError("no mdns")

    monkeypatch.setattr(dv.socket, "gethostbyname", _boom)
    warn = dv.check_dns({"api": "http://hal0.local:8080"})
    assert warn.status == "warn" and not warn.critical  # never blocks


def test_check_dns_plain_ip_is_pass_no_mdns() -> None:
    c = dv.check_dns({"api": "http://192.168.1.10:8080"})
    assert c.status == "pass" and "no mDNS" in c.detail


def test_check_capabilities_active_vs_none() -> None:
    active = dv.check_capabilities({"selections": {"embed": {"x": 1}, "voice": {}}})
    assert active.status == "pass" and "embed" in active.detail
    assert dv.check_capabilities({"selections": {}}).status == "warn"
    assert dv.check_capabilities(None).status == "warn"


def test_check_memory_states() -> None:
    assert dv.check_memory({"engine": None}).status == "pass"  # disabled is fine
    assert dv.check_memory({"engine": "hindsight", "reachable": False}).status == "warn"
    ok = dv.check_memory({"engine": "hindsight", "reachable": True, "banks_total": 3})
    assert ok.status == "pass" and "3 bank" in ok.detail


def test_service_checks_up_and_down() -> None:
    services = {
        "services": [
            {"id": "openwebui", "up": True, "detail": "reachable — /health ok"},
            {"id": "hermes", "up": False, "detail": "systemd unit inactive or absent"},
        ]
    }
    assert dv.check_openwebui(services).status == "pass"
    hermes = dv.check_hermes(services)
    assert hermes.status == "warn" and not hermes.critical  # optional → never critical


# ── roll-up ─────────────────────────────────────────────────────────────────────


def test_overall_status_precedence() -> None:
    crit = dv.build_checks(
        health=None,  # critical fail
        urls=None,
        system={"checks": {"slot_manager": {"slots": 1, "errored": []}}},
        capabilities={"selections": {"embed": {"x": 1}}},
        memory={"engine": None},
        services={"services": []},
    )
    assert dv.overall_status(crit) == "critical"

    warn = dv.build_checks(
        health={"version": "0.9.1"},
        urls={"api": "http://192.168.1.10:8080"},
        system={"checks": {"slot_manager": {"slots": 1, "errored": []}}},
        capabilities={"selections": {}},  # warn
        memory={"engine": None},
        services={"services": [{"id": "openwebui", "up": True}, {"id": "hermes", "up": True}]},
    )
    assert dv.overall_status(warn) == "warn"

    ok = dv.build_checks(
        health={"version": "0.9.1"},
        urls={"api": "http://192.168.1.10:8080"},
        system={"checks": {"slot_manager": {"slots": 2, "errored": []}}},
        capabilities={"selections": {"embed": {"x": 1}}},
        memory={"engine": None},
        services={"services": [{"id": "openwebui", "up": True}, {"id": "hermes", "up": True}]},
    )
    assert dv.overall_status(ok) == "ok"


# ── render + orchestration ──────────────────────────────────────────────────────


def _render_to_str(checks: list[dv.Check], urls: dict | None) -> str:
    buf = io.StringIO()
    con = Console(file=buf, force_terminal=False, width=100)
    dv.render_report(con, checks, urls)
    return buf.getvalue()


def test_render_report_includes_urls_and_links() -> None:
    checks = dv.build_checks(
        health={"version": "0.9.1"},
        urls={
            "api": "http://hal0.local:8080",
            "openwebui_enabled": True,
            "openwebui": "http://hal0.local:3001",
        },
        system={"checks": {"slot_manager": {"slots": 1, "errored": []}}},
        capabilities={"selections": {"embed": {"x": 1}}},
        memory={"engine": None},
        services={"services": [{"id": "openwebui", "up": True}, {"id": "hermes", "up": True}]},
    )
    out = _render_to_str(
        checks,
        {
            "api": "http://hal0.local:8080",
            "openwebui_enabled": True,
            "openwebui": "http://hal0.local:3001",
        },
    )
    assert "http://hal0.local:8080" in out
    assert "http://hal0.local:3001" in out
    assert dv.FIRST_RUN_GUIDE_URL in out
    assert dv.DISCORD_URL in out
    assert "PASS" in out


def test_run_verify_returns_2_on_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    # API down → critical → exit 2, but no raise.
    monkeypatch.setattr(
        dv,
        "gather_payloads",
        lambda base=None: {
            "health": None,
            "urls": None,
            "system": None,
            "capabilities": None,
            "memory": None,
            "services": None,
        },
    )
    buf = io.StringIO()
    rc = dv.run_verify(console=Console(file=buf, force_terminal=False, width=100))
    assert rc == 2
    assert "unreachable" in buf.getvalue()


def test_run_verify_returns_0_when_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dv,
        "gather_payloads",
        lambda base=None: {
            "health": {"version": "0.9.1"},
            "urls": {"api": "http://192.168.1.10:8080"},
            "system": {"checks": {"slot_manager": {"slots": 2, "errored": []}}},
            "capabilities": {"selections": {"embed": {"x": 1}}},
            "memory": {"engine": None},
            "services": {
                "services": [{"id": "openwebui", "up": True}, {"id": "hermes", "up": True}]
            },
        },
    )
    rc = dv.run_verify(console=Console(file=io.StringIO(), force_terminal=False, width=100))
    assert rc == 0


def test_run_verify_json_output_emits_verdict_and_diagnoses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json as jsonlib

    monkeypatch.setattr(
        dv,
        "gather_payloads",
        lambda base=None: {
            "health": None,
            "urls": None,
            "system": None,
            "capabilities": None,
            "memory": None,
            "services": None,
        },
    )
    buf = io.StringIO()
    rc = dv.run_verify(
        console=Console(file=buf, force_terminal=False, width=200),
        json_output=True,
    )
    assert rc == 2
    payload = jsonlib.loads(buf.getvalue())
    assert payload["verdict"] == "critical"
    ids = {d["id"] for d in payload["diagnoses"]}
    assert "HAL0-API-UNREACHABLE" in ids
    assert "HAL0-RUNNERS-NONE-HEALTHY" in ids
    api_row = next(d for d in payload["diagnoses"] if d["id"] == "HAL0-API-UNREACHABLE")
    assert api_row["severity"] == "critical"


def test_gather_payloads_tolerates_api_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.cli import _shared

    def _boom(path: str, *, base=None, **kw):
        raise _shared.CliApiError(f"{path} boom")

    monkeypatch.setattr(_shared, "api_get", _boom)
    payloads = dv.gather_payloads()
    assert set(payloads) == {"health", "urls", "system", "capabilities", "memory", "services"}
    assert all(v is None for v in payloads.values())


# ── CLI wiring ──────────────────────────────────────────────────────────────────


class _FakeCtx:
    invoked_subcommand = None


def test_doctor_verify_flag_invokes_report(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, bool] = {}

    def _fake_run_verify(*, console=None, base=None):
        called["ran"] = True
        return 2

    monkeypatch.setattr("hal0.cli.doctor_verify.run_verify", _fake_run_verify)
    with pytest.raises(typer.Exit) as exc:
        doctor(ctx=_FakeCtx(), verify=True, plain=False, ports=None)  # type: ignore[arg-type]
    assert called.get("ran") is True
    assert exc.value.exit_code == 2
