"""Issue #1451: an all-foreign namespace list must fail CLOSED, not collapse
to the default shared sweep.

The defect. ``resolve_read_datasets`` documented its list branch as
"fail-open-empty": unknown / foreign-private entries are dropped. That is
right for a *partial* list (``["agents", "nope"]`` → ``["agents"]``) and
catastrophically wrong for a list that filters to nothing, because every
downstream consumer read ``[]`` as falsy and substituted the default:

    reqs = [requested] if isinstance(requested, str) else list(requested or [_SHARED])
    ...
    return out or [_SHARED]

So ``dataset=["bogus-bank"]`` resolved to ``[]`` at the front door and back
to ``["shared"]`` inside the provider. The operator-visible consequence is
the one this file exists to prevent: a bulk ``memory_delete`` is approval-
gated on the *arguments* (``mcp/admin.py`` gates any list-valued dataset),
the operator sees a call naming a bank that does not exist, approves it,
and the executor resolves the empty list to the shared bank and deletes
shared documents. Reads had the quieter half of the same bug — a search
scoped exclusively to namespaces the caller may not address returned
shared results.

The posture encoded here: ``[]`` means *no banks*, never *all default
banks*. ``None`` (nothing requested) is the only input that may expand to
the default sweep. A non-empty request that resolves to no addressable
namespace is a 400 at the front door, and — defence in depth, because the
REST delete route and the providers are reachable without the resolver —
an empty list handed straight to a provider sweeps nothing.
"""

from __future__ import annotations

import pytest

from hal0.api.routes.memory import MemoryNamespaceInvalid
from hal0.mcp import memory as mcp_memory
from hal0.mcp.memory import MemorySchemaError
from hal0.memory.hindsight_provider import HindsightProvider
from hal0.memory.namespace import (
    DEFAULT_DATASET,
    MemoryNamespaceError,
    resolve_read_datasets,
)
from hal0.memory.pgvector_provider import PgVectorProvider


class _Client:
    """Minimal Hindsight client that records every bank it is asked about."""

    def __init__(self) -> None:
        self.banks_touched: list[tuple[str, str]] = []
        self.docs: dict[str, list[dict]] = {
            "shared": [{"document_id": "d1", "id": "f1", "text": "shared secret", "tags": []}]
        }

    async def list_memories(self, *, bank_id, limit=50, offset=0, types=None, query=None):
        self.banks_touched.append(("list", bank_id))
        return {"items": list(self.docs.get(bank_id, []))[offset : offset + limit]}

    async def delete_document(self, *, bank_id, document_id):
        self.banks_touched.append(("delete", bank_id))
        rows = self.docs.get(bank_id, [])
        kept = [d for d in rows if d["document_id"] != document_id]
        removed = len(rows) - len(kept)
        self.docs[bank_id] = kept
        return {"memory_units_deleted": removed}

    async def recall(self, *, bank_id, query, types=None, max_tokens=4096, tags=None, **kw):
        self.banks_touched.append(("recall", bank_id))
        return {"results": list(self.docs.get(bank_id, []))}


def _provider(client: _Client, *, unified: bool) -> HindsightProvider:
    return HindsightProvider(client=client, client_id="agent-x", unified_bank=unified)


# ── 1. the resolver: a non-empty request that filters to empty is an error ───


@pytest.mark.parametrize(
    "requested",
    [
        ["bogus-bank"],
        ["private:other-agent"],
        ["bogus-bank", "private:other-agent"],
        ["project:ok/../escape"],
    ],
)
def test_all_foreign_list_raises_instead_of_collapsing_to_shared(requested: list[str]) -> None:
    """The #1451 repro. Every one of these used to return ``[]``."""
    with pytest.raises(MemoryNamespaceError) as exc:
        resolve_read_datasets(requested, private=False, client_id="agent-x")
    # The message must name the rejected input, not just say "invalid" — the
    # operator reading the 400 needs to know which entry was unaddressable.
    assert (
        "bogus-bank" in str(exc.value)
        or "private:other-agent" in str(exc.value)
        or ("project:ok/../escape" in str(exc.value))
    )


def test_partial_list_still_fails_open_on_the_dropped_entries() -> None:
    """Unchanged behaviour: a list with at least one addressable namespace
    keeps degrading rather than erroring (the multi-namespace read contract)."""
    assert resolve_read_datasets(
        ["agents", "not-a-real-bank"], private=False, client_id="agent-x"
    ) == ["agents"]


def test_empty_list_is_an_unspecified_request_not_a_foreign_one() -> None:
    """``dataset=[]`` carries no namespace to reject — it is "unset", and
    resolves to the default like ``None`` does. Only a non-empty list that
    filters to nothing is a fail-closed error."""
    assert resolve_read_datasets([], private=False, client_id="agent-x") == DEFAULT_DATASET


def test_own_private_in_a_list_is_still_addressable() -> None:
    assert resolve_read_datasets(["private:agent-x"], private=False, client_id="agent-x") == [
        "private:agent-x"
    ]


# ── 2. the providers: [] sweeps nothing, None sweeps the default ─────────────


@pytest.mark.parametrize("unified", [True, False])
def test_empty_namespace_list_resolves_to_no_banks(unified: bool) -> None:
    p = _provider(_Client(), unified=unified)
    assert p._allowed_namespaces([], "agent-x") == []
    # ...and None still means "the caller asked for nothing" → default sweep.
    assert p._allowed_namespaces(None, "agent-x") != []


def test_pgvector_empty_namespace_list_resolves_to_no_banks() -> None:
    p = PgVectorProvider(client_id="agent-x")
    assert p._allowed([], "agent-x") == []
    assert p._allowed(None, "agent-x") != []


@pytest.mark.asyncio
@pytest.mark.parametrize("unified", [True, False])
async def test_delete_with_an_empty_dataset_deletes_nothing(unified: bool) -> None:
    """The executor-side half of the incident: even if an empty list reaches
    ``delete`` (the REST route forwards its list verbatim), it must not be
    re-expanded to the shared bank."""
    client = _Client()
    p = _provider(client, unified=unified)
    assert await p.delete(["d1"], client_id="agent-x", dataset=[]) == {"deleted": 0}
    assert [b for kind, b in client.banks_touched if kind == "delete"] == []
    assert client.docs["shared"], "the shared document was deleted by an empty-scope delete"


@pytest.mark.asyncio
@pytest.mark.parametrize("unified", [True, False])
async def test_reads_with_an_empty_dataset_return_nothing(unified: bool) -> None:
    client = _Client()
    p = _provider(client, unified=unified)
    assert await p.recall("secret", dataset=[], client_id="agent-x") == []
    assert (await p.list_items(dataset=[], client_id="agent-x"))["items"] == []
    assert client.banks_touched == []


# ── 3. the MCP dispatcher rejects before the executor runs ───────────────────


@pytest.mark.asyncio
async def test_mcp_delete_rejects_an_all_foreign_dataset() -> None:
    """Step 2 of the issue's live repro: this returned ``{'deleted': 2}``."""
    client = _Client()
    wrapper = _provider(client, unified=True)
    with pytest.raises(MemorySchemaError):
        await mcp_memory._memory_delete(
            wrapper,
            {"ids": ["d1"], "dataset": ["bogus-bank"]},
            client_id="agent-x",
            private=False,
        )
    assert client.banks_touched == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["_memory_search", "_memory_recall"])
async def test_mcp_reads_reject_an_all_foreign_dataset(tool: str) -> None:
    client = _Client()
    wrapper = _provider(client, unified=True)
    args = {"query": "secret", "dataset": ["private:other-agent"]}
    with pytest.raises(MemorySchemaError):
        await getattr(mcp_memory, tool)(wrapper, args, client_id="agent-x", private=False)
    assert client.banks_touched == []


# ── 4. the REST delete route stops forwarding its list verbatim ──────────────


@pytest.mark.asyncio
async def test_rest_delete_routes_its_list_through_the_shared_resolver() -> None:
    """``api/routes/memory.py`` built its list dataset by hand
    (``[str(d) for d in requested]``), skipping ``resolve_read_datasets``
    entirely — the exact drift the namespace module exists to prevent."""
    from hal0.api.routes import memory as memory_routes

    captured: dict = {}

    class _Wrapper:
        async def delete(self, *, ids, client_id, dataset):
            captured["dataset"] = dataset
            return {"deleted": len(ids)}

    request = _FakeRequest({"ids": ["d1"], "dataset": ["bogus-bank"]}, wrapper=_Wrapper())
    with pytest.raises(MemoryNamespaceInvalid):
        await memory_routes.memory_delete(request)
    assert "dataset" not in captured, "the wrapper was reached with an unresolvable scope"


@pytest.mark.asyncio
async def test_rest_delete_still_forwards_an_addressable_list() -> None:
    """Negative control for the test above — the guard must not break the
    legitimate multi-namespace delete."""
    from hal0.api.routes import memory as memory_routes

    captured: dict = {}

    class _Wrapper:
        async def delete(self, *, ids, client_id, dataset):
            captured["dataset"] = dataset
            return {"deleted": len(ids)}

    request = _FakeRequest({"ids": ["d1"], "dataset": ["agents", "nope"]}, wrapper=_Wrapper())
    assert await memory_routes.memory_delete(request) == {"deleted": 1}
    assert captured["dataset"] == ["agents"]


class _FakeRequest:
    """The handful of attributes ``memory_delete`` touches: headers, JSON
    body, and ``app.state.memory_provider`` / ``audit_log``."""

    def __init__(self, body: dict, *, wrapper: object) -> None:
        self._body = body
        self.headers: dict[str, str] = {}
        state = type("_S", (), {})()
        state.memory_provider = wrapper
        state.audit_log = None
        self.app = type("_App", (), {"state": state})()
        self.state = type("_S", (), {})()
        self.client = None
        self.url = type("_U", (), {"path": "/api/memory/delete"})()
        self.method = "POST"

    async def json(self) -> dict:
        return self._body
