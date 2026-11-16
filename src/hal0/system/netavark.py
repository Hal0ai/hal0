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

import json
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

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

log = logging.getLogger(__name__)

#: The two firewall drivers netavark can publish ports through. netavark >= 1.13
#: on most distros writes the ``inet netavark`` nftables table; netavark 1.4
#: (Ubuntu 24.04's podman 4.9.3) writes iptables-nft ``nat`` chains instead
#: (#3237, #3724).
BACKEND_NFTABLES = "nftables"
BACKEND_IPTABLES = "iptables"


@dataclass(frozen=True, slots=True)
class DnatRule:
    """One netavark DNAT rule, from either firewall backend.

    ``handle`` is the nftables rule handle — the only thing the ``prune-dnat``
    seam verb can delete by. iptables has no handles, so an iptables rule
    carries ``handle=None`` and its ``-A`` line in ``spec`` instead (shown to
    the operator as the manual ``iptables -t nat -D`` line; never executed).
    """

    chain: str
    dport: int
    target_ip: str
    target_port: int
    handle: int | None
    daddr: str | None = None
    spec: str | None = None

    def render(self) -> str:
        """Human-readable one-liner for doctor output and repair receipts."""
        guard = f"{self.daddr} " if self.daddr else ""
        where = f"(handle {self.handle})" if self.handle is not None else f"({self.chain})"
        return f"{guard}dport {self.dport} -> {self.target_ip}:{self.target_port} {where}"


@dataclass(frozen=True, slots=True)
class PortVerdict:
    """Corruption verdict for one published port.

    ``rules`` is in nftables evaluation order, so ``rules[0]`` is the rule that
    actually wins for traffic on this port.
    """

    port: int
    rules: tuple[DnatRule, ...]
    live_ips: frozenset[str]
    #: The running container that publishes this host port, when one does.
    owner: LiveContainer | None = None

    @property
    def first_match(self) -> DnatRule | None:
        return self.rules[0] if self.rules else None

    @property
    def missing(self) -> bool:
        """A running container publishes this port, but no DNAT rule reaches it.

        The #3724 black hole: the container is healthy and conmon still holds
        the host port, but the rule netavark installed for it is gone, so
        connections sit in the accept queue and time out. Unambiguous — the
        container's publication and its address both come from podman, and the
        rule table is read in full. ``podman network reload --all`` rebuilds
        every running container's rules without restarting anything.
        """
        owner = self.owner
        if owner is None or not owner.ips:
            return False
        return not any(r.target_ip in owner.ips for r in self.rules)

    @property
    def broken(self) -> bool:
        """Any finding at all: stale (``corrupt``) or ``missing``."""
        return self.corrupt or self.missing

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
        if not self.broken:
            return ""
        parts: list[str] = []
        if self.missing:
            assert self.owner is not None
            ips = ", ".join(sorted(self.owner.ips))
            parts.append(f"no DNAT rule reaches {self.owner.name} ({ips})")
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


# ── the iptables-nft backend (netavark 1.4, #3237 / #3724) ────────────────────
#
# netavark 1.4 publishes a port with TWO rules in the iptables ``nat`` table::
#
#   -A NETAVARK-HOSTPORT-DNAT -p tcp -m tcp --dport 8087 -m comment ... -j NETAVARK-DN-1D87...
#   -A NETAVARK-DN-1D87... -p tcp -m tcp --dport 8087 -j DNAT --to-destination 10.88.0.19:8087
#
# The first is the per-port jump out of the hostport chain, the second the
# per-network DNAT. Traffic reaches the DNAT only through the jump, and in the
# #3724 capture it was the JUMPS that had vanished for every container except
# the two most recently recreated. So on this backend a DNAT rule counts only
# when a hostport jump for its port leads into its chain; a DN-chain rule with
# no jump in front of it routes nothing.

#: ``-A NETAVARK-HOSTPORT-DNAT … -j NETAVARK-DN-<hash>``: the jump into a
#: network's DNAT chain. Its port guard is ``--dport N`` or ``--dports a,b:c``.
_IPT_JUMP_RE = re.compile(
    r"^-A\s+NETAVARK-HOSTPORT-DNAT\s+(?P<body>.*?)\s*-j\s+(?P<chain>NETAVARK-DN-\S+)"
)
_IPT_PORTS_RE = re.compile(r"--dports?\s+(?P<ports>[0-9,:]+)")
#: ``-A NETAVARK-DN-<hash> … --dport N … -j DNAT --to-destination IP:PORT``.
_IPT_DNAT_RE = re.compile(
    r"^-A\s+(?P<chain>NETAVARK-DN-\S+)\s+(?P<body>.*?)\s*-j\s+DNAT\s+"
    r"--to-destination\s+(?P<target_ip>\d{1,3}(?:\.\d{1,3}){3}):(?P<target_port>\d{1,5})\s*$"
)
_IPT_DPORT_RE = re.compile(r"--dport\s+(?P<dport>\d{1,5})\b")
_IPT_DADDR_RE = re.compile(r"(?:^|\s)-d\s+(?P<daddr>\d{1,3}(?:\.\d{1,3}){3})(?:/32)?\b")

#: Marks the iptables backend as present: netavark creates this chain the first
#: time it publishes a port and never removes it.
IPTABLES_HOSTPORT_CHAIN = "NETAVARK-HOSTPORT-DNAT"


def _expand_ports(spec: str) -> frozenset[int]:
    ports: set[int] = set()
    for part in spec.split(","):
        lo, _, hi = part.partition(":")
        if not lo.isdigit():
            continue
        if hi.isdigit():
            ports.update(range(int(lo), int(hi) + 1))
        else:
            ports.add(int(lo))
    return frozenset(ports)


def parse_iptables_dnat_rules(iptables_output: str) -> list[DnatRule]:
    """Parse ``iptables -t nat -S`` into the DNAT rules traffic can reach.

    Returned in evaluation order: hostport jumps in chain order, then the DNAT
    rules inside each jumped-to chain in their own order. A jump with no port
    guard reaches every rule in its chain.
    """
    jumps: list[tuple[str, frozenset[int] | None]] = []
    by_chain: dict[str, list[DnatRule]] = {}
    for raw in iptables_output.splitlines():
        line = raw.strip()
        jump = _IPT_JUMP_RE.match(line)
        if jump:
            guard = _IPT_PORTS_RE.search(jump.group("body"))
            jumps.append(
                (jump.group("chain"), _expand_ports(guard.group("ports")) if guard else None)
            )
            continue
        m = _IPT_DNAT_RE.match(line)
        if m is None:
            continue
        dport = _IPT_DPORT_RE.search(m.group("body"))
        if dport is None:
            continue
        daddr = _IPT_DADDR_RE.search(" " + m.group("body"))
        by_chain.setdefault(m.group("chain"), []).append(
            DnatRule(
                chain=m.group("chain"),
                dport=int(dport.group("dport")),
                target_ip=m.group("target_ip"),
                target_port=int(m.group("target_port")),
                handle=None,
                daddr=daddr.group("daddr") if daddr else None,
                spec=line,
            )
        )
    reachable: list[DnatRule] = []
    seen: set[int] = set()
    for chain, ports in jumps:
        for rule in by_chain.get(chain, ()):
            if (ports is None or rule.dport in ports) and id(rule) not in seen:
                seen.add(id(rule))
                reachable.append(rule)
    return reachable


# ── running containers and their published ports ─────────────────────────────


@dataclass(frozen=True, slots=True)
class LiveContainer:
    """A running container: its name, addresses and published host ports."""

    name: str
    ips: frozenset[str]
    host_ports: frozenset[int] = field(default_factory=frozenset)


#: The literal ``podman inspect`` format both the root-side wrapper verb and the
#: direct (root / dev) reader use, so one parser serves both. One line per
#: container: ``name|ip ip |{"8080/tcp":[{"HostIp":"","HostPort":"3001"}]}``.
#: Mirrored byte-for-byte in installer/wrappers/hal0-systemctl (dnat-snapshot).
CONTAINER_INSPECT_FORMAT = (
    "{{.Name}}|{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"
    "|{{json .NetworkSettings.Ports}}"
)

_IPV4_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")


def parse_containers(inspect_output: str) -> list[LiveContainer]:
    """Parse :data:`CONTAINER_INSPECT_FORMAT` lines. Malformed lines are skipped."""
    containers: list[LiveContainer] = []
    for line in inspect_output.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) != 3 or not parts[0]:
            continue
        name, ips_field, ports_field = parts
        ips = frozenset(t for t in ips_field.split() if _IPV4_RE.fullmatch(t))
        host_ports: set[int] = set()
        try:
            ports = json.loads(ports_field) if ports_field.strip() else {}
        except ValueError:
            ports = {}
        if isinstance(ports, dict):
            for bindings in ports.values():
                for binding in bindings or ():
                    hp = str((binding or {}).get("HostPort") or "")
                    if hp.isdigit() and 1 <= int(hp) <= 65535:
                        host_ports.add(int(hp))
        containers.append(
            LiveContainer(name=name.lstrip("/"), ips=ips, host_ports=frozenset(host_ports))
        )
    return containers


# ── the snapshot: one privileged read, both backends ──────────────────────────
#
# The audit needs three root-only reads: the nft table, the iptables nat table,
# and root's running containers. hal0-api runs as the unprivileged ``hal0``
# account, so on a provisioned box they come from ONE read-only seam verb,
# ``hal0-systemctl dnat-snapshot`` (no arguments), which prints them as framed
# sections. A root process (``hal0 doctor`` run by an admin) calls the same
# wrapper directly, and a box without the wrapper (dev, CI) runs the same three
# commands in-process and frames them identically, so one parser serves all.

#: Section header the wrapper and :func:`collect_snapshot_text` emit:
#: ``@@<nft|iptables|containers> <ok|absent>``.
_SECTION_RE = re.compile(r"^@@(?P<name>nft|iptables|containers)\s+(?P<status>ok|absent)\s*$")


@dataclass(frozen=True, slots=True)
class DnatSnapshot:
    """The live netavark DNAT rules plus the running containers they serve."""

    backend: str
    rules: tuple[DnatRule, ...]
    containers: tuple[LiveContainer, ...]

    @property
    def live_ips(self) -> frozenset[str]:
        return frozenset(ip for c in self.containers for ip in c.ips)


def _split_sections(text: str) -> dict[str, str | None]:
    sections: dict[str, str | None] = {}
    current: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            if current is not None:
                sections[current] = "\n".join(lines)
            current, lines = None, []
            if m.group("status") == "absent":
                sections[m.group("name")] = None
            else:
                current = m.group("name")
            continue
        if current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines)
    return sections


def parse_snapshot(text: str) -> DnatSnapshot:
    """Parse framed snapshot text and pick the backend netavark is using.

    The nftables table wins when it exists and holds DNAT rules, or when no
    iptables hostport chain exists either; otherwise the iptables chain decides.
    Raises :class:`NetavarkUnavailable` when there is neither, or when the
    container listing is missing (without it nothing can be judged).
    """
    sections = _split_sections(text)
    listing = sections.get("containers")
    if listing is None:
        raise NetavarkUnavailable("running-container listing unavailable")
    containers = tuple(parse_containers(listing))
    nft = sections.get("nft")
    ipt = sections.get("iptables")
    nft_rules = parse_dnat_rules(nft) if nft is not None else []
    ipt_present = ipt is not None and f"-N {IPTABLES_HOSTPORT_CHAIN}" in ipt
    if nft is not None and (nft_rules or not ipt_present):
        return DnatSnapshot(BACKEND_NFTABLES, tuple(nft_rules), containers)
    if ipt is not None and ipt_present:
        return DnatSnapshot(BACKEND_IPTABLES, tuple(parse_iptables_dnat_rules(ipt)), containers)
    raise NetavarkUnavailable(
        f"no `{NETAVARK_TABLE}` nftables table and no {IPTABLES_HOSTPORT_CHAIN} iptables chain"
    )


def _try(runner: Runner, argv: Sequence[str], timeout: float) -> str | None:
    try:
        proc = runner(  # nosec B603 — fixed argv, no shell
            list(argv), capture_output=True, text=True, check=False, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (proc.stdout or "") if proc.returncode == 0 else None


_IPT_KEEP_RE = re.compile(r"^-[AN]\s+NETAVARK-(HOSTPORT-DNAT|DN-)")


def collect_snapshot_text(*, runner: Runner = subprocess.run, timeout: float = 15.0) -> str:
    """The in-process equivalent of ``hal0-systemctl dnat-snapshot``.

    Used where the wrapper is not installed (dev, CI) and the process can read
    the firewall itself. Same framing, same commands, so :func:`parse_snapshot`
    cannot tell the two apart.
    """
    out: list[str] = []
    nft = _try(runner, ["nft", "-a", "list", "table", *NETAVARK_TABLE.split()], timeout)
    out += ["@@nft absent"] if nft is None else ["@@nft ok", nft]
    ipt = _try(runner, ["iptables", "-t", "nat", "-S"], timeout)
    if ipt is None:
        out.append("@@iptables absent")
    else:
        out += ["@@iptables ok", *(ln for ln in ipt.splitlines() if _IPT_KEEP_RE.match(ln))]
    ids = _try(runner, ["podman", "ps", "-q"], timeout)
    if ids is None:
        out.append("@@containers absent")
    elif not ids.split():
        out.append("@@containers ok")
    else:
        inspected = _try(
            runner,
            ["podman", "inspect", "--format", CONTAINER_INSPECT_FORMAT, *ids.split()],
            timeout,
        )
        out += ["@@containers absent"] if inspected is None else ["@@containers ok", inspected]
    return "\n".join(out) + "\n"


def read_snapshot(
    *,
    runner: Runner = subprocess.run,
    seam: Any | None = None,
    timeout: float = 20.0,
) -> DnatSnapshot:
    """Read the live DNAT snapshot. Raises :class:`NetavarkUnavailable`.

    Routes through the ``dnat-snapshot`` seam verb when the wrapper is reachable
    (the ``hal0`` service account via sudo, or root directly); falls back to
    in-process reads otherwise.
    """
    from hal0.system.seam import Hal0SeamMissing, SystemCtlSeam

    the_seam = seam if seam is not None else SystemCtlSeam()
    try:
        text = the_seam.dnat_snapshot(timeout=timeout)
    except Hal0SeamMissing:
        text = collect_snapshot_text(runner=runner, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        raise NetavarkUnavailable(
            f"dnat-snapshot failed ({exc.returncode}): {(exc.stderr or '').strip()}"
        ) from exc
    except Exception as exc:  # SeamTimeout, OSError, … — never a finding
        raise NetavarkUnavailable(f"dnat-snapshot failed: {exc}") from exc
    return parse_snapshot(text)


def audit_snapshot(snapshot: DnatSnapshot, ports: Iterable[int] = ()) -> list[PortVerdict]:
    """Classify every port a running container publishes, plus ``ports``. Pure.

    Covers the three signals: ``missing`` (a publisher with no rule reaching
    it), and the two stale ones, ``duplicate`` and ``dead_first_match``.
    """
    owners: dict[int, LiveContainer] = {}
    for container in snapshot.containers:
        for port in container.host_ports:
            owners.setdefault(port, container)
    live = snapshot.live_ips
    by_port: dict[int, list[DnatRule]] = {}
    for rule in snapshot.rules:
        by_port.setdefault(rule.dport, []).append(rule)
    return [
        PortVerdict(port=p, rules=tuple(by_port.get(p, ())), live_ips=live, owner=owners.get(p))
        for p in sorted(set(ports) | set(owners))
    ]


# ── the missing-rule repair: ``podman network reload --all`` ──────────────────
#
# A missing rule has exactly one non-disruptive repair: ``podman network reload
# --all`` re-runs netavark setup for every running container and so rebuilds
# every rule, without restarting anything. It is box-wide, so unattended
# callers (the slot watchdog, the post-recreate recheck) share one rate limit:
# at most one automatic reload per :data:`RELOAD_MIN_INTERVAL_S`, box-wide. An
# operator's ``hal0 doctor ports --fix`` is not rate-limited.
#
# Stale rules (``duplicate`` / ``dead_first_match``) are NEVER auto-repaired:
# on nftables a reload does not remove a leaked rule (it appends another), and
# deleting one is the operator's ``--fix`` decision (#1820).

#: Minimum spacing between two AUTOMATIC reloads, process-wide.
RELOAD_MIN_INTERVAL_S: float = 600.0


class ReloadGate:
    """Rate limit for unattended ``network reload`` calls. Thread-safe."""

    def __init__(
        self,
        min_interval_s: float = RELOAD_MIN_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._last: float | None = None
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        """Claim the next reload slot; ``False`` while the window is closed."""
        with self._lock:
            now = self._clock()
            if self._last is not None and now - self._last < self._min_interval_s:
                return False
            self._last = now
            return True

    def reset(self) -> None:
        with self._lock:
            self._last = None


#: The one box-wide gate every automatic reload goes through.
RELOAD_GATE = ReloadGate()


@dataclass(frozen=True, slots=True)
class ReloadResult:
    """What an automatic reload attempt did."""

    attempted: bool
    ok: bool
    detail: str = ""


def auto_reload(
    *, trigger: str, gate: ReloadGate | None = None, seam: Any | None = None
) -> ReloadResult:
    """Run ONE rate-limited ``podman network reload --all`` and log it.

    For unattended callers only. Never raises: a failed or refused reload is a
    :class:`ReloadResult`, and the caller decides what the slot looks like.
    """
    from hal0.system.seam import SystemCtlSeam

    the_gate = gate if gate is not None else RELOAD_GATE
    if not the_gate.try_acquire():
        log.info("netavark.network_reload_rate_limited", extra={"trigger": trigger})
        return ReloadResult(attempted=False, ok=False, detail="rate-limited")
    the_seam = seam if seam is not None else SystemCtlSeam()
    log.warning("netavark.network_reload", extra={"trigger": trigger})
    try:
        the_seam.network_reload()
    except Exception as exc:
        detail = str(getattr(exc, "stderr", None) or exc).strip()
        log.warning("netavark.network_reload_failed", extra={"trigger": trigger, "error": detail})
        return ReloadResult(attempted=True, ok=False, detail=detail)
    log.warning("netavark.network_reload_done", extra={"trigger": trigger})
    return ReloadResult(attempted=True, ok=True)


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
    "BACKEND_IPTABLES",
    "BACKEND_NFTABLES",
    "CONTAINER_INSPECT_FORMAT",
    "DNAT_CHAIN_RE",
    "LINGER_DIR",
    "NETAVARK_TABLE",
    "RELOAD_GATE",
    "RELOAD_MIN_INTERVAL_S",
    "DnatRule",
    "DnatSnapshot",
    "LiveContainer",
    "NetavarkUnavailable",
    "NetnsDurability",
    "PortVerdict",
    "ReloadGate",
    "ReloadResult",
    "audit_ports",
    "audit_snapshot",
    "auto_reload",
    "collect_snapshot_text",
    "parse_container_ips",
    "parse_containers",
    "parse_dnat_rules",
    "parse_iptables_dnat_rules",
    "parse_snapshot",
    "read_dnat_rules",
    "read_live_container_ips",
    "read_netns_durability",
    "read_snapshot",
]
