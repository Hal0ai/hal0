"""Assert the ``hal0-agent@.service`` template ships the directives we need.

These tests parse the unit file as text and assert directive-level
presence — they don't shell out to ``systemd-analyze`` because:

1. The template uses ``%i`` which the analyzer rejects when the file is
   parsed in isolation (it expects an instantiated unit name).
2. CI runs on a wide range of platforms; ``systemd-analyze`` isn't
   reliably available on macOS or non-systemd Linux dev boxes.

The directives we assert here are the ones that PROTECT a real
operator outcome (watchdog-on-hang, restart posture, sandboxing) —
drift on any of them should fail loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Locate the unit files relative to the repo root. Pytest runs from
# repo root by default; resolve via this file's path to stay robust
# against ``pytest path/to/tests/`` invocations from anywhere.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _REPO_ROOT / "installer" / "systemd" / "hal0-agent@.service"
_HERMES_OVERRIDE = (
    _REPO_ROOT / "installer" / "systemd" / "hal0-agent@hermes.service.d" / "override.conf"
)


@pytest.fixture(scope="module")
def template_text() -> str:
    assert _TEMPLATE.exists(), f"missing template unit at {_TEMPLATE}"
    return _TEMPLATE.read_text()


@pytest.fixture(scope="module")
def override_text() -> str:
    assert _HERMES_OVERRIDE.exists(), f"missing hermes override at {_HERMES_OVERRIDE}"
    return _HERMES_OVERRIDE.read_text()


# ---------------------------------------------------------------------------
# Dependency wiring
# ---------------------------------------------------------------------------


class TestDependencyWiring:
    """Soft dependency posture: ordering only, never a hard service pin."""

    def test_after_network(self, template_text: str) -> None:
        # `After=` controls start ordering; the network must be up before
        # the agent's hal0 provider plugin can probe /api/v1/health.
        assert re.search(r"^After=.*\bnetwork-online\.target\b", template_text, re.MULTILINE)

    def test_no_requires_or_bindsto(self, template_text: str) -> None:
        # Hard pins (`Requires=`/`BindsTo=`) on peer services would hold
        # the agent "active" forever when a peer wedges. Ordering-only
        # (`After=`/`Wants=`) is the contract — see DA-sec-ops MUST-FIX #5.
        assert not re.search(r"^Requires=", template_text, re.MULTILINE), (
            "Requires= reintroduces the deadlock-pin failure mode"
        )
        assert not re.search(r"^BindsTo=", template_text, re.MULTILINE), (
            "BindsTo= reintroduces the deadlock-pin failure mode"
        )


# ---------------------------------------------------------------------------
# Service runtime
# ---------------------------------------------------------------------------


class TestServiceRuntime:
    def test_type_notify(self, template_text: str) -> None:
        # `Type=notify` is what makes `WatchdogSec=` work. Without it
        # systemd can't observe hangs.
        assert re.search(r"^Type=notify\b", template_text, re.MULTILINE)

    def test_watchdog_60s(self, template_text: str) -> None:
        # `WatchdogSec=60` per DA-sec-ops MUST-FIX #5. The shim pings
        # at half-interval (25s) so a single miss doesn't trip.
        m = re.search(r"^WatchdogSec=(\d+)", template_text, re.MULTILINE)
        assert m is not None, "WatchdogSec= is required (Type=notify alone isn't enough)"
        assert int(m.group(1)) <= 120, "WatchdogSec should be ≤ 120s for fast hang detection"

    def test_restart_on_failure(self, template_text: str) -> None:
        # `on-failure` (NOT `always`) — manual SIGTERM via `systemctl stop`
        # should NOT trigger a restart loop.
        assert re.search(r"^Restart=on-failure\b", template_text, re.MULTILINE)

    def test_exec_start_uses_hal0_agent_shim(self, template_text: str) -> None:
        # ExecStart must go through the shim. Without the shim there's
        # no sd_notify, no watchdog ping, no mode-selection logic.
        assert re.search(
            r"^ExecStart=/usr/local/bin/hal0-agent %i serve\b",
            template_text,
            re.MULTILINE,
        )

    def test_exec_start_never_uses_mcp_serve(self, template_text: str) -> None:
        # DA-sec-ops MUST-FIX #1: `mcp serve` emits no event stream.
        # If anyone rewrites ExecStart to invoke hermes directly with
        # `mcp serve`, the chat surface goes dead day-1.
        # Scoped to `ExecStart=` lines only — the surrounding comments
        # intentionally mention `mcp serve` to explain why we DON'T use it.
        exec_lines = [line for line in template_text.splitlines() if line.startswith("ExecStart=")]
        assert exec_lines, "no ExecStart= directive found"
        for line in exec_lines:
            assert "mcp serve" not in line, (
                "ExecStart must not reference `mcp serve` — that mode emits no "
                "/api/events. See DA-sec-ops MUST-FIX #1."
            )

    def test_exec_stop_uses_hal0_agent_shim(self, template_text: str) -> None:
        assert re.search(
            r"^ExecStop=/usr/local/bin/hal0-agent %i stop\b",
            template_text,
            re.MULTILINE,
        )

    def test_template_parameterised_by_id(self, template_text: str) -> None:
        # `%i` (the lowercase instance specifier) is what makes
        # `hal0-agent@piccoder.service` work without a template edit.
        assert "%i" in template_text


# ---------------------------------------------------------------------------
# Sandboxing
# ---------------------------------------------------------------------------


class TestSandboxing:
    """Hardening directives per DA-sec-ops MUST-FIX #5."""

    @pytest.mark.parametrize(
        "directive",
        [
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "PrivateTmp=yes",
        ],
    )
    def test_hardening_directive_present(self, template_text: str, directive: str) -> None:
        # Each directive on its own line, exact value.
        pattern = rf"^{re.escape(directive)}\b"
        assert re.search(pattern, template_text, re.MULTILINE), (
            f"Required hardening directive missing: {directive}"
        )

    def test_readwritepaths_covers_runtime_dirs(self, template_text: str) -> None:
        # `ProtectSystem=strict` mounts /usr + /etc + /boot read-only;
        # without ReadWritePaths the agent can't write to its own state
        # dir. /etc/hal0 is required so the render-context ExecStartPre can
        # (re)write HERMES.md/STATE.md — otherwise it fails read-only-fs.
        m = re.search(r"^ReadWritePaths=(.+)$", template_text, re.MULTILINE)
        assert m is not None
        paths = m.group(1).split()
        for required in ("/etc/hal0", "/var/lib/hal0", "/var/log/hal0", "/run/hal0"):
            assert required in paths, (
                f"ReadWritePaths missing {required} — agent will fail to write state"
            )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_user_and_group_hal0(self, template_text: str) -> None:
        assert re.search(r"^User=hal0\b", template_text, re.MULTILINE)
        assert re.search(r"^Group=hal0\b", template_text, re.MULTILINE)

    def test_agent_id_env_set(self, template_text: str) -> None:
        # The shim's _find_child_pids scan keys off HAL0_AGENT_ID, and
        # downstream Hermes plugins use it for the X-hal0-Agent header.
        assert re.search(r'^Environment="HAL0_AGENT_ID=%i"', template_text, re.MULTILINE)

    def test_environment_file_per_instance(self, template_text: str) -> None:
        # Leading `-` makes a missing file non-fatal — important on
        # first boot before any operator override exists.
        assert re.search(
            r"^EnvironmentFile=-/etc/hal0/agents/%i\.env\b",
            template_text,
            re.MULTILINE,
        )


# ---------------------------------------------------------------------------
# Hermes override
# ---------------------------------------------------------------------------


class TestHermesOverride:
    def test_pins_hermes_home(self, override_text: str) -> None:
        # The agent's `$HERMES_HOME` is what `_claim_hermes_home` marks
        # with `.hal0-managed` — if this drifts, bootstrap will refuse
        # to write a fresh config. Canonical home is the NORMAL hermes
        # default `/var/lib/hal0/.hermes` (the dot-prefixed dir the
        # hermes binary resolves `~/.hermes` to for the hal0 user).
        assert re.search(
            r'^Environment="HERMES_HOME=/var/lib/hal0/\.hermes"',
            override_text,
            re.MULTILINE,
        )

    def test_does_not_reference_legacy_agents_hermes_home(self, override_text: str) -> None:
        # Regression guard: the old `/var/lib/hal0/agents/hermes` home
        # must not survive anywhere in the override after the migration.
        assert "/var/lib/hal0/agents/hermes" not in override_text

    def test_pins_dashboard_tui(self, override_text: str) -> None:
        # Belt-and-braces with the shim's `--tui` flag — env wins if
        # argv is ever stripped (e.g. operator drops a custom ExecStart).
        assert re.search(r'^Environment="HERMES_DASHBOARD_TUI=1"', override_text, re.MULTILINE)

    def test_inference_base_default(self, override_text: str) -> None:
        # Hermes's hal0 model-provider plugin discovers the OpenAI-compatible
        # base from HAL0_INFERENCE_BASE — hal0-api's own /v1 surface on 8080.
        assert re.search(
            r'^Environment="HAL0_INFERENCE_BASE=http://127\.0\.0\.1:8080"',
            override_text,
            re.MULTILINE,
        )

    def test_no_hardcoded_python_minor_in_web_dist(self, override_text: str) -> None:
        # Regression: the override used to pin
        # HERMES_WEB_DIST=…/lib/python3.12/site-packages/… which crash-looped
        # the agent on any other interpreter (py3.14 install). The path is now
        # resolved at runtime by the shim — the override must not hardcode a
        # `python3.<minor>` web_dist path anymore.
        assert not re.search(r"python3\.\d+.*web_dist", override_text), (
            "override.conf still pins a python3.<minor> web_dist path — resolve it "
            "at runtime in agent_shim._resolve_web_dist instead"
        )


# ---------------------------------------------------------------------------
# Hermes gateway — SYSTEM-scope secrets drop-in (#437)
# ---------------------------------------------------------------------------


class TestHermesGatewaySecretsDropin:
    """The gateway secrets drop-in the provisioner renders.

    NOTE on the trade-off: the gateway main unit is generated by
    ``hermes_cli.gateway.generate_systemd_unit`` (system scope:
    ``HERMES_HOME=/var/lib/hal0/.hermes``, ``User/Group=hal0``,
    ``WantedBy=multi-user.target``). ``hermes_cli`` is NOT installed in
    the hal0 test venv, and end-to-end systemd ``EnvironmentFile`` loading
    is not unit-testable without a live ``systemd``. So this guard asserts
    the *drop-in contract the provisioner owns* — file path, the
    ``EnvironmentFile=`` pointing at the secrets vault, the ``[Service]``
    section, and that the drop-in lives under the SYSTEM unit dir (not a
    user-scope ``~/.config/systemd/user`` path) — plus that no gateway
    constant references the legacy ``/var/lib/hal0/agents/hermes`` home.
    Phase-level idempotency + daemon-reload behaviour is covered in
    ``tests/agents/test_hermes_provision.py``.
    """

    def test_dropin_lives_under_system_unit_dir(self) -> None:
        from hal0.agents import hermes_provision as hp

        assert str(hp.GATEWAY_SYSTEMD_DROPIN_DIR) == (
            "/etc/systemd/system/hermes-gateway.service.d"
        )
        assert hp.GATEWAY_SYSTEMD_DROPIN_FILE.name == "10-hal0-secrets.conf"
        # System scope, never user scope — no XDG_RUNTIME_DIR / user-bus.
        assert "/.config/systemd/user" not in str(hp.GATEWAY_SYSTEMD_DROPIN_FILE)

    def test_dropin_body_wires_secrets_environment_file(self) -> None:
        from hal0.agents import hermes_provision as hp

        body = hp._gateway_dropin_body()
        assert "[Service]" in body
        assert "EnvironmentFile=/var/lib/hal0/secrets/agents/hermes.env" in body

    def test_dropin_does_not_reference_legacy_home(self) -> None:
        from hal0.agents import hermes_provision as hp

        body = hp._gateway_dropin_body()
        assert "/var/lib/hal0/agents/hermes" not in body
        assert "/var/lib/hal0/agents/hermes" not in str(hp.GATEWAY_SYSTEMD_DROPIN_FILE)


class TestInstallerGatewayWiring:
    """install.sh must create AND enable the system-scope hermes gateway.

    The provisioner only writes the secrets drop-in; the main unit comes
    from ``hermes gateway install --system --run-as-user hal0``. Without
    this the gateway (Telegram/Discord) never starts on a fresh install.
    """

    def _install_sh(self) -> str:
        return (_REPO_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")

    def test_installs_system_gateway_unit(self) -> None:
        assert "gateway install --system --run-as-user hal0" in self._install_sh()

    def test_enables_gateway_service(self) -> None:
        assert "enable --now hermes-gateway.service" in self._install_sh()
