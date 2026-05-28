"""Assert the ``hal0-agent@.service`` template ships the directives we need.

These tests parse the unit file as text and assert directive-level
presence — they don't shell out to ``systemd-analyze`` because:

1. The template uses ``%i`` which the analyzer rejects when the file is
   parsed in isolation (it expects an instantiated unit name).
2. CI runs on a wide range of platforms; ``systemd-analyze`` isn't
   reliably available on macOS or non-systemd Linux dev boxes.

The directives we assert here are the ones that PROTECT a real
operator outcome (Lemonade-deadlock survival, watchdog-on-hang,
sandboxing) — drift on any of them should fail loudly.
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
    """Lemonade is `Wants=`, not `Requires=` — survives the unload deadlock."""

    def test_wants_lemonade(self, template_text: str) -> None:
        # `Wants=hal0-lemonade.service` is the single most important line:
        # `Requires=` or `BindsTo=` would pin the agent in "active" forever
        # when lemonade hits its GPU-cleanup-after-unload deadlock.
        # See auto-memory `hal0_lemonade_unload_gpu_cleanup_hang`.
        assert re.search(r"^Wants=hal0-lemonade\.service\b", template_text, re.MULTILINE), (
            "Template MUST use `Wants=hal0-lemonade.service` (NOT Requires=/BindsTo=)"
            " — see DA-sec-ops MUST-FIX #5."
        )

    def test_after_lemonade_and_network(self, template_text: str) -> None:
        # `After=` controls start ordering; lemonade must boot first so
        # the agent's hal0 provider plugin can probe /api/v1/health.
        assert re.search(r"^After=.*\bhal0-lemonade\.service\b", template_text, re.MULTILINE)
        assert re.search(r"^After=.*\bnetwork-online\.target\b", template_text, re.MULTILINE)

    def test_no_requires_or_bindsto(self, template_text: str) -> None:
        # If anyone ever swaps Wants= back to Requires=/BindsTo=, this
        # catches it before CI green-merges through.
        assert not re.search(r"^Requires=.*hal0-lemonade", template_text, re.MULTILINE), (
            "Requires=hal0-lemonade reintroduces the deadlock-pin failure mode"
        )
        assert not re.search(r"^BindsTo=.*hal0-lemonade", template_text, re.MULTILINE), (
            "BindsTo=hal0-lemonade reintroduces the deadlock-pin failure mode"
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
        # dir. Assert all three required paths are listed.
        m = re.search(r"^ReadWritePaths=(.+)$", template_text, re.MULTILINE)
        assert m is not None
        paths = m.group(1).split()
        for required in ("/var/lib/hal0", "/var/log/hal0", "/run/hal0"):
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
        # to write a fresh config.
        assert re.search(
            r'^Environment="HERMES_HOME=/var/lib/hal0/agents/hermes"',
            override_text,
            re.MULTILINE,
        )

    def test_pins_dashboard_tui(self, override_text: str) -> None:
        # Belt-and-braces with the shim's `--tui` flag — env wins if
        # argv is ever stripped (e.g. operator drops a custom ExecStart).
        assert re.search(r'^Environment="HERMES_DASHBOARD_TUI=1"', override_text, re.MULTILINE)

    def test_lemonade_base_default(self, override_text: str) -> None:
        # 127.0.0.1:13305 matches `installer/install.sh`'s lemond bind.
        assert re.search(
            r'^Environment="HAL0_LEMONADE_BASE=http://127\.0\.0\.1:13305"',
            override_text,
            re.MULTILINE,
        )
