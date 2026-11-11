"""The two-bank layout and the read rule, in every layout (ADR-0058 7, 7b).

A new store keeps hal0's memory in ``hal0:shared`` and ``hal0:private``; a
store from before keeps its legacy unified or split banks. These tests pin,
for each layout: which bank every namespace maps to, what each kind of caller
reads of each stored shape, the private write's observation scope on the
wire, and the one-bank reads (reflect, raw recall) and deletes.

The engine double evaluates ``tag_groups`` the way the engine does
(:func:`hal0.memory.read_scope.tags_readable`), so a read's outcome here is
the outcome of the groups the provider sends.

Run targeted:
    .venv/bin/python -m pytest tests/memory/test_two_bank_layout.py -q
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.config.schema import MemoryConfig
from hal0.memory.bank_layout import LAYOUT_LEGACY, LAYOUT_TWO_BANK, BankLayout
from hal0.memory.hindsight_provider import HindsightProvider
from hal0.memory.projects import ProjectRegistry
from hal0.memory.read_scope import tags_readable

ME, OTHER = "hermes", "scribe"
SHARED, PRIVATE = "hal0:shared", "hal0:private"

UNIFIED = "legacy_unified"
SPLIT = "legacy_split"
TWO = "two_bank"
LAYOUTS = (UNIFIED, SPLIT, TWO)


def layout_kwargs(layout: str) -> dict[str, Any]:
    """The provider keywords the layout record gives (``provider_from_config``)."""
    if layout == TWO:
        record = BankLayout(LAYOUT_TWO_BANK)
    else:
        record = BankLayout(
            LAYOUT_LEGACY, legacy_platform_bank="shared", legacy_unified=layout == UNIFIED
        )
    return record.provider_kwargs(MemoryConfig())


class Engine:
    """The slice of the Hindsight client these reads and writes use."""

    def __init__(self) -> None:
        self.banks: dict[str, list[dict[str, Any]]] = {}
        self.retained: list[dict[str, Any]] = []
        self.recalls: list[dict[str, Any]] = []
        self.reflects: list[dict[str, Any]] = []
        self.deleted: list[tuple[str, str]] = []

    def put(self, bank: str, doc: str, text: str, tags: list[str]) -> None:
        self.banks.setdefault(bank, []).append(
            {"id": f"f-{doc}", "document_id": doc, "text": text, "tags": list(tags)}
        )

    async def retain(self, *, bank_id: str, content: str, document_id: str, **kw: Any) -> Any:
        self.retained.append({"bank_id": bank_id, "document_id": document_id, **kw})
        self.put(bank_id, document_id, content, kw.get("tags") or [])
        return {"operation_id": "op-1"}

    async def recall(self, *, bank_id: str, **kw: Any) -> Any:
        assert not (kw.get("tags") and kw.get("tag_groups")), "the engine refuses both (422)"
        self.recalls.append({"bank_id": bank_id, **kw})
        groups = kw.get("tag_groups") or []
        tags = kw.get("tags")
        out = []
        for fact in self.banks.get(bank_id, []):
            if groups and not tags_readable(groups, fact["tags"]):
                continue
            if tags and not set(tags) & set(fact["tags"]):
                continue
            out.append(dict(fact))
        return {"results": out}

    async def list_memories(self, *, bank_id: str, limit: int = 50, offset: int = 0, **_: Any):
        rows = self.banks.get(bank_id, [])
        return {"items": [dict(r) for r in rows[offset : offset + limit]], "total": len(rows)}

    async def delete_document(self, *, bank_id: str, document_id: str) -> Any:
        rows = self.banks.get(bank_id, [])
        kept = [r for r in rows if r["document_id"] != document_id]
        self.banks[bank_id] = kept
        removed = len(rows) - len(kept)
        self.deleted.append((bank_id, document_id))
        if not removed:
            import httpx

            request = httpx.Request("DELETE", "http://engine")
            raise httpx.HTTPStatusError(
                "404", request=request, response=httpx.Response(404, request=request)
            )
        return {"memory_units_deleted": removed}

    async def reflect(self, **kw: Any) -> Any:
        self.reflects.append(kw)
        return {"text": "an answer"}

    async def get_memory(self, *, bank_id: str, memory_id: str) -> Any:
        return next((dict(r) for r in self.banks.get(bank_id, []) if r["id"] == memory_id), {})

    async def update_memory(self, **kw: Any) -> Any:
        return {"ok": True, **kw}


def provider(layout: str, engine: Engine | None = None, **kw: Any) -> HindsightProvider:
    return HindsightProvider(client=engine or Engine(), **layout_kwargs(layout), **kw)


def texts(items: Any) -> set[str]:
    return {item["text"] for item in items}


# ── 1. the mapping table ──────────────────────────────────────────────────────

MAPPING = {
    # namespace:       (legacy unified, legacy split,       two-bank)
    "shared": ("shared", "shared", SHARED),
    f"private:{ME}": ("shared", f"private__{ME}", PRIVATE),
    f"private:{OTHER}": ("shared", f"private__{OTHER}", PRIVATE),
    "agents": ("agents", "agents", "agents"),
    "project:apollo": ("shared", "project__apollo", SHARED),
}


@pytest.mark.parametrize("namespace", sorted(MAPPING))
@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_namespace_maps_to_its_bank(layout: str, namespace: str) -> None:
    p = provider(layout)
    expected = MAPPING[namespace][LAYOUTS.index(layout)]
    assert p.bank_for(p._write_namespace(namespace, ME)) == expected


@pytest.mark.parametrize("layout", LAYOUTS)
def test_the_layout_is_reported(layout: str) -> None:
    p = provider(layout)
    assert p.layout == layout
    assert p.two_bank is (layout == TWO)
    assert p.private_bank == (PRIVATE if layout == TWO else None)
    assert p.unified_bank is (layout == UNIFIED)


def test_two_bank_banks_follow_the_config() -> None:
    record = BankLayout(LAYOUT_TWO_BANK)
    p = HindsightProvider(
        client=Engine(),
        **record.provider_kwargs(MemoryConfig(bank="a:shared", private_bank="a:private")),
    )
    assert (p.bank_for("shared"), p.bank_for(f"private:{ME}")) == ("a:shared", "a:private")


def test_two_bank_refuses_one_bank_for_both() -> None:
    with pytest.raises(ValueError, match="differ"):
        HindsightProvider(client=Engine(), two_bank=True, bank="x", private_bank="x")


# ── 2. writes ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_two_bank_private_write_lands_in_the_private_bank_with_its_own_scope() -> None:
    engine = Engine()
    p = provider(TWO, engine, client_id=ME)
    out = await p.add(
        "a note",
        dataset=f"private:{ME}",
        tags=["topic:x", "agent:forged", "visibility:shared"],
        document_id="n1",
        observation_scopes=[["topic:x"], ["agent:forged"]],
        client_id=ME,
    )
    item = engine.retained[0]
    assert item["bank_id"] == PRIVATE
    assert item["document_id"] == out["id"] == f"private:{ME}:n1"
    assert item["tags"] == [
        "topic:x",
        f"agent:{ME}",
        "kind:durable",
        "scope:global",
        "visibility:private",
    ]
    # The caller's scopes are ignored: the writer's own scope is sent.
    assert item["observation_scopes"] == [[f"agent:{ME}", "visibility:private"]]


@pytest.mark.asyncio
async def test_a_two_bank_private_write_with_a_project_scopes_by_project_too() -> None:
    engine = Engine()
    p = provider(TWO, engine, projects=ProjectRegistry(aliases={"atlas-mono": "atlas"}))
    await p.add("a note", dataset=f"private:{ME}", client_id=ME, project="Atlas Mono")
    item = engine.retained[0]
    assert "project:atlas" in item["tags"]
    assert item["observation_scopes"] == [[f"agent:{ME}", "visibility:private", "project:atlas"]]


@pytest.mark.asyncio
async def test_a_two_bank_shared_write_is_unchanged_in_shape() -> None:
    engine = Engine()
    p = provider(TWO, engine)
    await p.add("a note", client_id=ME, project="atlas")
    await p.add("a global note", client_id=ME)
    named, plain = engine.retained
    assert (named["bank_id"], plain["bank_id"]) == (SHARED, SHARED)
    assert named["observation_scopes"] == [["project:atlas"]]
    assert plain["observation_scopes"] == "shared"
    assert "visibility:private" not in named["tags"] + plain["tags"]


@pytest.mark.asyncio
async def test_a_two_bank_shared_write_may_not_claim_a_private_id() -> None:
    p = provider(TWO)
    with pytest.raises(ValueError, match="reserved"):
        await p.add("x", document_id=f"private:{ME}:n1", client_id=ME)


@pytest.mark.asyncio
async def test_the_legacy_unified_private_write_keeps_the_engine_default_scope() -> None:
    """Unchanged on an upgraded store: no scope is sent (#3547 is held off
    there by ``spec apply``)."""
    engine = Engine()
    await provider(UNIFIED, engine).add(
        "a note", dataset=f"private:{ME}", document_id="n1", client_id=ME
    )
    item = engine.retained[0]
    assert item["bank_id"] == "shared" and "observation_scopes" not in item
    assert item["document_id"] == f"private:{ME}:n1"


@pytest.mark.asyncio
async def test_a_two_bank_project_namespace_write_is_a_tag_in_the_shared_bank() -> None:
    engine = Engine()
    await provider(TWO, engine).add("x", dataset="project:apollo", client_id=ME)
    assert engine.retained[0]["bank_id"] == SHARED
    assert "project:apollo" in engine.retained[0]["tags"]


# ── 3. the read rule, per caller and per stored shape ─────────────────────────

SHAPES = {
    "untagged": [],
    "global": ["scope:global"],
    "project P": ["project:p"],
    "project Q": ["project:q"],
    "private own": [f"agent:{ME}", "visibility:private"],
    "private other": [f"agent:{OTHER}", "visibility:private"],
    "private no agent": ["visibility:private"],
    # What a private write with no client id is stamped with (#3560).
    "private unknown": ["agent:unknown", "visibility:private"],
}


def seeded(layout: str) -> tuple[Engine, HindsightProvider]:
    """Every shape stored where that layout keeps it."""
    engine = Engine()
    for name, tags in SHAPES.items():
        private = "visibility:private" in tags
        if layout == UNIFIED:
            bank = "shared"
        elif layout == SPLIT:
            owner = OTHER if name == "private other" else ME
            bank = f"private__{owner}" if private else "shared"
        else:
            bank = PRIVATE if private else SHARED
        engine.put(bank, name.replace(" ", "-"), name, tags)
    return engine, provider(layout, engine)


PUBLIC = {"untagged", "global", "project P", "project Q"}
READS = {
    # (layout, caller, dataset, project): what the read returns
    (UNIFIED, ME, "shared", None): PUBLIC | {"private own"},
    (UNIFIED, ME, "shared", "*"): PUBLIC | {"private own"},
    (UNIFIED, ME, "shared", "p"): {"untagged", "global", "project P"},
    (UNIFIED, None, "shared", None): PUBLIC,
    (SPLIT, ME, "shared", None): PUBLIC | {"private own", "private no agent", "private unknown"},
    (SPLIT, ME, "shared", "p"): {"untagged", "global", "project P"},
    (SPLIT, ME, f"private:{ME}", None): {"private own", "private no agent", "private unknown"},
    (TWO, ME, "shared", None): PUBLIC | {"private own"},
    (TWO, ME, "shared", "*"): PUBLIC | {"private own"},
    (TWO, ME, "shared", "p"): {"untagged", "global", "project P"},
    (TWO, ME, f"private:{ME}", None): {"private own"},
    (TWO, None, "shared", None): PUBLIC,
    (TWO, ME, f"private:{OTHER}", None): set(),
}


@pytest.mark.asyncio
@pytest.mark.parametrize(("layout", "caller", "dataset", "project"), sorted(READS, key=str))
async def test_recall_reads(layout, caller, dataset, project) -> None:
    _engine, p = seeded(layout)
    out = await p.recall("q", dataset=dataset, client_id=caller, project=project)
    assert texts(out) == READS[(layout, caller, dataset, project)]


@pytest.mark.asyncio
@pytest.mark.parametrize(("layout", "caller", "dataset", "project"), sorted(READS, key=str))
async def test_list_reads_the_same(layout, caller, dataset, project) -> None:
    _engine, p = seeded(layout)
    page = await p.list_items(dataset=dataset, client_id=caller, project=project)
    expected = READS[(layout, caller, dataset, project)]
    if layout == UNIFIED and dataset.startswith("private:"):
        expected = {t for t in expected if t.startswith("private")}
    assert texts(page["items"]) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", LAYOUTS)
async def test_search_takes_the_project_too(layout) -> None:
    _engine, p = seeded(layout)
    out = await p.search("q", dataset="shared", client_id=ME, project="p")
    assert texts(out) == {"untagged", "global", "project P"}


@pytest.mark.asyncio
async def test_a_two_bank_shared_read_asks_each_bank_for_what_the_caller_may_read() -> None:
    engine, p = seeded(TWO)
    await p.recall("q", dataset="shared", client_id=ME, tags=["topic:x"], tags_match="all")
    by_bank = {call["bank_id"]: call for call in engine.recalls}
    assert set(by_bank) == {SHARED, PRIVATE}
    assert by_bank[PRIVATE]["tag_groups"][0] == {
        "tags": [f"agent:{ME}"],
        "match": "any_strict",
    }
    # The caller's tags ride along as one more group; no ``tags`` key.
    for call in by_bank.values():
        assert call["tags"] is None
        assert {"tags": ["topic:x"], "match": "all"} in call["tag_groups"]


@pytest.mark.asyncio
async def test_an_anonymous_two_bank_read_never_touches_the_private_bank() -> None:
    engine, p = seeded(TWO)
    await p.recall("q", client_id=None)
    await p.list_items(client_id=None)
    assert {call["bank_id"] for call in engine.recalls} == {SHARED}


@pytest.mark.asyncio
async def test_a_legacy_read_with_no_project_is_sent_as_before() -> None:
    engine, p = seeded(UNIFIED)
    await p.recall("q", client_id=ME, tags=["topic:x"])
    (call,) = engine.recalls
    assert call["tags"] == ["topic:x"] and "tag_groups" not in call


@pytest.mark.asyncio
async def test_items_read_from_the_private_bank_answer_for_their_owner() -> None:
    _engine, p = seeded(TWO)
    out = await p.recall("q", dataset="shared", client_id=ME)
    datasets = {item["text"]: item["dataset"] for item in out}
    assert datasets["private own"] == f"private:{ME}"
    assert datasets["untagged"] == "shared"


@pytest.mark.asyncio
async def test_a_project_name_goes_through_the_registry() -> None:
    engine = Engine()
    engine.put(SHARED, "a", "atlas note", ["project:atlas"])
    engine.put(SHARED, "b", "tmp note", ["project:tmp"])
    p = provider(TWO, engine, projects=ProjectRegistry(aliases={"atlas-mono": "atlas"}))
    assert texts(await p.recall("q", client_id=ME, project="Atlas Mono")) == {"atlas note"}
    # A denied name is no project: global memory only.
    assert texts(await p.recall("q", client_id=ME, project="tmp")) == set()


@pytest.mark.asyncio
async def test_a_project_that_is_not_a_string_is_refused() -> None:
    from hal0.errors import BadRequest

    with pytest.raises(BadRequest):
        await provider(TWO).recall("q", client_id=ME, project=["p"])


# ── 4. one-bank reads ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_bank_reflect_on_shared_keeps_mental_models_and_the_visibility_rule() -> None:
    engine = Engine()
    await provider(TWO, engine).reflect("q", client_id=ME, project="p")
    (call,) = engine.reflects
    assert call["bank_id"] == SHARED and call["exclude_mental_models"] is False
    assert call["tags"] is None
    assert {"tags": ["project:p", "scope:global"], "match": "any"} in call["tag_groups"]
    assert any("or" in group for group in call["tag_groups"])  # the visibility rule


@pytest.mark.asyncio
async def test_two_bank_reflect_on_private_is_the_callers_own_without_mental_models() -> None:
    engine = Engine()
    await provider(TWO, engine).reflect("q", dataset=f"private:{ME}", client_id=ME)
    (call,) = engine.reflects
    assert call["bank_id"] == PRIVATE and call["exclude_mental_models"] is True
    assert call["tag_groups"] == [{"tags": [f"agent:{ME}"], "match": "any_strict"}]


@pytest.mark.asyncio
async def test_legacy_unified_reflect_still_leaves_mental_models_out() -> None:
    engine = Engine()
    await provider(UNIFIED, engine).reflect("q", client_id=ME)
    assert engine.reflects[0]["exclude_mental_models"] is True


@pytest.mark.parametrize(
    ("layout", "namespace", "holds"),
    [
        (UNIFIED, "shared", True),
        (SPLIT, "shared", False),
        (SPLIT, f"private:{ME}", False),
        (TWO, "shared", False),
        (TWO, f"private:{ME}", True),
    ],
)
def test_which_banks_hold_other_agents_private_memory(layout, namespace, holds) -> None:
    assert provider(layout).holds_other_agents_private(namespace) is holds


@pytest.mark.asyncio
async def test_two_bank_raw_recall_on_private_is_held_to_the_callers_agent_tag() -> None:
    _engine, p = seeded(TWO)
    out = await p.recall_raw("q", dataset=f"private:{ME}", client_id=ME)
    assert texts(out["results"]) == {"private own"}
    shared = await p.recall_raw("q", client_id=ME, tag_groups=[{"tags": ["project:p"]}])
    assert texts(shared["results"]) == {"project P"}


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", [None, "anonymous", "unknown"])
async def test_an_unidentified_caller_reads_nothing_of_the_private_bank(caller) -> None:
    """#3560 in the two-bank private bank: an unidentified caller owns nothing
    there, ``agent:unknown`` notes included, on every read path."""
    engine, p = seeded(TWO)
    raw = await p.recall_raw("q", dataset=f"private:{ME}", client_id=caller)
    assert texts(raw["results"]) == set()
    await p.reflect("q", dataset=f"private:{ME}", client_id=caller)
    assert not tags_readable(engine.reflects[0]["tag_groups"], SHAPES["private unknown"])
    assert texts(await p.recall("q", client_id=caller)) == PUBLIC
    assert p._filter_visible([{"tags": SHAPES["private unknown"]}], caller, bank=PRIVATE) == []


def test_no_caller_matches_an_unknown_owned_note_in_the_private_bank() -> None:
    p = provider(TWO)
    note = [{"tags": SHAPES["private unknown"]}]
    for caller in (None, "", "anonymous", "unknown", ME):
        assert p._filter_visible(note, caller, bank=PRIVATE) == [], caller


# ── 5. delete and curate ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_bank_delete_reaches_the_callers_own_and_shared_memory_only() -> None:
    engine, p = seeded(TWO)
    res = await p.delete(
        ["private-own", "private-other", "private-no-agent", "project-Q"],
        client_id=ME,
        dataset="shared",
    )
    assert res == {"deleted": 2}
    assert sorted(engine.deleted) == [(PRIVATE, "private-own"), (SHARED, "project-Q")]


@pytest.mark.asyncio
async def test_an_anonymous_two_bank_delete_cannot_reach_private_memory() -> None:
    engine, p = seeded(TWO)
    assert await p.delete(["private-own"], client_id=None) == {"deleted": 0}
    assert engine.deleted == []


@pytest.mark.asyncio
async def test_two_bank_curate_refuses_another_agents_private_memory() -> None:
    _engine, p = seeded(TWO)
    await p.curate("f-private-own", dataset=f"private:{ME}", client_id=ME, state="invalidated")
    with pytest.raises(PermissionError):
        await p.curate(
            "f-private-other", dataset=f"private:{ME}", client_id=ME, state="invalidated"
        )
