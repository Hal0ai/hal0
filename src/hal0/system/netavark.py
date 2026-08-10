"""Netavark DNAT-table audit + repair — the port black-hole detector (#1814).

Why this exists
---------------
Slot ports on a box are published with ``PublishPort=127.0.0.1:<p>:<p>``, which
netavark implements as one nftables DNAT rule per container, in the
``inet netavark`` table's ``nv_<netid>_dnat`` chain::

    ip daddr 127.0.0.1 tcp dport 8083 dnat ip to 10.88.0.117:8083 # handle 473

When a container goes away **without netavark's teardown running** — the podman
``cleaning up container …: netavark: open container netns: … No such file or
directory`` / ``status=125`` failure visible in a slot unit's journal — that
rule is left behind pointing at a container IP that no longer exists.

nftables is **first-match**. A leaked rule therefore permanently shadows the
correct rule for every *subsequent* container on that port: traffic is DNAT'd
into a black hole. It survives ``podman rm -f``, ``systemctl reset-failed`` and
a fresh ``hal0 slot load``; ``podman network reload --all`` does not repair it
(it fails with the same netns error and appends yet another rule). Deleting the
stale handle restores the port instantly.

The detector below is deliberately heuristic-free — both signals it reports are
unambiguous corruption:

``duplicate``
    more than one DNAT rule for the same published port. netavark emits exactly
    one per live container, so ``n > 1`` means ``n - 1`` leaks, whatever the
    targets look like.

``dead``
    the *first-match* rule for a port targets an IP that no running container
    holds. Nothing can reach it, and it shadows anything behind it.

Everything here is read-only and cheap (one ``nft`` list + one ``podman ps``
pass). Repair is a separate, explicitly-invoked path — see
:meth:`hal0.system.seam.SystemCtlSeam.prune_dnat`, which routes the privileged
delete through the ``hal0-systemctl prune-dnat`` seam verb rather than shelling
``nft`` out of the API/CLI.

Root cause of the leak itself (fixed separately in the same PR): podman on a
hal0 box keeps container network namespaces under ``/run/user/0/netns``. That
tmpfs is ``user-runtime-dir@0.service``'s, and logind unmounts it when root's
last login session ends — so a single root SSH login/logout cycle strands the
netns of every running container, and the *next* stop leaks. Pinning it up with
``loginctl enable-linger root`` (installer) is the source fix; this module is
the detector and the repair for boxes that already accumulated leaks.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

#: The nft table netavark owns. Fixed by netavark itself, not configurable.
NETAVARK_TABLE = "inet netavark"

#: Per-network DNAT chain, e.g. ``nv_2f259bab_10_88_0_0_nm16_dnat``. The chains
#: this module will ever read, and the ONLY chains the seam's repair verb will
#: ever delete from.
DNAT_CHAIN_RE = re.compile(r"^nv_[A-Za-z0-9_]+_dnat$")

_CHAIN_HEADER_RE = re.compile(r"^\s*chain\s+(?P<name>\S+)\s*\{")

#: One DNAT rule line inside a ``nv_*_dnat`` chain. Two shapes occur in the
#: wild: with an ``ip daddr`` guard (the loopback-published slot ports) and
#: without (a ``0.0.0.0``-published port such as OpenWebUI's 3001). Both are
#: matched; anything else in the chain (the ``NETAVARK-HOSTPORT-SETMARK`` jump
#: rules that accompany every publish) is not a DNAT rule and is ignored.
_DNAT_RULE_RE = re.compile(
    r"^\s*(?:ip\s+daddr\s+(?P<daddr>\S+)\s+)?"
    r"(?:tcp|udp)\s+dport\s+(?P<dport>\d{1,5})\s+"
    r"dnat\s+ip\s+to\s+(?P<target_ip>\d{1,3}(?:\.\d{1,3}){3}):(?P<target_port>\d{1,5})"
    r"\s*#\s*handle\s+(?P<handle>\d+)\s*$"
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class DnatRule:
    """One netavark DNAT rule, as parsed from ``nft -a list table``."""

    chain: str
    dport: int
    target_ip: str
    target_port: int
    handle: int
    daddr: str | None = None

    def render(self) -> str:
        """Human-readable one-liner for doctor output and repair receipts."""
        guard = f"{self.daddr} " if self.daddr else ""
        return (
            f"{guard}dport {self.dport} -> {self.target_ip}:{self.target_port} "
            f"(handle {self.handle})"
        )


@dataclass(frozen=True, slots=True)
class PortVerdict:
    """Corruption verdict for one published port.

    ``rules`` is in nftables evaluation order, so ``rules[0]`` is the rule that
    actually wins for traffic on this port.
    """

    port: int
    rules: tuple[DnatRule, ...]
    live_ips: frozenset[str]

    @property
    def first_match(self) -> DnatRule | None:
        return self.rules[0] if self.rules else None

    @property
    def duplicate(self) -> bool:
        """More than one DNAT rule for this port — ``n - 1`` of them are leaks."""
        return len(self.rules) > 1

    @property
    def dead_first_match(self) -> bool:
        """The winning rule targets an IP no running container holds."""
        first = self.first_match
        return first is not None and first.target_ip not in self.live_ips

    @property
    def corrupt(self) -> bool:
        return self.duplicate or self.dead_first_match

    @property
    def stale(self) -> tuple[DnatRule, ...]:
        """Rules a repair should delete: every rule whose target is not live.

        Live-targeted rules are never touched, even when duplicated — deleting
        the rule a working container is reachable through would *cause* the
        outage this exists to fix. On a port whose live rule is shadowed, that
        is exactly right: dropping the dead shadows promotes the live rule to
        first match.
        """
        return tuple(r for r in self.rules if r.target_ip not in self.live_ips)

    def reason(self) -> str:
        """One-line explanation, or ``""`` when the port is clean."""
        if not self.corrupt:
            return ""
        parts: list[str] = []
        if self.duplicate:
            parts.append(f"{len(self.rules)} DNAT rules (netavark emits exactly 1 per container)")
        if self.dead_first_match:
            first = self.first_match
            assert first is not None
            parts.append(f"first match targets {first.target_ip}, which no running container holds")
        return "; ".join(parts)


def parse_dnat_rules(nft_output: str) -> list[DnatRule]:
    """Parse ``nft -a list table inet netavark`` output into DNAT rules.

    Only lines inside a ``nv_*_dnat`` chain are considered, and only lines that
    are genuinely DNAT rules. Rules are returned in file order, which is
    nftables evaluation order within a chain.
    """
    rules: list[DnatRule] = []
    chain: str | None = None
    for line in nft_output.splitlines():
        header = _CHAIN_HEADER_RE.match(line)
        if header:
            name = header.group("name")
            chain = name if DNAT_CHAIN_RE.match(name) else None
            continue
        if line.strip() == "}":
            chain = None
            continue
        if chain is None:
            continue
        m = _DNAT_RULE_RE.match(line)
        if m is None:
            continue
        rules.append(
            DnatRule(
                chain=chain,
                dport=int(m.group("dport")),
                target_ip=m.group("target_ip"),
                target_port=int(m.group("target_port")),
                handle=int(m.group("handle")),
                daddr=m.group("daddr"),
            )
        )
    return rules


def parse_container_ips(podman_output: str) -> set[str]:
    """Collect IPv4 addresses out of a whitespace/newline-separated dump.

    Fed by ``podman ps -q | xargs podman inspect --format '…IPAddress…'``; a
    container with several networks contributes several addresses, and empty
    fields (host-network containers) simply contribute none.
    """
    return {
        tok
        for tok in podman_output.replace(",", " ").split()
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", tok)
    }


def _run(runner: Runner, argv: Sequence[str], *, timeout: float) -> str:
    proc = runner(  # nosec B603 — fixed argv, no shell
        list(argv), capture_output=True, text=True, check=False, timeout=timeout
    )
    if proc.returncode != 0:
        raise NetavarkUnavailable(
            f"{argv[0]} failed ({proc.returncode}): {(proc.stderr or '').strip()}"
        )
    return proc.stdout or ""


class NetavarkUnavailable(RuntimeError):
    """``nft`` / ``podman`` missing, unreadable, or the netavark table absent.

    Not a finding — a box with no netavark table (host networking only, or no
    container runtime) has nothing to audit. Callers report this as "skipped",
    never as corruption.
    """


def read_dnat_rules(*, runner: Runner = subprocess.run, timeout: float = 10.0) -> list[DnatRule]:
    """Read the live netavark DNAT rules. Raises :class:`NetavarkUnavailable`."""
    out = _run(runner, ["nft", "-a", "list", "table", *NETAVARK_TABLE.split()], timeout=timeout)
    return parse_dnat_rules(out)


def read_live_container_ips(*, runner: Runner = subprocess.run, timeout: float = 15.0) -> set[str]:
    """IPv4 addresses of every **running** container.

    ``podman ps`` (no ``-a``) is the definition of "live" that matters here: a
    created-but-not-running container has no netns and no DNAT rule of its own,
    so its old address must count as dead.
    """
    ids = _run(runner, ["podman", "ps", "-q"], timeout=timeout).split()
    if not ids:
        return set()
    fmt = "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"
    out = _run(runner, ["podman", "inspect", "--format", fmt, *ids], timeout=timeout)
    return parse_container_ips(out)


def audit_ports(
    ports: Iterable[int],
    rules: Sequence[DnatRule],
    live_ips: Iterable[str],
) -> list[PortVerdict]:
    """Classify each published port against the live rule table. Pure."""
    live = frozenset(live_ips)
    by_port: dict[int, list[DnatRule]] = {}
    for rule in rules:
        by_port.setdefault(rule.dport, []).append(rule)
    return [
        PortVerdict(port=port, rules=tuple(by_port.get(port, ())), live_ips=live)
        for port in sorted(set(ports))
    ]


# ── the leak's source: a netns directory logind can unmount (#1814) ───────────
#
# podman on a hal0 box stores container network namespaces as bind mounts under
# ``/run/user/0/netns``. That tmpfs is ``user-runtime-dir@0.service``'s, and
# logind unmounts it when root's LAST login session ends. So one root SSH
# login/logout cycle strands the netns of every container that is running at the
# time, and the next stop/restart of any of them fails teardown with
# ``netavark: open container netns: … No such file or directory`` — leaking the
# DNAT rule this module hunts.
#
# Measured on a live box (podman 5.7.0): a container started in one SSH session
# has a valid SandboxKey; after that session closes and a new one opens, the
# same SandboxKey is dangling. Removing a dangling-netns container leaks its
# rule; removing one whose netns survived does not. ``loginctl enable-linger
# root`` pins ``/run/user/0`` up independently of sessions and the leak stops.

_NETNS_UNDER_RUNTIME_DIR_RE = re.compile(r"^/run/user/\d+/")

#: Where systemd records lingering users (``loginctl enable-linger <user>``).
LINGER_DIR = "/var/lib/systemd/linger"


@dataclass(frozen=True, slots=True)
class NetnsDurability:
    """Whether container netns paths can survive a root login/logout cycle."""

    sandbox_keys: tuple[str, ...]
    linger_enabled: bool

    @property
    def volatile(self) -> bool:
        """True when netns live in a runtime dir logind may unmount.

        Only a problem while linger is off — with linger on, the runtime dir is
        pinned for the life of the boot, which is exactly as durable as
        ``/run/netns``.
        """
        return not self.linger_enabled and any(
            _NETNS_UNDER_RUNTIME_DIR_RE.match(k) for k in self.sandbox_keys
        )

    @property
    def dangling(self) -> tuple[str, ...]:
        """Sandbox keys that have ALREADY been unmounted out from under a
        running container — every one of these will leak a DNAT rule on stop."""
        return tuple(k for k in self.sandbox_keys if k and not os.path.exists(k))


def read_netns_durability(
    *,
    runner: Runner = subprocess.run,
    timeout: float = 15.0,
    linger_dir: str = LINGER_DIR,
) -> NetnsDurability:
    """Probe the netns storage location + root's linger state."""
    ids = _run(runner, ["podman", "ps", "-q"], timeout=timeout).split()
    keys: tuple[str, ...] = ()
    if ids:
        out = _run(
            runner,
            ["podman", "inspect", "--format", "{{.NetworkSettings.SandboxKey}}", *ids],
            timeout=timeout,
        )
        keys = tuple(line.strip() for line in out.splitlines() if line.strip())
    return NetnsDurability(
        sandbox_keys=keys,
        linger_enabled=os.path.exists(os.path.join(linger_dir, "root")),
    )


__all__ = [
    "DNAT_CHAIN_RE",
    "LINGER_DIR",
    "NETAVARK_TABLE",
    "DnatRule",
    "NetavarkUnavailable",
    "NetnsDurability",
    "PortVerdict",
    "audit_ports",
    "parse_container_ips",
    "parse_dnat_rules",
    "read_dnat_rules",
    "read_live_container_ips",
    "read_netns_durability",
]
