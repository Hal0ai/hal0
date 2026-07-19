"""Tests for the D hardened-perms env seam (hal0-agentenv).

The provisioner writes two .env files into directories the hardened model pins
root:root — the secrets vault (/var/lib/hal0/secrets/agents/hermes.env) and the
driver env (/etc/hal0/agents/hermes.env). When hal0-api runs unprivileged it
can't write those dirs, so ``_write_secrets_env`` / ``_write_driver_env`` branch
on euid: root writes directly (+ re-pins root:root), non-root delegates to
``sudo -n hal0-agentenv``.

These tests assert both branches. The autouse ``_euid_root_by_default`` fixture
(conftest) makes euid==0 the default; the seam tests override to non-root.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hal0.agents import hermes_provision as hp

# ── secrets vault ────────────────────────────────────────────────────────────


def test_secrets_env_root_writes_directly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """euid==0: merge into the vault directly, 0600, no sudo."""
    vault = tmp_path / "hermes.env"
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", vault)
    # default fixture already forces euid==0; be explicit for clarity.
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
    with patch.object(hp.subprocess, "run") as run:
        hp._write_secrets_env({"A": "1", "B": "2"})
    run.assert_not_called()
    assert vault.read_text() == "A=1\nB=2\n"
    assert (vault.stat().st_mode & 0o777) == 0o600


def test_secrets_env_root_preserves_existing_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """euid==0 merge keeps operator comments + unrelated keys, replaces matches."""
    vault = tmp_path / "hermes.env"
    vault.write_text("# operator note\nA=1\nKEEP=yes\n")
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", vault)
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
    hp._write_secrets_env({"A": "99", "C": "3"})
    assert vault.read_text() == "# operator note\nA=99\nKEEP=yes\nC=3\n"


def test_secrets_env_nonroot_routes_through_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """euid!=0: pipe KEY=VALUE updates to `sudo -n hal0-agentenv merge-secrets`."""
    monkeypatch.setattr(hp.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(hp, "_HAL0_AGENTENV", "/usr/lib/hal0/bin/hal0-agentenv")
    with patch.object(hp.subprocess, "run") as run:
        hp._write_secrets_env({"A": "1", "B": "2"})
    run.assert_called_once()
    args, kwargs = run.call_args
    assert args[0] == [
        "sudo",
        "-n",
        "/usr/lib/hal0/bin/hal0-agentenv",
        "merge-secrets",
        "hermes",
    ]
    assert kwargs["input"] == "A=1\nB=2\n"
    assert kwargs["check"] is True
    assert kwargs["text"] is True


def test_secrets_env_nonroot_propagates_seam_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero seam exit must raise (so voice_wire surfaces it, not swallow)."""
    monkeypatch.setattr(hp.os, "geteuid", lambda: 1000)

    def _boom(*_a, **_k):
        raise subprocess.CalledProcessError(1, ["sudo"])

    monkeypatch.setattr(hp.subprocess, "run", _boom)
    with pytest.raises(subprocess.CalledProcessError):
        hp._write_secrets_env({"A": "1"})


def test_voice_wire_surfaces_seam_failure_as_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """voice_wire returns FAIL (not a swallowed OK) when the seam write fails."""
    monkeypatch.setattr(hp.os, "geteuid", lambda: 1000)

    def _boom(*_a, **_k):
        raise subprocess.CalledProcessError(1, ["sudo", "-n", "hal0-agentenv"])

    monkeypatch.setattr(hp.subprocess, "run", _boom)

    class _IO:
        def fetch_slots(self):
            return [
                {
                    "name": "kokoro",
                    "type": "tts",
                    "state": "ready",
                    "backend_url": "http://127.0.0.1:8084/v1",
                },
            ]

        def run(self, *_a, **_k):  # config-set path; unreached on the FAIL above
            raise AssertionError("config-set should not run after a secrets failure")

    state = hp.BootstrapState(hermes_home="/tmp/hh", agent_id="hermes-agent")
    out = hp._phase_voice_wire(hp._StepCtx(state=state, io=_IO()))
    assert out.status == hp.PhaseStatus.FAIL
    assert "secrets env write" in out.reason


# ── driver env ───────────────────────────────────────────────────────────────


def test_driver_env_nonroot_routes_through_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """euid!=0: the non-secret driver env is written via the seam too."""
    # Point at a non-existent path so the hash-skip doesn't early-return.
    monkeypatch.setattr(hp, "DRIVER_ENV_PATH", tmp_path / "agents" / "hermes.env")
    monkeypatch.setattr(hp.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(hp, "_HAL0_AGENTENV", "/usr/lib/hal0/bin/hal0-agentenv")
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"), agent_id="hermes-agent")
    with patch.object(hp.subprocess, "run") as run:
        path, wrote = hp._write_driver_env(state)
    assert wrote is True
    run.assert_called_once()
    args, kwargs = run.call_args
    assert args[0][:4] == ["sudo", "-n", "/usr/lib/hal0/bin/hal0-agentenv", "write-driver-env"]
    assert "HAL0_API_URL=" in kwargs["input"]
    # Seam path was used — nothing written to the real (root-owned) location.
    assert not path.exists()


def test_driver_env_root_writes_directly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """euid==0: write the driver env directly, no sudo."""
    target = tmp_path / "agents" / "hermes.env"
    monkeypatch.setattr(hp, "DRIVER_ENV_PATH", target)
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"), agent_id="hermes-agent")
    with patch.object(hp.subprocess, "run") as run:
        path, wrote = hp._write_driver_env(state)
    run.assert_not_called()
    assert wrote is True
    assert path.exists()
    assert "HAL0_API_URL=" in path.read_text()


# ── seed TOML (manager seed / MCP allow-list) ────────────────────────────────


def test_seed_toml_nonroot_routes_through_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """euid!=0: the seed TOML write lands in root:root /etc/hal0/agents via the seam.

    The read-merge itself runs unprivileged (the file is 0644 world-readable);
    only the WRITE is delegated to `sudo -n hal0-agentenv write-seed-toml`.
    """
    monkeypatch.setattr(hp, "INSTALL_SEED_PATH", tmp_path / "agents" / "hermes.toml")
    monkeypatch.setattr(hp.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(hp, "_HAL0_AGENTENV", "/usr/lib/hal0/bin/hal0-agentenv")
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"), agent_id="hermes-agent")
    with patch.object(hp.subprocess, "run") as run:
        path, wrote = hp._write_seed_toml(state, repair=True)
    assert wrote is True
    run.assert_called_once()
    args, kwargs = run.call_args
    assert args[0][:4] == ["sudo", "-n", "/usr/lib/hal0/bin/hal0-agentenv", "write-seed-toml"]
    assert kwargs["input"]  # non-empty serialized TOML body
    assert kwargs["check"] is True
    # Seam path was used — nothing written to the real (root-owned) location.
    assert not path.exists()


def test_seed_toml_root_writes_directly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """euid==0: write the seed TOML directly, no sudo."""
    target = tmp_path / "agents" / "hermes.toml"
    monkeypatch.setattr(hp, "INSTALL_SEED_PATH", target)
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"), agent_id="hermes-agent")
    with patch.object(hp.subprocess, "run") as run:
        path, wrote = hp._write_seed_toml(state, repair=True)
    run.assert_not_called()
    assert wrote is True
    assert path.exists()
    assert path.read_text()  # non-empty TOML
