"""#1814 — the doctor surfaces for stale netavark DNAT rules.

Covers the two ``doctor all`` rows (corruption + its source, netns durability)
and the ``SystemCtlSeam.prune_dnat`` client side. The parser/classifier itself
is pinned in ``tests/system/test_netavark.py`` against a real box capture.
"""

from __future__ import annotations

import subprocess

import pytest

from hal0.cli.doctor_all import check_netns_durability, check_port_dnat
from hal0.cli.doctor_verify import _FAIL, _PASS, _WARN
from hal0.system import netavark
from hal0.system.netavark import NetavarkUnavailable, NetnsDurability
from hal0.system.seam import SystemCtlSeam

NFT = """\
table inet netavark { # handle 2
\tchain nv_2f259bab_10_88_0_0_nm16_dnat { # handle 45
\t\tip daddr 127.0.0.1 tcp dport 8081 dnat ip to 10.88.0.80:8081 # handle 341
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.92:8083 # handle 385
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.117:8083 # handle 473
\t}
}
"""

SLOTS = [{"name": "agent", "port": 8081}, {"name": "embed", "port": 8083}]


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(netavark, "read_dnat_rules", lambda: netavark.parse_dnat_rules(NFT))
    monkeypatch.setattr(netavark, "read_live_container_ips", lambda: {"10.88.0.80", "10.88.0.117"})


# ── doctor all: Port DNAT rules ──────────────────────────────────────────────


def test_flags_the_poisoned_port_and_names_the_repair(live: None) -> None:
    check = check_port_dnat(SLOTS)
    assert check.status == _FAIL
    assert "8083" in check.detail
    assert "8081" not in check.detail  # the clean port is never named
    assert "hal0 doctor ports --fix" in check.detail


def test_passes_when_every_port_is_clean(live: None) -> None:
    check = check_port_dnat([{"name": "agent", "port": 8081}])
    assert check.status == _PASS


def test_no_bound_ports_is_a_pass(live: None) -> None:
    assert check_port_dnat([]).status == _PASS


def test_missing_slots_payload_warns_rather_than_accusing() -> None:
    assert check_port_dnat(None).status == _WARN


def test_absent_netavark_table_warns_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No nft / no netavark table is not corruption — absence of evidence."""

    def boom() -> list[netavark.DnatRule]:
        raise NetavarkUnavailable("nft failed (1): No such file or directory")

    monkeypatch.setattr(netavark, "read_dnat_rules", boom)
    check = check_port_dnat(SLOTS)
    assert check.status == _WARN
    assert "unavailable" in check.detail


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
