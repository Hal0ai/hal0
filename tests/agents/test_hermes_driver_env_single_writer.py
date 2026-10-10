"""One writer for the Hermes driver env (#2372) and an honest header (#2373).

``/etc/hal0/agents/hermes.env`` used to have two writers: the provisioner's
``hermes_provision._write_driver_env`` and ``HermesDriver._write_env_file``,
which the dashboard/API install path ran. The second one wrote the pre-ADR-0012
``HAL0_BEARER_TOKEN``, dropped ``HAL0_MCP_TOKEN`` and the OAuth lines, and wrote
straight into the root:root directory instead of through the hal0-agentenv
seam. The driver now delegates to ``hermes_provision.refresh_driver_env``.

The file is regenerated whole on every reprovision and OAuth refresh, so its
header must not invite edits (#2373). It names the systemd drop-in instead,
which is also where ``override.conf`` sends an operator's ``HERMES_WEB_DIST``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hal0.agents import hermes_provision as hp
from hal0.agents.hermes.driver import HermesDriver

_REPO = Path(__file__).resolve().parents[2]
_OVERRIDE_CONF = _REPO / "installer" / "systemd" / "hal0-agent@hermes.service.d" / "override.conf"


class _FakeRunner:
    def run(self, *_a: object, **_k: object) -> None:  # pragma: no cover — must not run
        raise AssertionError("thin register must not shell out")


@pytest.fixture
def driver_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the provisioner's driver env at a tmp path with a known key + OAuth line."""
    target = tmp_path / "etc-agents" / "hermes.env"
    monkeypatch.setattr(hp, "DRIVER_ENV_PATH", target)
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer="admin": "svc-key-1")
    monkeypatch.setattr(
        "hal0.oauth.store.driver_env_lines", lambda: ["HAL0_OAUTH_GITHUB_TOKEN=gh-1"]
    )
    return target


def _driver() -> HermesDriver:
    return HermesDriver(runner=_FakeRunner(), prober=lambda: True)


def _keys(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in body.splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


# ── #2372: the driver no longer has its own writer ──────────────────────────


def test_driver_has_no_second_env_writer() -> None:
    assert not hasattr(HermesDriver, "_write_env_file")


def test_dashboard_install_writes_the_provisioner_body(driver_env: Path) -> None:
    """The API install path renders the same file the provisioner does."""
    _driver().install(bearer_token="caller-token")

    body = driver_env.read_text(encoding="utf-8")
    keys = _keys(body)
    assert keys["HAL0_MCP_TOKEN"] == "svc-key-1"
    assert keys["HAL0_OAUTH_GITHUB_TOKEN"] == "gh-1"
    assert "HAL0_BEARER_TOKEN" not in keys
    assert "caller-token" not in body
    # Byte-identical to what a reprovision writes: there is only one renderer.
    driver_env.unlink()
    hp.refresh_driver_env()
    assert driver_env.read_text(encoding="utf-8") == body


def test_dashboard_install_unprivileged_goes_through_the_seam(
    driver_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unprivileged hal0-api: no direct write into root:root /etc/hal0/agents."""
    monkeypatch.setattr(hp.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(hp, "_HAL0_AGENTENV", "/usr/lib/hal0/bin/hal0-agentenv")
    with patch.object(hp.subprocess, "run") as run:
        _driver().install(bearer_token="caller-token")

    run.assert_called_once()
    args, kwargs = run.call_args
    assert args[0] == [
        "sudo",
        "-n",
        "/usr/lib/hal0/bin/hal0-agentenv",
        "write-driver-env",
        "hermes",
    ]
    assert "HAL0_MCP_TOKEN=svc-key-1\n" in kwargs["input"]
    assert "HAL0_BEARER_TOKEN" not in kwargs["input"]
    assert not driver_env.exists()


def test_dashboard_install_keeps_mode_and_owner(
    driver_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rewrite by the install path lands 0600 and re-pins root:root."""
    driver_env.parent.mkdir(parents=True)
    driver_env.write_text("HAL0_API_URL=http://stale\n", encoding="utf-8")
    driver_env.chmod(0o644)
    chowns: list[tuple[str, int, int]] = []
    monkeypatch.setattr(hp.os, "chown", lambda p, u, g: chowns.append((str(p), u, g)))

    _driver().install(bearer_token=None)

    assert (driver_env.stat().st_mode & 0o777) == 0o600
    assert (str(driver_env), 0, 0) in chowns


# ── managed keys follow the source of truth on every rewrite ─────────────────


def test_reprovision_and_oauth_refresh_update_managed_keys(
    driver_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _driver().install(bearer_token=None)
    assert _keys(driver_env.read_text())["HAL0_MCP_TOKEN"] == "svc-key-1"

    # Key rotation, then a reprovision.
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer="admin": "svc-key-2")
    hp._write_driver_env(hp.BootstrapState(hermes_home="/tmp/hh", agent_id="hermes-agent"))
    assert _keys(driver_env.read_text())["HAL0_MCP_TOKEN"] == "svc-key-2"

    # An OAuth disconnect, then the refresh the OAuth route runs.
    monkeypatch.setattr("hal0.oauth.store.driver_env_lines", lambda: [])
    hp.refresh_driver_env()
    keys = _keys(driver_env.read_text())
    assert "HAL0_OAUTH_GITHUB_TOKEN" not in keys
    assert keys["HAL0_MCP_TOKEN"] == "svc-key-2"
    assert (driver_env.stat().st_mode & 0o777) == 0o600


# ── #2373: header, override.conf and the writer agree ───────────────────────


def test_header_says_do_not_edit_and_names_the_drop_in(driver_env: Path) -> None:
    hp.refresh_driver_env()
    body = driver_env.read_text(encoding="utf-8")
    header = [line for line in body.splitlines() if line.startswith("#")]
    assert "safe to edit" not in body
    assert "do not edit" in header[0]
    assert any("systemctl edit hal0-agent@hermes" in line for line in header)


def test_an_operator_line_in_the_env_file_is_not_kept(driver_env: Path) -> None:
    """The file is regenerated whole: this is why the header forbids edits."""
    hp.refresh_driver_env()
    with driver_env.open("a", encoding="utf-8") as f:
        f.write("HERMES_WEB_DIST=/opt/dist\n")
    hp.refresh_driver_env()
    assert "HERMES_WEB_DIST" not in driver_env.read_text(encoding="utf-8")


def test_override_conf_points_web_dist_at_a_drop_in() -> None:
    text = _OVERRIDE_CONF.read_text(encoding="utf-8")
    flat = " ".join(line.lstrip("# ").strip() for line in text.splitlines())
    assert "set HERMES_WEB_DIST in /etc/hal0/agents/hermes.env" not in flat
    assert "systemctl edit hal0-agent@hermes" in flat
    assert "Environment=HERMES_WEB_DIST=" in flat
