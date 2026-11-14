"""``hal0 memory knowledge rehome`` against the fold-in's fake engine.

The box is folded first, for real, by ``hal0 memory migrate fold-in``
(``tests/memory/test_fold_in.py``): ``Hal0::Agents`` brings the reference
box's shape, five standard pages and an ``Initiatives`` folder of 26 pages
(32 nodes), none of them naming a project, so the fold-in leaves them at the
root of ``hal0:shared``; ``claude-code::hal0`` brings the five standard pages
into ``project:hal0``, where their names are then taken. The fake engine
learns the two routes the rehome adds, as hindsight-api 1.2.0 serves them:
``PATCH .../knowledge-base/nodes/{id}`` (``update_knowledge_node``: one
transaction; ``parent_id`` moves the node, and its children with it, since
they keep theirs; a page's ``tags`` replace its model's, its ``trigger`` is
patched over the stored one; a name is the page's and its model's) and
``POST .../mental-models/{id}/clear`` (``clear_mental_model``: content ``''``,
``structured_content`` and ``last_refresh_failed_at`` NULL).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from hal0.memory import fold_in as fi
from hal0.memory import knowledge_pages as kp
from hal0.memory import knowledge_rehome as kr
from tests.memory.test_fold_in import (
    FAST,
    NORMAL_PORT,
    PLUGIN_TRIGGER,
    STANDARD_PAGES,
    Box,
    doc,
    empty_bank,
    fact,
)

BANK = "Hal0::Agents"
CODING = "claude-code::hal0"
TARGET = "hal0:shared"
INITIATIVES = 26


def _std(index: int) -> str:
    return f"kp-std-{index}"


def _ini(index: int) -> str:
    return f"kp-ini-{index:02d}"


def seed_agents(box: Box, *, agent_tag: str | None = "agent:claude-code") -> None:
    """``Hal0::Agents`` as on the reference box: 5 standard pages and an Initiatives
    folder with 26 pages, tagged by the plugin and naming no project."""
    b = box.engine.banks.setdefault(BANK, empty_bank())
    own = [agent_tag] if agent_tag else []

    def page(node_id: str, name: str, parent: str | None, tags: list[str], content: str) -> None:
        mm = node_id.replace("kp-", "mm-")
        b["models"][mm] = {
            "id": mm,
            "name": name,
            "source_query": f"What about {name}?",
            "tags": [*tags, *own],
            "trigger": json.dumps(PLUGIN_TRIGGER),
            "content": content,
            "last_refreshed_at": "2026-11-12T00:00:00+00:00",
            "max_tokens": 4096,
        }
        b["pages"].append(
            {
                "id": node_id,
                "kind": "page",
                "name": name,
                "parent_id": parent,
                "mental_model_id": mm,
            }
        )

    for index, name in enumerate(STANDARD_PAGES):
        page(_std(index), name, None, [f"knowledge:std-{index}"], f"{name}: other projects too")
    b["pages"].append(
        {"id": "kf-initiatives", "kind": "folder", "name": "Initiatives", "parent_id": None}
    )
    for index in range(INITIATIVES):
        page(
            _ini(index),
            f"Initiative {index:02d}",
            "kf-initiatives",
            ["knowledge:feature-work"],
            f"See [[page:{_std(0)}]] for initiative {index:02d}.",
        )


def seed_coding(box: Box) -> None:
    """``claude-code::hal0``: the five standard pages, which the fold-in places."""
    b = box.engine.banks.setdefault(CODING, empty_bank())
    for index, name in enumerate(STANDARD_PAGES):
        mm = f"cc-mm-{index}"
        b["models"][mm] = {
            "id": mm,
            "name": name,
            "source_query": f"What are the {name.lower()}?",
            "tags": [f"knowledge:std-{index}"],
            "trigger": PLUGIN_TRIGGER,
            "content": "",
            "last_refreshed_at": "2026-11-12T00:00:00+00:00",
        }
        b["pages"].append(
            {
                "id": f"cc-kp-{index}",
                "kind": "page",
                "name": name,
                "parent_id": None,
                "mental_model_id": mm,
            }
        )


class Writes:
    """The engine routes the rehome adds, as hindsight-api 1.2.0 serves them."""

    def __init__(self, box: Box) -> None:
        self.box = box
        self.calls: list[str] = []
        #: The nth move (1-based) answers 500 and changes nothing: a run that stops.
        self.fail_move_at: int | None = None
        self.moves = 0
        inner = box.engine.handler

        async def handler(request: httpx.Request) -> httpx.Response:
            got = self.serve(request)
            return got if got is not None else await inner(request)

        box.engine.handler = handler  # type: ignore[method-assign]

    def serve(self, request: httpx.Request) -> httpx.Response | None:
        m = re.fullmatch(
            r"/v1/default/banks/([^/]+)/(knowledge-base/nodes|mental-models)/([^/]+)(/clear)?",
            request.url.path,
        )
        if m is None:
            return None
        bank = self.box.engine.banks[unquote(m.group(1))]
        item = unquote(m.group(3))
        if request.method == "POST" and m.group(4):
            model = bank["models"].get(item)
            if model is None:
                return httpx.Response(404, json={"detail": "not found"})
            self.calls.append(f"clear:{item}")
            model.update({"content": "", "structured_content": None})
            model["last_refresh_failed_at"] = None
            return httpx.Response(200, json=model)
        if request.method != "PATCH" or m.group(2) != "knowledge-base/nodes":
            return None
        body = json.loads(request.content)
        node = next((p for p in bank["pages"] if p["id"] == item), None)
        if node is None:
            return httpx.Response(404, json={"detail": "not found"})
        if "parent_id" in body:
            self.moves += 1
            if self.moves == self.fail_move_at:
                return httpx.Response(500, json={"detail": "boom"})
            parent = body["parent_id"]
            if parent is not None and not any(
                p["id"] == parent and p["kind"] == "folder" for p in bank["pages"]
            ):
                return httpx.Response(400, json={"detail": "parent is not a folder"})
        name = body.get("name", node["name"])
        parent = body.get("parent_id", node.get("parent_id"))
        if node["kind"] == "page" and any(
            p is not node
            and p["kind"] == "page"
            and (p.get("parent_id") or "") == (parent or "")
            and str(p["name"]).lower() == str(name).lower()
            for p in bank["pages"]
        ):
            # ``uq_kp_folder_pagename``: the whole patch rolls back.
            return httpx.Response(409, json={"detail": "name taken"})
        self.calls.append(f"patch:{item}:{','.join(sorted(body))}")
        node["name"], node["parent_id"] = name, parent
        model = bank["models"].get(node.get("mental_model_id")) if node["kind"] == "page" else None
        if model is not None:
            model["name"] = name
            if "tags" in body:
                model["tags"] = list(body["tags"])
            if "trigger" in body:
                stored = model.get("trigger")
                stored = json.loads(stored) if isinstance(stored, str) else dict(stored or {})
                model["trigger"] = {**stored, **body["trigger"]}
        return httpx.Response(200, json=node)


async def fold_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **seed: Any) -> Box:
    """A box folded by the fold-in, cut over to ``two_bank``, with the real case in it."""
    box = Box(tmp_path, monkeypatch)
    box.clients.update({"claude-code", "cc"})
    seed_agents(box, **seed)
    seed_coding(box)
    result = await box.fold(owners={"coding-agent": "cc"}).run()
    assert result["cut_over"] is True, result
    return box


def folded_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **seed: Any) -> Box:
    return asyncio.run(fold_box(tmp_path, monkeypatch, **seed))


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Box:
    return folded_box(tmp_path, monkeypatch)


def rehome(box: Box, **kw: Any) -> kr.KnowledgeRehome:
    kw.setdefault("bank", BANK)
    kw.setdefault("project", "hal0")
    return kr.KnowledgeRehome(
        box.client(f"http://127.0.0.1:{NORMAL_PORT}"),
        memory_config=lambda: box.cfg,
        known_clients=lambda: set(box.clients),
        privileged=lambda: box.privileged,
        timeouts=FAST,
        **kw,
    )


def new(old: str, kind: str = "page") -> str:
    return kp.folded_node_id(BANK, old, kind)


def nodes(box: Box) -> dict[str, dict[str, Any]]:
    return {str(p["id"]): p for p in box.engine.banks[TARGET]["pages"]}


def folder_id(box: Box) -> str:
    (folder,) = [
        p
        for p in box.engine.banks[TARGET]["pages"]
        if p["kind"] == "folder" and p["name"] == "project:hal0" and not p.get("parent_id")
    ]
    return str(folder["id"])


def model_of(box: Box, node_id: str) -> dict[str, Any]:
    node = nodes(box)[node_id]
    return box.engine.banks[TARGET]["models"][node["mental_model_id"]]


def all_old() -> list[tuple[str, str]]:
    return [
        *((_std(i), "page") for i in range(len(STANDARD_PAGES))),
        ("kf-initiatives", "folder"),
        *((_ini(i), "page") for i in range(INITIATIVES)),
    ]


# ── the real case after the fold-in ───────────────────────────────────


def test_the_fold_in_left_the_banks_pages_at_the_root_unstamped(box: Box) -> None:
    """The starting point: 32 nodes under the fold-in's ids, none in a project folder."""
    here = nodes(box)
    for old, kind in all_old():
        assert new(old, kind) in here, old
    for i in range(len(STANDARD_PAGES)):
        assert here[new(_std(i))]["parent_id"] is None
        assert model_of(box, new(_std(i)))["id"] == f"hal0-agents-mm-std-{i}"
    assert here[new(_ini(0))]["parent_id"] == new("kf-initiatives", "folder")
    rows = kp.load_page_map()["targets"][TARGET]
    assert _std(0) not in rows


async def test_the_dry_run_reports_the_real_shape_and_writes_nothing(box: Box) -> None:
    writes = Writes(box)
    before = json.dumps(box.engine.banks[TARGET], sort_keys=True, default=str)
    report = await rehome(box, owner="claude-code").plan()
    assert report["refusals"] == []
    assert report["nodes"] == {
        "total": 32,
        "pages": 31,
        "folders": 1,
        "top_level": 6,
        "to_move": 6,
        "already_placed": 0,
        "in_moving_subtrees": 32,
    }
    assert report["folder"]["exists"] is True
    assert sorted((r["from"], r["to"]) for r in report["renames"]) == sorted(
        (name, f"{name} (Hal0::Agents)") for name in STANDARD_PAGES
    )
    assert report["owner"]["owners"] == {"claude-code": 31}
    assert report["content"] == {"cleared": 31, "kept": False}
    assert report["keep_content"]["allowed"] is True
    assert report["keep_content"]["private_facts"] == 0
    assert report["page_map"]["rows"] == 32
    assert report["changes"] > 0
    assert writes.calls == []
    assert json.dumps(box.engine.banks[TARGET], sort_keys=True, default=str) == before


async def test_apply_moves_stamps_empties_and_maps_every_node(box: Box) -> None:
    writes = Writes(box)
    report = await rehome(box, owner="claude-code").rehome()
    assert report["verified"] is True and report["changed"] is True
    assert report["done"] == {"stamped": 31, "cleared": 31, "moved": 6}
    here, folder = nodes(box), folder_id(box)
    names_in_folder = sorted(p["name"] for p in here.values() if p.get("parent_id") == folder)
    assert names_in_folder == sorted(
        [*STANDARD_PAGES, *(f"{n} (Hal0::Agents)" for n in STANDARD_PAGES), "Initiatives"]
    )
    # The folder moved with its children: they keep their parent.
    kf = new("kf-initiatives", "folder")
    assert here[kf]["parent_id"] == folder
    assert all(here[new(_ini(i))]["parent_id"] == kf for i in range(INITIATIVES))
    for old, kind in all_old():
        if kind != "page":
            continue
        model = model_of(box, new(old))
        assert kp.trigger_scope_held(model["trigger"], "hal0"), old
        assert model["trigger"]["tags_match"] == "all"
        assert model["trigger"]["refresh_after_consolidation"] is True
        assert "agent:claude-code" in model["tags"] and "project:hal0" in model["tags"]
        assert [t for t in model["tags"] if t.startswith("agent:")] == ["agent:claude-code"]
        assert model["content"] == "" and model["last_refresh_failed_at"] is None
    # Renamed page and model alike.
    assert model_of(box, new(_std(0)))["name"] == "Component map (Hal0::Agents)"
    # Each page is emptied before the move that lets compat see it.
    last_clear = max(i for i, c in enumerate(writes.calls) if c.startswith("clear:"))
    first_move = min(i for i, c in enumerate(writes.calls) if "parent_id" in c)
    assert last_clear < first_move
    rows = kp.load_page_map()["targets"][TARGET]
    for old, kind in all_old():
        assert rows[old] == {
            "id": new(old, kind),
            "kind": kind,
            "folder": folder,
            "project": "hal0",
            "bank": BANK,
        }


async def test_a_run_after_a_complete_one_changes_nothing(box: Box) -> None:
    writes = Writes(box)
    await rehome(box, owner="claude-code").rehome()
    calls, mtime = len(writes.calls), kp.page_map_path().stat().st_mtime_ns
    again = await rehome(box, owner="claude-code").rehome()
    assert again["changes"] == 0 and again["changed"] is False and again["verified"] is True
    assert again["nodes"]["to_move"] == 0 and again["nodes"]["already_placed"] == 6
    assert writes.calls[calls:] == []
    assert kp.page_map_path().stat().st_mtime_ns == mtime
    assert (await rehome(box, owner="claude-code").plan())["changes"] == 0


async def test_a_run_that_stopped_part_way_is_completed_by_the_next(box: Box) -> None:
    writes = Writes(box)
    writes.fail_move_at = 4
    with pytest.raises(fi.FoldInError):
        await rehome(box, owner="claude-code").rehome()
    # Stopped after 3 moves: what compat can see is stamped and empty already.
    folder = folder_id(box)
    shown = [p for p in nodes(box).values() if p.get("parent_id") == folder]
    assert sum(1 for p in shown if p["id"] in {new(o, k) for o, k in all_old()}) == 3
    for old, kind in all_old():
        if kind == "page":
            model = model_of(box, new(old))
            assert model["content"] == ""
            assert kp.trigger_scope_held(model["trigger"], "hal0")
    report = await rehome(box, owner="claude-code").rehome()
    assert report["verified"] is True
    assert report["done"]["moved"] == 3
    assert report["nodes"]["already_placed"] == 3
    rows = kp.load_page_map()["targets"][TARGET]
    assert all(old in rows for old, _ in all_old())


async def test_without_owner_a_page_goes_to_its_agent_tag_or_the_pseudo_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = await fold_box(tmp_path, monkeypatch, agent_tag=None)
    report = await rehome(box).plan()
    assert report["owner"]["owners"] == {"hal0-legacy-hal0-agents": 31}
    assert len(report["owner"]["pseudo_owned"]) == 31
    tagged = await rehome(await fold_box(tmp_path / "b", monkeypatch)).plan()
    assert tagged["owner"]["owners"] == {"claude-code": 31}


async def test_keep_content_is_refused_when_the_bank_holds_private_facts(box: Box) -> None:
    writes = Writes(box)
    box.engine.banks[BANK]["docs"]["private:claude-code:s1"] = doc(
        "private:claude-code:s1",
        fact("a secret", "agent:claude-code", "visibility:private"),
        tags=("visibility:private",),
    )
    plan = await rehome(box, owner="claude-code").plan()
    assert plan["keep_content"]["allowed"] is False
    assert plan["keep_content"]["private_facts"] == 1
    assert plan["refusals"] == []  # the dry run without --keep-content says why, refuses nothing
    keep = await rehome(box, owner="claude-code", keep_content=True).plan()
    assert [r["code"] for r in keep["refusals"]] == ["keep_content_private"]
    with pytest.raises(kr.RehomeRefused):
        await rehome(box, owner="claude-code", keep_content=True).rehome()
    assert writes.calls == []


async def test_keep_content_keeps_it_and_counts_the_old_links(box: Box) -> None:
    writes = Writes(box)
    before = model_of(box, new(_ini(3)))["content"]
    report = await rehome(box, owner="claude-code", keep_content=True).rehome()
    assert report["keep_content"]["allowed"] is True
    assert report["keep_content"]["old_links"] == INITIATIVES
    assert kr.KEPT_CONTENT_NOTE in report["keep_content"]["why"]
    assert report["done"]["cleared"] == 0
    assert not any(c.startswith("clear:") for c in writes.calls)
    assert model_of(box, new(_ini(3)))["content"] == before


async def test_refused_before_the_cutover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    box = Box(tmp_path, monkeypatch)
    seed_agents(box)
    report = await rehome(box).plan()
    assert [r["code"] for r in report["refusals"]] == ["not_two_bank"]


async def test_apply_needs_root(box: Box) -> None:
    box.privileged = False
    report = await rehome(box).plan(apply=True)
    assert "not_privileged" in [r["code"] for r in report["refusals"]]
    with pytest.raises(kr.RehomeRefused):
        await rehome(box).rehome()


async def test_a_bank_the_fold_in_did_not_fold_is_refused(box: Box) -> None:
    report = await rehome(box, bank="nope::bank").plan()
    assert [r["code"] for r in report["refusals"]] == ["not_folded"]


async def test_a_mutated_scope_stamp_fails_the_placement_check(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation check: a trigger without the private guard is caught after the move."""
    Writes(box)
    real = kp.page_trigger

    def without_guard(chosen: Any, leaf: list[str], slug: str) -> dict[str, Any]:
        out = real(chosen, leaf, slug)
        out["tag_groups"] = [g for g in out["tag_groups"] if g != kp.NOT_PRIVATE_GROUP]
        return out

    monkeypatch.setattr(kp, "page_trigger", without_guard)
    with pytest.raises(fi.FoldInError, match="not in project:hal0 with its scope"):
        await rehome(box, owner="claude-code").rehome()


async def test_a_mutated_project_scope_fails_the_placement_check(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation check: a trigger scoped to another project is caught too."""
    Writes(box)
    real = kp.page_trigger
    monkeypatch.setattr(kp, "page_trigger", lambda c, leaf, _s: real(c, leaf, "other"))
    with pytest.raises(fi.FoldInError, match="not in project:hal0 with its scope"):
        await rehome(box, owner="claude-code").rehome()
