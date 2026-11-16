"""#1814 / #3724 — the doctor surfaces for netavark DNAT rules.

Covers the ``doctor all`` rows (DNAT rules + their leak source, netns
durability), ``hal0 doctor ports`` and its ``--fix`` path, and the
``SystemCtlSeam`` client side of the ``prune-dnat`` / ``dnat-snapshot`` /
``network-reload`` verbs. The parser/classifier itself is pinned in
``tests/system/test_netavark.py`` and ``tests/system/test_netavark_backends.py``.
"""

from __future__ import annotations

import subprocess
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from hal0.cli.doctor_all import check_netns_durability, check_port_dnat
from hal0.cli.doctor_verify import _FAIL, _PASS, _WARN
from hal0.system import netavark
from hal0.system.netavark import NetavarkUnavailable, NetnsDurability
from hal0.system.seam import Hal0SeamMissing, SystemCtlSeam

NFT = """\
table inet netavark { # handle 2
\tchain nv_2f259bab_10_88_0_0_nm16_dnat { # handle 45
\t\tip daddr 127.0.0.1 tcp dport 8081 dnat ip to 10.88.0.80:8081 # handle 341
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.92:8083 # handle 385
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.117:8083 # handle 473
\t}
}
"""

CONTAINERS = (
    'hal0-slot-agent|10.88.0.80 |{"8081/tcp":[{"HostIp":"127.0.0.1","HostPort":"8081"}]}\n'
    'hal0-slot-embed|10.88.0.117 |{"8083/tcp":[{"HostIp":"127.0.0.1","HostPort":"8083"}]}\n'
)

DN = "NETAVARK-DN-1D8721804F16F"
#: netavark 1.4 / iptables-nft (#3237, #3724): no ``inet netavark`` table. The
#: hostport jump for 8083 is gone (the #3724 shape); 8081's survives.
IPT_MISSING = (
    "-N NETAVARK-HOSTPORT-DNAT\n"
    f"-N {DN}\n"
    f"-A {DN} -p tcp -m tcp --dport 8081 -j DNAT --to-destination 10.88.0.80:8081\n"
    f"-A {DN} -p tcp -m tcp --dport 8083 -j DNAT --to-destination 10.88.0.117:8083\n"
    f"-A NETAVARK-HOSTPORT-DNAT -p tcp -m tcp --dport 8081 -j {DN}\n"
)
IPT_CLEAN = IPT_MISSING + f"-A NETAVARK-HOSTPORT-DNAT -p tcp -m tcp --dport 8083 -j {DN}\n"

SLOTS = [
    {"name": "agent", "port": 8081, "state": "ready"},
    {"name": "embed", "port": 8083, "state": "ready"},
]


def _snap(*, nft: str | None = None, ipt: str | None = None) -> netavark.DnatSnapshot:
    text = ["@@nft absent"] if nft is None else ["@@nft ok", nft]
    text += ["@@iptables absent"] if ipt is None else ["@@iptables ok", ipt]
    text += ["@@containers ok", CONTAINERS]
    return netavark.parse_snapshot("\n".join(text))


def _reader(snap: netavark.DnatSnapshot):
    return lambda: snap


def _unavailable() -> netavark.DnatSnapshot:
    raise NetavarkUnavailable("no `inet netavark` nftables table and no NETAVARK-HOSTPORT-DNAT")


# ── doctor all: Port DNAT rules ──────────────────────────────────────────────


def test_flags_the_poisoned_port_and_names_the_repair() -> None:
    check = check_port_dnat(SLOTS, _reader(_snap(nft=NFT)))
    assert check.status == _FAIL
    assert "8083" in check.detail
    assert "8081" not in check.detail  # the clean port is never named
    assert "hal0 doctor ports --fix" in check.detail
    assert "nftables" in check.detail


def test_passes_when_every_port_is_clean() -> None:
    check = check_port_dnat([{"name": "agent", "port": 8081}], _reader(_snap(ipt=IPT_CLEAN)))
    assert check.status == _PASS
    assert "iptables backend" in check.detail


def test_iptables_missing_rule_fails_naming_container_and_port() -> None:
    """#3724: the row used to say "audit skipped" on this box."""
    check = check_port_dnat(SLOTS, _reader(_snap(ipt=IPT_MISSING)))
    assert check.status == _FAIL
    assert "hal0-slot-embed:8083" in check.detail
    assert "8081" not in check.detail
    assert "iptables backend" in check.detail
    assert "hal0 doctor ports --fix" in check.detail


def test_missing_rule_is_found_even_with_no_slot_ports() -> None:
    """Every running publisher is audited, not just slot ports (OpenWebUI,
    langfuse lost their rules too in #3724)."""
    check = check_port_dnat([], _reader(_snap(ipt=IPT_MISSING)))
    assert check.status == _FAIL
    assert "hal0-slot-embed:8083" in check.detail


def test_no_bound_ports_and_no_audit_is_a_pass() -> None:
    assert check_port_dnat([], _unavailable).status == _PASS


def test_missing_slots_payload_warns_rather_than_accusing() -> None:
    assert check_port_dnat(None).status == _WARN


def test_unavailable_audit_warns_rather_than_failing() -> None:
    """No rules on either backend is not corruption — absence of evidence."""
    check = check_port_dnat(SLOTS, _unavailable)
    assert check.status == _WARN
    assert "unavailable" in check.detail


# ── hal0 doctor ports (+ --fix) ──────────────────────────────────────────────


class _FakeSeam:
    reloads = 0
    pruned: ClassVar[list[tuple[int, int]]] = []
    fail_reload = False

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def network_reload(self, **_: Any) -> None:
        type(self).reloads += 1
        if type(self).fail_reload:
            raise subprocess.CalledProcessError(125, ["podman"], stderr="netns gone")

    def prune_dnat(self, port: int, handle: int, **_: Any) -> None:
        type(self).pruned.append((port, handle))


@pytest.fixture
def doctor(monkeypatch: pytest.MonkeyPatch):
    """Drive ``hal0 doctor ports`` against a scripted sequence of snapshots."""
    from hal0.cli import _shared, doctor_commands
    from hal0.system import seam as seam_mod

    monkeypatch.setattr(_shared, "api_get", lambda *_a, **_k: SLOTS)
    _FakeSeam.reloads, _FakeSeam.pruned, _FakeSeam.fail_reload = 0, [], False
    monkeypatch.setattr(seam_mod, "SystemCtlSeam", _FakeSeam)

    def run(snaps: list[netavark.DnatSnapshot], *args: str):
        queue = list(snaps)
        monkeypatch.setattr(netavark, "read_snapshot", lambda **_k: queue.pop(0))
        return CliRunner().invoke(doctor_commands.app, ["ports", *args])

    return run


def test_doctor_ports_reports_missing_rule_and_names_the_fix(doctor) -> None:
    result = doctor([_snap(ipt=IPT_MISSING)])
    assert result.exit_code == 1, result.output
    assert "hal0-slot-embed:8083" in result.output
    assert "hal0 doctor ports --fix" in result.output
    assert "audit skipped" not in result.output
    assert _FakeSeam.reloads == 0  # never a side effect of the audit


def test_doctor_ports_fix_reloads_networks_and_reaudits(doctor) -> None:
    result = doctor([_snap(ipt=IPT_MISSING), _snap(ipt=IPT_CLEAN)], "--fix")
    assert result.exit_code == 0, result.output
    assert _FakeSeam.reloads == 1
    assert "podman network reload --all" in result.output
    assert "clean after repair" in result.output


def test_doctor_ports_fix_fails_when_the_rule_is_still_missing(doctor) -> None:
    result = doctor([_snap(ipt=IPT_MISSING), _snap(ipt=IPT_MISSING)], "--fix")
    assert result.exit_code == 1, result.output
    assert _FakeSeam.reloads == 1


def test_doctor_ports_fix_reports_a_failed_reload(doctor) -> None:
    _FakeSeam.fail_reload = True
    result = doctor([_snap(ipt=IPT_MISSING), _snap(ipt=IPT_CLEAN)], "--fix")
    assert result.exit_code == 1, result.output
    assert "netns gone" in result.output


def test_doctor_ports_fix_prunes_stale_nft_rules_without_reloading(doctor) -> None:
    clean = NFT.replace(
        "\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.92:8083 # handle 385\n", ""
    )
    result = doctor([_snap(nft=NFT), _snap(nft=clean)], "--fix")
    assert result.exit_code == 0, result.output
    assert _FakeSeam.pruned == [(8083, 385)]
    assert _FakeSeam.reloads == 0


def test_doctor_ports_fix_names_the_manual_delete_for_stale_iptables(doctor) -> None:
    stale = IPT_CLEAN.replace(
        f"-A {DN} -p tcp -m tcp --dport 8083 -j DNAT --to-destination 10.88.0.117:8083\n",
        f"-A {DN} -p tcp -m tcp --dport 8083 -j DNAT --to-destination 10.88.0.9:8083\n"
        f"-A {DN} -p tcp -m tcp --dport 8083 -j DNAT --to-destination 10.88.0.117:8083\n",
    )
    result = doctor([_snap(ipt=stale), _snap(ipt=stale)], "--fix")
    assert result.exit_code == 1, result.output
    assert f"sudo iptables -t nat -D {DN}" in result.output
    assert "10.88.0.9:8083" in result.output
    assert _FakeSeam.pruned == []


def test_doctor_ports_clean_box_exits_zero(doctor) -> None:
    result = doctor([_snap(ipt=IPT_CLEAN)])
    assert result.exit_code == 0, result.output
    assert "clean on every published port" in result.output


# ── doctor all: Container netns (the leak's source) ──────────────────────────


def test_dangling_netns_fails_and_names_linger(tmp_path) -> None:
    state = NetnsDurability(sandbox_keys=(str(tmp_path / "gone"),), linger_enabled=False)
    check = check_netns_durability(lambda: state)
    assert check.status == _FAIL
    assert "loginctl enable-linger root" in check.detail


class _Volatile:
    """Netns that still exist but sit in a runtime dir logind may unmount."""

    sandbox_keys = ("/run/user/0/netns/x",)
    dangling: tuple[str, ...] = ()
    volatile = True


def test_volatile_but_intact_netns_only_warns() -> None:
    check = check_netns_durability(_Volatile)
    assert check.status == _WARN
    assert "loginctl enable-linger root" in check.detail


def test_durable_netns_passes(tmp_path) -> None:
    key = tmp_path / "netns-live"
    key.write_text("")
    state = NetnsDurability(sandbox_keys=(str(key),), linger_enabled=False)
    assert check_netns_durability(lambda: state).status == _PASS


def test_no_containers_is_a_pass() -> None:
    state = NetnsDurability(sandbox_keys=(), linger_enabled=False)
    assert check_netns_durability(lambda: state).status == _PASS


# ── the seam client ──────────────────────────────────────────────────────────


def _seam(calls: list[list[str]]) -> SystemCtlSeam:
    def run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "pruned", "")

    return SystemCtlSeam(run=run, is_hal0_user=lambda: True, seam_bin="/seam/hal0-systemctl")


def test_prune_dnat_routes_through_sudo_with_two_integers() -> None:
    calls: list[list[str]] = []
    _seam(calls).prune_dnat(8083, 385)
    assert calls == [["sudo", "-n", "/seam/hal0-systemctl", "prune-dnat", "8083", "385"]]


def test_prune_dnat_dry_run_uses_the_check_verb() -> None:
    calls: list[list[str]] = []
    _seam(calls).prune_dnat(8083, 385, dry_run=True)
    assert calls[0][3] == "check-dnat"


@pytest.mark.parametrize("port", [0, 65536, -1, "8083", True, 8083.0])
def test_prune_dnat_rejects_a_bad_port_before_spawning_anything(port: object) -> None:
    calls: list[list[str]] = []
    with pytest.raises(ValueError):
        _seam(calls).prune_dnat(port, 385)  # type: ignore[arg-type]
    assert calls == []


@pytest.mark.parametrize("handle", [0, -1, "385", True, None])
def test_prune_dnat_rejects_a_bad_handle_before_spawning_anything(handle: object) -> None:
    calls: list[list[str]] = []
    with pytest.raises(ValueError):
        _seam(calls).prune_dnat(8083, handle)  # type: ignore[arg-type]
    assert calls == []


# ── the dnat-snapshot / network-reload seam clients (#3724) ──────────────────


def _recording_seam(calls: list[list[str]], *, hal0: bool, seam_bin: str = "/seam/hal0-systemctl"):
    def run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "@@nft absent\n", "")

    return SystemCtlSeam(run=run, is_hal0_user=lambda: hal0, seam_bin=seam_bin)


def test_dnat_snapshot_routes_through_sudo_with_no_arguments() -> None:
    calls: list[list[str]] = []
    assert _recording_seam(calls, hal0=True).dnat_snapshot() == "@@nft absent\n"
    assert calls == [["sudo", "-n", "/seam/hal0-systemctl", "dnat-snapshot"]]


def test_network_reload_routes_through_sudo_with_no_arguments() -> None:
    calls: list[list[str]] = []
    _recording_seam(calls, hal0=True).network_reload()
    assert calls == [["sudo", "-n", "/seam/hal0-systemctl", "network-reload"]]


def test_root_calls_the_installed_wrapper_directly(monkeypatch, tmp_path) -> None:
    wrapper = tmp_path / "hal0-systemctl"
    wrapper.write_text("#!/bin/sh\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr("hal0.system.seam.os.geteuid", lambda: 0)
    calls: list[list[str]] = []
    seam = _recording_seam(calls, hal0=False, seam_bin=str(wrapper))
    seam.dnat_snapshot()
    seam.network_reload()
    assert calls == [[str(wrapper), "dnat-snapshot"], [str(wrapper), "network-reload"]]


def test_dnat_snapshot_without_a_reachable_wrapper_is_seam_missing(monkeypatch) -> None:
    monkeypatch.setattr("hal0.system.seam.os.geteuid", lambda: 1000)
    calls: list[list[str]] = []
    with pytest.raises(Hal0SeamMissing):
        _recording_seam(calls, hal0=False).dnat_snapshot()
    assert calls == []


def test_root_without_the_wrapper_reloads_with_podman_directly(monkeypatch) -> None:
    monkeypatch.setattr("hal0.system.seam.os.geteuid", lambda: 0)
    calls: list[list[str]] = []
    _recording_seam(calls, hal0=False, seam_bin="/nonexistent/hal0-systemctl").network_reload()
    assert calls == [["podman", "network", "reload", "--all"]]
