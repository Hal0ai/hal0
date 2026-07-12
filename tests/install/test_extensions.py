from hal0.install import extensions as ext_mod
from hal0.install.extensions import (
    get_extension,
    install_extension,
    install_openwebui,
    list_extensions,
)
from hal0.install.orchestrate import ExtensionOutcome


def test_registry_has_grouped_extensions():
    apps = list_extensions(kind="app")
    agents = list_extensions(kind="agent")
    assert any(e.id == "openwebui" for e in apps)
    assert {e.id for e in agents} >= {"hermes", "pi"}
    assert get_extension("openwebui").default_enabled is True
    assert get_extension("pi").default_enabled is False


def test_get_unknown_extension_returns_none():
    assert get_extension("nope") is None


def test_install_agent_runs_hal0_agent_install(monkeypatch):
    ran = []
    monkeypatch.setattr("hal0.install.extensions._run", lambda *a, **k: ran.append(a[0]))
    out = install_extension("hermes")
    assert isinstance(out, ExtensionOutcome) and out.installed is True
    assert any("agent" in c and "install" in c and "hermes" in c for c in ran)


def test_install_unknown_extension_skips():
    out = install_extension("nope")
    assert out.installed is False and out.skipped == "unknown_extension"


def test_install_pi_extension_resolves_to_bundled_agent_id(monkeypatch):
    """Extension id "pi" must translate to the "pi-coder" bundled-agent id
    at the ``hal0 agent install`` boundary (BUNDLED_AGENTS in
    agents/manager.py only recognises "pi-coder") — without this, enabling
    "Pi" during setup silently failed with "unknown bundled agent"."""
    from hal0.agents.manager import BUNDLED_AGENTS

    ran = []
    monkeypatch.setattr(ext_mod, "_run", lambda *a, **k: ran.append(a[0]))
    out = install_extension("pi")
    assert isinstance(out, ExtensionOutcome) and out.installed is True
    assert ran == [["hal0", "agent", "install", "pi-coder"]]
    assert "pi-coder" in BUNDLED_AGENTS


# ── OpenWebUI wiring (issue #1102 / Q9) ─────────────────────────────────────
#
# install_extension("openwebui") and the standalone install_openwebui() must
# be the SAME code path so "install now" (apply_setup) and "install later"
# (`hal0 app install openwebui`) are behaviourally identical.


def test_install_extension_openwebui_delegates_to_install_openwebui(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ext_mod,
        "install_openwebui",
        lambda: calls.append("called") or ExtensionOutcome(ext_id="openwebui", installed=True),
    )
    out = install_extension("openwebui")
    assert calls == ["called"]
    assert out.installed is True


def test_install_openwebui_enables_unit_when_podman_usable(monkeypatch):
    ran = []
    monkeypatch.setattr(ext_mod, "_podman_usable", lambda: True)
    monkeypatch.setattr(ext_mod, "_wait_active", lambda unit, timeout=15.0: True)
    monkeypatch.setattr(ext_mod, "_run_ok", lambda cmd: ran.append(cmd) or True)

    out = install_openwebui()

    assert out.installed is True and out.error is None
    assert ["systemctl", "enable", "--now", "hal0-openwebui.service"] in ran


def test_install_openwebui_quiesces_unit_without_podman(monkeypatch):
    ran = []
    monkeypatch.setattr(ext_mod, "_podman_usable", lambda: False)
    monkeypatch.setattr(ext_mod, "_run_ok", lambda cmd: ran.append(cmd) or True)

    out = install_openwebui()

    assert out.installed is False
    assert out.skipped == "no_container_runtime"
    assert ["systemctl", "disable", "--now", "hal0-openwebui.service"] in ran
    assert ["systemctl", "reset-failed", "hal0-openwebui.service"] in ran
    # No enable attempt when the runtime is unusable.
    assert all(cmd[:2] != ["systemctl", "enable"] for cmd in ran)


def test_install_openwebui_reports_error_when_enable_fails(monkeypatch):
    monkeypatch.setattr(ext_mod, "_podman_usable", lambda: True)
    monkeypatch.setattr(ext_mod, "_run_ok", lambda cmd: False)

    out = install_openwebui()

    assert out.installed is False
    assert out.error is not None and "enable" in out.error


def test_install_openwebui_surfaces_slow_start_without_failing(monkeypatch):
    monkeypatch.setattr(ext_mod, "_podman_usable", lambda: True)
    monkeypatch.setattr(ext_mod, "_wait_active", lambda unit, timeout=15.0: False)
    monkeypatch.setattr(ext_mod, "_run_ok", lambda cmd: True)

    out = install_openwebui()

    # Enabled, but the active-confirmation lagged — not fatal (matches
    # install.sh's "warn, don't fail" posture for a slow first boot).
    assert out.installed is True
    assert out.error == "not_active_yet"
