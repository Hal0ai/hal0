"""Private visibility on the bank-wide memory read tools (#3573).

Documents, knowledge pages, mental models, tags, operations, bank stats and
memory history read one bank, and that bank can hold several agents' private
memory: ``shared`` under the unified layout, ``hal0:private`` under the
two-bank layout. Each read must hold another agent's private memory back from
the caller, judged on the tags of what the engine returns and not on the
bank's name. The two layouts are exercised as a unified provider over
``shared`` and as a non-unified provider whose platform bank is
``hal0:private``: the rule must hold for both.

The MCP tests at the end go through :func:`hal0.mcp.memory.make_dispatcher`
with the real provider, the way both MCP mounts reach it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hal0.errors import NotFound
from hal0.mcp.memory import make_dispatcher
from hal0.memory.hindsight_provider import HindsightProvider
from hal0.memory.read_scope import model_scope_confined_to

ALICE_PRIVATE = ["visibility:private", "agent:alice", "topic:secret"]


class _Resp:
    status_code = 404


class _EngineNotFound(Exception):
    def __init__(self) -> None:
        super().__init__("Client error '404 Not Found'")
        self.response = _Resp()


class _Engine:
    """An engine double that evaluates the tag filters these reads send."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, dict[str, Any]]] = {}
        self.models: dict[str, dict[str, dict[str, Any]]] = {}
        self.trees: dict[str, dict[str, Any]] = {}
        self.pages: dict[str, dict[str, Any]] = {}
        self.ops: dict[str, dict[str, dict[str, Any]]] = {}
        self.memories: dict[str, dict[str, Any]] = {}
        self.updated: list[str] = []
        self.refreshed: list[str] = []
        self.cancelled: list[str] = []
        self.retried: list[str] = []
        self.model_gets = 0
        self.gets_in_flight = 0
        self.max_gets_in_flight = 0

    def doc(self, bank: str, doc_id: str, tags: list[str], text: str = "body") -> None:
        self.docs.setdefault(bank, {})[doc_id] = {
            "id": doc_id,
            "bank_id": bank,
            "tags": list(tags),
            "original_text": text,
        }

    async def list_documents(
        self, *, bank_id, q=None, tags=None, tags_match=None, limit=100, offset=0
    ):
        rows = list(self.docs.get(bank_id, {}).values())
        if tags:
            rows = [r for r in rows if set(tags) <= set(r["tags"])]
        page = rows[offset : offset + limit]
        return {
            "items": [{"id": r["id"], "tags": list(r["tags"])} for r in page],
            "total": len(rows),
            "limit": limit,
            "offset": offset,
        }

    async def get_document(self, *, bank_id, document_id):
        doc = self.docs.get(bank_id, {}).get(document_id)
        if doc is None:
            raise _EngineNotFound()
        return dict(doc)

    async def list_mental_models(self, *, bank_id, detail=None, **_kwargs):
        rows = [dict(m) for m in self.models.get(bank_id, {}).values()]
        if detail in (None, "metadata"):
            # Hindsight 1.2.0 lists every model with ``trigger: null`` (and no
            # content) at its default ``metadata`` detail (#3584).
            rows = [{**r, "trigger": None, "content": None} for r in rows]
        return {"items": rows}

    async def get_mental_model(self, *, bank_id, mental_model_id):
        self.model_gets += 1
        self.gets_in_flight += 1
        self.max_gets_in_flight = max(self.max_gets_in_flight, self.gets_in_flight)
        try:
            await asyncio.sleep(0)
            model = self.models.get(bank_id, {}).get(mental_model_id)
            if model is None:
                raise _EngineNotFound()
            return dict(model)
        finally:
            self.gets_in_flight -= 1

    async def create_mental_model(self, *, bank_id, name, source_query, id=None, **kwargs):
        mm_id = id or f"mm-{len(self.models.get(bank_id, {})) + 1}"
        self.models.setdefault(bank_id, {})[mm_id] = {
            "id": mm_id,
            "name": name,
            "tags": list(kwargs.get("tags") or []),
            "trigger": kwargs.get("trigger") or {},
            "content": "",
        }
        return {"mental_model_id": mm_id, "operation_id": "op-mm"}

    async def update_mental_model(self, *, bank_id, mental_model_id, **kwargs):
        model = self.models.get(bank_id, {}).get(mental_model_id)
        if model is None:
            raise _EngineNotFound()
        for key, value in kwargs.items():
            if value is not None:
                model[key] = value
        self.updated.append(mental_model_id)
        return dict(model)

    async def delete_mental_model(self, *, bank_id, mental_model_id):
        if self.models.get(bank_id, {}).pop(mental_model_id, None) is None:
            raise _EngineNotFound()
        return {"success": True}

    async def refresh_mental_model(self, *, bank_id, mental_model_id):
        if mental_model_id not in self.models.get(bank_id, {}):
            raise _EngineNotFound()
        self.refreshed.append(mental_model_id)
        return {"operation_id": "op-refresh"}

    async def cancel_operation(self, *, bank_id, operation_id):
        self.cancelled.append(operation_id)
        return {"success": True}

    async def retry_operation(self, *, bank_id, operation_id):
        self.retried.append(operation_id)
        return {"success": True}

    async def get_knowledge_base_tree(self, *, bank_id):
        return self.trees.get(bank_id, {"roots": []})

    async def get_knowledge_base_page(self, *, bank_id, page_id):
        page = self.pages.get(page_id)
        if page is None:
            raise _EngineNotFound()
        return dict(page)

    async def list_operations(self, *, bank_id, **_kwargs):
        rows = []
        for op in self.ops.get(bank_id, {}).values():
            rows.append({k: v for k, v in op.items() if k != "task_payload"})
        return {"operations": rows, "total": len(rows)}

    async def get_operation(self, *, bank_id, operation_id, include_payload=None):
        op = self.ops.get(bank_id, {}).get(operation_id)
        if op is None:
            return {"operation_id": operation_id, "status": "not_found"}
        if include_payload:
            return dict(op)
        return {k: v for k, v in op.items() if k != "task_payload"}

    async def list_tags(self, *, bank_id, **_kwargs):
        counts: dict[str, int] = {}
        for doc in self.docs.get(bank_id, {}).values():
            for tag in doc["tags"]:
                counts[tag] = counts.get(tag, 0) + 1
        items = [{"tag": t, "count": c} for t, c in counts.items()]
        return {"items": items, "total": len(items), "limit": 100, "offset": 0}

    async def bank_stats(self, *, bank_id, refresh=None):
        n = len(self.docs.get(bank_id, {}))
        return {
            "bank_id": bank_id,
            "total_nodes": n * 3,
            "total_documents": n,
            "pending_operations": 0,
            "failed_operations": 1,
        }

    async def get_memory(self, *, bank_id, memory_id):
        fact = self.memories.get(memory_id)
        if fact is None:
            raise _EngineNotFound()
        return dict(fact)

    async def memory_history(self, *, bank_id, memory_id):
        return {"items": [{"memory_id": memory_id}]}


def _seed(engine: _Engine, bank: str) -> None:
    engine.doc(bank, "a-priv", ALICE_PRIVATE, "alice's secret")
    engine.doc(bank, "a-shared", ["agent:alice", "topic:x"], "alice, shared")
    engine.doc(bank, "b-shared", ["agent:bob", "scope:global"], "bob, shared")
    engine.models[bank] = {
        "mm-whole": {"id": "mm-whole", "name": "Whole bank", "tags": [], "content": "all"},
        "mm-topic": {"id": "mm-topic", "name": "Topic", "tags": ["topic:x"], "content": "x"},
        "mm-alice": {
            "id": "mm-alice",
            "name": "Alice",
            "tags": ["agent:alice", "visibility:private"],
            "trigger": {"exclude_mental_models": True},
            "content": "alice only",
        },
    }
    engine.trees[bank] = {
        "roots": [
            {
                "id": "folder",
                "kind": "folder",
                "name": "Notes",
                "children": [
                    {"id": "p-whole", "kind": "page", "name": "Whole", "tags": []},
                    {"id": "p-alice", "kind": "page", "name": "Alice", "tags": ["agent:alice"]},
                ],
            }
        ]
    }
    engine.pages["p-whole"] = {"id": "p-whole", "name": "Whole", "markdown": "everything"}
    engine.pages["p-alice"] = {"id": "p-alice", "name": "Alice", "markdown": "alice"}
    engine.ops[bank] = {
        "op-a": {
            "operation_id": "op-a",
            "task_type": "retain",
            "status": "completed",
            "task_payload": {"contents": [{"content": "secret", "tags": ALICE_PRIVATE}]},
        },
        "op-b": {
            "operation_id": "op-b",
            "task_type": "retain",
            "status": "completed",
            "task_payload": {"contents": [{"content": "hello", "tags": ["agent:bob"]}]},
        },
        "op-batch": {
            "operation_id": "op-batch",
            "task_type": "batch_retain",
            "status": "completed",
            "child_operations": [{"operation_id": "op-a", "status": "completed"}],
        },
        "op-c": {"operation_id": "op-c", "task_type": "consolidation", "status": "completed"},
    }
    engine.memories["f-a"] = {"id": "f-a", "tags": ALICE_PRIVATE, "text": "secret"}
    engine.memories["f-b"] = {"id": "f-b", "tags": ["agent:bob"], "text": "hello"}


LAYOUTS = [
    pytest.param({"unified_bank": True, "bank": "shared"}, id="unified-shared"),
    pytest.param({"unified_bank": False, "bank": "hal0:private"}, id="one-private-bank"),
]


def _provider(layout: dict[str, Any]) -> tuple[HindsightProvider, _Engine, str]:
    engine = _Engine()
    bank = layout["bank"]
    _seed(engine, bank)
    return HindsightProvider(client=engine, **layout), engine, bank


OTHERS = [pytest.param("bob", id="second-agent"), pytest.param(None, id="unidentified")]
OTHERS_ALL = [*OTHERS, pytest.param("anonymous", id="anonymous"), pytest.param("unknown", id="unk")]


# ── documents ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", OTHERS_ALL)
async def test_documents_hide_another_agents_private_document(layout, caller):
    p, _engine, _bank = _provider(layout)
    listed = await p.list_documents(client_id=caller)
    assert {r["id"] for r in listed["items"]} == {"a-shared", "b-shared"}
    with pytest.raises(NotFound) as hidden:
        await p.get_document("a-priv", client_id=caller)
    with pytest.raises(NotFound) as absent:
        await p.get_document("no-such-doc", client_id=caller)
    # The same answer for a hidden document and a missing one.
    assert hidden.value.code == absent.value.code == "memory.document_not_found"
    shared = await p.get_document("b-shared", client_id=caller)
    assert shared["original_text"] == "bob, shared"


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_documents_owner_reads_own_private_document(layout):
    p, _engine, _bank = _provider(layout)
    listed = await p.list_documents(client_id="alice")
    assert {r["id"] for r in listed["items"]} == {"a-priv", "a-shared", "b-shared"}
    doc = await p.get_document("a-priv", client_id="alice")
    assert doc["original_text"] == "alice's secret"


@pytest.mark.asyncio
async def test_documents_per_agent_private_bank_owner_unaffected():
    """Per-bank layout: the owner's own private bank still reads in full."""
    engine = _Engine()
    engine.doc("private__alice", "a-priv", ALICE_PRIVATE)
    p = HindsightProvider(client=engine, unified_bank=False)
    listed = await p.list_documents(dataset="private:alice", client_id="alice")
    assert [r["id"] for r in listed["items"]] == ["a-priv"]
    assert (await p.get_document("a-priv", dataset="private:alice", client_id="alice"))["id"]


# ── mental models and knowledge pages ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", OTHERS)
async def test_mental_models_withheld_while_bank_holds_foreign_private(layout, caller):
    p, _engine, _bank = _provider(layout)
    listed = await p.list_mental_models(client_id=caller)
    assert listed["items"] == []
    for mm in ("mm-whole", "mm-topic", "mm-alice", "mm-missing"):
        with pytest.raises(NotFound) as exc:
            await p.get_mental_model(mm, client_id=caller)
        assert exc.value.code == "memory.mental_model_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_mental_models_owner_reads_all_when_only_own_private(layout):
    p, _engine, _bank = _provider(layout)
    listed = await p.list_mental_models(client_id="alice")
    assert {m["id"] for m in listed["items"]} == {"mm-whole", "mm-topic", "mm-alice"}
    assert (await p.get_mental_model("mm-alice", client_id="alice"))["content"] == "alice only"


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_cleared_model_keeps_no_reflect_response_for_agents(layout):
    """#3576: the engine's clear empties ``content`` and leaves
    ``reflect_response`` (the old text and its sources). Until a refresh
    writes new content, an agent read carries no reflect_response; that is
    what keeps a model the repair has cleared and flagged from showing its
    owner what it was built from before."""
    p, engine, bank = _provider(layout)
    engine.doc(bank, "b-priv", ["visibility:private", "agent:bob"])
    model = engine.models[bank]["mm-alice"]
    model["content"] = ""
    model["structured_content"] = None
    model["reflect_response"] = {"text": "bob's secret", "based_on": {"world": [{"id": "f"}]}}
    got = await p.get_mental_model("mm-alice", client_id="alice")
    assert got["content"] == "" and got["reflect_response"] is None
    listed = await p.list_mental_models(client_id="alice", detail="full")
    assert [m["reflect_response"] for m in listed["items"]] == [None]
    updated = await p.update_mental_model("mm-alice", client_id="alice", name="renamed")
    assert updated.get("reflect_response") is None

    # Once a refresh has written content, its reflect_response is its own.
    model["content"] = "alice only"
    model["reflect_response"] = {"text": "alice only"}
    got = await p.get_mental_model("mm-alice", client_id="alice")
    assert got["reflect_response"] == {"text": "alice only"}


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_mental_model_confined_to_owner_survives_foreign_private(layout):
    """Another agent's private note in the bank: alice keeps the model scoped
    to her own tag and loses the whole-bank ones."""
    p, engine, bank = _provider(layout)
    engine.doc(bank, "b-priv", ["visibility:private", "agent:bob"])
    listed = await p.list_mental_models(client_id="alice")
    assert {m["id"] for m in listed["items"]} == {"mm-alice"}


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_metadata_listing_judges_each_model_on_its_stored_trigger(layout):
    """#3584: the engine lists ``trigger: null`` at its default detail. The
    owner's flagged model is judged on its stored trigger, the listing keeps
    the caller's detail (no content), and a whole-bank model stays hidden
    whatever its trigger says."""
    p, engine, bank = _provider(layout)
    engine.doc(bank, "b-priv", ["visibility:private", "agent:bob"])
    engine.models[bank]["mm-whole"]["trigger"] = {"exclude_mental_models": True}
    listed = await p.list_mental_models(client_id="alice", detail="metadata")
    assert [m["id"] for m in listed["items"]] == ["mm-alice"]
    row = listed["items"][0]
    assert row["content"] is None
    assert row["trigger"] == {"exclude_mental_models": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_metadata_listing_reads_triggers_with_bounded_concurrency(layout):
    p, engine, bank = _provider(layout)
    engine.doc(bank, "b-priv", ["visibility:private", "agent:bob"])
    for i in range(40):
        engine.models[bank][f"mm-a{i}"] = {
            "id": f"mm-a{i}",
            "name": f"A{i}",
            "tags": ["agent:alice"],
            "trigger": {"exclude_mental_models": True},
            "content": "mine",
        }
    listed = await p.list_mental_models(client_id="alice")
    assert len(listed["items"]) == 41  # mm-alice plus the forty
    assert 1 < engine.max_gets_in_flight <= 8


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_metadata_listing_reads_no_trigger_without_foreign_private(layout):
    p, engine, _bank = _provider(layout)
    listed = await p.list_mental_models(client_id="alice")
    assert {m["id"] for m in listed["items"]} == {"mm-whole", "mm-topic", "mm-alice"}
    assert engine.model_gets == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_metadata_listing_drops_a_model_deleted_while_listing(layout):
    p, engine, bank = _provider(layout)
    engine.doc(bank, "b-priv", ["visibility:private", "agent:bob"])
    real = engine.list_mental_models

    async def listing_then_delete(**kw):
        out = await real(**kw)
        del engine.models[bank]["mm-alice"]
        return out

    engine.list_mental_models = listing_then_delete
    assert (await p.list_mental_models(client_id="alice"))["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", [*OTHERS, "alice"])
async def test_mental_models_unaffected_without_private_memory(layout, caller):
    p, engine, bank = _provider(layout)
    del engine.docs[bank]["a-priv"]
    listed = await p.list_mental_models(client_id=caller)
    expected = {"mm-whole", "mm-topic"} | ({"mm-alice"} if caller == "alice" else set())
    assert {m["id"] for m in listed["items"]} == expected
    assert (await p.get_mental_model("mm-whole", client_id=caller))["content"] == "all"


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", OTHERS)
async def test_knowledge_pages_withheld_from_other_callers(layout, caller):
    p, _engine, _bank = _provider(layout)
    tree = await p.knowledge_base_tree(client_id=caller)
    folder = tree["roots"][0]
    assert folder["id"] == "folder" and folder["children"] == []
    for page in ("p-whole", "p-alice", "p-missing"):
        with pytest.raises(NotFound) as exc:
            await p.knowledge_base_page(page, client_id=caller)
        assert exc.value.code == "memory.knowledge_page_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_knowledge_pages_owner_reads(layout):
    p, _engine, _bank = _provider(layout)
    tree = await p.knowledge_base_tree(client_id="alice")
    assert {c["id"] for c in tree["roots"][0]["children"]} == {"p-whole", "p-alice"}
    assert (await p.knowledge_base_page("p-alice", client_id="alice"))["markdown"] == "alice"


def test_model_scope_confinement_rules():
    own = "agent:alice"
    ex = {"exclude_mental_models": True}

    def m(tags, **trigger):
        return {"tags": tags, "trigger": {**ex, **trigger}}

    assert model_scope_confined_to(m([own, "visibility:private"]), "alice")
    assert model_scope_confined_to(m([own], tags_match="any_strict"), "alice")
    assert model_scope_confined_to(m([own, "topic:x"], tags_match="exact"), "alice")
    # Non-strict matches admit untagged memories and untagged mental models.
    assert not model_scope_confined_to(m([own], tags_match="all"), "alice")
    assert not model_scope_confined_to(m([own], tags_match="any"), "alice")
    assert not model_scope_confined_to(m([own, "topic:x"], tags_match="any_strict"), "alice")
    assert not model_scope_confined_to(m([]), "alice")
    assert not model_scope_confined_to(m([own]), "bob")
    assert not model_scope_confined_to(m(["agent:unknown"]), "unknown")
    # Without exclude_mental_models a refresh also reads the other models.
    assert not model_scope_confined_to({"tags": [own, "visibility:private"]}, "alice")
    assert not model_scope_confined_to(
        {"tags": [own], "trigger": {"exclude_mental_models": False}}, "alice"
    )
    # tag_groups replace the flat tags; a leaf defaults to any_strict.
    assert not model_scope_confined_to(m([own], tag_groups=[{"tags": ["topic:x"]}]), "alice")
    assert model_scope_confined_to(m([], tag_groups=[{"tags": [own]}]), "alice")
    assert model_scope_confined_to(
        m([], tag_groups=[{"tags": [own], "match": "all_strict"}]), "alice"
    )
    assert not model_scope_confined_to(m([], tag_groups=[{"tags": [own], "match": "all"}]), "alice")
    assert not model_scope_confined_to(m([], tag_groups=[{"tags": [own], "match": "any"}]), "alice")


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("match", ["all", "any"])
@pytest.mark.parametrize("where", ["tags_match", "leaf"])
async def test_pre_existing_non_strict_model_is_not_served(layout, match, where):
    """bob's model scoped ``agent:bob`` with a non-strict match also drew on
    untagged whole-bank models; with alice's private note in the bank it is
    not bob's to read."""
    p, engine, bank = _provider(layout)
    trigger: dict[str, Any] = {"exclude_mental_models": True}
    if where == "tags_match":
        trigger["tags_match"] = match
    else:
        trigger["tag_groups"] = [{"tags": ["agent:bob"], "match": match}]
    engine.models[bank]["mm-bob"] = {
        "id": "mm-bob",
        "tags": ["agent:bob"],
        "trigger": trigger,
        "content": "drew on working-context",
    }
    assert (await p.list_mental_models(client_id="bob"))["items"] == []
    with pytest.raises(NotFound):
        await p.get_mental_model("mm-bob", client_id="bob")


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_agent_created_and_retriggered_models_exclude_other_models(layout):
    p, engine, bank = _provider(layout)
    await p.create_mental_model(
        name="n",
        source_query="q",
        id="mm-new",
        client_id="alice",
        tags=["agent:alice"],
        trigger={"exclude_mental_models": False, "tags_match": "all_strict"},
    )
    assert engine.models[bank]["mm-new"]["trigger"]["exclude_mental_models"] is True
    out = await p.update_mental_model(
        "mm-alice", client_id="alice", trigger={"fact_types": ["world"]}
    )
    assert out["trigger"] == {"exclude_mental_models": True, "fact_types": ["world"]}


# ── tags ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", OTHERS)
async def test_tags_only_on_foreign_private_documents_are_withheld(layout, caller):
    p, _engine, _bank = _provider(layout)
    tags = {r["tag"] for r in (await p.list_tags(client_id=caller))["items"]}
    assert "topic:secret" not in tags
    assert "visibility:private" not in tags
    # agent:alice is also on alice's shared document, so it stays.
    assert {"agent:alice", "topic:x", "agent:bob", "scope:global"} <= tags


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_tags_owner_sees_own_private_tags(layout):
    p, _engine, _bank = _provider(layout)
    tags = {r["tag"] for r in (await p.list_tags(client_id="alice"))["items"]}
    assert {"topic:secret", "visibility:private"} <= tags


# ── operations ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", OTHERS)
async def test_operations_over_foreign_private_memory_are_hidden(layout, caller):
    p, _engine, _bank = _provider(layout)
    listed = await p.list_operations(client_id=caller)
    assert {o["operation_id"] for o in listed["operations"]} == {"op-b", "op-c"}
    for op_id in ("op-a", "op-batch"):
        hidden = await p.get_operation(op_id, client_id=caller, include_payload=True)
        missing = await p.get_operation("op-missing", client_id=caller)
        assert hidden == {"operation_id": op_id, "status": "not_found"}
        assert missing == {"operation_id": "op-missing", "status": "not_found"}
    shared = await p.get_operation("op-b", client_id=caller)
    assert shared["status"] == "completed"
    assert "task_payload" not in shared


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_operations_owner_polls_own_private_retain(layout):
    p, _engine, _bank = _provider(layout)
    listed = await p.list_operations(client_id="alice")
    assert {o["operation_id"] for o in listed["operations"]} == {"op-a", "op-b", "op-batch", "op-c"}
    op = await p.get_operation("op-a", client_id="alice")
    assert op["status"] == "completed" and "task_payload" not in op
    with_payload = await p.get_operation("op-a", client_id="alice", include_payload=True)
    assert with_payload["task_payload"]["contents"][0]["content"] == "secret"


# ── bank stats ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", OTHERS)
async def test_bank_stats_counts_withheld_from_other_callers(layout, caller):
    p, _engine, bank = _provider(layout)
    stats = await p.bank_stats(client_id=caller)
    assert stats == {
        "bank_id": bank,
        "pending_operations": 0,
        "failed_operations": 1,
        "counts_withheld": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_bank_stats_full_for_owner_and_without_private_memory(layout):
    p, engine, bank = _provider(layout)
    assert (await p.bank_stats(client_id="alice"))["total_documents"] == 3
    del engine.docs[bank]["a-priv"]
    assert (await p.bank_stats(client_id="bob"))["total_documents"] == 2


# ── memory history ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", OTHERS)
async def test_memory_history_of_foreign_private_memory_is_404(layout, caller):
    p, _engine, _bank = _provider(layout)
    with pytest.raises(NotFound) as hidden:
        await p.memory_history("f-a", client_id=caller)
    with pytest.raises(NotFound) as absent:
        await p.memory_history("f-missing", client_id=caller)
    assert hidden.value.code == absent.value.code == "memory.memory_not_found"
    assert (await p.memory_history("f-b", client_id=caller))["items"]


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_memory_history_owner_reads(layout):
    p, _engine, _bank = _provider(layout)
    assert (await p.memory_history("f-a", client_id="alice"))["items"]


# ── review round: writes on hidden models, scopes, counts ─────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", OTHERS)
async def test_mental_model_writes_on_a_hidden_model_are_404(layout, caller):
    """An update with no fields would return the model's content; re-tagging
    it to the caller would make that content pass the read rule."""
    p, engine, bank = _provider(layout)
    for mm in ("mm-whole", "mm-alice"):
        for write in (
            p.update_mental_model(mm, client_id=caller),
            p.update_mental_model(mm, client_id=caller, tags=[f"agent:{caller}"]),
            p.refresh_mental_model(mm, client_id=caller),
            p.delete_mental_model(mm, client_id=caller),
        ):
            with pytest.raises(NotFound) as exc:
                await write
            assert exc.value.code == "memory.mental_model_not_found"
    assert engine.updated == [] and engine.refreshed == []
    assert set(engine.models[bank]) == {"mm-whole", "mm-topic", "mm-alice"}


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_mental_model_writes_on_a_readable_model_work(layout):
    p, engine, _bank = _provider(layout)
    out = await p.update_mental_model("mm-whole", client_id="alice", name="Renamed")
    assert out["name"] == "Renamed" and out["content"] == "all"
    await p.refresh_mental_model("mm-whole", client_id="alice")
    assert engine.refreshed == ["mm-whole"]
    await p.delete_mental_model("mm-topic", client_id="alice")


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_mental_model_rescope_refused_while_foreign_private_present(layout):
    """Content written under the old scope must not reach a caller through a
    new one: with bob's private note in the bank, alice may not widen her
    own model, and a confined-to-confined change is still allowed."""
    from hal0.errors import BadRequest

    p, engine, bank = _provider(layout)
    engine.doc(bank, "b-priv", ["visibility:private", "agent:bob"])
    with pytest.raises(BadRequest) as exc:
        await p.update_mental_model("mm-alice", client_id="alice", tags=["topic:x"])
    assert exc.value.code == "memory.mental_model_rescope_refused"
    with pytest.raises(BadRequest):
        await p.update_mental_model(
            "mm-alice", client_id="alice", trigger={"tags_match": "any", "tag_groups": None}
        )
    assert engine.updated == []
    out = await p.update_mental_model(
        "mm-alice", client_id="alice", tags=["agent:alice", "topic:x"]
    )
    assert out["content"] == "alice only"


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_mental_model_update_answer_withholds_content_it_now_hides(layout):
    p, _engine, _bank = _provider(layout)
    out = await p.update_mental_model(
        "mm-whole", client_id="alice", tags=["visibility:private", "agent:bob"]
    )
    assert out["id"] == "mm-whole" and "content" not in out


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_empty_tag_groups_scope_is_the_whole_bank(layout):
    """The engine treats ``tag_groups: []`` as set and filtering nothing."""
    from hal0.errors import BadRequest

    p, engine, bank = _provider(layout)
    engine.models[bank]["mm-bob"] = {
        "id": "mm-bob",
        "tags": ["agent:bob"],
        "trigger": {"tag_groups": []},
        "content": "built from the whole bank",
    }
    with pytest.raises(NotFound):
        await p.get_mental_model("mm-bob", client_id="bob")
    for bad in ([], {}, "x"):
        with pytest.raises(BadRequest) as exc:
            await p.create_mental_model(
                name="n",
                source_query="q",
                client_id="bob",
                tags=["agent:bob"],
                trigger={"tag_groups": bad},
            )
        assert exc.value.code == "memory.mental_model_scope_invalid"
    assert not model_scope_confined_to(
        {"tags": ["agent:bob"], "trigger": {"tag_groups": []}}, "bob"
    )
    assert not model_scope_confined_to(
        {"tags": ["agent:bob"], "trigger": {"tag_groups": "x"}}, "bob"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", OTHERS)
async def test_listing_totals_do_not_count_hidden_rows(layout, caller):
    """A caller-chosen filter that matches only another agent's private
    document reads like one that matches nothing."""
    p, _engine, _bank = _provider(layout)
    docs = await p.list_documents(client_id=caller, tags=["topic:secret"])
    assert docs["items"] == [] and docs["total"] == 0
    everything = await p.list_documents(client_id=caller, limit=2)
    assert [r["id"] for r in everything["items"]] == ["a-shared", "b-shared"]
    assert everything["total"] == 3  # a full page, plus one
    models = await p.list_mental_models(client_id=caller)
    assert models.get("total", 0) == 0
    tags = await p.list_tags(client_id=caller)
    assert tags["total"] == len(tags["items"])
    ops = await p.list_operations(client_id=caller)
    assert ops["total"] == len(ops["operations"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", OTHERS)
async def test_cancel_and_retry_of_a_hidden_operation_are_404(layout, caller):
    p, engine, _bank = _provider(layout)
    for op_id in ("op-a", "op-batch", "op-missing"):
        for write in (
            p.cancel_operation(op_id, client_id=caller),
            p.retry_operation(op_id, client_id=caller),
        ):
            with pytest.raises(NotFound) as exc:
                await write
            assert exc.value.code == "memory.operation_not_found"
    assert engine.cancelled == [] and engine.retried == []
    await p.cancel_operation("op-b", client_id=caller)
    await p.retry_operation("op-c", client_id=caller)
    assert engine.cancelled == ["op-b"] and engine.retried == ["op-c"]


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_bank_export_and_import_operations_are_hidden(layout):
    """An export or import carries the whole bank's content and names no
    owner, so it is hidden from every agent (the admin API still shows it)."""
    p, engine, bank = _provider(layout)
    for op_type in ("export_bank", "import_bank", "export_documents", "import_documents"):
        engine.ops[bank][op_type] = {
            "operation_id": op_type,
            "task_type": op_type,
            "status": "completed",
        }
    for caller in ("alice", "bob", None):
        listed = await p.list_operations(client_id=caller)
        assert not {o["operation_id"] for o in listed["operations"]} & {
            "export_bank",
            "import_bank",
            "export_documents",
            "import_documents",
        }


# ── through the MCP dispatcher ───────────────────────────────────────────────


def _dispatcher(p: HindsightProvider, client_id: str | None):
    return make_dispatcher(p, client_id_resolver=lambda: client_id or "anonymous")


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("caller", OTHERS)
async def test_mcp_tools_hold_back_foreign_private_memory(layout, caller):
    p, _engine, _bank = _provider(layout)
    call = _dispatcher(p, caller)

    listed = await call("memory_documents_list", {})
    assert listed["status"] == "ok"
    assert "a-priv" not in {r["id"] for r in listed["items"]}

    hidden = await call("memory_documents_get", {"document_id": "a-priv"})
    absent = await call("memory_documents_get", {"document_id": "no-such-doc"})
    assert hidden["status"] == absent["status"] == "error"
    assert hidden["error"]["code"] == absent["error"]["code"] == "memory.document_not_found"

    assert (await call("memory_mental_model_list", {}))["items"] == []
    mm = await call("memory_mental_model_get", {"id": "mm-alice"})
    assert mm["error"]["code"] == "memory.mental_model_not_found"

    tree = await call("memory_kb_tree", {})
    assert tree["roots"][0]["children"] == []
    page = await call("memory_kb_read", {"page_id": "p-alice"})
    assert page["error"]["code"] == "memory.knowledge_page_not_found"

    tags = {r["tag"] for r in (await call("memory_tags_list", {}))["items"]}
    assert "topic:secret" not in tags

    ops = await call("memory_operation_list", {})
    assert "op-a" not in {o["operation_id"] for o in ops["operations"]}
    op = await call("memory_operation_get", {"id": "op-a", "include_payload": True})
    assert op["operation_status"] == "not_found" and "task_payload" not in op

    stats = await call("memory_bank_stats", {})
    assert stats["counts_withheld"] is True and "total_documents" not in stats

    history = await call("memory_history", {"id": "f-a"})
    assert history["error"]["code"] == "memory.memory_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_mcp_tools_owner_reads_own_private_memory(layout):
    p, _engine, _bank = _provider(layout)
    call = _dispatcher(p, "alice")
    doc = await call("memory_documents_get", {"document_id": "a-priv"})
    assert doc["original_text"] == "alice's secret"
    assert (await call("memory_mental_model_get", {"id": "mm-alice"}))["content"] == "alice only"
    assert (await call("memory_kb_read", {"page_id": "p-alice"}))["markdown"] == "alice"
    op = await call("memory_operation_get", {"id": "op-a"})
    assert op["operation_status"] == "completed"
    assert (await call("memory_bank_stats", {}))["total_documents"] == 3


# ── the real two-bank layout (ADR-0058 decision 7) ────────────────────────────
#
# A provider built the way a new store's layout record builds it: ``shared`` is
# ``hal0:shared`` and every ``private:<agent>`` is ``hal0:private``, one bank for
# all agents. A read of ``private:<self>`` goes to that bank, where a row is its
# owner's alone whatever else it is tagged: so even a row that lost its
# ``visibility:private`` tag, or has no ``agent:`` tag, stays hidden.

TWO_SHARED, TWO_PRIVATE = "hal0:shared", "hal0:private"


def _two_bank() -> tuple[HindsightProvider, _Engine]:
    engine = _Engine()
    _seed(engine, TWO_PRIVATE)
    # Rows only the private bank's own rule catches: owned but untagged as
    # private, and private with no owner.
    engine.doc(TWO_PRIVATE, "a-untagged", ["agent:alice", "topic:y"], "alice, no private tag")
    engine.doc(TWO_PRIVATE, "nobody", ["visibility:private"], "no owner")
    engine.doc(TWO_PRIVATE, "b-own", ["visibility:private", "agent:bob"], "bob's own note")
    engine.doc(TWO_SHARED, "s-1", ["scope:global"], "shared note")
    engine.memories["f-untagged"] = {"id": "f-untagged", "tags": ["agent:alice"], "text": "x"}
    engine.ops[TWO_PRIVATE]["op-untagged"] = {
        "operation_id": "op-untagged",
        "task_type": "retain",
        "status": "completed",
        "task_payload": {"contents": [{"content": "alice", "tags": ["agent:alice"]}]},
    }
    p = HindsightProvider(client=engine, two_bank=True, bank=TWO_SHARED, private_bank=TWO_PRIVATE)
    return p, engine


@pytest.mark.asyncio
async def test_two_bank_private_documents_of_another_agent_stay_hidden():
    p, _engine = _two_bank()
    ns = "private:bob"
    listed = await p.list_documents(dataset=ns, client_id="bob")
    assert {r["id"] for r in listed["items"]} == {"b-own", "b-shared"}  # b-shared carries agent:bob
    for doc_id in ("a-priv", "a-untagged", "nobody", "a-shared"):
        with pytest.raises(NotFound):
            await p.get_document(doc_id, dataset=ns, client_id="bob")
    assert (await p.get_document("b-own", dataset=ns, client_id="bob"))["id"] == "b-own"
    # The shared bank is unaffected.
    shared = await p.list_documents(client_id="bob")
    assert {r["id"] for r in shared["items"]} == {"s-1"}


@pytest.mark.asyncio
async def test_two_bank_owner_reads_own_private_documents():
    p, _engine = _two_bank()
    listed = await p.list_documents(dataset="private:alice", client_id="alice")
    assert {r["id"] for r in listed["items"]} == {"a-priv", "a-shared", "a-untagged"}


@pytest.mark.asyncio
async def test_two_bank_operations_of_another_agent_stay_hidden():
    p, _engine = _two_bank()
    ns = "private:bob"
    listed = await p.list_operations(dataset=ns, client_id="bob")
    assert {o["operation_id"] for o in listed["operations"]} == {
        "op-b",
        "op-c",
    }  # op-b is bob's own
    for op_id in ("op-a", "op-batch", "op-untagged"):
        got = await p.get_operation(op_id, dataset=ns, client_id="bob", include_payload=True)
        assert got == {"operation_id": op_id, "status": "not_found"}
        with pytest.raises(NotFound):
            await p.cancel_operation(op_id, dataset=ns, client_id="bob")


@pytest.mark.asyncio
async def test_two_bank_mental_models_built_from_other_agents_stay_hidden():
    """The private bank holds other agents' private memory once a second agent
    writes there: only a model confined to the caller is read."""
    p, _engine = _two_bank()
    ns = "private:bob"
    listed = await p.list_mental_models(dataset=ns, client_id="bob")
    assert [m["id"] for m in listed.get("items", [])] == []
    for mm in ("mm-whole", "mm-topic", "mm-alice"):
        with pytest.raises(NotFound):
            await p.get_mental_model(mm, dataset=ns, client_id="bob")
        with pytest.raises(NotFound):
            await p.refresh_mental_model(mm, dataset=ns, client_id="bob")
    stats = await p.bank_stats(dataset=ns, client_id="bob")
    assert stats["counts_withheld"] is True


@pytest.mark.asyncio
async def test_two_bank_memory_history_of_another_agent_is_404():
    p, _engine = _two_bank()
    for memory_id in ("f-a", "f-untagged"):
        with pytest.raises(NotFound):
            await p.memory_history(memory_id, dataset="private:bob", client_id="bob")
    owned = await p.memory_history("f-untagged", dataset="private:alice", client_id="alice")
    assert owned["items"]


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", [None, "anonymous", "unknown"])
async def test_two_bank_unidentified_caller_reads_nothing_private(caller):
    p, _engine = _two_bank()
    for doc_id in ("a-priv", "nobody", "b-own"):
        with pytest.raises(NotFound):
            await p.get_document(doc_id, dataset="private:bob", client_id=caller)


# ── two-bank isolation, the remaining read tools (final review of #3572) ─────


class _DirectiveEngine(_Engine):
    """The engine double plus directives, filtered the way the engine filters
    a listing (``tags`` with its match; no filter returns every directive)."""

    def __init__(self) -> None:
        super().__init__()
        self.directives: dict[str, dict[str, dict[str, Any]]] = {}
        self.deleted_directives: list[str] = []

    def directive(self, bank: str, d_id: str, tags: list[str], content: str = "rule") -> None:
        self.directives.setdefault(bank, {})[d_id] = {
            "id": d_id,
            "name": d_id,
            "content": content,
            "tags": list(tags),
            "is_active": True,
        }

    async def list_directives(self, *, bank_id, tags=None, tags_match=None, **_kw):
        rows = list(self.directives.get(bank_id, {}).values())
        if tags:
            rows = [r for r in rows if set(tags) & set(r["tags"])]
        return {"items": [dict(r) for r in rows], "total": len(rows)}

    async def get_directive(self, *, bank_id, directive_id):
        row = self.directives.get(bank_id, {}).get(directive_id)
        if row is None:
            raise _EngineNotFound()
        return dict(row)

    async def create_directive(self, *, bank_id, name, content, tags=None, **_kw):
        self.directive(bank_id, name, list(tags or []), content)
        return dict(self.directives[bank_id][name])

    async def update_directive(self, *, bank_id, directive_id, **kwargs):
        row = self.directives.get(bank_id, {}).get(directive_id)
        if row is None:
            raise _EngineNotFound()
        row.update({k: v for k, v in kwargs.items() if v is not None})
        return dict(row)

    async def delete_directive(self, *, bank_id, directive_id):
        if self.directives.get(bank_id, {}).pop(directive_id, None) is None:
            raise _EngineNotFound()
        self.deleted_directives.append(directive_id)
        return {"success": True}


def _two_bank_full() -> tuple[HindsightProvider, _DirectiveEngine]:
    engine = _DirectiveEngine()
    _seed(engine, TWO_PRIVATE)
    engine.doc(TWO_PRIVATE, "a-untagged", ["agent:alice", "topic:only-alice"], "alice")
    engine.doc(TWO_PRIVATE, "b-own", ["visibility:private", "agent:bob", "topic:bob"], "bob")
    engine.trees[TWO_PRIVATE]["roots"][0]["children"].append(
        {"id": "p-bob", "kind": "page", "name": "Bob", "tags": ["agent:bob", "visibility:private"]}
    )
    engine.pages["p-bob"] = {"id": "p-bob", "name": "Bob", "markdown": "bob"}
    engine.directive(TWO_PRIVATE, "d-alice", ["agent:alice", "visibility:private"], "alice rule")
    engine.directive(TWO_PRIVATE, "d-nobody", [], "no owner")
    engine.directive(TWO_PRIVATE, "d-bob", ["agent:bob", "visibility:private"], "bob rule")
    engine.directive(TWO_SHARED, "d-shared", [], "everyone")
    p = HindsightProvider(client=engine, two_bank=True, bank=TWO_SHARED, private_bank=TWO_PRIVATE)
    return p, engine


@pytest.mark.asyncio
async def test_two_bank_tags_of_another_agents_private_memory_stay_hidden():
    p, _engine = _two_bank_full()
    tags = {r["tag"] for r in (await p.list_tags(dataset="private:bob", client_id="bob"))["items"]}
    assert "topic:secret" not in tags  # alice's private document
    assert "topic:only-alice" not in tags  # alice's row with no private tag
    assert "topic:bob" in tags


@pytest.mark.asyncio
async def test_two_bank_knowledge_pages_of_another_agent_stay_hidden():
    p, _engine = _two_bank_full()
    tree = await p.knowledge_base_tree(dataset="private:bob", client_id="bob")
    pages = {c["id"] for root in tree["roots"] for c in root.get("children", [])}
    assert "p-alice" not in pages and "p-whole" not in pages
    for page_id in ("p-alice", "p-whole"):
        with pytest.raises(NotFound):
            await p.knowledge_base_page(page_id, dataset="private:bob", client_id="bob")


@pytest.mark.asyncio
async def test_two_bank_mental_model_writes_on_another_agents_model_are_404():
    p, engine = _two_bank_full()
    for mm in ("mm-whole", "mm-topic", "mm-alice"):
        with pytest.raises(NotFound):
            await p.update_mental_model(mm, dataset="private:bob", client_id="bob", name="x")
        with pytest.raises(NotFound):
            await p.delete_mental_model(mm, dataset="private:bob", client_id="bob")
    assert engine.updated == []
    assert set(engine.models[TWO_PRIVATE]) == {"mm-whole", "mm-topic", "mm-alice"}


@pytest.mark.asyncio
async def test_two_bank_directives_are_their_writers_own():
    p, engine = _two_bank_full()
    ns = "private:bob"
    listed = await p.list_directives(dataset=ns, client_id="bob")
    assert [d["id"] for d in listed["items"]] == ["d-bob"]
    assert listed["total"] == 1
    for d_id in ("d-alice", "d-nobody", "d-missing"):
        with pytest.raises(NotFound) as refused:
            await p.get_directive(d_id, dataset=ns, client_id="bob")
        assert refused.value.code == "memory.directive_not_found"
        with pytest.raises(NotFound):
            await p.update_directive(d_id, dataset=ns, client_id="bob", content="hijack")
        with pytest.raises(NotFound):
            await p.delete_directive(d_id, dataset=ns, client_id="bob")
    assert engine.directives[TWO_PRIVATE]["d-alice"]["content"] == "alice rule"
    assert engine.deleted_directives == []
    assert (await p.get_directive("d-bob", dataset=ns, client_id="bob"))["content"] == "bob rule"
    # The shared bank's directives are everyone's, as before.
    shared = await p.list_directives(client_id="bob")
    assert [d["id"] for d in shared["items"]] == ["d-shared"]


@pytest.mark.asyncio
async def test_two_bank_directive_writes_are_stamped_with_their_owner():
    p, engine = _two_bank_full()
    ns = "private:bob"
    await p.create_directive(
        name="d-new",
        content="be brief",
        dataset=ns,
        client_id="bob",
        tags=["topic:style", "agent:alice", "visibility:shared"],
    )
    assert engine.directives[TWO_PRIVATE]["d-new"]["tags"] == [
        "topic:style",
        "agent:bob",
        "visibility:private",
    ]
    # Re-tagging cannot make a directive bank-wide or another agent's.
    await p.update_directive("d-bob", dataset=ns, client_id="bob", tags=[])
    assert engine.directives[TWO_PRIVATE]["d-bob"]["tags"] == ["agent:bob", "visibility:private"]
    # Another agent's directive never shows up in this one's listing.
    alice = await p.list_directives(dataset="private:alice", client_id="alice")
    assert {d["id"] for d in alice["items"]} == {"d-alice"}


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", [None, "anonymous", "unknown"])
async def test_two_bank_private_directive_needs_an_owner(caller):
    from hal0.errors import Forbidden

    p, engine = _two_bank_full()
    with pytest.raises(Forbidden):
        await p.create_directive(name="d-x", content="x", dataset="private:bob", client_id=caller)
    assert "d-x" not in engine.directives[TWO_PRIVATE]


@pytest.mark.asyncio
async def test_two_bank_private_reflect_is_held_to_the_callers_directives():
    """The engine loads a reflect's directives through its ``tag_groups``
    (untagged ones always, tagged ones only inside the groups), and a
    private-bank reflect is held to the caller's ``agent:`` tag: another
    agent's private directive is outside it."""
    from hal0.memory.read_scope import tags_readable

    calls: list[dict[str, Any]] = []

    class _Reflecting(_DirectiveEngine):
        async def reflect(self, **kwargs):
            calls.append(kwargs)
            return {"text": "ok"}

    engine = _Reflecting()
    p = HindsightProvider(client=engine, two_bank=True, bank=TWO_SHARED, private_bank=TWO_PRIVATE)
    await p.reflect("q", dataset="private:bob", client_id="bob")
    groups = calls[0]["tag_groups"]
    assert not tags_readable(groups, ["agent:alice", "visibility:private"])
    assert tags_readable(groups, ["agent:bob", "visibility:private"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "layout",
    [
        {"two_bank": True, "bank": TWO_SHARED, "private_bank": TWO_PRIVATE},
        {"unified_bank": True},
        {"unified_bank": False},
    ],
    ids=["two-bank", "unified", "split"],
)
async def test_no_caller_can_plant_a_directive_as_another_agents(layout):
    """Delta review of #3572: on the platform bank a caller's agent:/visibility:
    tags were kept, so bob could write a directive that alice's reflect scope
    (not private OR agent:alice) loads as hers. They are dropped on every bank,
    and the writer is stamped as the owner (#3600)."""
    engine = _DirectiveEngine()
    p = HindsightProvider(client=engine, **layout)
    forged = ["agent:alice", "visibility:private", "Visibility:Private", "topic:x"]
    await p.create_directive(name="d-forged", content="obey bob", client_id="bob", tags=forged)
    [platform] = engine.directives
    own = ["topic:x", "agent:bob", "visibility:private"]
    assert engine.directives[platform]["d-forged"]["tags"] == own
    await p.update_directive("d-forged", client_id="bob", tags=forged)
    assert engine.directives[platform]["d-forged"]["tags"] == own
    # Left untouched when the update sends no tags.
    await p.update_directive("d-forged", client_id="bob", content="still bob's")
    assert engine.directives[platform]["d-forged"]["tags"] == own
    # Alice never sees it as hers, or at all.
    listed = await p.list_directives(client_id="alice")
    assert listed["items"] == []


# ── directive owner rule, every layout (#3600) ─────────────────────────────
#
# A rehearsal on a legacy unified store found another agent rewriting
# hermes's directive and an anonymous caller planting a bank-wide one that
# steered hermes's reflect. Through the provider (both MCP mounts) a directive
# is its writer's on every bank. (layout, dataset) below covers the unified,
# split and two-bank layouts, split and two-bank on both kinds of bank.

_TWO = {"two_bank": True, "bank": TWO_SHARED, "private_bank": TWO_PRIVATE}
DIRECTIVE_LAYOUTS = [
    pytest.param({"unified_bank": True}, "shared", id="unified"),
    pytest.param({"unified_bank": False}, "shared", id="split-shared"),
    pytest.param({"unified_bank": False}, "private:hermes", id="split-private"),
    pytest.param(_TWO, "shared", id="two-bank-shared"),
    pytest.param(_TWO, "private:hermes", id="two-bank-private"),
]
#: The layouts whose bank holds bank-wide directives every agent reflects on.
SHARED_DIRECTIVE_LAYOUTS = [
    pytest.param({"unified_bank": True}, id="unified"),
    pytest.param({"unified_bank": False}, id="split-shared"),
    pytest.param(_TWO, id="two-bank-shared"),
]
UNIDENTIFIED = [None, "anonymous", "unknown"]


class _ReflectingDirectiveEngine(_DirectiveEngine):
    def __init__(self) -> None:
        super().__init__()
        self.reflects: list[dict[str, Any]] = []

    async def reflect(self, **kwargs):
        self.reflects.append(kwargs)
        return {"text": "ok"}


def _other_dataset(dataset: str) -> str:
    """What another agent addresses: its own private namespace in place of
    hermes's (it cannot name ``private:hermes``), the same bank otherwise."""
    return "private:claude-code" if dataset.startswith("private:") else dataset


async def _hermes_directive(layout: dict[str, Any], dataset: str):
    engine = _ReflectingDirectiveEngine()
    p = HindsightProvider(client=engine, **layout)
    await p.create_directive(
        name="d-hermes", content="hermes rule", dataset=dataset, client_id="hermes"
    )
    [bank] = engine.directives
    return p, engine, bank


@pytest.mark.asyncio
@pytest.mark.parametrize(("layout", "dataset"), DIRECTIVE_LAYOUTS)
async def test_a_directive_is_stamped_with_its_writer(layout, dataset):
    p, engine, bank = await _hermes_directive(layout, dataset)
    assert engine.directives[bank]["d-hermes"]["tags"] == ["agent:hermes", "visibility:private"]
    forged = ["agent:alice", "Visibility:Shared", "topic:x"]
    await p.update_directive("d-hermes", dataset=dataset, client_id="hermes", tags=forged)
    assert engine.directives[bank]["d-hermes"]["tags"] == [
        "topic:x",
        "agent:hermes",
        "visibility:private",
    ]
    # Re-tagging to nothing cannot make it bank-wide.
    await p.update_directive("d-hermes", dataset=dataset, client_id="hermes", tags=[])
    assert engine.directives[bank]["d-hermes"]["tags"] == ["agent:hermes", "visibility:private"]
    await p.update_directive("d-hermes", dataset=dataset, client_id="hermes", content="v2")
    assert engine.directives[bank]["d-hermes"]["content"] == "v2"
    await p.delete_directive("d-hermes", dataset=dataset, client_id="hermes")
    assert engine.deleted_directives == ["d-hermes"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("layout", "dataset"), DIRECTIVE_LAYOUTS)
async def test_another_agents_directive_is_404(layout, dataset):
    """The rehearsal: claude-code's update rewrote hermes's directive."""
    p, engine, bank = await _hermes_directive(layout, dataset)
    other = _other_dataset(dataset)
    listed = await p.list_directives(dataset=other, client_id="claude-code")
    assert "d-hermes" not in {d["id"] for d in listed["items"]}
    for call in (
        p.get_directive("d-hermes", dataset=other, client_id="claude-code"),
        p.update_directive("d-hermes", dataset=other, client_id="claude-code", content="x"),
        p.delete_directive("d-hermes", dataset=other, client_id="claude-code"),
    ):
        with pytest.raises(NotFound) as refused:
            await call
        assert refused.value.code == "memory.directive_not_found"
    assert engine.directives[bank]["d-hermes"]["content"] == "hermes rule"
    assert engine.deleted_directives == []
    own = await p.list_directives(dataset=dataset, client_id="hermes")
    assert {d["id"] for d in own["items"]} == {"d-hermes"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("layout", "dataset"), DIRECTIVE_LAYOUTS)
@pytest.mark.parametrize("caller", UNIDENTIFIED)
async def test_an_unidentified_caller_writes_no_directive(layout, dataset, caller):
    from hal0.errors import Forbidden

    p, engine, bank = await _hermes_directive(layout, dataset)
    for call in (
        p.create_directive(name="d-anon", content="obey", client_id=caller),
        p.update_directive("d-hermes", client_id=caller, content="x"),
        p.delete_directive("d-hermes", client_id=caller),
    ):
        with pytest.raises(Forbidden) as refused:
            await call
        assert refused.value.code == "memory.directive_owner_required"
        assert refused.value.status == 403
    assert all("d-anon" not in rows for rows in engine.directives.values())
    assert engine.directives[bank]["d-hermes"]["content"] == "hermes rule"
    assert engine.deleted_directives == []


# Not split-private: there each agent's directives sit in its own bank, which
# no other agent's reflect reads, so a "planted" row there cannot be compared
# with hermes's scope (the two never share a bank).
STEER_LAYOUTS = [c for c in DIRECTIVE_LAYOUTS if c.id != "split-private"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("layout", "dataset"), STEER_LAYOUTS)
async def test_another_agents_directive_does_not_steer_hermes_reflect(layout, dataset):
    """The rehearsal: a directive another agent created applied to hermes's
    reflect. Now it carries its writer, outside hermes's reflect scope."""
    from hal0.memory.read_scope import tags_readable

    p, engine, _bank = await _hermes_directive(layout, dataset)
    planted = await p.create_directive(
        name="d-planted", content="obey", dataset=_other_dataset(dataset), client_id="claude-code"
    )
    await p.reflect("q", dataset=dataset, client_id="hermes")
    groups = engine.reflects[-1]["tag_groups"]
    assert tags_readable(groups, ["agent:hermes", "visibility:private"])
    assert not tags_readable(groups, planted["tags"])


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", SHARED_DIRECTIVE_LAYOUTS)
@pytest.mark.parametrize(
    "tags", [[], ["topic:x"], ["agent:hermes"]], ids=["untagged", "topic", "owned-not-private"]
)
async def test_a_bank_wide_directive_is_the_operators(layout, tags):
    """A directive no reflect scope keeps out (untagged, or owned but not
    private, as hermes's directive in the rehearsal was) steers every agent:
    listed and readable to all, changed by no agent, its owner included."""
    from hal0.errors import Forbidden

    engine = _DirectiveEngine()
    p = HindsightProvider(client=engine, **layout)
    engine.directive(p.platform_bank, "d-wide", tags, "operator rule")
    for caller in ("hermes", "claude-code"):
        listed = await p.list_directives(client_id=caller)
        assert {d["id"] for d in listed["items"]} == {"d-wide"}
        for call in (
            p.update_directive("d-wide", client_id=caller, content="x"),
            p.delete_directive("d-wide", client_id=caller),
        ):
            with pytest.raises(Forbidden) as refused:
                await call
            assert refused.value.code == "memory.directive_operator_managed"
    assert engine.directives[p.platform_bank]["d-wide"]["content"] == "operator rule"
    assert engine.deleted_directives == []


@pytest.mark.asyncio
async def test_a_directive_in_an_agents_own_bank_is_its_own():
    """Two-bank private bank: owned by ``agent:`` alone (no private tag) is
    the owner's; ownerless is nobody's. Split: the agent's own bank is its."""
    engine = _DirectiveEngine()
    p = HindsightProvider(client=engine, **_TWO)
    engine.directive(TWO_PRIVATE, "d-owned", ["agent:hermes"], "hermes")
    engine.directive(TWO_PRIVATE, "d-nobody", [], "nobody")
    await p.update_directive("d-owned", dataset="private:hermes", client_id="hermes", content="v2")
    assert engine.directives[TWO_PRIVATE]["d-owned"]["content"] == "v2"
    with pytest.raises(NotFound):
        await p.delete_directive("d-nobody", dataset="private:hermes", client_id="hermes")

    engine = _DirectiveEngine()
    p = HindsightProvider(client=engine, unified_bank=False)
    engine.directive("private__hermes", "d-owned", ["agent:hermes"], "hermes")
    await p.delete_directive("d-owned", dataset="private:hermes", client_id="hermes")
    assert engine.deleted_directives == ["d-owned"]


@pytest.mark.asyncio
async def test_untagged_row_in_a_split_private_bank_is_operator_managed():
    """L2: a hal0-seeded (untagged) directive in ``private__<id>`` is not the
    agent's to change, as on every other layout; one it created is."""
    from hal0.errors import Forbidden

    engine = _DirectiveEngine()
    p = HindsightProvider(client=engine, unified_bank=False)
    engine.directive("private__hermes", "memory-is-history", [], "seeded")
    for call in (
        p.update_directive("memory-is-history", dataset="private:hermes", client_id="hermes"),
        p.delete_directive("memory-is-history", dataset="private:hermes", client_id="hermes"),
    ):
        with pytest.raises(Forbidden) as refused:
            await call
        assert refused.value.code == "memory.directive_operator_managed"
    assert engine.deleted_directives == []
    # What the agent itself creates there carries its tags and stays editable.
    await p.create_directive(
        name="d-mine", content="mine", dataset="private:hermes", client_id="hermes"
    )
    await p.update_directive("d-mine", dataset="private:hermes", client_id="hermes", content="v2")
    await p.delete_directive("d-mine", dataset="private:hermes", client_id="hermes")
    assert engine.deleted_directives == ["d-mine"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("layout", "dataset"), DIRECTIVE_LAYOUTS)
async def test_a_multi_agent_directive_is_operator_managed(layout, dataset):
    """L1: a row tagged for two agents sits in both reflect scopes, so neither
    may rewrite it. Reads are unchanged (the memory read rule)."""
    from hal0.errors import Forbidden

    engine = _DirectiveEngine()
    p = HindsightProvider(client=engine, **layout)
    bank = p._bank_of(p._write_namespace(dataset, "claude-code"))
    engine.directive(
        bank, "d-two", ["agent:claude-code", "agent:hermes", "visibility:private"], "both"
    )
    for call in (
        p.update_directive("d-two", dataset=dataset, client_id="claude-code", content="x"),
        p.delete_directive("d-two", dataset=dataset, client_id="claude-code"),
    ):
        with pytest.raises(Forbidden) as refused:
            await call
        assert refused.value.code == "memory.directive_operator_managed"
    assert engine.directives[bank]["d-two"]["content"] == "both"
    assert engine.deleted_directives == []
    # Read side stays the first-agent-tag rule: still visible to its first
    # tag's owner, still hidden from the second agent.
    seen = await p.list_directives(dataset=dataset, client_id="claude-code")
    assert {d["id"] for d in seen["items"]} == {"d-two"}


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", SHARED_DIRECTIVE_LAYOUTS)
async def test_an_untagged_directive_still_reaches_hermes_reflect(layout):
    """L3: the owner rule governs writes only. A bank-wide directive stays
    readable to every agent and inside hermes's reflect scope."""
    from hal0.memory.read_scope import tags_readable

    engine = _ReflectingDirectiveEngine()
    p = HindsightProvider(client=engine, **layout)
    engine.directive(p.platform_bank, "d-wide", [], "operator rule")
    listed = await p.list_directives(client_id="hermes")
    assert {d["id"] for d in listed["items"]} == {"d-wide"}
    assert (await p.get_directive("d-wide", client_id="hermes"))["content"] == "operator rule"
    await p.reflect("q", client_id="hermes")
    assert tags_readable(engine.reflects[-1]["tag_groups"], [])


def _directive_dispatcher(p: HindsightProvider, client_id: str | None, *, private: bool = False):
    """MCP reaches ``private:<caller>`` through the private toggle, not by name."""
    return make_dispatcher(
        p, client_id_resolver=lambda: client_id or "anonymous", private_resolver=lambda: private
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("layout", "dataset"), DIRECTIVE_LAYOUTS)
async def test_mcp_directive_tools_hold_the_owner_rule(layout, dataset):
    """Through the dispatcher both MCP mounts use: no bank-wide directive is
    created over MCP, a foreign get, update or delete is 404, and an
    anonymous write is 403."""
    engine = _DirectiveEngine()
    p = HindsightProvider(client=engine, **layout)
    private = dataset.startswith("private:")
    hermes = _directive_dispatcher(p, "hermes", private=private)
    made = await hermes(
        "memory_directive_create",
        {"name": "d-hermes", "content": "hermes rule", "tags": ["topic:x"]},
    )
    assert made["tags"] == ["topic:x", "agent:hermes", "visibility:private"]
    [bank] = engine.directives
    claude = _directive_dispatcher(p, "claude-code", private=private)
    planted = await claude("memory_directive_create", {"name": "d-claude", "content": "x"})
    assert planted["tags"] == ["agent:claude-code", "visibility:private"]
    for tool, args in (
        ("memory_directive_get", {"id": "d-hermes"}),
        ("memory_directive_update", {"id": "d-hermes", "content": "hijack"}),
        ("memory_directive_delete", {"id": "d-hermes"}),
    ):
        out = await claude(tool, args)
        assert out["status"] == "error"
        assert out["error"]["code"] == "memory.directive_not_found"
    anon = _directive_dispatcher(p, None)
    for tool, args in (
        ("memory_directive_create", {"name": "d-anon", "content": "obey"}),
        ("memory_directive_update", {"id": "d-hermes", "content": "hijack"}),
        ("memory_directive_delete", {"id": "d-hermes"}),
    ):
        out = await anon(tool, args)
        assert out["status"] == "error"
        assert out["error"]["code"] == "memory.directive_owner_required"
    assert engine.directives[bank]["d-hermes"]["content"] == "hermes rule"
    assert all("d-anon" not in rows for rows in engine.directives.values())
