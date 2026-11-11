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

    async def list_mental_models(self, *, bank_id, **_kwargs):
        return {"items": [dict(m) for m in self.models.get(bank_id, {}).values()]}

    async def get_mental_model(self, *, bank_id, mental_model_id):
        model = self.models.get(bank_id, {}).get(mental_model_id)
        if model is None:
            raise _EngineNotFound()
        return dict(model)

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
async def test_mental_model_confined_to_owner_survives_foreign_private(layout):
    """Another agent's private note in the bank: alice keeps the model scoped
    to her own tag and loses the whole-bank ones."""
    p, engine, bank = _provider(layout)
    engine.doc(bank, "b-priv", ["visibility:private", "agent:bob"])
    listed = await p.list_mental_models(client_id="alice")
    assert {m["id"] for m in listed["items"]} == {"mm-alice"}


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
