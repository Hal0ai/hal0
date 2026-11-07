"""The live-listener scan — a port somebody else is bound on is never handed out.

``hal0.ports._listener_claims`` enumerates the host's LISTEN sockets inside
the port pool and reports them as claims, so ``PortAuthority`` refuses to
allocate a port that is genuinely bound. The suite-wide fixture in
``tests/conftest.py`` blinds that scan for the whole tree (#3465) so unit
tests do not depend on whatever the dev/CI box happens to be listening on.

That fixture leaves the production behaviour unpinned: without a test that
restores the real scan, nobody would notice if the scan stopped working.

This module restores the genuine ``_listener_claims`` on the ``hal0.ports``
module object for the duration of the test — explicitly, on the module
object, not via ``monkeypatch.undo()``. Assigning it there covers both call
paths: the module global used by ``hal0.ports.collect_claims`` and the
symbol ``hal0.ports.authority.PortAuthority._listener_ports`` re-imports at
call time. The original function object is captured at import time, before
any fixture has had a chance to patch it.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from hal0 import ports as ports_mod
from hal0.ports.authority import PortAuthority, PortPoolExhausted
from hal0.slots.identity import SlotIdentityStore

# Captured at import time, before the suite-wide fixture patches the module
# attribute. ``__module__`` proves it is the function defined in
# ``hal0.ports`` and not a stub installed by a fixture.
_REAL_LISTENER_CLAIMS = ports_mod._listener_claims


def _stores(tmp_path: Path, pool: tuple[int, int]):
    db = tmp_path / "hal0.db"
    ident = SlotIdentityStore(db_path=db)
    auth = PortAuthority(pool=pool, db_path=db)
    return ident, auth


def _slot(ident: SlotIdentityStore, name: str) -> int:
    return ident.create(name=name, slot_type="llm").id


def _bind_ephemeral() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock


def _can_enumerate() -> bool:
    """Whether this environment can enumerate host TCP listeners at all.

    ``_listener_claims`` swallows every exception and returns ``[]``, so a
    broken scan and an unlistable host look the same. Check the host
    enumeration directly: if it works, the scan must report the bound port.
    """
    try:
        import psutil

        psutil.net_connections(kind="tcp")
    except Exception:
        return False
    return True


def test_bound_port_is_never_granted(tmp_path: Path) -> None:
    """A port with a live LISTEN socket on it is refused, not allocated.

    The socket is bound on an ephemeral port this test picks for itself, so
    the test needs no particular host port to be free and cannot collide
    with an ambient listener.
    """
    assert _REAL_LISTENER_CLAIMS.__module__ == "hal0.ports", (
        "the captured listener scan is not hal0.ports' own function"
    )

    with _bind_ephemeral() as bound:
        host_port = bound.getsockname()[1]

        # A second, free port: bind it to learn a free ephemeral port, then
        # release it. The pool below holds both, so the scan must skip the
        # bound one and grant the free one.
        with _bind_ephemeral() as probe:
            free_port = probe.getsockname()[1]
        assert free_port != host_port

        # Restore the real scan on the module object: the suite-wide fixture
        # in tests/conftest.py has already patched it away for this tree.
        stubbed = ports_mod._listener_claims
        ports_mod._listener_claims = _REAL_LISTENER_CLAIMS
        try:
            assert ports_mod._listener_claims is _REAL_LISTENER_CLAIMS
            if not _can_enumerate():
                pytest.skip("host socket enumeration unavailable in this environment")
            seen = {
                c.port
                for c in _REAL_LISTENER_CLAIMS(min(host_port, free_port), max(host_port, free_port))
            }
            assert host_port in seen, "the real scan must see the bound port"

            ident, auth = _stores(tmp_path, pool=(host_port, host_port))
            sid = _slot(ident, "a")

            # The only port in the pool is bound, so there is nothing to grant.
            with pytest.raises(PortPoolExhausted):
                auth.acquire(sid, preferred=host_port)
            assert auth.held_by(sid) is None

            # With a wider pool the bound port is skipped in favour of a free
            # one rather than handed out.
            wide = tmp_path / "wide"
            wide.mkdir()
            ident2, auth2 = _stores(
                wide, pool=(min(host_port, free_port), max(host_port, free_port))
            )
            sid2 = _slot(ident2, "b")
            granted = auth2.acquire(sid2, preferred=host_port)
            assert granted != host_port
        finally:
            ports_mod._listener_claims = stubbed
