"""Tests for the real ``NextStep`` remediation the doctor-verify family emits.

``health_report.to_diagnosis`` used to hard-code ``next_steps=[]`` for every
one of the 7 checks (WS-K / §21.4 doctor retrofit had wired the shape but not
the content). Each ``check_*`` classifier now carries its own remediation for
every fail/warn branch it can reach — this module pins that per-branch
content and confirms it survives the ``Check`` -> ``Diagnosis`` adapter.
"""

from __future__ import annotations

from hal0.cli import doctor_verify as dv
from hal0.cli.doctor_diagnosis import to_diagnosis
from hal0.diagnostics import NextStep

# ── check_api ────────────────────────────────────────────────────────────────


def test_check_api_unreachable_offers_hal0_serve() -> None:
    c = dv.check_api(None)
    assert c.next_steps == (NextStep(kind="command", label="hal0 serve", target="hal0 serve"),)


def test_check_api_pass_has_no_steps() -> None:
    assert dv.check_api({"version": "1.0.0"}).next_steps == ()


# ── check_dns ────────────────────────────────────────────────────────────────


def test_check_dns_unresolved_offers_manual_and_doc(monkeypatch) -> None:
    def _boom(_h: str) -> str:
        raise OSError("no mdns")

    monkeypatch.setattr(dv.socket, "gethostbyname", _boom)
    c = dv.check_dns({"api": "http://hal0.local:8080"})
    kinds = [s.kind for s in c.next_steps]
    assert kinds == ["manual", "doc"]
    assert c.next_steps[1].target == "/docs/operate/services/#mdns-discovery-toggle"


def test_check_dns_pass_has_no_steps(monkeypatch) -> None:
    monkeypatch.setattr(dv.socket, "gethostbyname", lambda h: "192.0.2.1")
    assert dv.check_dns({"api": "http://hal0.local:8080"}).next_steps == ()


# ── check_runners ────────────────────────────────────────────────────────────


def test_check_runners_no_slots_configured_offers_create_and_doc() -> None:
    c = dv.check_runners({"checks": {"slot_manager": {"slots": 0, "errored": []}}})
    assert c.next_steps[0] == NextStep(
        kind="command", label="hal0 slot create", target="hal0 slot create"
    )
    assert c.next_steps[1].kind == "doc"


def test_check_runners_all_errored_offers_restart_per_slot() -> None:
    payload = {"checks": {"slot_manager": {"slots": 2, "errored": ["a", "b"]}}}
    c = dv.check_runners(payload)
    assert c.status == "fail" and c.critical
    assert [s.target for s in c.next_steps] == [
        "hal0 slot restart a",
        "hal0 slot restart b",
    ]


def test_check_runners_partial_errored_offers_restart_per_slot() -> None:
    payload = {"checks": {"slot_manager": {"slots": 3, "errored": ["b"]}}}
    c = dv.check_runners(payload)
    assert c.status == "warn"
    assert c.next_steps == (
        NextStep(kind="command", label="hal0 slot restart b", target="hal0 slot restart b"),
    )


def test_check_runners_errored_steps_are_capped() -> None:
    errored = [f"slot-{i}" for i in range(5)]
    payload = {"checks": {"slot_manager": {"slots": 5, "errored": errored}}}
    c = dv.check_runners(payload)
    assert len(c.next_steps) == 3


def test_check_runners_unreachable_and_not_wired_offer_restart_hal0_api() -> None:
    restart = NextStep(
        kind="command", label="systemctl restart hal0-api", target="systemctl restart hal0-api"
    )
    assert dv.check_runners(None).next_steps == (restart,)
    assert dv.check_runners({"checks": {}}).next_steps == (restart,)


def test_check_runners_pass_has_no_steps() -> None:
    payload = {"checks": {"slot_manager": {"slots": 2, "errored": []}}}
    assert dv.check_runners(payload).next_steps == ()


# ── check_capabilities ───────────────────────────────────────────────────────


def test_check_capabilities_none_configured_offers_manual_and_doc() -> None:
    c = dv.check_capabilities({"selections": {}})
    kinds = [s.kind for s in c.next_steps]
    assert kinds == ["manual", "doc"]
    assert c.next_steps[1].target == "/docs/concepts/capabilities-and-profiles"


def test_check_capabilities_unreachable_offers_restart_hal0_api() -> None:
    c = dv.check_capabilities(None)
    assert c.next_steps[0].target == "systemctl restart hal0-api"


def test_check_capabilities_active_has_no_steps() -> None:
    c = dv.check_capabilities({"selections": {"embed": {"x": 1}}})
    assert c.next_steps == ()


# ── check_memory ─────────────────────────────────────────────────────────────


def test_check_memory_admin_unreachable_offers_restart_hal0_api() -> None:
    assert dv.check_memory(None).next_steps[0].target == "systemctl restart hal0-api"


def test_check_memory_degraded_offers_restart_hal0_api() -> None:
    c = dv.check_memory({"enabled": True, "engine": None})
    assert c.next_steps[0].target == "systemctl restart hal0-api"


def test_check_memory_engine_unreachable_offers_restart_hindsight_api() -> None:
    c = dv.check_memory({"engine": "hindsight", "reachable": False})
    assert c.next_steps[0].target == "systemctl restart hindsight-api"


def test_check_memory_disabled_and_reachable_have_no_steps() -> None:
    assert dv.check_memory({"enabled": False, "engine": None}).next_steps == ()
    assert dv.check_memory({"engine": "hindsight", "reachable": True}).next_steps == ()


# ── check_openwebui / check_hermes ──────────────────────────────────────────


def test_service_checks_down_offer_matching_restart_unit() -> None:
    services = {
        "services": [
            {"id": "openwebui", "up": False, "detail": "crashed"},
            {"id": "hermes", "up": False, "detail": "inactive"},
        ]
    }
    owui = dv.check_openwebui(services)
    hermes = dv.check_hermes(services)
    assert owui.next_steps[0].target == "systemctl restart hal0-openwebui"
    assert hermes.next_steps[0].target == "systemctl restart hal0-agent@hermes"


def test_service_checks_not_reported_offer_restart_too() -> None:
    c = dv.check_openwebui({"services": []})
    assert c.status == "warn"
    assert c.next_steps[0].target == "systemctl restart hal0-openwebui"


def test_service_checks_up_have_no_steps() -> None:
    services = {"services": [{"id": "openwebui", "up": True, "detail": "reachable"}]}
    assert dv.check_openwebui(services).next_steps == ()


# ── every check offers ≥1 remediation somewhere in its state space ─────────


def test_every_check_family_has_at_least_one_actionable_branch(monkeypatch) -> None:
    """Not every single branch needs a step (e.g. dns's "no host advertised
    yet" is a downstream symptom of the api check, which already covers it),
    but every one of the 7 check families must have at least one branch that
    does — otherwise a real fail/warn would strand the operator."""

    def _boom(_h: str) -> str:
        raise OSError("no mdns")

    monkeypatch.setattr(dv.socket, "gethostbyname", _boom)
    assert dv.check_api(None).next_steps
    assert dv.check_dns({"api": "http://x.local"}).next_steps
    assert dv.check_runners(None).next_steps
    assert dv.check_capabilities(None).next_steps
    assert dv.check_memory(None).next_steps
    assert dv.check_openwebui({"services": []}).next_steps
    assert dv.check_hermes({"services": []}).next_steps


# ── Check -> Diagnosis adapter carries next_steps through ───────────────────


def test_to_diagnosis_carries_next_steps() -> None:
    c = dv.check_api(None)
    d = to_diagnosis(c)
    assert d.next_steps == list(c.next_steps)


def test_to_diagnosis_pass_has_empty_next_steps() -> None:
    c = dv.check_api({"version": "1.0.0"})
    d = to_diagnosis(c)
    assert d.next_steps == []
