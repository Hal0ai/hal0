"""The knowledge-base routes of the engine-compatible memory endpoint (#3633).

The Hindsight coding-agents plugin keeps curated pages in its bank's knowledge
base and calls six engine routes for them (tree, page read, search, page
create, node update, folder create). Through ``/api/memory/compat`` every bank
name is the platform bank, so each project's pages live in one folder at the
root of that bank's tree, ``project:<slug>``, and the plugin is shown that
folder as its whole tree. These tests drive the real router, provider and REST
client against the engine double of ``test_memory_compat_routes``, in each
bank layout, and assert what reaches the engine and what comes back.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hal0.api.auth import AuthEnforcementMiddleware
from hal0.bridge.store import BridgeStore
from hal0.memory.read_scope import project_scope_groups
from tests.api import test_memory_compat_routes as routes
from tests.api.test_memory_compat_routes import (
    ADMIN_KEY,
    CLIENT_KEY,
    ENGINE_BANK,
    URL_BANK,
    Engine,
    _auth,
    _code,
    _url,
    build_app,
    compat_page_trigger,
    seed_project_pages,
)

# The route tests' fixtures: a box with both keys, a fresh auth posture, a
# bridge store with client ``cc`` and its key, and the engine double.
_box_keys = routes._box_keys
_fresh_posture_cache = routes._fresh_posture_cache
store = routes.store
key = routes.key
engine = routes.engine

KB = URL_BANK + "/knowledge-base"
ENGINE_KB = ENGINE_BANK + "/knowledge-base"
NOT_PRIVATE = {"not": {"tags": ["visibility:private"], "match": "any_strict"}}

#: The three layouts a store can be on. Every bank name maps to the platform
#: bank in all of them, so the knowledge base behaves the same in each.
LAYOUTS = {
    "legacy_unified": {"unified_bank": True},
    "legacy_split": {"unified_bank": False},
    "two_bank": {"unified_bank": False, "two_bank": True},
}

#: The plugin's own bodies (dist/mcp-server.js ``seedPages`` and
#: ``captureInitiative``).
PAGE_TRIGGER = {
    "fact_types": ["world", "experience", "observation"],
    "tags_match": "all",
    "refresh_after_consolidation": True,
}
SEED_PAGE = {
    "name": "Core concepts",
    "source_query": "What are the core concepts of this project?",
    "tags": ["knowledge:concept"],
    "max_tokens": 4096,
    "trigger": PAGE_TRIGGER,
}


@pytest.fixture(params=sorted(LAYOUTS))
def layout(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def client(engine: Engine, layout: str) -> Iterator[TestClient]:
    app = build_app(engine.handler, **LAYOUTS[layout])
    yield TestClient(AuthEnforcementMiddleware(app), client=("127.0.0.1", 50000))


def _seed_neighbours(engine: Engine) -> None:
    """Beside the project's own page (``seed_project_pages``): what else the
    platform bank's tree holds, none of which ``cc`` in ``hal0`` may see,
    except the page another client of the project wrote."""
    kb = engine.kb
    seed_project_pages(engine)
    kb.page(
        "Conventions and patterns",
        parent_id="kf-hal0",
        node_id="kp-conventions",
        tags=["knowledge:convention", "agent:codex", "kind:durable", "project:hal0"],
        trigger=compat_page_trigger("hal0", ["knowledge:convention"]),
        content="Commits are signed off.",
    )
    # Carried in by a fold-in: the plugin's own trigger, built from the whole
    # bank's untagged memory and models.
    kb.page(
        "Folded component map",
        parent_id="kf-hal0",
        node_id="kp-folded",
        tags=["knowledge:component", "project:hal0"],
        trigger=PAGE_TRIGGER,
    )
    # Scoped to the project but not kept clear of private memory.
    kb.page(
        "Without the private guard",
        parent_id="kf-hal0",
        node_id="kp-unguarded",
        tags=["agent:cc", "kind:durable", "project:hal0"],
        trigger={"tag_groups": project_scope_groups("hal0"), "exclude_mental_models": True},
    )
    # Another agent's private page.
    kb.page(
        "Hermes notes",
        parent_id="kf-hal0",
        node_id="kp-private",
        tags=["agent:hermes", "visibility:private", "project:hal0"],
        trigger=compat_page_trigger("hal0", []),
    )
    # hal0's own page at the root, and another project's folder.
    kb.page("Overview", parent_id=None, node_id="kp-root", tags=[], trigger={})
    kb.folder("project:atlas", node_id="kf-atlas")
    kb.page(
        "Atlas map",
        parent_id="kf-atlas",
        node_id="kp-atlas",
        tags=["agent:cc", "kind:durable", "project:atlas"],
        trigger=compat_page_trigger("atlas", []),
    )


def _names(nodes: list[dict[str, Any]]) -> list[str]:
    out = []
    for node in nodes:
        out.append(node["name"])
        out.extend(_names(node.get("children") or []))
    return out


def _writes(engine: Engine) -> list[str]:
    return [c.line for c in engine.calls if c.method != "GET"]


# ── tree ────────────────────────────────────────────────────────────────────


def test_the_tree_is_the_project_folder_and_only_pages_with_a_held_scope(
    client: TestClient, engine: Engine, key: str
) -> None:
    _seed_neighbours(engine)
    resp = client.get(_url(KB + "/tree"), headers=_auth(key))
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-hal0-Project"] == "hal0"
    roots = resp.json()["roots"]
    assert _names(roots) == ["Initiatives", "Component map", "Conventions and patterns"]
    assert all(node["parent_id"] is None for node in roots)
    by_id = {node["id"]: node for node in roots}
    # The caller's own page whole; another client's without its writer.
    assert by_id["kp-architecture"]["tags"] == [
        "knowledge:component",
        "agent:cc",
        "kind:durable",
        "project:hal0",
    ]
    assert by_id["kp-conventions"]["tags"] == [
        "knowledge:convention",
        "kind:durable",
        "project:hal0",
    ]
    assert "codex" not in resp.text
    # The fields the plugin reads, as the engine sent them.
    assert by_id["kp-architecture"]["description"] == "How is this codebase put together?"
    assert by_id["kp-architecture"]["trigger"]["tags_match"] == "all"
    assert engine.lines() == [f"GET {ENGINE_KB}/tree"]


def test_another_clients_page_shows_only_the_trigger_fields_the_plugin_reads(
    client: TestClient, engine: Engine, key: str
) -> None:
    """``trigger.tag_groups`` carries the writer's own tags in its scope leaf;
    the plugin reads only ``trigger.tags_match`` (its re-sync drift check)."""
    _seed_neighbours(engine)
    engine.kb.page(
        "Codex runbook",
        parent_id="kf-hal0",
        node_id="kp-runbook",
        tags=["agent:codex", "kind:durable", "project:hal0", "codex-secret-label"],
        trigger=compat_page_trigger("hal0", ["codex-secret-label"]),
    )
    roots = client.get(_url(KB + "/tree"), headers=_auth(key)).json()["roots"]
    by_id = {node["id"]: node for node in roots}
    assert by_id["kp-conventions"]["trigger"] == {"tags_match": "all"}
    assert by_id["kp-runbook"]["trigger"] == {"tags_match": "all"}
    assert by_id["kp-runbook"]["tags"] == ["kind:durable", "project:hal0"]
    assert "codex-secret-label" not in str(roots)
    # The caller's own page keeps its whole trigger.
    own = by_id["kp-architecture"]["trigger"]
    assert own["exclude_mental_models"] is True
    assert {"tags": ["knowledge:component"], "match": "all", "resolve": "exact"} in own[
        "tag_groups"
    ]


def test_a_project_with_no_folder_has_an_empty_tree_not_a_404(
    client: TestClient, engine: Engine, key: str
) -> None:
    """The plugin reads 404 as "no pages" and would then never create one."""
    assert client.get(_url(KB + "/tree"), headers=_auth(key)).json() == {"roots": []}
    engine.overrides[("GET", ENGINE_KB + "/tree")] = httpx.Response(
        404, json={"detail": "Bank not found"}
    )
    resp = client.get(_url(KB + "/tree"), headers=_auth(key))
    assert (resp.status_code, resp.json()) == (200, {"roots": []})


def test_an_engine_failure_on_the_tree_is_not_a_404(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine.overrides[("GET", ENGINE_KB + "/tree")] = httpx.Response(500, json={})
    resp = client.get(_url(KB + "/tree"), headers=_auth(key))
    assert (resp.status_code, _code(resp)) == (502, "memory.compat_engine_error")


# ── page read ───────────────────────────────────────────────────────────────


def test_the_callers_own_page_is_read_as_the_engine_renders_it(
    client: TestClient, engine: Engine, key: str
) -> None:
    _seed_neighbours(engine)
    resp = client.get(_url(KB + "/pages/kp-architecture"), headers=_auth(key))
    assert resp.status_code == 200, resp.text
    page = resp.json()
    assert page["body"] == "The API sits in front of the engine."
    assert '  - "agent:cc"' in page["markdown"]
    assert engine.lines() == [f"GET {ENGINE_KB}/tree", f"GET {ENGINE_KB}/pages/kp-architecture"]


def test_another_clients_page_is_read_without_its_writer(
    client: TestClient, engine: Engine, key: str
) -> None:
    _seed_neighbours(engine)
    page = client.get(_url(KB + "/pages/kp-conventions"), headers=_auth(key)).json()
    assert page["tags"] == ["knowledge:convention", "kind:durable", "project:hal0"]
    assert "codex" not in page["markdown"]
    assert page["markdown"].startswith("---\n")
    assert '  - "knowledge:convention"' in page["markdown"]
    assert page["body"] == "Commits are signed off."


@pytest.mark.parametrize(
    "page_id", ["kp-folded", "kp-unguarded", "kp-private", "kp-root", "kp-atlas", "kp-none"]
)
def test_a_page_the_tree_does_not_show_is_404_without_reading_it(
    client: TestClient, engine: Engine, key: str, page_id: str
) -> None:
    _seed_neighbours(engine)
    resp = client.get(_url(KB + f"/pages/{page_id}"), headers=_auth(key))
    assert (resp.status_code, _code(resp)) == (404, "memory.knowledge_page_not_found")
    assert engine.lines() == [f"GET {ENGINE_KB}/tree"]


# ── search ──────────────────────────────────────────────────────────────────


def test_search_asks_for_the_project_and_returns_only_shown_pages(
    client: TestClient, engine: Engine, key: str
) -> None:
    _seed_neighbours(engine)
    resp = client.get(
        _url(KB + "/search"), params={"q": "release process", "limit": 3}, headers=_auth(key)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["id"] for r in body["results"]] == ["kp-architecture", "kp-conventions"]
    assert body["total"] == 2
    assert set(body["results"][0]) == {
        "id",
        "name",
        "mental_model_id",
        "source_query",
        "snippet",
        "score",
        "updated_at",
    }
    call = engine.calls[-1]
    assert call.line == f"GET {ENGINE_KB}/search"
    assert call.params == {
        "q": ["release process"],
        "limit": ["50"],
        "tags": ["project:hal0"],
        "tags_match": ["all_strict"],
    }


def test_search_cuts_to_the_callers_limit(client: TestClient, engine: Engine, key: str) -> None:
    _seed_neighbours(engine)
    resp = client.get(_url(KB + "/search"), params={"q": "x", "limit": 1}, headers=_auth(key))
    assert [r["id"] for r in resp.json()["results"]] == ["kp-architecture"]


def test_search_with_no_shown_pages_asks_the_engine_nothing(
    client: TestClient, engine: Engine, key: str
) -> None:
    resp = client.get(_url(KB + "/search"), params={"q": "x"}, headers=_auth(key))
    assert resp.json() == {"results": [], "total": 0}
    assert engine.lines() == [f"GET {ENGINE_KB}/tree"]


@pytest.mark.parametrize(
    "params", [{}, {"q": " "}, {"q": "x", "limit": "0"}, {"q": "x", "limit": "51"}]
)
def test_search_refuses_a_bad_query(
    client: TestClient, engine: Engine, key: str, params: dict[str, str]
) -> None:
    resp = client.get(_url(KB + "/search"), params=params, headers=_auth(key))
    assert resp.status_code == 400
    assert engine.calls == []


# ── page create ─────────────────────────────────────────────────────────────


def test_the_first_page_creates_the_project_folder_and_goes_through_the_envelope(
    client: TestClient, engine: Engine, key: str
) -> None:
    resp = client.post(_url(KB + "/pages"), headers=_auth(key), json=SEED_PAGE)
    assert resp.status_code == 201, resp.text
    # The engine's answer, as the plugin parses it.
    assert set(resp.json()) == {"page_id", "mental_model_id", "operation_id"}
    assert _writes(engine) == [f"POST {ENGINE_KB}/folders", f"POST {ENGINE_KB}/pages"]
    folder_call, page_call = (c for c in engine.calls if c.method == "POST")
    assert folder_call.body == {"name": "project:hal0"}
    folder = engine.kb.get(engine.kb.nodes[0]["id"])
    assert folder is not None and folder["parent_id"] is None
    assert page_call.body == {
        "name": "Core concepts",
        "source_query": "What are the core concepts of this project?",
        "parent_id": folder["id"],
        "tags": ["knowledge:concept", "agent:cc", "kind:durable", "project:hal0"],
        "max_tokens": 4096,
        "trigger": {
            **PAGE_TRIGGER,
            "tag_groups": [
                {"tags": ["knowledge:concept"], "match": "all"},
                {"tags": ["project:hal0", "scope:global"], "match": "any"},
                NOT_PRIVATE,
            ],
            "exclude_mental_models": True,
        },
    }
    # And the page, as the engine stores its trigger, is shown in the tree.
    tree = client.get(_url(KB + "/tree"), headers=_auth(key)).json()
    assert _names(tree["roots"]) == ["Core concepts"]


def test_a_second_page_reuses_the_folder(client: TestClient, engine: Engine, key: str) -> None:
    seed_project_pages(engine)
    resp = client.post(_url(KB + "/pages"), headers=_auth(key), json=SEED_PAGE)
    assert resp.status_code == 201
    assert _writes(engine) == [f"POST {ENGINE_KB}/pages"]
    assert engine.calls[-1].body["parent_id"] == "kf-hal0"


def test_an_initiative_page_goes_in_the_folder_the_plugin_names(
    client: TestClient, engine: Engine, key: str
) -> None:
    seed_project_pages(engine)
    body = {
        "name": "Memory compat knowledge pages",
        "source_query": 'Summarize the "Memory compat knowledge pages" initiative.',
        "parent_id": "kf-initiatives",
        "tags": ["knowledge:feature-work"],
        "trigger": PAGE_TRIGGER,
    }
    resp = client.post(_url(KB + "/pages"), headers=_auth(key), json=body)
    assert resp.status_code == 201
    page_id = resp.json()["page_id"]
    assert engine.calls[-1].body["parent_id"] == "kf-initiatives"
    tree = client.get(_url(KB + "/tree"), headers=_auth(key)).json()
    initiatives = next(n for n in tree["roots"] if n["id"] == "kf-initiatives")
    assert [c["id"] for c in initiatives["children"]] == [page_id]


@pytest.mark.parametrize("parent_id", ["kf-atlas", "kf-hal0-elsewhere", "kp-architecture", 7])
def test_a_parent_outside_the_project_folder_is_refused(
    client: TestClient, engine: Engine, key: str, parent_id: Any
) -> None:
    _seed_neighbours(engine)
    for route in ("/pages", "/folders"):
        resp = client.post(
            _url(KB + route), headers=_auth(key), json={**SEED_PAGE, "parent_id": parent_id}
        )
        assert (resp.status_code, _code(resp)) == (403, "memory.compat_knowledge_parent_refused")
    assert _writes(engine) == []


def test_forged_tags_and_scope_are_replaced(client: TestClient, engine: Engine, key: str) -> None:
    seed_project_pages(engine)
    body = {
        **SEED_PAGE,
        "tags": [
            "knowledge:concept",
            "agent:codex",
            "visibility:private",
            "scope:global",
            "project:atlas",
            "source:bridge:codex",
            "kind:ephemeral",
        ],
        "trigger": {
            "tags_match": "any",
            "tag_groups": [],
            "exclude_mental_models": False,
            "exclude_mental_model_ids": ["mm-1"],
        },
        "managed": True,
    }
    resp = client.post(_url(KB + "/pages"), headers=_auth(key), json=body)
    assert resp.status_code == 201
    sent = engine.calls[-1].body
    assert sent["tags"] == ["knowledge:concept", "agent:cc", "kind:ephemeral", "project:hal0"]
    assert sent["trigger"] == {
        "tags_match": "any",
        "tag_groups": [
            {"tags": ["knowledge:concept"], "match": "any"},
            {"tags": ["project:hal0", "scope:global"], "match": "any"},
            NOT_PRIVATE,
        ],
        "exclude_mental_models": True,
    }
    assert resp.headers["X-hal0-Ignored-Fields"] == (
        "managed,trigger.exclude_mental_model_ids,trigger.exclude_mental_models,trigger.tag_groups"
    )


def test_a_page_with_no_tags_is_built_from_the_project_scope(
    client: TestClient, engine: Engine, key: str
) -> None:
    seed_project_pages(engine)
    body = {"name": "Everything", "source_query": "What is this project?"}
    assert client.post(_url(KB + "/pages"), headers=_auth(key), json=body).status_code == 201
    assert engine.calls[-1].body["trigger"] == {
        "tag_groups": [{"tags": ["project:hal0", "scope:global"], "match": "any"}, NOT_PRIVATE],
        "exclude_mental_models": True,
    }


def test_a_duplicate_name_is_the_engines_409(client: TestClient, engine: Engine, key: str) -> None:
    """The plugin's seed tolerates 409 (``req(..., [409])``)."""
    seed_project_pages(engine)
    body = {**SEED_PAGE, "name": "Component map"}
    resp = client.post(_url(KB + "/pages"), headers=_auth(key), json=body)
    assert resp.status_code == 409
    assert "already exists" in resp.text


def test_an_engine_404_on_a_write_is_502(client: TestClient, engine: Engine, key: str) -> None:
    """The plugin takes a 404 on a page write as "pages unsupported"."""
    seed_project_pages(engine)
    engine.overrides[("POST", ENGINE_KB + "/pages")] = httpx.Response(404, json={})
    resp = client.post(_url(KB + "/pages"), headers=_auth(key), json=SEED_PAGE)
    assert (resp.status_code, _code(resp)) == (502, "memory.compat_engine_error")


@pytest.mark.parametrize(
    "body",
    [
        {"source_query": "q"},
        {"name": "n"},
        {"name": "", "source_query": "q"},
        {"name": "n", "source_query": "q", "tags": "knowledge:x"},
        {"name": "n", "source_query": "q", "trigger": []},
        {"name": "n", "source_query": "q", "trigger": {"tags_match": "most"}},
        {"name": "n", "source_query": "q", "max_tokens": 0},
    ],
)
def test_a_malformed_page_is_400(
    client: TestClient, engine: Engine, key: str, body: dict[str, Any]
) -> None:
    resp = client.post(_url(KB + "/pages"), headers=_auth(key), json=body)
    assert resp.status_code == 400
    assert engine.calls == []


# ── node update ─────────────────────────────────────────────────────────────


def test_the_plugins_resync_of_its_own_page_keeps_the_envelope_and_scope(
    client: TestClient, engine: Engine, key: str
) -> None:
    seed_project_pages(engine)
    body = {"source_query": "How is it put together today?", "tags": ["knowledge:component"]}
    resp = client.patch(_url(KB + "/nodes/kp-architecture"), headers=_auth(key), json=body)
    assert resp.status_code == 200, resp.text
    call = engine.calls[-1]
    assert call.line == f"PATCH {ENGINE_KB}/nodes/kp-architecture"
    assert call.body == {
        "source_query": "How is it put together today?",
        "tags": ["knowledge:component", "agent:cc", "kind:durable", "project:hal0"],
        "trigger": {
            "tags_match": "all",
            "tag_groups": [
                {"tags": ["knowledge:component"], "match": "all"},
                {"tags": ["project:hal0", "scope:global"], "match": "any"},
                NOT_PRIVATE,
            ],
            "exclude_mental_models": True,
        },
    }
    assert resp.json()["id"] == "kp-architecture"
    assert resp.json()["parent_id"] is None
    # Still the caller's, still shown.
    tree = client.get(_url(KB + "/tree"), headers=_auth(key)).json()
    assert "Component map" in _names(tree["roots"])


def test_a_trigger_only_update_keeps_the_pages_own_tags(
    client: TestClient, engine: Engine, key: str
) -> None:
    seed_project_pages(engine)
    body = {"trigger": {"tags_match": "all_strict", "refresh_cron": "0 3 * * *"}}
    resp = client.patch(_url(KB + "/nodes/kp-architecture"), headers=_auth(key), json=body)
    assert resp.status_code == 200
    sent = engine.calls[-1].body
    assert sent["tags"] == ["knowledge:component", "agent:cc", "kind:durable", "project:hal0"]
    assert sent["trigger"]["refresh_cron"] == "0 3 * * *"
    assert sent["trigger"]["tag_groups"][0] == {
        "tags": ["knowledge:component"],
        "match": "all_strict",
    }


def test_a_rename_alone_leaves_tags_and_scope_alone(
    client: TestClient, engine: Engine, key: str
) -> None:
    seed_project_pages(engine)
    resp = client.patch(
        _url(KB + "/nodes/kp-architecture"), headers=_auth(key), json={"name": "Components"}
    )
    assert resp.status_code == 200
    assert engine.calls[-1].body == {"name": "Components"}


def test_another_clients_page_cannot_be_changed(
    client: TestClient, engine: Engine, store: BridgeStore, key: str
) -> None:
    """codex wrote it; cc may read it and not change it, and codex may."""
    seed_project_pages(engine, owner="codex")
    body = {"source_query": "Rewritten by someone else"}
    resp = client.patch(_url(KB + "/nodes/kp-architecture"), headers=_auth(key), json=body)
    assert (resp.status_code, _code(resp)) == (404, "memory.compat_knowledge_page_not_owned")
    assert _writes(engine) == []
    _row, codex = store.add_client("codex", created_by="t")
    resp = client.patch(_url(KB + "/nodes/kp-architecture"), headers=_auth(codex), json=body)
    assert resp.status_code == 200


def test_a_page_naming_two_agents_belongs_to_neither(
    client: TestClient, engine: Engine, key: str
) -> None:
    engine.kb.folder("project:hal0", node_id="kf-hal0")
    engine.kb.page(
        "Shared",
        parent_id="kf-hal0",
        node_id="kp-two",
        tags=["agent:cc", "agent:codex", "project:hal0"],
        trigger=compat_page_trigger("hal0", []),
    )
    resp = client.patch(_url(KB + "/nodes/kp-two"), headers=_auth(key), json={"name": "Mine"})
    assert (resp.status_code, _code(resp)) == (404, "memory.compat_knowledge_page_not_owned")


def test_a_folder_cannot_be_changed(client: TestClient, engine: Engine, key: str) -> None:
    seed_project_pages(engine)
    for node_id in ("kf-initiatives", "kf-hal0"):
        resp = client.patch(_url(KB + f"/nodes/{node_id}"), headers=_auth(key), json={"name": "x"})
        assert (resp.status_code, _code(resp)) == (404, "memory.compat_knowledge_page_not_owned")
    assert _writes(engine) == []


@pytest.mark.parametrize(
    "node_id", ["kp-folded", "kp-unguarded", "kp-private", "kp-root", "kp-atlas", "kf-atlas", "x"]
)
def test_a_node_outside_what_the_tree_shows_is_refused(
    client: TestClient, engine: Engine, key: str, node_id: str
) -> None:
    """404: the plugin's page re-sync ends on it, where a 403 would raise
    and abort the pass that imports the client's memory."""
    _seed_neighbours(engine)
    resp = client.patch(_url(KB + f"/nodes/{node_id}"), headers=_auth(key), json={"name": "x"})
    assert (resp.status_code, _code(resp)) == (404, "memory.compat_knowledge_node_refused")
    assert _writes(engine) == []


def test_an_update_that_changes_nothing_is_400(
    client: TestClient, engine: Engine, key: str
) -> None:
    seed_project_pages(engine)
    resp = client.patch(
        _url(KB + "/nodes/kp-architecture"), headers=_auth(key), json={"parent_id": None}
    )
    assert resp.status_code == 400
    assert _writes(engine) == []


# ── folder create ───────────────────────────────────────────────────────────


def test_a_folder_goes_in_the_project_folder(client: TestClient, engine: Engine, key: str) -> None:
    """The plugin's ``ensureFolder("Initiatives")``."""
    resp = client.post(_url(KB + "/folders"), headers=_auth(key), json={"name": "Initiatives"})
    assert resp.status_code == 201, resp.text
    folder = resp.json()
    assert folder["name"] == "Initiatives"
    assert folder["parent_id"] is None
    project, made = engine.kb.nodes
    assert project["name"] == "project:hal0"
    assert made["parent_id"] == project["id"]
    assert engine.calls[-1].body == {"name": "Initiatives", "parent_id": project["id"]}
    tree = client.get(_url(KB + "/tree"), headers=_auth(key)).json()
    assert [n["id"] for n in tree["roots"]] == [folder["id"]]


def test_two_project_folders_are_read_as_one(client: TestClient, engine: Engine, key: str) -> None:
    """Folder names are not unique in the engine. Should two exist, both are
    read and new nodes go in the one with the lowest id."""
    seed_project_pages(engine)
    engine.kb.folder("project:hal0", node_id="kf-another")
    engine.kb.page(
        "Late page",
        parent_id="kf-another",
        node_id="kp-late",
        tags=["agent:cc", "kind:durable", "project:hal0"],
        trigger=compat_page_trigger("hal0", []),
    )
    tree = client.get(_url(KB + "/tree"), headers=_auth(key)).json()
    assert {n["id"] for n in tree["roots"]} == {"kf-initiatives", "kp-architecture", "kp-late"}
    client.post(_url(KB + "/folders"), headers=_auth(key), json={"name": "Runbooks"})
    assert engine.calls[-1].body["parent_id"] == "kf-another"


# ── who ─────────────────────────────────────────────────────────────────────


ROUTES = [
    ("GET", "/tree", None),
    ("GET", "/pages/kp-architecture", None),
    ("GET", "/search?q=x", None),
    ("POST", "/pages", SEED_PAGE),
    ("PATCH", "/nodes/kp-architecture", {"name": "x"}),
    ("POST", "/folders", {"name": "x"}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_route_needs_a_memory_key(
    client: TestClient,
    engine: Engine,
    store: BridgeStore,
    method: str,
    path: str,
    body: Any,
) -> None:
    seed_project_pages(engine)
    url = _url(KB + path)
    assert client.request(method, url, json=body).status_code == 401
    _row, agents_only = store.add_client("runner", created_by="t", scopes=("agents",))
    resp = client.request(method, url, headers=_auth(agents_only), json=body)
    assert (resp.status_code, _code(resp)) == (403, "memory.compat_scope_required")
    resp = client.request(method, url, headers=_auth(CLIENT_KEY), json=body)
    assert resp.status_code == 403
    assert engine.calls == []


def test_the_admin_credential_writes_pages_as_admin(client: TestClient, engine: Engine) -> None:
    seed_project_pages(engine)
    resp = client.post(_url(KB + "/pages"), headers=_auth(ADMIN_KEY), json=SEED_PAGE)
    assert resp.status_code == 201
    assert "agent:admin" in engine.calls[-1].body["tags"]
    # cc's page is not the admin's to change.
    resp = client.patch(
        _url(KB + "/nodes/kp-architecture"), headers=_auth(ADMIN_KEY), json={"name": "x"}
    )
    assert (resp.status_code, _code(resp)) == (404, "memory.compat_knowledge_page_not_owned")


def test_writes_wait_for_the_write_gate(
    client: TestClient,
    engine: Engine,
    key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from hal0.memory import write_gate

    seed_project_pages(engine)
    gate = tmp_path / "memory-writes-paused.json"
    monkeypatch.setattr(write_gate, "gate_path", lambda: gate)
    write_gate.engage_gate(run_id="run-1", path=gate)
    try:
        for method, path, body in ROUTES[3:]:
            resp = client.request(method, _url(KB + path), headers=_auth(key), json=body)
            assert (resp.status_code, _code(resp)) == (503, "memory.writes_paused")
        assert client.get(_url(KB + "/tree"), headers=_auth(key)).status_code == 200
    finally:
        write_gate.lift_gate(path=gate)
    assert _writes(engine) == []


def test_the_bank_in_the_url_decides_the_project(
    client: TestClient, engine: Engine, key: str
) -> None:
    """``<harness>::<repository>`` under any harness name, and through an
    alias, lands on one folder; another repository's bank sees its own."""
    _seed_neighbours(engine)
    for bank in ("coding-agent%3A%3Ahal0-mono", "claude-code%3A%3Ahal0", "codex%3A%3Ahal0-mono"):
        tree = client.get(_url(f"/v1/default/banks/{bank}/knowledge-base/tree"), headers=_auth(key))
        assert "Component map" in _names(tree.json()["roots"]), bank
    atlas = client.get(
        _url("/v1/default/banks/coding-agent%3A%3Aatlas/knowledge-base/tree"), headers=_auth(key)
    )
    assert atlas.headers["X-hal0-Project"] == "atlas"
    assert _names(atlas.json()["roots"]) == ["Atlas map"]
