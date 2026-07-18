"""Central port-claim registry — one authority for who owns which port.

Motivation (2026-07-11): slot auto-assign scanned only ``/etc/hal0/slots``
TOMLs, but ports are claimed in more places — runtime slot snapshots can
differ from the TOML (the FLM trio maps children to virtual ports:
``flm-stt``'s TOML said 8088 while its runtime row claimed 8089), the API
itself owns one, and arbitrary processes may already be listening. Result:
``slot_create`` handed out 8089 twice.

Design: there is deliberately NO stored allocation table — stored tables
drift the moment something is deleted out-of-band. Claims are recomputed
from live truth on every question:

  - ``slot-config``   — every slot TOML's ``port`` / ``[server] port``,
                        including disabled slots (config still owns the port)
  - ``slot-runtime``  — the slot manager's live snapshots (catches virtual
                        ports that never appear in a TOML)
  - ``reserved``      — the API's own port + operator-reserved extras
  - ``listener``      — sockets actually in LISTEN state inside the pool

Deleting a slot therefore releases its port instantly and atomically: the
claim's source is gone, so the claim is gone. A port is *free* iff no
source claims it; a *conflict* is one port with two distinct owners
(a slot matching its own runtime row / listener is not a conflict).
"""

from __future__ import annotations

import contextlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "PortClaim",
    "collect_claims",
    "conflicts",
    "next_free",
    "port_report",
]


@dataclass(frozen=True, slots=True)
class PortClaim:
    port: int
    owner: str  # "slot:ops", "api", "listener:llama-server", …
    source: str  # slot-config | slot-runtime | reserved | listener
    # Slots in one co-resident group (the FLM trio) legitimately share a
    # port — same group on the same port is NOT a conflict.
    group: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"port": self.port, "owner": self.owner, "source": self.source}
        if self.group:
            out["group"] = self.group
        return out


# ── claim collection ─────────────────────────────────────────────────────────


def _config_claims(slots_dir: Path) -> list[PortClaim]:
    claims: list[PortClaim] = []
    if not slots_dir.is_dir():
        return claims
    for f in sorted(slots_dir.glob("*.toml")):
        try:
            with f.open("rb") as fh:
                cfg = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        name = str(cfg.get("name") or f.stem)
        # device=npu slots co-reside in one FLM process and share its port
        # by design — same derivation as slot_view's coresident marker.
        group = "npu-flm-trio" if cfg.get("device") == "npu" else None
        for value in (
            cfg.get("port"),
            (cfg.get("server") or {}).get("port") if isinstance(cfg.get("server"), dict) else None,
        ):
            if isinstance(value, int) and value > 0:
                claims.append(PortClaim(value, f"slot:{name}", "slot-config", group))
    return claims


def _runtime_claims(slot_snapshots: list[dict[str, Any]] | None) -> list[PortClaim]:
    claims: list[PortClaim] = []
    for snap in slot_snapshots or []:
        port = snap.get("port")
        name = snap.get("name")
        group = snap.get("coresident_group") or None
        if isinstance(port, int) and port > 0 and name:
            claims.append(PortClaim(port, f"slot:{name}", "slot-runtime", group))
    return claims


def _listener_claims(start: int, end: int) -> list[PortClaim]:
    """Sockets in LISTEN state within the pool — the reality check.

    Best-effort: psutil may lack permission for other processes' names;
    the port itself is still reported (owner falls back to "listener").
    """
    claims: list[PortClaim] = []
    try:
        import psutil

        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            port = conn.laddr.port
            if not (start <= port <= end):
                continue
            owner = "listener"
            if conn.pid:
                with contextlib.suppress(psutil.Error):
                    owner = f"listener:{psutil.Process(conn.pid).name()}"
            claims.append(PortClaim(port, owner, "listener"))
    except Exception:  # pragma: no cover — psutil absent / restricted
        return []
    return claims


def collect_claims(
    *,
    slots_dir: Path,
    pool: tuple[int, int],
    slot_snapshots: list[dict[str, Any]] | None = None,
    reserved: dict[int, str] | None = None,
    include_listeners: bool = True,
) -> list[PortClaim]:
    """Aggregate every known claim, deduplicated on (port, owner, source)."""
    claims = _config_claims(slots_dir)
    claims += _runtime_claims(slot_snapshots)
    for port, owner in (reserved or {}).items():
        claims.append(PortClaim(int(port), owner, "reserved"))
    if include_listeners:
        claims += _listener_claims(*pool)
    seen: set[tuple[int, str, str]] = set()
    out: list[PortClaim] = []
    for c in sorted(claims, key=lambda c: (c.port, c.source, c.owner)):
        key = (c.port, c.owner, c.source)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


# ── questions asked of the registry ──────────────────────────────────────────


def _owners(claims: list[PortClaim], port: int) -> set[str]:
    """Distinct owners of ``port`` — bare listeners fold into a slot owner
    on the same port (the slot's own server socket is not a second owner)."""
    named = {c.owner for c in claims if c.port == port and c.source != "listener"}
    listeners = {c.owner for c in claims if c.port == port and c.source == "listener"}
    return named if named else listeners


def conflicts(claims: list[PortClaim]) -> list[dict[str, Any]]:
    """Ports with more than one distinct owner.

    Owners that all belong to one co-resident group (the FLM trio shares
    its container's port by design) are folded into a single owner.
    """
    out: list[dict[str, Any]] = []
    for port in sorted({c.port for c in claims}):
        owners = _owners(claims, port)
        if len(owners) > 1:
            groups = {c.owner: c.group for c in claims if c.port == port and c.group}
            distinct_groups = {groups.get(o) for o in owners}
            if len(distinct_groups) == 1 and None not in distinct_groups:
                continue  # one co-resident family sharing its port
            out.append(
                {
                    "port": port,
                    "owners": sorted(owners),
                    "claims": [c.as_dict() for c in claims if c.port == port],
                }
            )
    return out


def next_free(claims: list[PortClaim], start: int, end: int) -> int | None:
    """Lowest port in [start, end] with NO claim from any source."""
    used = {c.port for c in claims}
    for port in range(start, end + 1):
        if port not in used:
            return port
    return None


def claimed_by_other(claims: list[PortClaim], port: int, owner: str) -> set[str]:
    """Owners other than ``owner`` holding ``port`` (for create/edit checks)."""
    return {o for o in _owners(claims, port) if o != owner}


def port_report(
    *,
    slots_dir: Path,
    pool: tuple[int, int],
    slot_snapshots: list[dict[str, Any]] | None = None,
    reserved: dict[int, str] | None = None,
    authority_claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The full picture: pool, per-port claims, conflicts, next free.

    ``authority_claims`` (rework §11.2) is the fifth source: the
    ``port_claim`` rows written by :class:`hal0.ports.authority.PortAuthority`.
    The harvester keeps recomputing its four live-truth sources unchanged;
    the authority rows ride alongside so callers can compare issued claims
    against observed reality. Omitted (``None``) → the key is absent and the
    report is identical to the four-source view, so no existing consumer
    changes.
    """
    claims = collect_claims(
        slots_dir=slots_dir,
        pool=pool,
        slot_snapshots=slot_snapshots,
        reserved=reserved,
    )
    report: dict[str, Any] = {
        "pool": {"start": pool[0], "end": pool[1]},
        "claims": [c.as_dict() for c in claims],
        "conflicts": conflicts(claims),
        "next_free": next_free(claims, *pool),
    }
    if authority_claims is not None:
        report["authority_claims"] = authority_claims
    return report
