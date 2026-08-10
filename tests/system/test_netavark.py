"""Stale-DNAT detection (#1814).

Fixtures are VERBATIM captures from the box the bug was diagnosed on (CT151,
podman 5.7.0, netavark), not hand-written approximations — the whole point of
the detector is that it reads real ``nft -a list table inet netavark`` output.
"""

from __future__ import annotations

import subprocess

import pytest

from hal0.system.netavark import (
    NetavarkUnavailable,
    NetnsDurability,
    audit_ports,
    parse_container_ips,
    parse_dnat_rules,
    read_dnat_rules,
    read_live_container_ips,
    read_netns_durability,
)

# ── real capture ─────────────────────────────────────────────────────────────
#
# Port 8081 (agent, never restarted): 1 rule -> 10.88.0.80, which IS a running
# container. Clean.
# Port 8083 (embed, restarted many times): 6 rules; the live container is
# 10.88.0.117 but .92/.100/.102/.105/.109 shadow it. Black-holed.
# Ports 8086/8089: 1 rule each, targeting IPs no running container holds — the
# single-rule "first match points at a dead IP" case.
# Port 3001 (OpenWebUI): the no-``ip daddr`` shape, live target. Clean.
REAL_NFT = """\
table inet netavark { # handle 2
\tchain INPUT { # handle 1
\t\ttype filter hook input priority filter; policy accept;
\t\tip saddr 10.88.0.0/16 meta l4proto { tcp, udp } th dport 53 accept # handle 41
\t}

\tchain NETAVARK-HOSTPORT-DNAT { # handle 6
\t\ttcp dport 3001 jump nv_2f259bab_10_88_0_0_nm16_dnat # handle 46
\t\tip daddr 127.0.0.1 tcp dport 8083 jump nv_2f259bab_10_88_0_0_nm16_dnat # handle 442
\t}

\tchain nv_2f259bab_10_88_0_0_nm16_dnat { # handle 45
\t\tip saddr 10.88.0.0/16 tcp dport 3001 jump NETAVARK-HOSTPORT-SETMARK # handle 47
\t\tip saddr 127.0.0.1 tcp dport 3001 jump NETAVARK-HOSTPORT-SETMARK # handle 48
\t\ttcp dport 3001 dnat ip to 10.88.0.4:8080 # handle 49
\t\tip saddr 10.88.0.0/16 ip daddr 127.0.0.1 tcp dport 8081 jump NETAVARK-HOSTPORT-SETMARK # handle 339
\t\tip saddr 127.0.0.1 ip daddr 127.0.0.1 tcp dport 8081 jump NETAVARK-HOSTPORT-SETMARK # handle 340
\t\tip daddr 127.0.0.1 tcp dport 8081 dnat ip to 10.88.0.80:8081 # handle 341
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.92:8083 # handle 385
\t\tip daddr 127.0.0.1 tcp dport 8086 dnat ip to 10.88.0.94:8086 # handle 389
\t\tip daddr 127.0.0.1 tcp dport 8089 dnat ip to 10.88.0.98:8089 # handle 405
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.100:8083 # handle 413
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.102:8083 # handle 417
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.105:8083 # handle 429
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.109:8083 # handle 445
\t\tip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.117:8083 # handle 473
\t}
}
"""

#: The three containers that were actually running when REAL_NFT was captured.
REAL_LIVE_IPS = {"10.88.0.4", "10.88.0.80", "10.88.0.117"}


# ── parsing ──────────────────────────────────────────────────────────────────


def test_parses_only_dnat_rules_from_the_dnat_chain() -> None:
    rules = parse_dnat_rules(REAL_NFT)
    # 10 dnat rules; the SETMARK jumps and the NETAVARK-HOSTPORT-DNAT jump rules
    # (which also carry `tcp dport <p>`) must NOT be counted.
    assert len(rules) == 10
    assert {r.chain for r in rules} == {"nv_2f259bab_10_88_0_0_nm16_dnat"}
    assert all(r.handle not in {46, 47, 48, 442} for r in rules)


def test_parses_the_no_daddr_shape() -> None:
    rule = next(r for r in parse_dnat_rules(REAL_NFT) if r.dport == 3001)
    assert rule.daddr is None
    assert (rule.target_ip, rule.target_port, rule.handle) == ("10.88.0.4", 8080, 49)


def test_parses_the_loopback_shape() -> None:
    rule = next(r for r in parse_dnat_rules(REAL_NFT) if r.handle == 341)
    assert rule.daddr == "127.0.0.1"
    assert (rule.dport, rule.target_ip, rule.target_port) == (8081, "10.88.0.80", 8081)


def test_rules_keep_nftables_evaluation_order() -> None:
    handles = [r.handle for r in parse_dnat_rules(REAL_NFT) if r.dport == 8083]
    assert handles == [385, 413, 417, 429, 445, 473]


def test_empty_and_foreign_output_yield_nothing() -> None:
    assert parse_dnat_rules("") == []
    assert parse_dnat_rules("table inet filter {\n\tchain INPUT {\n\t}\n}\n") == []


def test_parse_container_ips_ignores_blanks_and_non_ips() -> None:
    assert parse_container_ips("10.88.0.4 \n\n10.88.0.80,10.88.0.117\nnot-an-ip\n") == {
        "10.88.0.4",
        "10.88.0.80",
        "10.88.0.117",
    }


# ── classification ───────────────────────────────────────────────────────────


def _verdicts(ports: list[int]) -> dict[int, object]:
    rules = parse_dnat_rules(REAL_NFT)
    return {v.port: v for v in audit_ports(ports, rules, REAL_LIVE_IPS)}


def test_clean_single_rule_port_is_not_flagged() -> None:
    v = _verdicts([8081])[8081]
    assert len(v.rules) == 1
    assert not v.duplicate and not v.dead_first_match and not v.corrupt
    assert v.stale == ()
    assert v.reason() == ""


def test_clean_no_daddr_port_is_not_flagged() -> None:
    assert not _verdicts([3001])[3001].corrupt


def test_poisoned_port_is_flagged_as_duplicate_and_dead_first_match() -> None:
    v = _verdicts([8083])[8083]
    assert len(v.rules) == 6
    assert v.duplicate and v.dead_first_match and v.corrupt
    assert v.first_match.handle == 385
    # The live container's rule (handle 473 -> .117) must survive repair.
    assert [r.handle for r in v.stale] == [385, 413, 417, 429, 445]
    assert "6 DNAT rules" in v.reason()
    assert "10.88.0.92" in v.reason()


def test_single_rule_pointing_at_a_dead_ip_is_flagged() -> None:
    v = _verdicts([8086])[8086]
    assert not v.duplicate
    assert v.dead_first_match and v.corrupt
    assert [r.handle for r in v.stale] == [389]


def test_port_with_no_rule_at_all_is_not_corruption() -> None:
    v = _verdicts([9999])[9999]
    assert v.rules == ()
    assert not v.corrupt
    assert v.stale == ()


def test_audit_covers_every_requested_port_once_sorted() -> None:
    verdicts = audit_ports([8083, 8081, 8081], parse_dnat_rules(REAL_NFT), REAL_LIVE_IPS)
    assert [v.port for v in verdicts] == [8081, 8083]


def test_a_port_whose_only_rule_is_live_but_duplicated_keeps_the_live_rule() -> None:
    """Duplicate rules both pointing at the SAME live container: still flagged
    (netavark emits one per container), but repair must delete neither — cutting
    a live rule is the outage this tool exists to prevent."""
    nft = (
        "table inet netavark { # handle 2\n"
        "\tchain nv_abc_dnat { # handle 45\n"
        "\t\tip daddr 127.0.0.1 tcp dport 8091 dnat ip to 10.88.0.7:8091 # handle 10\n"
        "\t\tip daddr 127.0.0.1 tcp dport 8091 dnat ip to 10.88.0.7:8091 # handle 11\n"
        "\t}\n"
        "}\n"
    )
    v = audit_ports([8091], parse_dnat_rules(nft), {"10.88.0.7"})[0]
    assert v.corrupt and v.duplicate and not v.dead_first_match
    assert v.stale == ()


# ── live readers (runner injected; no nft/podman needed) ─────────────────────


def _fake_runner(table: dict[str, tuple[int, str]]):
    def run(argv, **kwargs):
        key = argv[0]
        code, out = table[key]
        return subprocess.CompletedProcess(argv, code, out, "boom" if code else "")

    return run


def test_read_dnat_rules_uses_the_netavark_table() -> None:
    seen: list[list[str]] = []

    def run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, REAL_NFT, "")

    rules = read_dnat_rules(runner=run)
    assert seen == [["nft", "-a", "list", "table", "inet", "netavark"]]
    assert len(rules) == 10


def test_read_dnat_rules_raises_when_the_table_is_absent() -> None:
    with pytest.raises(NetavarkUnavailable):
        read_dnat_rules(runner=_fake_runner({"nft": (1, "")}))


def test_read_live_container_ips_short_circuits_with_no_containers() -> None:
    calls: list[str] = []

    def run(argv, **kwargs):
        calls.append(argv[1])
        return subprocess.CompletedProcess(argv, 0, "\n", "")

    assert read_live_container_ips(runner=run) == set()
    assert calls == ["ps"]  # no `podman inspect` on an empty box


def test_read_live_container_ips_parses_inspect_output() -> None:
    def run(argv, **kwargs):
        if argv[1] == "ps":
            return subprocess.CompletedProcess(argv, 0, "abc\ndef\n", "")
        return subprocess.CompletedProcess(argv, 0, "10.88.0.4 \n10.88.0.80 \n", "")

    assert read_live_container_ips(runner=run) == {"10.88.0.4", "10.88.0.80"}


# ── the leak's source: netns durability ──────────────────────────────────────


def test_netns_under_runtime_dir_without_linger_is_volatile() -> None:
    state = NetnsDurability(sandbox_keys=("/run/user/0/netns/netns-abc",), linger_enabled=False)
    assert state.volatile


def test_netns_under_runtime_dir_with_linger_is_durable() -> None:
    state = NetnsDurability(sandbox_keys=("/run/user/0/netns/netns-abc",), linger_enabled=True)
    assert not state.volatile


def test_netns_outside_runtime_dir_is_durable() -> None:
    state = NetnsDurability(sandbox_keys=("/run/netns/netns-abc",), linger_enabled=False)
    assert not state.volatile


def test_dangling_reports_keys_that_no_longer_exist(tmp_path) -> None:
    alive = tmp_path / "netns-alive"
    alive.write_text("")
    state = NetnsDurability(
        sandbox_keys=(str(alive), str(tmp_path / "netns-gone")), linger_enabled=False
    )
    assert state.dangling == (str(tmp_path / "netns-gone"),)


def test_read_netns_durability_reads_linger_marker(tmp_path) -> None:
    def run(argv, **kwargs):
        if argv[1] == "ps":
            return subprocess.CompletedProcess(argv, 0, "abc\n", "")
        return subprocess.CompletedProcess(argv, 0, "/run/user/0/netns/netns-abc\n", "")

    off = read_netns_durability(runner=run, linger_dir=str(tmp_path))
    assert off.sandbox_keys == ("/run/user/0/netns/netns-abc",)
    assert off.volatile

    (tmp_path / "root").write_text("")
    on = read_netns_durability(runner=run, linger_dir=str(tmp_path))
    assert on.linger_enabled and not on.volatile
