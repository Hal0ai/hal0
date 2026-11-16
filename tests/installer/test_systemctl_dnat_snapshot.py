"""#3724 — the ``dnat-snapshot`` and ``network-reload`` verbs of hal0-systemctl.

Runs the REAL bash wrapper with stub ``nft`` / ``iptables`` / ``podman`` on
PATH: no root, no sudo, no firewall touched. ``dnat-snapshot``'s output is fed
straight into :func:`hal0.system.netavark.parse_snapshot`, so the framing the
wrapper prints and the framing Python reads cannot drift apart.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from hal0.system import netavark

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "installer" / "wrappers" / "hal0-systemctl"
DN = "NETAVARK-DN-1D8721804F16F"

IPTABLES = f"""\
-P PREROUTING ACCEPT
-N {DN}
-N NETAVARK-HOSTPORT-DNAT
-N ts-postrouting
-A POSTROUTING -j ts-postrouting
-A {DN} -p tcp -m tcp --dport 8087 -j DNAT --to-destination 10.88.0.19:8087
-A {DN} -p tcp -m tcp --dport 3001 -j DNAT --to-destination 10.88.0.109:8080
-A NETAVARK-HOSTPORT-DNAT -p tcp -m tcp --dport 3001 -m comment --comment "dnat name: podman id: 4591" -j {DN}
-A ts-postrouting -m mark --mark 0x40000/0xff0000 -j MASQUERADE
"""

INSPECT = (
    'hal0-slot-2|10.88.0.19 |{"8087/tcp":[{"HostIp":"","HostPort":"8087"}]}\n'
    'hal0-openwebui|10.88.0.109 |{"8080/tcp":[{"HostIp":"","HostPort":"3001"}]}\n'
)


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fakebin(tmp_path: Path) -> Path:
    binder = tmp_path / "bin"
    binder.mkdir()
    (tmp_path / "ipt.txt").write_text(IPTABLES)
    (tmp_path / "inspect.txt").write_text(INSPECT)
    log = tmp_path / "calls.txt"
    # netavark 1.4 box: no `inet netavark` table.
    _stub(binder / "nft", f'echo "nft $*" >> {log}\necho "Error: No such file" >&2\nexit 1\n')
    _stub(
        binder / "iptables",
        f'echo "iptables $*" >> {log}\n'
        f'[[ "$*" == "-t nat -S" ]] && {{ cat {tmp_path}/ipt.txt; exit 0; }}\nexit 2\n',
    )
    _stub(
        binder / "podman",
        f'echo "podman $*" >> {log}\n'
        'if [[ "$1" == "ps" ]]; then echo abc; echo def; exit 0; fi\n'
        f'if [[ "$1" == "inspect" ]]; then cat {tmp_path}/inspect.txt; exit 0; fi\n'
        'if [[ "$1 $2 $3" == "network reload --all" ]]; then echo reloaded; exit 0; fi\n'
        "exit 1\n",
    )
    return binder


def _run(fakebin: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:/usr/bin:/bin"
    # `bash <wrapper>`: the suite's privileged-seam guard (tests/conftest.py)
    # keys on the program name, and network-reload is a mutating verb. Every
    # binary it can reach here is a stub on PATH.
    return subprocess.run(
        ["bash", str(WRAPPER), *args], capture_output=True, text=True, check=False, env=env
    )


def test_dnat_snapshot_frames_both_backends_and_the_containers(fakebin: Path) -> None:
    proc = _run(fakebin, "dnat-snapshot")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "@@nft absent" in out
    assert "@@iptables ok" in out
    assert "@@containers ok" in out
    # Only netavark's hostport/DN lines leave the root side.
    assert "ts-postrouting" not in out
    assert "-P PREROUTING" not in out


def test_dnat_snapshot_output_parses_into_a_missing_rule_finding(fakebin: Path) -> None:
    snap = netavark.parse_snapshot(_run(fakebin, "dnat-snapshot").stdout)
    assert snap.backend == netavark.BACKEND_IPTABLES
    missing = {v.port: v.owner.name for v in netavark.audit_snapshot(snap) if v.missing and v.owner}
    assert missing == {8087: "hal0-slot-2"}


def test_dnat_snapshot_uses_the_literal_inspect_format(fakebin: Path, tmp_path: Path) -> None:
    _run(fakebin, "dnat-snapshot")
    calls = (tmp_path / "calls.txt").read_text()
    assert f"podman inspect --format {netavark.CONTAINER_INSPECT_FORMAT} abc def" in calls
    # Read-only: no reload, no nft/iptables write.
    assert "reload" not in calls
    assert " -D " not in calls and "delete" not in calls


def test_dnat_snapshot_takes_no_arguments(fakebin: Path) -> None:
    proc = _run(fakebin, "dnat-snapshot", "--all")
    assert proc.returncode == 64
    assert "takes no arguments" in proc.stderr


def test_network_reload_runs_exactly_podman_network_reload_all(
    fakebin: Path, tmp_path: Path
) -> None:
    proc = _run(fakebin, "network-reload")
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "calls.txt").read_text().splitlines() == ["podman network reload --all"]


@pytest.mark.parametrize("extra", ["--all", "podman", "hal0-slot-1", ""])
def test_network_reload_refuses_any_argument(fakebin: Path, tmp_path: Path, extra: str) -> None:
    proc = _run(fakebin, "network-reload", extra)
    assert proc.returncode == 64
    assert not (tmp_path / "calls.txt").exists()


def test_both_verbs_are_advertised_in_help() -> None:
    proc = subprocess.run([str(WRAPPER), "help"], capture_output=True, text=True, check=False)
    assert "dnat-snapshot" in proc.stdout
    assert "network-reload" in proc.stdout
