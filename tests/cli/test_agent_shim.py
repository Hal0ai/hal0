"""Tests for ``hal0-agent`` — the systemd-unit shim.

Most coverage is at the unit level: argv parsing, config resolution,
the Hermes argv builder, and the readiness-poll edges. ``cmd_serve``
isn't exercised end-to-end (it would block on a real subprocess and
HTTP poll); instead we test the building blocks it composes from.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hal0.cli import agent_shim

# ---------------------------------------------------------------------------
# Argv parsing
# ---------------------------------------------------------------------------


class TestArgvParsing:
    def test_serve_subcommand(self) -> None:
        parser = agent_shim._build_parser()
        ns = parser.parse_args(["hermes", "serve"])
        assert ns.agent_id == "hermes"
        assert ns.subcommand == "serve"

    @pytest.mark.parametrize("sub", ["serve", "stop", "status", "reprovision", "render-context"])
    def test_all_subcommands_accepted(self, sub: str) -> None:
        parser = agent_shim._build_parser()
        ns = parser.parse_args(["hermes", sub])
        assert ns.subcommand == sub

    def test_unknown_subcommand_rejected(self) -> None:
        parser = agent_shim._build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["hermes", "bogus"])

    def test_missing_args_rejected(self) -> None:
        parser = agent_shim._build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["hermes"])


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


class TestAgentConfig:
    def test_builtin_hermes_no_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Empty conf dir → falls through to _BUILTIN_AGENT_TYPES["hermes"].
        monkeypatch.setattr(agent_shim, "_AGENTS_CONF_DIR", tmp_path)
        cfg = agent_shim._load_agent_config("hermes")
        assert cfg.agent_id == "hermes"
        assert cfg.agent_type == "hermes"
        # Canonical home is the dot-prefixed `.<agent_id>` convention
        # (== the hermes default `~/.hermes` for the hal0 user).
        assert cfg.home == Path("/var/lib/hal0/.hermes")
        assert cfg.venv == Path("/var/lib/hal0/venvs/hermes")
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 9119

    def test_toml_overrides_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_shim, "_AGENTS_CONF_DIR", tmp_path)
        (tmp_path / "hermes.toml").write_text(
            """
            type = "hermes"
            home = "/tmp/custom-home"
            venv = "/tmp/custom-venv"
            host = "0.0.0.0"
            port = 9999
            """
        )
        cfg = agent_shim._load_agent_config("hermes")
        assert cfg.home == Path("/tmp/custom-home")
        assert cfg.venv == Path("/tmp/custom-venv")
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9999

    def test_unknown_id_without_toml_dies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(agent_shim, "_AGENTS_CONF_DIR", tmp_path)
        with pytest.raises(SystemExit) as exc:
            agent_shim._load_agent_config("piccoder")
        assert exc.value.code == 1

    def test_custom_id_with_toml_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The whole point of `type=...` in the toml is that the operator
        # can ship a new instance without code changes.
        monkeypatch.setattr(agent_shim, "_AGENTS_CONF_DIR", tmp_path)
        (tmp_path / "piccoder.toml").write_text('type = "hermes"\n')
        cfg = agent_shim._load_agent_config("piccoder")
        assert cfg.agent_id == "piccoder"
        assert cfg.agent_type == "hermes"
        # Defaults derive from the id, not the type. The home follows the
        # `.<agent_id>` convention (#437 home normalization).
        assert cfg.home == Path("/var/lib/hal0/.piccoder")
        assert cfg.venv == Path("/var/lib/hal0/venvs/piccoder")

    def test_malformed_toml_dies(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_shim, "_AGENTS_CONF_DIR", tmp_path)
        (tmp_path / "hermes.toml").write_text("not = valid = toml = at all\n")
        with pytest.raises(SystemExit):
            agent_shim._load_agent_config("hermes")

    def test_status_url_uses_host_port(self) -> None:
        cfg = agent_shim.AgentConfig(
            agent_id="hermes",
            agent_type="hermes",
            home=Path("/x"),
            venv=Path("/y"),
            host="10.0.0.1",
            port=4242,
        )
        # Unauthenticated /health liveness route (the /api/* surface is
        # auth-gated and returns 401, which would stall the notify start).
        assert cfg.status_url == "http://10.0.0.1:4242/health"

    def test_hermes_bin_resolves_under_venv(self) -> None:
        cfg = agent_shim.AgentConfig(
            agent_id="hermes",
            agent_type="hermes",
            home=Path("/x"),
            venv=Path("/var/lib/hal0/venvs/hermes"),
            host="127.0.0.1",
            port=9119,
        )
        assert cfg.hermes_bin == Path("/var/lib/hal0/venvs/hermes/bin/hermes")


# ---------------------------------------------------------------------------
# Hermes argv + env builders
# ---------------------------------------------------------------------------


def _cfg(**kw: Any) -> agent_shim.AgentConfig:
    defaults: dict[str, Any] = dict(
        agent_id="hermes",
        agent_type="hermes",
        home=Path("/var/lib/hal0/.hermes"),
        venv=Path("/var/lib/hal0/venvs/hermes"),
        host="127.0.0.1",
        port=9119,
    )
    defaults.update(kw)
    return agent_shim.AgentConfig(**defaults)


class TestHermesArgv:
    def test_chooses_dashboard_subcommand(self) -> None:
        # This is THE most important assertion in the file.
        # Picking the wrong subcommand here = dead chat day-1
        # (DA-sec-ops MUST-FIX #1).
        argv = agent_shim._build_hermes_argv(_cfg())
        assert "dashboard" in argv
        assert "mcp" not in argv
        assert "serve" not in argv  # `serve` is a subcommand of `mcp`

    def test_includes_tui_flag(self) -> None:
        # Without --tui the dashboard runs but /api/pty refuses upgrades
        # and the chat tab is hidden. Verified against
        # hermes_cli/main.py:14069-14076.
        argv = agent_shim._build_hermes_argv(_cfg())
        assert "--tui" in argv

    @staticmethod
    def _mk_web_dist(venv: Path, pyver: str = "python3.14") -> Path:
        dist = venv / "lib" / pyver / "site-packages" / "hermes_cli" / "web_dist"
        dist.mkdir(parents=True)
        return dist

    def test_skip_build_present_when_web_dist_exists(self, tmp_path: Path) -> None:
        # Built dist present → pass --skip-build to avoid npm at runtime.
        self._mk_web_dist(tmp_path)
        argv = agent_shim._build_hermes_argv(_cfg(venv=tmp_path))
        assert "--skip-build" in argv

    def test_skip_build_omitted_when_web_dist_missing(self, tmp_path: Path) -> None:
        # No built dist → DON'T pass --skip-build: that combo makes hermes
        # hard-exit 1 and crash-loop the unit (the original install failure).
        # Drop it so a box with npm can build instead.
        argv = agent_shim._build_hermes_argv(_cfg(venv=tmp_path))
        assert "--skip-build" not in argv

    def test_skip_build_version_agnostic_not_pinned_to_312(self, tmp_path: Path) -> None:
        # Regression for the hardcoded python3.12 path: a python3.14 venv must
        # still be recognized so --skip-build is passed.
        self._mk_web_dist(tmp_path, "python3.14")
        argv = agent_shim._build_hermes_argv(_cfg(venv=tmp_path))
        assert "--skip-build" in argv

    def test_binds_loopback_only(self) -> None:
        # Default cfg → 127.0.0.1. Binding to 0.0.0.0 would let any LAN
        # host reach /api/pty without hal0-api's Origin/HMAC checks
        # (DA-sec-ops #2).
        argv = agent_shim._build_hermes_argv(_cfg())
        host_idx = argv.index("--host")
        assert argv[host_idx + 1] == "127.0.0.1"

    def test_no_open_browser(self) -> None:
        argv = agent_shim._build_hermes_argv(_cfg())
        assert "--no-open" in argv

    def test_uses_venv_hermes_binary(self) -> None:
        argv = agent_shim._build_hermes_argv(_cfg())
        assert argv[0] == "/var/lib/hal0/venvs/hermes/bin/hermes"

    def test_port_overridable(self) -> None:
        argv = agent_shim._build_hermes_argv(_cfg(port=9999))
        port_idx = argv.index("--port")
        assert argv[port_idx + 1] == "9999"


class TestHermesEnv:
    def test_agent_id_passed_through(self) -> None:
        env = agent_shim._build_hermes_env(_cfg(agent_id="hermes"))
        assert env["HAL0_AGENT_ID"] == "hermes"

    def test_custom_agent_id_passed_through(self) -> None:
        # Future-proofing for v0.4 piccoder.
        env = agent_shim._build_hermes_env(_cfg(agent_id="piccoder"))
        assert env["HAL0_AGENT_ID"] == "piccoder"

    def test_hermes_home_passed_through(self) -> None:
        env = agent_shim._build_hermes_env(_cfg(home=Path("/custom/home")))
        assert env["HERMES_HOME"] == "/custom/home"

    def test_dashboard_tui_set(self) -> None:
        # Belt-and-braces with the --tui flag in argv.
        env = agent_shim._build_hermes_env(_cfg())
        assert env["HERMES_DASHBOARD_TUI"] == "1"

    def test_notify_socket_stripped(self) -> None:
        # The shim owns sd_notify; child shouldn't be able to send
        # READY=1 / STOPPING=1 on our behalf.
        with patch.dict(os.environ, {"NOTIFY_SOCKET": "/run/systemd/notify"}):
            env = agent_shim._build_hermes_env(_cfg())
        assert "NOTIFY_SOCKET" not in env

    @staticmethod
    def _mk_web_dist(venv: Path, pyver: str = "python3.14") -> Path:
        dist = venv / "lib" / pyver / "site-packages" / "hermes_cli" / "web_dist"
        dist.mkdir(parents=True)
        return dist

    def test_web_dist_resolved_to_actual_venv_python(self, tmp_path: Path) -> None:
        # HERMES_WEB_DIST is set to the version-correct resolved path.
        dist = self._mk_web_dist(tmp_path, "python3.14")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERMES_WEB_DIST", None)
            env = agent_shim._build_hermes_env(_cfg(venv=tmp_path))
        assert env["HERMES_WEB_DIST"] == str(dist)

    def test_stale_web_dist_env_replaced_with_resolved(self, tmp_path: Path) -> None:
        # A stale python3.12 path inherited from an old unit drop-in does NOT
        # exist → must be replaced by the auto-resolved python3.14 dir. This is
        # the in-place fix for boxes that didn't re-run the installer.
        dist = self._mk_web_dist(tmp_path, "python3.14")
        stale = "/var/lib/hal0/venvs/hermes/lib/python3.12/site-packages/hermes_cli/web_dist"
        with patch.dict(os.environ, {"HERMES_WEB_DIST": stale}):
            env = agent_shim._build_hermes_env(_cfg(venv=tmp_path))
        assert env["HERMES_WEB_DIST"] == str(dist)

    def test_valid_user_web_dist_env_honored(self, tmp_path: Path) -> None:
        # An operator-set HERMES_WEB_DIST that resolves to a real dir (hand-built
        # tree via hermes.env) wins — the shim must not clobber it.
        user_dist = tmp_path / "custom-dist"
        user_dist.mkdir()
        venv = tmp_path / "venv"
        self._mk_web_dist(venv, "python3.14")
        with patch.dict(os.environ, {"HERMES_WEB_DIST": str(user_dist)}):
            env = agent_shim._build_hermes_env(_cfg(venv=venv))
        assert env["HERMES_WEB_DIST"] == str(user_dist)

    def test_web_dist_env_dropped_when_missing(self, tmp_path: Path) -> None:
        # No built dist anywhere + a stale inherited value → pop it so hermes
        # falls back to its own __file__ default, not a known-wrong path.
        stale = "/var/lib/hal0/venvs/hermes/lib/python3.12/site-packages/hermes_cli/web_dist"
        with patch.dict(os.environ, {"HERMES_WEB_DIST": stale}):
            env = agent_shim._build_hermes_env(_cfg(venv=tmp_path))
        assert "HERMES_WEB_DIST" not in env


# ---------------------------------------------------------------------------
# sd_notify
# ---------------------------------------------------------------------------


class TestSdNotify:
    def test_no_notify_socket_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        assert agent_shim._sd_notify("READY=1") is False

    @pytest.mark.skipif(
        sys.platform != "linux",
        reason="AF_UNIX datagram + abstract namespace is Linux-only",
    )
    def test_sends_to_unix_socket(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sock_path = tmp_path / "notify.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        listener.bind(str(sock_path))
        listener.settimeout(1.0)
        try:
            monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
            assert agent_shim._sd_notify("READY=1\n") is True
            received, _ = listener.recvfrom(64)
            assert received == b"READY=1\n"
        finally:
            listener.close()


# ---------------------------------------------------------------------------
# _find_child_pids — keys on cmdline + HAL0_AGENT_ID env
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="/proc scan is Linux-only")
class TestFindChildPids:
    """Spawn a sleeping subprocess with controlled env + cmdline so we can
    assert against a known PID. ``monkeypatch.setenv`` doesn't rewrite
    ``/proc/$pid/environ`` (that's frozen at exec time) so we can't use
    the test process itself.
    """

    @pytest.fixture
    def sleeper(self) -> Any:
        """Yield a (pid, cmdline_needle) for a long-running child with
        ``HAL0_AGENT_ID=hermes`` in its env and ``hal0-agent-needle`` in
        its cmdline.
        """

        # `sh -c "exec -a <name> sleep 30"` would be cleanest but `exec
        # -a` is bash-only and CI may run with dash as /bin/sh. Use a
        # python -c child whose argv contains the needle string directly.
        needle = "hal0-agent-needle-marker"
        env = {**os.environ, "HAL0_AGENT_ID": "hermes"}
        proc = subprocess.Popen(
            [sys.executable, "-c", f"# {needle}\nimport time; time.sleep(30)"],
            env=env,
        )
        try:
            # Give the kernel a moment to flush /proc/$pid/cmdline.
            import time as _t

            _t.sleep(0.1)
            yield proc.pid, needle
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_matches_when_needle_AND_agent_id_match(self, sleeper: Any) -> None:
        pid, needle = sleeper
        matches = agent_shim._find_child_pids(needle=needle, agent_id="hermes")
        assert pid in matches

    def test_excludes_when_needle_missing(self, sleeper: Any) -> None:
        pid, _needle = sleeper
        # Cmdline-needle doesn't match → no match even with right agent id.
        matches = agent_shim._find_child_pids(
            needle="/definitely/not/in/cmdline", agent_id="hermes"
        )
        assert pid not in matches

    def test_excludes_when_agent_id_wrong(self, sleeper: Any) -> None:
        pid, needle = sleeper
        # Cmdline matches BUT agent id doesn't → no match (proves AND-gate).
        matches = agent_shim._find_child_pids(needle=needle, agent_id="piccoder")
        assert pid not in matches


# ---------------------------------------------------------------------------
# cmd_status / cmd_stop / cmd_reprovision integration points
# ---------------------------------------------------------------------------


class TestCmdStatus:
    def test_returns_0_when_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_shim, "_is_ready", lambda cfg: True)
        rc = agent_shim.cmd_status(_cfg())
        assert rc == 0

    def test_returns_nonzero_when_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_shim, "_is_ready", lambda cfg: False)
        rc = agent_shim.cmd_status(_cfg())
        assert rc == 1


class TestCmdStop:
    def test_idempotent_when_no_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_shim, "_find_child_pids", lambda *_a, **_k: [])
        assert agent_shim.cmd_stop(_cfg()) == 0


class TestCmdReprovision:
    def test_shells_out_to_hal0_agent_bootstrap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: dict[str, list[str]] = {}

        def fake_call(argv: list[str], *_a: Any, **_kw: Any) -> int:
            recorded["argv"] = argv
            return 0

        monkeypatch.setattr(subprocess, "call", fake_call)
        rc = agent_shim.cmd_reprovision(_cfg())
        assert rc == 0
        assert recorded["argv"][-3:] == ["bootstrap", "hermes", "--repair"]


# ---------------------------------------------------------------------------
# main() dispatch
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_dispatches_to_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(agent_shim, "_AGENTS_CONF_DIR", tmp_path)
        called: dict[str, agent_shim.AgentConfig] = {}

        def fake_status(cfg: agent_shim.AgentConfig) -> int:
            called["cfg"] = cfg
            return 7

        monkeypatch.setattr(agent_shim, "_DISPATCH", {"status": fake_status})
        # Rebuild the parser with only `status` allowed for this test —
        # but parse_args is what gates the choices, so we patch around it.
        monkeypatch.setattr(
            agent_shim,
            "_build_parser",
            _make_parser_allowing_only_status,
        )
        rc = agent_shim.main(["hermes", "status"])
        assert rc == 7
        assert called["cfg"].agent_id == "hermes"


def _make_parser_allowing_only_status() -> Any:
    import argparse

    p = argparse.ArgumentParser(prog="hal0-agent")
    p.add_argument("agent_id")
    p.add_argument("subcommand", choices=["status"])
    return p


class TestIsReady:
    """``_is_ready`` accepts 2xx AND auth-challenges (401/403) as 'reachable'.

    The hermes dashboard auth-gates ``/api/*``; an auth response still proves
    the socket is open, so the shim must sd_notify READY rather than loop.
    """

    @staticmethod
    def _cfg() -> agent_shim.AgentConfig:
        return agent_shim.AgentConfig(
            agent_id="hermes",
            agent_type="hermes",
            home=Path("/x"),
            venv=Path("/y"),
            host="127.0.0.1",
            port=9119,
        )

    def test_2xx_is_ready(self) -> None:
        with patch("hal0.cli.agent_shim.urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.status = 200
            assert agent_shim._is_ready(self._cfg()) is True

    @pytest.mark.parametrize(
        ("code", "expected"),
        [(401, True), (403, True), (404, False), (500, False)],
    )
    def test_http_error_codes(self, code: int, expected: bool) -> None:
        import urllib.error

        err = urllib.error.HTTPError("http://x", code, "msg", {}, None)  # type: ignore[arg-type]
        with patch("hal0.cli.agent_shim.urllib.request.urlopen", side_effect=err):
            assert agent_shim._is_ready(self._cfg()) is expected

    def test_connection_error_not_ready(self) -> None:
        import urllib.error

        with patch(
            "hal0.cli.agent_shim.urllib.request.urlopen",
            side_effect=urllib.error.URLError("boom"),
        ):
            assert agent_shim._is_ready(self._cfg()) is False


# ---------------------------------------------------------------------------
# cmd_render_context
# ---------------------------------------------------------------------------


def test_render_context_dispatch_calls_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: dict[str, Path] = {}

    def fake_render(*, hermes_home: Path) -> dict[str, object]:
        called["home"] = hermes_home
        return {
            "state_written": True,
            "hermes_written": False,
            "degraded": False,
            "state_path": "/etc/hal0/STATE.md",
        }

    monkeypatch.setattr(agent_shim, "_render_live_context", fake_render)

    cfg = agent_shim.AgentConfig(
        agent_id="hermes",
        agent_type="hermes",
        home=tmp_path,
        venv=tmp_path / "venv",
        host="127.0.0.1",
        port=8133,
    )
    rc = agent_shim.cmd_render_context(cfg)
    assert rc == 0
    assert called["home"] == tmp_path


def test_render_context_in_parser_choices() -> None:
    parser = agent_shim._build_parser()
    args = parser.parse_args(["hermes", "render-context"])
    assert args.subcommand == "render-context"
