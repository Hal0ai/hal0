"""A fold-in's knowledge pages, read through ``/api/memory/compat`` (#3635).

End to end across both engine doubles: ``hal0 memory migrate fold-in`` folds
two old coding banks with the coding-agents plugin's pages into
``hal0:shared`` in the fold-in's fake engine
(``tests/memory/test_fold_in.py``); the folded tree, with each page's model,
is then what the compat route tests' engine double serves
(``tests/api/test_memory_compat_routes.py``), and the plugin's calls go
through the real router: the tree, a page read by the id the page had in its
old bank, and a search. A client of the page's project sees it; a client of
another project does not, by either id.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api.auth import AuthEnforcementMiddleware
from hal0.bridge.store import BridgeStore
from hal0.memory import knowledge_pages as kp
from tests.api import test_memory_compat_routes as routes
from tests.api.test_memory_compat_routes import Engine, _auth, _code, _url, build_app
from tests.memory.test_fold_in import (
    CT105_BANKS,
    STANDARD_PAGES,
    Box,
    _std_id,
    seed_ct105,
    seed_pages,
)

_box_keys = routes._box_keys
_fresh_posture_cache = routes._fresh_posture_cache
store = routes.store

WEB = "/v1/default/banks/coding-agent%3A%3Aweb/knowledge-base"
ATLAS = "/v1/default/banks/coding-agent%3A%3Aatlas/knowledge-base"


@pytest.fixture
def folded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Box:
    """Two old coding banks with pages, folded with their harness mapped to ``cc``."""
    box = Box(tmp_path / "box", monkeypatch)
    box.clients.add("cc")
    seed_pages(box)
    seed_pages(box, "coding-agent::atlas", prefix="a-")
    import asyncio

    result = asyncio.run(box.fold(owners={"coding-agent": "cc"}).run())
    assert result["cut_over"] is True
    return box


def _serve(box: Box) -> Engine:
    """The compat engine double, holding ``hal0:shared``'s folded tree."""
    engine = Engine()
    bank = box.engine.banks["hal0:shared"]
    for node in bank["pages"]:
        if node["kind"] == "folder":
            engine.kb.folder(node["name"], parent_id=node.get("parent_id"), node_id=node["id"])
            continue
        model = bank["models"][node["mental_model_id"]]
        engine.kb.page(
            node["name"],
            parent_id=node.get("parent_id"),
            node_id=node["id"],
            tags=list(model["tags"]),
            trigger=model["trigger"],
            source_query=model["source_query"],
            content=model["content"],
        )
    return engine


@pytest.fixture
def served(folded: Box) -> Engine:
    return _serve(folded)


@pytest.fixture
def client(served: Engine) -> Iterator[TestClient]:
    app = build_app(served.handler, unified_bank=False, two_bank=True)
    yield TestClient(AuthEnforcementMiddleware(app), client=("127.0.0.1", 50000))


@pytest.fixture
def key(store: BridgeStore, folded: Box) -> str:
    _row, plain = store.add_client("cc", created_by="t")
    return plain


def _names(nodes: list[dict[str, Any]]) -> list[str]:
    out = []
    for node in nodes:
        out.append(node["name"])
        out.extend(_names(node.get("children") or []))
    return out


def test_the_tree_shows_a_projects_folded_pages_to_its_clients_only(
    client: TestClient, key: str
) -> None:
    web = client.get(_url(WEB + "/tree"), headers=_auth(key))
    assert web.status_code == 200, web.text
    assert sorted(_names(web.json()["roots"])) == ["Bank layout", "Component map", "Initiatives"]
    atlas = client.get(_url(ATLAS + "/tree"), headers=_auth(key)).json()["roots"]
    assert sorted(_names(atlas)) == ["Bank layout", "Component map", "Initiatives"]
    web_ids = {n["id"] for n in web.json()["roots"]}
    assert not web_ids & {n["id"] for n in atlas}
    # The folded page is the caller's own: its owner was mapped to cc.
    page = next(n for n in web.json()["roots"] if n["name"] == "Component map")
    assert "agent:cc" in page["tags"]


def test_a_page_is_read_by_the_id_it_had_in_the_old_bank(client: TestClient, key: str) -> None:
    new = kp.folded_node_id("coding-agent::web", "kp-init", "page")
    by_old = client.get(_url(WEB + "/pages/kp-init"), headers=_auth(key))
    assert by_old.status_code == 200, by_old.text
    assert by_old.json()["id"] == new
    assert by_old.json()["name"] == "Bank layout"
    by_new = client.get(_url(WEB + f"/pages/{new}"), headers=_auth(key))
    assert by_new.json() == by_old.json()
    # The link the page synthesis wrote names the new id; the old one resolves too.
    linked = kp.folded_node_id("coding-agent::web", "kp-map", "page")
    assert f"[[page:{linked}]]" in by_old.json()["body"]
    assert client.get(_url(WEB + "/pages/kp-map"), headers=_auth(key)).json()["id"] == linked


def test_another_projects_client_cannot_read_a_page_by_either_id(
    client: TestClient, key: str
) -> None:
    new = kp.folded_node_id("coding-agent::web", "kp-init", "page")
    for page_id in ("kp-init", new):
        resp = client.get(_url(ATLAS + f"/pages/{page_id}"), headers=_auth(key))
        assert resp.status_code == 404, page_id
        assert _code(resp) == "memory.knowledge_page_not_found"


def test_search_finds_a_projects_folded_pages_only(client: TestClient, key: str) -> None:
    hits = client.get(_url(WEB + "/search"), params={"q": "map"}, headers=_auth(key)).json()
    ids = {r["id"] for r in hits["results"]}
    assert ids == {
        kp.folded_node_id("coding-agent::web", "kp-map", "page"),
        kp.folded_node_id("coding-agent::web", "kp-init", "page"),
    }


def test_an_old_id_updates_the_page_it_became(client: TestClient, key: str, served: Engine) -> None:
    resp = client.patch(
        _url(WEB + "/nodes/kp-map"), json={"source_query": "What is where?"}, headers=_auth(key)
    )
    assert resp.status_code == 200, resp.text
    node = served.kb.get(kp.folded_node_id("coding-agent::web", "kp-map", "page"))
    assert node is not None and node["source_query"] == "What is where?"
    # An old folder id is a parent in the project's folder, as its new id is.
    made = client.post(
        _url(WEB + "/pages"),
        json={"name": "New", "source_query": "q?", "parent_id": "kf-init"},
        headers=_auth(key),
    )
    assert made.status_code == 201, made.text
    new_page = served.kb.get(made.json()["page_id"])
    assert new_page is not None
    assert new_page["parent_id"] == kp.folded_node_id("coding-agent::web", "kf-init", "folder")


def test_without_the_map_an_old_id_is_a_page_that_does_not_exist(
    client: TestClient, key: str
) -> None:
    """Mutation check of the map: the old id reaches its page only through it."""
    kp.page_map_path().unlink()
    resp = client.get(_url(WEB + "/pages/kp-init"), headers=_auth(key))
    assert resp.status_code == 404
    assert _code(resp) == "memory.knowledge_page_not_found"


def test_a_map_row_pointing_at_another_projects_page_does_not_widen_the_view(
    client: TestClient, key: str
) -> None:
    """Mutation check of the visibility rule: the map only says which node an
    old id meant; compat still shows a node only when the caller's view holds it."""
    import json

    path = kp.page_map_path()
    data = json.loads(path.read_text())
    atlas_page = kp.folded_node_id("coding-agent::atlas", "a-kp-map", "page")
    data["targets"]["hal0:shared"]["kp-init"]["id"] = atlas_page
    path.write_text(json.dumps(data))
    resp = client.get(_url(WEB + "/pages/kp-init"), headers=_auth(key))
    assert resp.status_code == 404


HAL0 = "/v1/default/banks/coding-agent%3A%3Ahal0-mono/knowledge-base"


def test_the_reference_boxs_three_banks_read_back_through_compat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: BridgeStore
) -> None:
    """Three coding banks of one project, five standard names each: one plain
    set and two renamed sets in the tree, and every old id still reads."""
    import asyncio

    box = Box(tmp_path / "box", monkeypatch)
    seed_ct105(box)
    result = asyncio.run(box.fold(owners={"coding-agent": "cc"}).run())
    assert result["cut_over"] is True
    engine = _serve(box)
    app = build_app(engine.handler, unified_bank=False, two_bank=True)
    client = TestClient(AuthEnforcementMiddleware(app), client=("127.0.0.1", 50000))
    _row, key = store.add_client("cc", created_by="t")
    roots = client.get(_url(HAL0 + "/tree"), headers=_auth(key)).json()["roots"]
    names = sorted(n["name"] for n in roots)
    assert names == sorted(
        [*STANDARD_PAGES]
        + [f"{n} (Hal0::Agents)" for n in STANDARD_PAGES]
        + [f"{n} (pi-coder)" for n in STANDARD_PAGES]
    )
    for bank in CT105_BANKS:
        for index, name in enumerate(STANDARD_PAGES):
            resp = client.get(_url(HAL0 + f"/pages/{_std_id(bank, index)}"), headers=_auth(key))
            assert resp.status_code == 200, (bank, index, resp.text)
            assert resp.json()["id"] == kp.folded_node_id(bank, _std_id(bank, index), "page")
            shown = name if bank == "coding-agent::hal0-mono" else f"{name} ({bank})"
            assert resp.json()["name"] == shown
            if bank == "coding-agent::hal0-mono":
                # Its pages were scoped to hal0 already and keep their content:
                # the link to the bank's first page names that page's new id.
                first = kp.folded_node_id(bank, _std_id(bank, 0), "page")
                assert f"[[page:{first}]]" in resp.json()["body"]
            else:
                # A bank whose project comes from tags: rebuilt from nothing.
                assert not resp.json()["body"]
