from hal0.install.extensions import (
    get_extension,
    install_extension,
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
