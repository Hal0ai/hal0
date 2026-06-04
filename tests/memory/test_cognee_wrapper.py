"""Smoke + contract tests for :mod:`hal0.memory.cognee_wrapper`.

Coverage maps to ADR-0005 §2 (public schema) and §3 (namespace rule)
and §5 (audit log). One test per requirement, plus a delete +
list_items round-trip and a tag/date filter check.

All tests are ``@pytest.mark.slow`` because every test spins up a
fresh Cognee + LanceDB + Kuzu install and the first invocation in
the session downloads the bge-small-en-v1.5 ONNX model (~70 MB).
Local fastembed cache makes subsequent runs ~2-3s each.
"""

from __future__ import annotations

import asyncio
from datetime import UTC

import pytest

# pytestmark applies @pytest.mark.slow to every test in this file —
# the harness driver opts in explicitly so a default `pytest` run on
# laptop dev hardware doesn't spend 90s on these.
pytestmark = pytest.mark.slow


@pytest.fixture
def make_wrapper(cognee_dir, reset_cognee_singletons):
    """Factory: return a fresh CogneeWrapper per call.

    Imports the wrapper INSIDE the fixture so the
    ``reset_cognee_singletons`` teardown (which drops cognee from
    ``sys.modules``) sees a clean import on the next test.
    """
    from hal0.memory.cognee_wrapper import CogneeWrapper

    def _make(*, client_id: str = "test-client", private_mode: bool = False):
        return CogneeWrapper(
            cognee_dir=cognee_dir,
            client_id=client_id,
            private_mode=private_mode,
        )

    return _make


# ── §2 add/search round-trip ──────────────────────────────────────────────


async def test_add_search_round_trip(make_wrapper):
    """The spec's required smoke: add → search returns the item."""
    w = make_wrapper()
    res = await w.add("the answer to memory testing is forty-two")
    assert "id" in res and "timestamp" in res

    hits = await w.search(query="memory testing answer", limit=5)
    assert len(hits) >= 1, f"expected at least one hit, got {hits!r}"
    assert any("forty-two" in h["text"] for h in hits)

    # Schema field check — every promised key from ADR-0005 §2.
    first = hits[0]
    for key in ("id", "text", "timestamp", "dataset", "tags", "source", "metadata", "score"):
        assert key in first, f"missing key {key!r} in {first!r}"


# ── §3 dataset isolation ──────────────────────────────────────────────────


async def test_private_writes_invisible_to_other_clients(make_wrapper):
    """Alice in private_mode writes to private:alice; Bob can't see it.

    Asserts:
      1. Alice's own search returns the item (private read picks up
         own bucket).
      2. Bob's search across `shared` does NOT see Alice's item.
      3. Bob's search trying to peek at `private:alice` is silently
         scoped down to his own namespace and also returns nothing.
    """
    alice = make_wrapper(client_id="alice", private_mode=True)
    await alice.add("alice's secret note about her birthday party")

    # Alice sees her own private write.
    alice_hits = await alice.search(query="birthday party secret", limit=10)
    assert any("secret note" in h["text"] for h in alice_hits)

    # Bob is a different client on the SAME wrapper-backing-store.
    bob = make_wrapper(client_id="bob", private_mode=False)
    bob_hits = await bob.search(query="birthday party secret", limit=10)
    assert not any("secret note" in h["text"] for h in bob_hits), (
        f"bob saw alice's private item: {bob_hits!r}"
    )

    # Bob trying to address Alice's bucket by name — silently dropped,
    # no error, no leak.
    bob_peek = await bob.search(query="birthday", dataset="private:alice", limit=10)
    assert not any("secret note" in h["text"] for h in bob_peek)


async def test_shared_writes_visible_to_same_client(make_wrapper):
    """Shared writes are visible to the writer (sanity check on the §3 rule)."""
    w = make_wrapper(client_id="alice", private_mode=False)
    await w.add("shared knowledge: hal0 ships memory MCP in v0.2")
    hits = await w.search(query="hal0 memory v0.2", limit=5)
    assert any("v0.2" in h["text"] for h in hits)


# ── §2 tag AND-filter ────────────────────────────────────────────────────


async def test_tag_and_filter(make_wrapper):
    """Tag filter is AND-match: all requested tags must be present."""
    w = make_wrapper()
    await w.add("tagged item one", tags=["foo", "bar"])
    await w.add("tagged item two", tags=["foo"])
    await w.add("tagged item three", tags=["bar", "baz"])

    # foo only -> items 1 + 2
    foo = await w.search(query="tagged item", tags=["foo"], limit=10)
    foo_texts = {h["text"] for h in foo}
    assert "tagged item one" in foo_texts
    assert "tagged item two" in foo_texts
    assert "tagged item three" not in foo_texts

    # foo AND bar -> only item 1
    both = await w.search(query="tagged item", tags=["foo", "bar"], limit=10)
    both_texts = {h["text"] for h in both}
    assert both_texts == {"tagged item one"}


# ── §2 date range filter ─────────────────────────────────────────────────


async def test_date_range_filter(make_wrapper):
    """`before` and `after` clip results by timestamp.

    We can't easily wind the clock back inside the wrapper (it stamps
    `datetime.now`), so we capture timestamps around the writes and
    use them as filter pivots.
    """
    from datetime import datetime, timedelta

    w = make_wrapper()
    t0 = datetime.now(UTC).isoformat()
    await w.add("first item by time")
    # Sleep just enough that the next item gets a later timestamp.
    await asyncio.sleep(0.05)
    pivot = datetime.now(UTC).isoformat()
    await asyncio.sleep(0.05)
    await w.add("second item by time")
    t_end = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()

    # after=pivot -> only the second item.
    later = await w.search(query="item by time", after=pivot, limit=10)
    later_texts = {h["text"] for h in later}
    assert later_texts == {"second item by time"}, later_texts

    # before=pivot -> only the first item.
    earlier = await w.search(query="item by time", before=pivot, limit=10)
    earlier_texts = {h["text"] for h in earlier}
    assert earlier_texts == {"first item by time"}, earlier_texts

    # Wide window catches both.
    both = await w.search(query="item by time", after=t0, before=t_end, limit=10)
    assert {h["text"] for h in both} == {"first item by time", "second item by time"}


# ── §2 delete decrements list_items ──────────────────────────────────────


async def test_delete_single_id_decrements_list(make_wrapper):
    """Deleting one item by id removes exactly that item from list_items."""
    w = make_wrapper()
    a = await w.add("alpha")
    b = await w.add("beta")
    await w.add("gamma")

    initial = await w.list_items(limit=50)
    assert {i["text"] for i in initial["items"]} == {"alpha", "beta", "gamma"}

    res = await w.delete(ids=[b["id"]])
    assert res == {"deleted": 1}

    after = await w.list_items(limit=50)
    assert {i["text"] for i in after["items"]} == {"alpha", "gamma"}

    # Deleting an unknown id is a no-op, not a crash.
    res2 = await w.delete(ids=["00000000-0000-0000-0000-000000000000"])
    assert res2 == {"deleted": 0}
    _ = a  # silence linter; the test only needs id references on b


async def test_delete_custom_dataset_item_by_owner(make_wrapper):
    """Deleting an own item in a CUSTOM dataset (e.g. ADR-0011 ``agents``)
    actually removes it.

    Regression for the Peer-memory stale-card flood: the delete guard
    hardcoded the allowed-read set to ``shared`` + own-private, so any id
    whose row lived in a custom dataset (``agents``) was silently skipped,
    ``deleted`` stayed 0, and Hermes bootstrap's "delete-then-rewrite"
    dedup leaked one identity card per run.
    """
    w = make_wrapper(client_id="hermes-agent")
    card = await w.add("identity card", dataset="agents", tags=["agent-identity"])

    res = await w.delete(ids=[card["id"]])
    assert res == {"deleted": 1}

    remaining = await w.search(
        query="identity card", dataset="agents", tags=["agent-identity"], limit=10
    )
    assert remaining == []


async def test_delete_does_not_reach_other_clients_private(make_wrapper):
    """The dataset guard still blocks deleting another client's private item.

    Tightening the guard to honor custom datasets must NOT regress the
    cross-client private protection it was written for.
    """
    alice = make_wrapper(client_id="alice", private_mode=True)
    secret = await alice.add("alice secret")  # lands in private:alice

    bob = make_wrapper(client_id="bob", private_mode=False)
    res = await bob.delete(ids=[secret["id"]])
    assert res == {"deleted": 0}

    # Still there for the owner.
    still = await alice.search(query="alice secret", limit=10)
    assert any(h["text"] == "alice secret" for h in still)


async def test_list_items_pagination(make_wrapper):
    """Cursor-based pagination walks the dataset in timestamp DESC order."""
    w = make_wrapper()
    for i in range(5):
        await w.add(f"paged-item-{i}")
        await asyncio.sleep(0.02)  # distinct timestamps

    page1 = await w.list_items(limit=2)
    assert len(page1["items"]) == 2
    assert page1["next_cursor"] is not None

    page2 = await w.list_items(limit=2, cursor=page1["next_cursor"])
    assert len(page2["items"]) == 2
    assert page2["next_cursor"] is not None

    page3 = await w.list_items(limit=2, cursor=page2["next_cursor"])
    # Last page returns the remaining 1 item and a null cursor.
    assert len(page3["items"]) == 1
    assert page3["next_cursor"] is None


# ── §5 audit log emitted for every op ────────────────────────────────────


async def test_audit_log_emitted_per_op(make_wrapper):
    """Every public op records an entry on the wrapper's audit_tail.

    The wrapper mirrors every audit event to ``self.audit_tail`` AND
    a structlog ``hal0.memory.audit`` event. The tail is what tests
    inspect — Cognee reconfigures structlog itself during the first
    ``add``, so capturing the structlog channel reliably in test is
    fragile. The production audit-log surface (journald) reads the
    structlog stream; ``audit_tail`` is the test mirror.
    """
    w = make_wrapper(client_id="auditor")

    add_res = await w.add("auditable add", tags=["a"])
    await w.search(query="auditable")
    await w.list_items(limit=10)
    await w.delete(ids=[add_res["id"]])

    ops = [e["op"] for e in w.audit_tail]
    assert "add" in ops, ops
    assert "search" in ops, ops
    assert "list_items" in ops, ops
    assert "delete" in ops, ops

    # Every event carries the client_id stamped by the constructor.
    assert all(e["client_id"] == "auditor" for e in w.audit_tail), w.audit_tail

    # Add + delete events count what they did + name the dataset.
    add_event = next(e for e in w.audit_tail if e["op"] == "add")
    delete_event = next(e for e in w.audit_tail if e["op"] == "delete")
    assert add_event["dataset"] == "shared"
    assert delete_event["deleted"] == 1

    # Every event carries the AUDIT_EVENT marker so a journald
    # consumer can grep on the event name.
    assert all(e["event"] == "hal0.memory.audit" for e in w.audit_tail)


# ── Misc shape contract checks ───────────────────────────────────────────


async def test_source_is_auto_injected_from_client_id(make_wrapper):
    """`source` falls back to client_id when caller omits it.

    Reads through list_items so we see the stored sidecar value
    rather than relying on what `add` echoes back.
    """
    w = make_wrapper(client_id="alpha-bot")
    await w.add("a thing alpha-bot did")
    items = await w.list_items(limit=5)
    assert any(i["source"] == "alpha-bot" for i in items["items"])


async def test_search_returns_empty_when_store_is_fresh(make_wrapper):
    """Searching an empty store returns ``[]`` rather than raising.

    Cognee's CHUNKS retriever raises NoDataError on a fresh install;
    the wrapper's contract (§2) is that search ALWAYS returns a list.
    """
    w = make_wrapper()
    out = await w.search(query="nothing here yet", limit=5)
    assert out == []
