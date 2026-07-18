"""PortAuthority (rework §11.2) — the single writer of port ownership.

Pins: allocate from the pool, reserve by owner, reject a duplicate live
claim, honour the coresident carve-out (trio shadows share the anchor's
port), reconcile orphaned claims, release on deletion, and survive a
concurrent double-grab of the same free port (the ``uq_port_claim_live``
race). Slots are created through :class:`SlotIdentityStore` on the same DB
so the ``port_claim.slot_id`` foreign key is satisfied.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from hal0.ports.authority import PortAuthority, PortPoolExhausted
from hal0.slots.identity import SlotIdentityStore

POOL = (8081, 8099)


def _stores(tmp_path: Path, pool: tuple[int, int] = POOL):
    db = tmp_path / "hal0.db"
    ident = SlotIdentityStore(db_path=db)
    auth = PortAuthority(pool=pool, db_path=db)
    return ident, auth


def _slot(ident: SlotIdentityStore, name: str, **kw) -> int:
    return ident.create(name=name, slot_type=kw.pop("slot_type", "llm"), **kw).id


# ── allocate ──────────────────────────────────────────────────────────────────


def test_acquire_grants_and_binds(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    sid = _slot(ident, "a")
    port = auth.acquire(sid, include_listeners=False)
    assert POOL[0] <= port <= POOL[1]
    assert auth.held_by(sid) == port
    assert auth.is_free(port, include_listeners=False) is False


def test_acquire_honours_preferred_when_free(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    sid = _slot(ident, "a")
    assert auth.acquire(sid, preferred=8090, include_listeners=False) == 8090


def test_acquire_skips_preferred_when_taken(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    a = _slot(ident, "a")
    b = _slot(ident, "b")
    auth.acquire(a, preferred=8081, include_listeners=False)
    other = auth.acquire(b, preferred=8081, include_listeners=False)
    assert other != 8081


def test_acquire_lowest_free(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    a = _slot(ident, "a")
    b = _slot(ident, "b")
    assert auth.acquire(a, include_listeners=False) == 8081
    assert auth.acquire(b, include_listeners=False) == 8082


# ── reserve ───────────────────────────────────────────────────────────────────


def test_reserve_blocks_allocation(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    auth.reserve(8081, label="api")
    sid = _slot(ident, "a")
    assert auth.acquire(sid, include_listeners=False) == 8082  # 8081 reserved


def test_reserve_is_idempotent_same_label(tmp_path: Path) -> None:
    _ident, auth = _stores(tmp_path)
    auth.reserve(8080, label="api")
    auth.reserve(8080, label="api")  # no raise
    assert len(auth.claims()) == 1


def test_reserve_conflict_different_owner(tmp_path: Path) -> None:
    import sqlite3

    _ident, auth = _stores(tmp_path)
    auth.reserve(8080, label="api")
    with pytest.raises(sqlite3.IntegrityError):
        auth.reserve(8080, label="something-else")


# ── reject duplicate ──────────────────────────────────────────────────────────


def test_no_two_live_claims_on_one_port(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    a = _slot(ident, "a")
    b = _slot(ident, "b")
    port = auth.acquire(a, include_listeners=False)
    other = auth.acquire(b, preferred=port, include_listeners=False)
    assert other != port
    live_on_port = [c for c in auth.claims() if c.port == port]
    assert len(live_on_port) == 1


# ── release / reacquire ───────────────────────────────────────────────────────


def test_release_frees_the_port(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    sid = _slot(ident, "a")
    port = auth.acquire(sid, include_listeners=False)
    released = auth.release(sid)
    assert released == 1
    assert auth.held_by(sid) is None
    assert auth.is_free(port, include_listeners=False) is True


def test_release_then_reacquire_same_port(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    a = _slot(ident, "a")
    port = auth.acquire(a, preferred=8085, include_listeners=False)
    auth.release(a)
    b = _slot(ident, "b")
    # The same port is grantable again after release.
    assert auth.acquire(b, preferred=8085, include_listeners=False) == port
    # Audit trail: one released row + one live row on that port.
    all_on_port = [c for c in auth.claims(live_only=False) if c.port == port]
    assert len(all_on_port) == 2
    assert sum(1 for c in all_on_port if c.released_at is None) == 1


def test_reallocate_moves_port(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    sid = _slot(ident, "a")
    auth.acquire(sid, preferred=8081, include_listeners=False)
    new_port = auth.reallocate(sid, preferred=8095, include_listeners=False)
    assert new_port == 8095
    assert auth.held_by(sid) == 8095


# ── coresident carve-out (trio) ───────────────────────────────────────────────


def test_coresident_shadow_shares_anchor_port(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    anchor = _slot(ident, "anchor", device="npu", coresident_group="npu-flm-trio")
    stt = _slot(
        ident, "stt", slot_type="transcription", device="npu", coresident_group="npu-flm-trio"
    )
    anchor_port = auth.acquire(
        anchor, coresident_group="npu-flm-trio", include_listeners=False
    )
    shadow_port = auth.acquire(
        stt, coresident_group="npu-flm-trio", include_listeners=False
    )
    assert shadow_port == anchor_port
    # Only ONE live claim exists for the shared port (the anchor's).
    assert sum(1 for c in auth.claims() if c.port == anchor_port) == 1
    # held_by resolves the shadow through the group.
    assert auth.held_by(stt, coresident_group="npu-flm-trio") == anchor_port


# ── reconcile ─────────────────────────────────────────────────────────────────


def test_reconcile_releases_orphaned_claim(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    sid = _slot(ident, "a")
    port = auth.acquire(sid, include_listeners=False)
    # Simulate an out-of-band slot delete: the FK sets slot_id NULL but a
    # crash skipped the release, leaving a live claim with no owner.
    ident.delete(sid)
    assert auth.is_free(port, include_listeners=False) is False  # still live
    summary = auth.reconcile()
    assert summary["orphans_released"] == 1
    assert auth.is_free(port, include_listeners=False) is True


def test_delete_slot_then_release_frees_port(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path)
    sid = _slot(ident, "a")
    port = auth.acquire(sid, include_listeners=False)
    auth.release(sid)
    ident.delete(sid)
    assert auth.is_free(port, include_listeners=False) is True


# ── exhaustion ────────────────────────────────────────────────────────────────


def test_pool_exhaustion_raises(tmp_path: Path) -> None:
    ident, auth = _stores(tmp_path, pool=(8081, 8082))
    auth.acquire(_slot(ident, "a"), include_listeners=False)
    auth.acquire(_slot(ident, "b"), include_listeners=False)
    with pytest.raises(PortPoolExhausted):
        auth.acquire(_slot(ident, "c"), include_listeners=False)


# ── concurrency: the uq_port_claim_live race ──────────────────────────────────


def test_double_claim_rejected_under_concurrency(tmp_path: Path) -> None:
    """N threads each acquire for a distinct slot at once. The partial
    unique index guarantees no two land on the same port — the losers of
    each race retry and settle on distinct free ports."""
    n = 12
    ident, auth = _stores(tmp_path, pool=(8081, 8081 + n + 4))
    sids = [_slot(ident, f"s{i}") for i in range(n)]
    granted: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker(sid: int) -> None:
        barrier.wait()  # maximise the collision window
        port = auth.acquire(sid, include_listeners=False)
        with lock:
            granted.append(port)

    threads = [threading.Thread(target=worker, args=(s,)) for s in sids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(granted) == n
    assert len(set(granted)) == n  # every grant is a distinct port
