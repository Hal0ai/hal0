"""Integration tests for the REST shims under ``/api/memory/{add,search,list,delete}``.

Issue #317: ``/api/memory/add`` used to hardcode ``dataset`` to ``"shared"``
and to ignore the ``X-hal0-Agent`` + ``X-hal0-Private`` identity headers
that the dashboard, Hermes bootstrap, and ``hal0 agent`` CLI all send.
This test module pins the post-fix contract:

  - ``X-hal0-Agent`` is the post-ADR-0012 identity header (no Bearer).
  - ``X-hal0-Private: 1`` promotes writes to ``private:<agent>`` and
    expands reads to ``[shared, private:<agent>]`` per ADR-0005 §3.
  - An explicit ``dataset`` in the body wins over the default but the
    ``--private`` toggle still wins over both (writes are forced into
    the caller's private namespace — clients can't smuggle data into
    ``shared`` while in private mode).
  - ``source`` on ``add`` is server-injected from the agent header so
    callers cannot lie about their identity (ADR-0005 §5).

Uses a duck-typed ``StubWrapper`` mirroring :class:`CogneeWrapper`'s
public surface — same pattern as ``tests/api/test_memory_graph_route``
and ``tests/mcp/test_memory``. The point here is the *route* behavior,
not the wrapper; the wrapper has its own coverage under ``tests/memory/``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes


class StubWrapper:
    """Duck-typed stand-in for :class:`CogneeWrapper`.

    Captures every ``add`` / ``search`` / ``list_items`` / ``delete``
    call so tests can assert the route forwarded the resolved dataset
    + server-injected source without spinning up Cognee.
    """

    def __init__(self) -> None:
        self.add_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self._counter = 0

    async def add(
        self,
        *,
        text: str,
        dataset: str,
        tags: list[str],
        source: str | None,
        metadata: dict[str, Any],
        client_id: str | None = None,
    ) -> dict[str, Any]:
        self.add_calls.append(
            {
                "text": text,
                "dataset": dataset,
                "tags": tags,
                "source": source,
                "metadata": metadata,
                "client_id": client_id,
            }
        )
        self._counter += 1
        return {"id": f"id-{self._counter}", "timestamp": "2026-05-28T00:00:00Z"}

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.search_calls.append(kwargs)
        return [{"id": "id-1", "text": "stub", "score": 0.9}]

    async def list_items(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        return {"items": [{"id": "id-1"}], "next_cursor": None}

    async def delete(self, *, ids: list[str], client_id: str | None = None) -> dict[str, Any]:
        self.delete_calls.append({"ids": list(ids), "client_id": client_id})
        return {"deleted": len(ids)}


@pytest.fixture
def stub_wrapper() -> StubWrapper:
    return StubWrapper()


def _build_app(stub: StubWrapper) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_wrapper = stub
    return app


@pytest.fixture
def client(stub_wrapper: StubWrapper) -> Iterator[TestClient]:
    app = _build_app(stub_wrapper)
    with TestClient(app) as c:
        yield c


# ── /api/memory/add ────────────────────────────────────────────────────────


def test_add_default_writes_to_shared_no_headers(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """No identity headers → behaves like the old default: shared dataset,
    anonymous source. Backwards-compatibility guard."""
    r = client.post("/api/memory/add", json={"text": "hello"})
    assert r.status_code == 200, r.text
    call = stub_wrapper.add_calls[0]
    assert call["dataset"] == "shared"
    assert call["source"] == "anonymous"
    assert call["tags"] == []
    assert call["metadata"] == {}


def test_add_private_header_promotes_dataset_to_caller_namespace(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """``X-hal0-Agent: hermes-agent`` + ``X-hal0-Private: 1`` → writes
    land under ``private:hermes-agent`` (issue #317 main repro)."""
    r = client.post(
        "/api/memory/add",
        json={"text": "probe", "tags": ["probe"]},
        headers={"X-hal0-Agent": "hermes-agent", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    call = stub_wrapper.add_calls[0]
    assert call["dataset"] == "private:hermes-agent"
    # ADR-0005 §5 — source is server-injected from the agent header.
    assert call["source"] == "hermes-agent"


def test_add_explicit_private_dataset_passthrough(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """Explicit ``dataset=private:hermes-agent`` body field is honored
    when the agent header matches — mirrors the MCP ``dataset`` arg."""
    r = client.post(
        "/api/memory/add",
        json={"text": "probe", "dataset": "private:hermes-agent"},
        headers={"X-hal0-Agent": "hermes-agent", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    call = stub_wrapper.add_calls[0]
    assert call["dataset"] == "private:hermes-agent"


def test_add_private_mode_wins_over_body_dataset(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """A caller in ``--private`` mode cannot escape into ``shared`` by
    passing ``dataset="shared"`` in the body — the toggle is a posture,
    not a per-call switch (ADR-0005 §3, mirrors MCP behavior)."""
    r = client.post(
        "/api/memory/add",
        json={"text": "secret", "dataset": "shared"},
        headers={"X-hal0-Agent": "hermes-agent", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    call = stub_wrapper.add_calls[0]
    assert call["dataset"] == "private:hermes-agent"


def test_add_custom_dataset_passthrough_no_private(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """Non-private callers can address custom datasets (e.g. the
    Hermes bootstrap ``agents`` dataset) explicitly."""
    r = client.post(
        "/api/memory/add",
        json={"text": "ident card", "dataset": "agents"},
        headers={"X-hal0-Agent": "hermes-agent"},
    )
    assert r.status_code == 200, r.text
    call = stub_wrapper.add_calls[0]
    assert call["dataset"] == "agents"
    assert call["source"] == "hermes-agent"


def test_add_private_without_agent_header_rejected(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """``X-hal0-Private: 1`` without ``X-hal0-Agent`` cannot promote —
    refuse rather than silently writing to ``private:anonymous``."""
    r = client.post(
        "/api/memory/add",
        json={"text": "probe"},
        headers={"X-hal0-Private": "1"},
    )
    assert r.status_code >= 400
    assert stub_wrapper.add_calls == []


def test_add_caller_supplied_source_rejected(client: TestClient, stub_wrapper: StubWrapper) -> None:
    """Mirror ADR-0005 §5: ``source`` is server-injected — clients
    cannot supply it (would otherwise let a compromised agent claim
    another agent's identity in the audit trail)."""
    r = client.post(
        "/api/memory/add",
        json={"text": "x", "source": "fake-agent"},
        headers={"X-hal0-Agent": "hermes-agent"},
    )
    assert r.status_code >= 400
    assert stub_wrapper.add_calls == []


# ── /api/memory/search ─────────────────────────────────────────────────────


def test_search_private_mode_expands_to_both_namespaces(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """Private-mode read sees ``[shared, private:<agent>]`` per
    ADR-0005 §3 — mirrors :func:`hal0.mcp.memory._memory_search`."""
    r = client.post(
        "/api/memory/search",
        json={"query": "probe"},
        headers={"X-hal0-Agent": "hermes-agent", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    call = stub_wrapper.search_calls[0]
    assert call["dataset"] == ["shared", "private:hermes-agent"]


def test_search_explicit_private_dataset(client: TestClient, stub_wrapper: StubWrapper) -> None:
    """Explicit ``dataset=private:hermes-agent`` flows through; the
    REST handler does not collapse the namespace before passing to the
    wrapper (the wrapper enforces cross-client read isolation)."""
    r = client.post(
        "/api/memory/search",
        json={"query": "probe", "dataset": "private:hermes-agent"},
        headers={"X-hal0-Agent": "hermes-agent", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    call = stub_wrapper.search_calls[0]
    assert call["dataset"] == "private:hermes-agent"


def test_search_default_no_headers_is_shared(client: TestClient, stub_wrapper: StubWrapper) -> None:
    """No identity headers → default to ``shared`` (back-compat)."""
    r = client.post("/api/memory/search", json={"query": "probe"})
    assert r.status_code == 200, r.text
    call = stub_wrapper.search_calls[0]
    assert call["dataset"] == "shared"


# ── /api/memory/list ───────────────────────────────────────────────────────


def test_list_private_mode_targets_own_namespace(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """List with ``X-hal0-Private: 1`` resolves to the caller's own
    private bucket — required for the ``hal0 agent`` CLI ``memory list``
    subcommand to enumerate per-agent items."""
    r = client.get(
        "/api/memory/list",
        headers={"X-hal0-Agent": "hermes-agent", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    call = stub_wrapper.list_calls[0]
    assert call["dataset"] == "private:hermes-agent"


def test_list_explicit_dataset_query_param(client: TestClient, stub_wrapper: StubWrapper) -> None:
    """The ``?dataset=`` query param still wins for non-private listers."""
    r = client.get("/api/memory/list?dataset=agents")
    assert r.status_code == 200, r.text
    call = stub_wrapper.list_calls[0]
    assert call["dataset"] == "agents"


# ── /api/memory/delete ─────────────────────────────────────────────────────


def test_delete_passes_ids_unchanged(client: TestClient, stub_wrapper: StubWrapper) -> None:
    """Delete is by id and doesn't touch the namespace surface, but
    we sanity-pin that the route forwards ids verbatim regardless of
    identity headers."""
    r = client.post(
        "/api/memory/delete",
        json={"ids": ["a", "b"]},
        headers={"X-hal0-Agent": "hermes-agent", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    assert stub_wrapper.delete_calls == [{"ids": ["a", "b"], "client_id": "hermes-agent"}]
    assert r.json() == {"deleted": 2}


# ── PR #366 review hardening (closes #317 + #367) ──────────────────────────
#
# Six new contract pins surfaced by the request-changes review. The
# first cluster (path traversal, ``private:`` prefix in the agent
# header) covers the ADR-0005 §5 identity-shape audit findings; the
# second (body ``dataset="private:other"`` with ``private=False``)
# covers the namespace-resolver hardening; the third (list endpoint
# without an agent header) mirrors the existing ``/add`` contract on
# the read surface.


def test_add_agent_header_path_traversal_rejected(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """``X-hal0-Agent: ../etc/passwd`` is rejected at 400 — the agent
    id feeds the Cognee dataset name + the audit log's ``source`` field,
    so path-traversal candidates must never reach the wrapper."""
    r = client.post(
        "/api/memory/add",
        json={"text": "x"},
        headers={"X-hal0-Agent": "../etc/passwd"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "memory.agent_id_invalid"
    assert stub_wrapper.add_calls == []


def test_add_agent_header_private_prefix_rejected(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """``X-hal0-Agent: private:bob`` is rejected — would otherwise
    manufacture a ``private:private:bob`` dataset when X-hal0-Private
    is set. The private namespace is reachable only via the toggle."""
    r = client.post(
        "/api/memory/add",
        json={"text": "x"},
        headers={"X-hal0-Agent": "private:bob"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "memory.agent_id_invalid"
    assert stub_wrapper.add_calls == []


def test_add_agent_header_with_colon_variant_rejected(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """Colon in agent id (e.g. ``hermes-agent:1``) is rejected by the
    ``[a-zA-Z0-9_-]{1,64}`` regex — even non-``private:`` colons cannot
    flow through because they'd corrupt the dataset string."""
    r = client.post(
        "/api/memory/add",
        json={"text": "x"},
        headers={"X-hal0-Agent": "hermes-agent:1"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "memory.agent_id_invalid"
    assert stub_wrapper.add_calls == []


def test_add_body_private_dataset_without_private_header_rejected(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """Body ``dataset=private:other`` with no ``X-hal0-Private`` is
    rejected — non-private callers cannot address the private namespace
    by name. The toggle is the only path to ``private:<x>``."""
    r = client.post(
        "/api/memory/add",
        json={"text": "x", "dataset": "private:other"},
        headers={"X-hal0-Agent": "hermes-agent"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "memory.namespace_invalid"
    assert stub_wrapper.add_calls == []


def test_list_private_without_agent_header_rejected(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """``X-hal0-Private: 1`` on ``/list`` without an agent header is
    rejected — mirrors :func:`test_add_private_without_agent_header_rejected`
    on the read surface so callers get a consistent 400 across CRUD."""
    r = client.get(
        "/api/memory/list",
        headers={"X-hal0-Private": "1"},
    )
    assert r.status_code >= 400, r.text
    assert stub_wrapper.list_calls == []


def test_cross_client_private_writes_not_visible_to_other_agents(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """Agent A writes private; Agent B reads without private headers and
    does NOT see A's private rows.

    Two layers of containment under test:
      1. The REST handler does not forward A's ``private:A`` dataset to
         B's default read (B's resolved dataset is ``"shared"``).
      2. The wrapper search call captured for B contains only ``shared``
         — no leakage of A's namespace through the route surface.

    The wrapper-level enforcement (own-private intersection) is covered
    in ``tests/memory/test_cognee_wrapper.py``. This test pins that the
    REST veneer doesn't undo it.
    """
    # Agent A writes private.
    r = client.post(
        "/api/memory/add",
        json={"text": "A's secret"},
        headers={"X-hal0-Agent": "agent-a", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    assert stub_wrapper.add_calls[0]["dataset"] == "private:agent-a"

    # Agent B searches without the private header.
    r2 = client.post(
        "/api/memory/search",
        json={"query": "secret"},
        headers={"X-hal0-Agent": "agent-b"},
    )
    assert r2.status_code == 200, r2.text
    # Agent B's resolved read scope is just ``shared`` — the route does
    # not unilaterally union in any other agent's private namespace.
    search_call = stub_wrapper.search_calls[-1]
    assert search_call["dataset"] == "shared"
    assert "private:agent-a" not in (
        search_call["dataset"]
        if isinstance(search_call["dataset"], list)
        else [search_call["dataset"]]
    )


# ── Read-side namespace plumbing (Phase D regression fix) ──────────────────


def test_search_route_passes_resolved_client_id_to_wrapper(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """REST search route must thread X-hal0-Agent through as client_id so
    the wrapper's _allowed_read_datasets honors per-call identity.
    Without this, hermes-agent writes to private:hermes-agent but
    memory_search returns 0 because the singleton wrapper drops
    private:hermes-agent as "another client's private".
    """
    r = client.post(
        "/api/memory/search",
        json={"query": "hi"},
        headers={"X-hal0-Agent": "hermes-agent", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    sc = stub_wrapper.search_calls[-1]
    assert sc["client_id"] == "hermes-agent"


def test_list_route_passes_resolved_client_id_to_wrapper(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """REST list route mirrors search — must thread client_id."""
    r = client.get(
        "/api/memory/list",
        headers={"X-hal0-Agent": "hermes-agent", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    lc = stub_wrapper.list_calls[-1]
    assert lc["client_id"] == "hermes-agent"


def test_add_route_passes_resolved_client_id_to_wrapper(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    """REST add route threads client_id so audit log stamps the
    per-call identity instead of the singleton's "anonymous".
    """
    r = client.post(
        "/api/memory/add",
        json={"text": "secret"},
        headers={"X-hal0-Agent": "agent-a", "X-hal0-Private": "1"},
    )
    assert r.status_code == 200, r.text
    ac = stub_wrapper.add_calls[-1]
    assert ac["client_id"] == "agent-a"
    assert ac["source"] == "agent-a"
    assert ac["dataset"] == "private:agent-a"


def test_anonymous_call_passes_no_client_id(client: TestClient, stub_wrapper: StubWrapper) -> None:
    """No X-hal0-Agent header → client_id=None passed to wrapper so the
    wrapper falls back to its constructor value (legacy behavior).
    """
    r = client.post(
        "/api/memory/add",
        json={"text": "anon"},
    )
    assert r.status_code == 200, r.text
    ac = stub_wrapper.add_calls[-1]
    assert ac["client_id"] is None
