"""#1814 — root-side guard for the ``prune-dnat`` seam verb.

``prune-dnat`` is the only verb in ``installer/wrappers/hal0-systemctl`` that
edits nftables, and the wrapper is reachable as root by the unprivileged
``hal0`` service account (packaging/sudoers/hal0-systemctl). So the guard has to
hold with the caller trusted for nothing beyond two integers.

These tests exercise the REAL bash wrapper through its side-effect-free
``check-dnat`` verb — the same ``validate_prunable_dnat`` the delete arm calls —
with stub ``nft`` and ``podman`` binaries on PATH. No root, no sudo, no
provisioned box, and nothing touches a real firewall. Same posture as the #1740
``check-quadlet`` suite.

The ruleset fixture is a verbatim capture from the box the bug was diagnosed on.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-systemctl"

RULESET = """\
table inet netavark { # handle 2
\tchain POSTROUTING { # handle 3
\t\tip saddr 10.88.0.0/16 jump nv_2f259bab_10_88_0_0_nm16 # handle 44
\t}

\tchain NETAVARK-HOSTPORT-DNAT { # handle 6
\t\tip daddr 127.0.0.1 tcp dport 8083 jump nv_2f259bab_10_88_0_0_nm16_dnat # handle 442
\t}

\tchain nv_2f259bab_10_88_0_0_nm16_dnat { # handle 45
\t\tip saddr 127.0.0.1 ip daddr 127.0.0.1 tcp dport 8083 jump NETAVARK-HOSTPORT-SETMARK # handle 444
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.92:8083 # handle 385
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.117:8083 # handle 473
\t\tip daddr 127.0.0.1 tcp dport 8081 dnat ip to 10.88.0.80:8081 # handle 341
\t}
}
"""

#: 10.88.0.117 (embed) and 10.88.0.80 (agent) are running; .92 is the leak.
LIVE_IPS = "10.88.0.117 \n10.88.0.80 \n"


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fakebin(tmp_path: Path) -> Path:
    """A PATH dir with stub ``nft`` + ``podman``, plus the real coreutils."""
    binder = tmp_path / "bin"
    binder.mkdir()
    ruleset = tmp_path / "ruleset.txt"
    ruleset.write_text(RULESET)
    ips = tmp_path / "ips.txt"
    ips.write_text(LIVE_IPS)
    _stub(
        binder / "nft",
        f'if [[ "$*" == *"list table"* ]]; then cat {ruleset}; exit 0; fi\n'
        f'echo "$*" >> {tmp_path}/nft-calls.txt\n',
    )
    _stub(
        binder / "podman",
        f'if [[ "$1" == "ps" ]]; then echo abc; echo def; exit 0; fi\n'
        f'if [[ "$1" == "inspect" ]]; then cat {ips}; exit 0; fi\nexit 1\n',
    )
    return binder


def _check(fakebin: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:/usr/bin:/bin"
    return subprocess.run(
        [str(WRAPPER), "check-dnat", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


# ── the accepted case ────────────────────────────────────────────────────────


def test_accepts_a_dead_target_rule(fakebin: Path) -> None:
    proc = _check(fakebin, "8083", "385")
    assert proc.returncode == 0, proc.stderr
    assert "nv_2f259bab_10_88_0_0_nm16_dnat" in proc.stdout
    assert "10.88.0.92" in proc.stdout


# ── the refusals that make this narrow ───────────────────────────────────────


def test_refuses_a_handle_whose_target_is_a_running_container(fakebin: Path) -> None:
    """The core safety property: repair can never cut a working port."""
    proc = _check(fakebin, "8083", "473")
    assert proc.returncode == 64
    assert "running container" in proc.stderr


def test_refuses_a_live_single_rule_port(fakebin: Path) -> None:
    proc = _check(fakebin, "8081", "341")
    assert proc.returncode == 64
    assert "running container" in proc.stderr


def test_refuses_a_handle_that_belongs_to_a_different_port(fakebin: Path) -> None:
    """Handle 385 IS a real DNAT handle, but for 8083 — asking to prune it as
    port 8090 must not work, or the port argument would be decorative."""
    proc = _check(fakebin, "8090", "385")
    assert proc.returncode == 64
    assert "no dport-8090 dnat rule at handle 385" in proc.stderr


def test_refuses_a_handle_outside_any_nv_dnat_chain(fakebin: Path) -> None:
    """Handle 442 is the NETAVARK-HOSTPORT-DNAT *jump* rule for 8083, and 444 is
    a SETMARK jump inside the dnat chain. Neither is a DNAT rule; deleting
    either would break hostport routing wholesale."""
    for handle in ("442", "444", "44"):
        proc = _check(fakebin, "8083", handle)
        assert proc.returncode == 64, handle
        assert "no dport-8083 dnat rule" in proc.stderr


def test_refuses_an_unknown_handle(fakebin: Path) -> None:
    assert _check(fakebin, "8083", "999999").returncode == 64


@pytest.mark.parametrize(
    "port",
    ["0", "65536", "-1", "80a", "8083;rm", "8083 8081", "", "0x1f90", "+8083"],
)
def test_rejects_a_non_port(fakebin: Path, port: str) -> None:
    proc = _check(fakebin, port, "385")
    assert proc.returncode == 64
    assert "port" in proc.stderr


@pytest.mark.parametrize(
    "handle",
    ["0", "-3", "38a", "385;nft flush ruleset", "385 473", "", "handle"],
)
def test_rejects_a_non_handle(fakebin: Path, handle: str) -> None:
    proc = _check(fakebin, "8083", handle)
    assert proc.returncode == 64
    assert "handle" in proc.stderr


def test_check_dnat_never_touches_the_firewall(fakebin: Path, tmp_path: Path) -> None:
    """The dry-run verb must not issue a single mutating nft call."""
    _check(fakebin, "8083", "385")
    assert not (tmp_path / "nft-calls.txt").exists()


def test_dies_when_the_tools_are_missing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    env = dict(os.environ)
    env["PATH"] = str(empty)
    proc = subprocess.run(
        [str(WRAPPER), "check-dnat", "8083", "385"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode != 0


def test_verb_is_advertised_in_help() -> None:
    proc = subprocess.run([str(WRAPPER), "help"], capture_output=True, text=True, check=False)
    assert "prune-dnat <port> <handle>" in proc.stdout
    assert "check-dnat <port> <handle>" in proc.stdout
