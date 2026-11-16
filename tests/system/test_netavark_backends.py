"""Backend-aware DNAT audit (#3724): iptables + nftables, missing/stale/duplicate.

The iptables fixtures are lines from ``iptables -t nat -S`` on the box #3724
was diagnosed on (Ubuntu 24.04, podman 4.9.3, netavark 1.4.0, iptables-nft),
trimmed to three containers. ``IPT_ISSUE`` is the failure shape: the
``NETAVARK-HOSTPORT-DNAT`` jumps survive only for the most recently recreated
container, while the per-network ``NETAVARK-DN-*`` DNAT rules are still there.
"""

from __future__ import annotations

import subprocess

import pytest

from hal0.system import netavark
from hal0.system.netavark import (
    BACKEND_IPTABLES,
    BACKEND_NFTABLES,
    DnatSnapshot,
    LiveContainer,
    NetavarkUnavailable,
    ReloadGate,
    audit_snapshot,
    auto_reload,
    collect_snapshot_text,
    parse_containers,
    parse_iptables_dnat_rules,
    parse_snapshot,
    read_snapshot,
)
from hal0.system.seam import Hal0SeamMissing

_DN = "NETAVARK-DN-1D8721804F16F"


def _jump(port: int, cid: str) -> str:
    return (
        f"-A NETAVARK-HOSTPORT-DNAT -p tcp -m tcp --dport {port} -m comment "
        f'--comment "dnat name: podman id: {cid}" -j {_DN}'
    )


def _dn(port: int, target: str) -> str:
    return (
        f"-A {_DN} -s 10.88.0.0/16 -p tcp -m tcp --dport {port} -j NETAVARK-HOSTPORT-SETMARK\n"
        f"-A {_DN} -s 127.0.0.1/32 -p tcp -m tcp --dport {port} -j NETAVARK-HOSTPORT-SETMARK\n"
        f"-A {_DN} -p tcp -m tcp --dport {port} -j DNAT --to-destination {target}"
    )


_HEADER = (
    "-N NETAVARK-1D8721804F16F\n"
    f"-N {_DN}\n"
    "-N NETAVARK-HOSTPORT-DNAT\n"
    "-N NETAVARK-HOSTPORT-MASQ\n"
    "-N NETAVARK-HOSTPORT-SETMARK"
)
_DN_RULES = "\n".join(
    [_dn(8087, "10.88.0.19:8087"), _dn(8082, "10.88.0.70:8082"), _dn(3001, "10.88.0.109:8080")]
)

IPT_HEALTHY = "\n".join(
    [_HEADER, _DN_RULES, _jump(8087, "ccc0"), _jump(8082, "5cd7"), _jump(3001, "4591")]
)
#: #3724: only OpenWebUI (recreated last) kept its hostport jump.
IPT_ISSUE = "\n".join([_HEADER, _DN_RULES, _jump(3001, "4591")])

CONTAINERS = (
    'hal0-slot-2|10.88.0.19 |{"8087/tcp":[{"HostIp":"","HostPort":"8087"}]}\n'
    'hal0-slot-1|10.88.0.70 |{"8082/tcp":[{"HostIp":"","HostPort":"8082"}]}\n'
    'hal0-openwebui|10.88.0.109 |{"8080/tcp":[{"HostIp":"","HostPort":"3001"}]}\n'
)

NFT_CLEAN = """\
table inet netavark { # handle 2
\tchain nv_2f259bab_10_88_0_0_nm16_dnat { # handle 45
\t\ttcp dport 8087 dnat ip to 10.88.0.19:8087 # handle 10
\t\ttcp dport 8082 dnat ip to 10.88.0.70:8082 # handle 11
\t\ttcp dport 3001 dnat ip to 10.88.0.109:8080 # handle 12
\t}
}
"""


def _framed(
    *, nft: str | None = None, ipt: str | None = None, containers: str | None = CONTAINERS
) -> str:
    out = []
    out += ["@@nft absent"] if nft is None else ["@@nft ok", nft]
    out += ["@@iptables absent"] if ipt is None else ["@@iptables ok", ipt]
    out += ["@@containers absent"] if containers is None else ["@@containers ok", containers]
    return "\n".join(out) + "\n"


def _by_port(snapshot: DnatSnapshot) -> dict[int, netavark.PortVerdict]:
    return {v.port: v for v in audit_snapshot(snapshot)}


# ── parsing ──────────────────────────────────────────────────────────────────


def test_iptables_parser_keeps_only_reachable_dnat_rules() -> None:
    rules = parse_iptables_dnat_rules(IPT_HEALTHY)
    assert [(r.dport, r.target_ip, r.target_port) for r in rules] == [
        (8087, "10.88.0.19", 8087),
        (8082, "10.88.0.70", 8082),
        (3001, "10.88.0.109", 8080),
    ]
    assert all(r.handle is None and r.spec and r.spec.startswith(f"-A {_DN}") for r in rules)


def test_iptables_rule_without_a_hostport_jump_routes_nothing() -> None:
    assert [r.dport for r in parse_iptables_dnat_rules(IPT_ISSUE)] == [3001]


def test_iptables_parser_reads_multiport_and_loopback_guards() -> None:
    text = (
        "-N NETAVARK-HOSTPORT-DNAT\n"
        f"-A NETAVARK-HOSTPORT-DNAT -d 127.0.0.1/32 -p tcp -m multiport --dports 8081,8090:8091 -j {_DN}\n"
        f"-A {_DN} -d 127.0.0.1/32 -p tcp -m tcp --dport 8081 -j DNAT --to-destination 10.88.0.80:8081\n"
        f"-A {_DN} -d 127.0.0.1/32 -p tcp -m tcp --dport 8090 -j DNAT --to-destination 10.88.0.81:8090\n"
        f"-A {_DN} -d 127.0.0.1/32 -p tcp -m tcp --dport 8099 -j DNAT --to-destination 10.88.0.82:8099\n"
    )
    rules = parse_iptables_dnat_rules(text)
    assert [r.dport for r in rules] == [8081, 8090]
    assert rules[0].daddr == "127.0.0.1"


def test_parse_containers_reads_names_ips_and_published_ports() -> None:
    got = parse_containers(
        CONTAINERS
        + 'langfuse_clickhouse_1|10.89.0.2 |{"8123/tcp":[{"HostIp":"127.0.0.1","HostPort":"8123"}],"9009/tcp":null}\n'
        + "hostnet||{}\n"
        + "garbage line\n"
    )
    by_name = {c.name: c for c in got}
    assert by_name["hal0-openwebui"].host_ports == frozenset({3001})
    assert by_name["langfuse_clickhouse_1"].host_ports == frozenset({8123})
    assert by_name["hostnet"].ips == frozenset()
    assert "garbage line" not in by_name


# ── backend selection ────────────────────────────────────────────────────────


def test_iptables_backend_is_chosen_when_the_nft_table_is_absent() -> None:
    assert parse_snapshot(_framed(ipt=IPT_HEALTHY)).backend == BACKEND_IPTABLES


def test_nftables_backend_wins_when_its_table_has_rules() -> None:
    assert parse_snapshot(_framed(nft=NFT_CLEAN, ipt=IPT_HEALTHY)).backend == BACKEND_NFTABLES


def test_empty_nft_table_defers_to_a_present_iptables_chain() -> None:
    empty = "table inet netavark { # handle 2\n}\n"
    assert parse_snapshot(_framed(nft=empty, ipt=IPT_HEALTHY)).backend == BACKEND_IPTABLES


def test_neither_backend_is_unavailable_not_a_finding() -> None:
    with pytest.raises(NetavarkUnavailable):
        parse_snapshot(_framed(ipt="-P PREROUTING ACCEPT\n"))


def test_missing_container_listing_is_unavailable() -> None:
    with pytest.raises(NetavarkUnavailable):
        parse_snapshot(_framed(ipt=IPT_HEALTHY, containers=None))


# ── the three signals on both backends ───────────────────────────────────────


def test_iptables_healthy_box_is_clean() -> None:
    verdicts = _by_port(parse_snapshot(_framed(ipt=IPT_HEALTHY)))
    assert sorted(verdicts) == [3001, 8082, 8087]
    assert not any(v.broken for v in verdicts.values())


def test_iptables_issue_capture_reports_the_missing_rules_by_container() -> None:
    verdicts = _by_port(parse_snapshot(_framed(ipt=IPT_ISSUE)))
    missing = {p: v.owner.name for p, v in verdicts.items() if v.missing and v.owner}
    assert missing == {8082: "hal0-slot-1", 8087: "hal0-slot-2"}
    assert not verdicts[3001].broken
    assert "no DNAT rule reaches hal0-slot-1 (10.88.0.70)" in verdicts[8082].reason()
    # Missing is not stale: nothing to delete.
    assert not verdicts[8082].corrupt and verdicts[8082].stale == ()


def test_iptables_stale_and_duplicate_rules_are_flagged() -> None:
    text = (
        IPT_HEALTHY
        + "\n"
        + (f"-A {_DN} -p tcp -m tcp --dport 8087 -j DNAT --to-destination 10.88.0.5:8087")
    )
    # Put the dead rule FIRST in the chain so it wins.
    lines = text.splitlines()
    dead = lines.pop()
    lines.insert(
        lines.index(
            f"-A {_DN} -p tcp -m tcp --dport 8087 -j DNAT --to-destination 10.88.0.19:8087"
        ),
        dead,
    )
    v = _by_port(parse_snapshot(_framed(ipt="\n".join(lines))))[8087]
    assert v.duplicate and v.dead_first_match and v.corrupt and not v.missing
    assert [r.target_ip for r in v.stale] == ["10.88.0.5"]


def test_nftables_missing_rule_is_flagged() -> None:
    nft = NFT_CLEAN.replace("\t\ttcp dport 8082 dnat ip to 10.88.0.70:8082 # handle 11\n", "")
    v = _by_port(parse_snapshot(_framed(nft=nft)))[8082]
    assert v.missing and v.owner and v.owner.name == "hal0-slot-1"


def test_nftables_rule_pointing_at_another_ip_is_missing_and_stale() -> None:
    nft = NFT_CLEAN.replace("10.88.0.70:8082", "10.88.0.99:8082")
    v = _by_port(parse_snapshot(_framed(nft=nft)))[8082]
    assert v.missing and v.dead_first_match
    assert [r.handle for r in v.stale] == [11]


def test_nftables_duplicate_live_rules_are_duplicate_not_missing() -> None:
    nft = NFT_CLEAN.replace(
        "# handle 10\n", "# handle 10\n\t\ttcp dport 8087 dnat ip to 10.88.0.19:8087 # handle 13\n"
    )
    v = _by_port(parse_snapshot(_framed(nft=nft)))[8087]
    assert v.duplicate and not v.missing and v.stale == ()


def test_requested_port_without_a_publisher_is_never_missing() -> None:
    snap = parse_snapshot(_framed(ipt=IPT_HEALTHY))
    v = {x.port: x for x in audit_snapshot(snap, [8099])}[8099]
    assert v.owner is None and not v.broken


def test_container_without_an_address_is_never_missing() -> None:
    snap = DnatSnapshot(
        BACKEND_IPTABLES, (), (LiveContainer("odd", frozenset(), frozenset({9000})),)
    )
    assert not audit_snapshot(snap)[0].missing


# ── readers ──────────────────────────────────────────────────────────────────


class _Seam:
    def __init__(self, text: str | None = None, exc: Exception | None = None) -> None:
        self.text, self.exc, self.reloads = text, exc, 0

    def dnat_snapshot(self, *, timeout: float | None = None) -> str:
        if self.exc:
            raise self.exc
        assert self.text is not None
        return self.text

    def network_reload(self) -> None:
        self.reloads += 1
        if self.exc:
            raise self.exc


def test_read_snapshot_uses_the_seam_verb() -> None:
    snap = read_snapshot(seam=_Seam(_framed(ipt=IPT_ISSUE)))
    assert snap.backend == BACKEND_IPTABLES


def test_read_snapshot_falls_back_to_in_process_reads_without_the_seam() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        out = {
            "nft": (1, ""),
            "iptables": (0, "-P PREROUTING ACCEPT\n" + IPT_ISSUE),
        }.get(argv[0])
        if out is None:
            out = (0, "a\nb\n") if argv[1] == "ps" else (0, CONTAINERS)
        return subprocess.CompletedProcess(argv, out[0], out[1], "")

    snap = read_snapshot(runner=run, seam=_Seam(exc=Hal0SeamMissing("no wrapper")))
    assert snap.backend == BACKEND_IPTABLES
    assert {v.port for v in audit_snapshot(snap) if v.missing} == {8082, 8087}
    assert ["iptables", "-t", "nat", "-S"] in calls
    assert calls[-1][:3] == ["podman", "inspect", "--format"]
    assert calls[-1][3] == netavark.CONTAINER_INSPECT_FORMAT


def test_collect_snapshot_text_keeps_only_netavark_lines() -> None:
    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "iptables":
            return subprocess.CompletedProcess(
                argv, 0, "-A ts-postrouting -j MASQUERADE\n" + IPT_ISSUE, ""
            )
        if argv[0] == "podman":
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "")

    text = collect_snapshot_text(runner=run)
    assert "ts-postrouting" not in text
    assert "@@nft absent" in text and "@@containers ok" in text


def test_a_failing_seam_is_unavailable() -> None:
    err = subprocess.CalledProcessError(
        64, ["hal0-systemctl"], stderr="dnat-snapshot: podman ps failed"
    )
    with pytest.raises(NetavarkUnavailable, match="podman ps failed"):
        read_snapshot(seam=_Seam(exc=err))


# ── the rate-limited automatic reload ────────────────────────────────────────


def test_reload_gate_allows_one_reload_per_window() -> None:
    now = [1000.0]
    gate = ReloadGate(min_interval_s=600, clock=lambda: now[0])
    assert gate.try_acquire()
    now[0] += 599
    assert not gate.try_acquire()
    now[0] += 2
    assert gate.try_acquire()


def test_auto_reload_runs_once_then_is_rate_limited() -> None:
    gate = ReloadGate(min_interval_s=600, clock=lambda: 0.0)
    seam = _Seam()
    first = auto_reload(trigger="t", gate=gate, seam=seam)
    second = auto_reload(trigger="t", gate=gate, seam=seam)
    assert first.attempted and first.ok
    assert not second.attempted and second.detail == "rate-limited"
    assert seam.reloads == 1


def test_auto_reload_failure_is_a_result_not_an_exception() -> None:
    gate = ReloadGate(min_interval_s=600, clock=lambda: 0.0)
    err = subprocess.CalledProcessError(125, ["podman"], stderr="netns gone")
    result = auto_reload(trigger="t", gate=gate, seam=_Seam(exc=err))
    assert result.attempted and not result.ok and "netns gone" in result.detail
