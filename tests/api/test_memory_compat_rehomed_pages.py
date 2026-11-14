"""Rehomed knowledge pages, read through ``/api/memory/compat``.

End to end across both engine doubles, as ``test_memory_compat_folded_pages``
does for the fold-in: the box is folded with ``Hal0::Agents`` holding the
reference box's 32 nodes that name no project, then ``hal0 memory knowledge
rehome --bank Hal0::Agents --project hal0`` moves them into ``project:hal0``
(``tests/memory/test_knowledge_rehome.py``). The compat route tests' engine
double then serves ``hal0:shared``, and the coding-agents plugin, reaching
compat as ``claude-code`` with bank ``claude-code::hal0``, is shown the pages
and reads them by the ids they had in ``Hal0::Agents``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api.auth import AuthEnforcementMiddleware
from hal0.bridge.store import BridgeStore
from hal0.memory import knowledge_pages as kp
from hal0.memory.fold_in import FoldInError
from tests.api import test_memory_compat_routes as routes
from tests.api.test_memory_compat_folded_pages import _serve
from tests.api.test_memory_compat_routes import _auth, _url, build_app
from tests.memory.test_fold_in import STANDARD_PAGES, Box
from tests.memory.test_knowledge_rehome import (
    BANK,
    INITIATIVES,
    Writes,
    _ini,
    _std,
    fold_box,
    rehome,
)

_box_keys = routes._box_keys
_fresh_posture_cache = routes._fresh_posture_cache
store = routes.store

HAL0 = "/v1/default/banks/claude-code%3A%3Ahal0/knowledge-base"
OTHER = "/v1/default/banks/claude-code%3A%3Aatlas/knowledge-base"


def _names(nodes: list[dict[str, Any]]) -> list[str]:
    out = []
    for node in nodes:
        out.append(node["name"])
        out.extend(_names(node.get("children") or []))
    return out


def _client(box: Box) -> TestClient:
    # An unplaced page's model keeps the trigger as the old bank's export gave
    # it, a JSON string; the compat engine double takes the decoded object.
    for model in box.engine.banks["hal0:shared"]["models"].values():
        if isinstance(model.get("trigger"), str):
            model["trigger"] = json.loads(model["trigger"])
    app = build_app(_serve(box).handler, unified_bank=False, two_bank=True)
    return TestClient(AuthEnforcementMiddleware(app), client=("127.0.0.1", 50000))


def _box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kw: Any) -> Box:
    async def go() -> Box:
        box = await fold_box(tmp_path / "box", monkeypatch)
        Writes(box)
        if kw.pop("apply", True):
            await rehome(box, owner="claude-code", **kw).rehome()
        return box

    return asyncio.run(go())


def test_before_the_rehome_compat_shows_none_of_the_banks_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: BridgeStore
) -> None:
    box = _box(tmp_path, monkeypatch, apply=False)
    _row, key = store.add_client("claude-code", created_by="t")
    roots = _client(box).get(_url(HAL0 + "/tree"), headers=_auth(key)).json()["roots"]
    # Only claude-code::hal0's own five standard pages, which the fold-in placed.
    assert sorted(_names(roots)) == sorted(STANDARD_PAGES)


def test_after_the_rehome_compat_shows_every_page_and_resolves_old_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: BridgeStore
) -> None:
    box = _box(tmp_path, monkeypatch)
    _row, key = store.add_client("claude-code", created_by="t")
    client = _client(box)
    roots = client.get(_url(HAL0 + "/tree"), headers=_auth(key)).json()["roots"]
    initiatives = [f"Initiative {i:02d}" for i in range(INITIATIVES)]
    assert sorted(_names(roots)) == sorted(
        [
            *STANDARD_PAGES,
            *(f"{n} (Hal0::Agents)" for n in STANDARD_PAGES),
            "Initiatives",
            *initiatives,
        ]
    )
    # The plugin's own pages: it may change them.
    page = next(n for n in roots if n["name"] == "Component map (Hal0::Agents)")
    assert "agent:claude-code" in page["tags"]
    for old, name in [
        (_std(0), "Component map (Hal0::Agents)"),
        (_ini(7), "Initiative 07"),
    ]:
        resp = client.get(_url(HAL0 + f"/pages/{old}"), headers=_auth(key))
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == kp.folded_node_id(BANK, old, "page")
        assert resp.json()["name"] == name
        assert not resp.json()["body"]
    # An old folder id is a parent in the project's folder.
    made = client.post(
        _url(HAL0 + "/pages"),
        json={"name": "New one", "source_query": "q?", "parent_id": "kf-initiatives"},
        headers=_auth(key),
    )
    assert made.status_code == 201, made.text


def test_another_projects_client_sees_none_of_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: BridgeStore
) -> None:
    box = _box(tmp_path, monkeypatch)
    _row, key = store.add_client("claude-code", created_by="t")
    client = _client(box)
    resp = client.get(_url(OTHER + "/tree"), headers=_auth(key))
    assert resp.status_code == 200
    assert resp.json()["roots"] == []
    for page_id in (_std(0), kp.folded_node_id(BANK, _std(0), "page")):
        assert client.get(_url(OTHER + f"/pages/{page_id}"), headers=_auth(key)).status_code == 404


def test_a_mutated_project_label_hides_the_pages_from_compat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: BridgeStore
) -> None:
    """Mutation check of the tags half of the stamp: a page whose labels lack
    ``project:hal0`` fails compat's read rule however its trigger reads."""
    real = kp.page_tags

    def unlabelled(raw: Any, *, agent: str, resolution: Any) -> tuple[list[str], list[str]]:
        labels, leaf = real(raw, agent=agent, resolution=resolution)
        return [t for t in labels if not t.startswith("project:")], leaf

    box = _box(tmp_path, monkeypatch, apply=False)
    monkeypatch.setattr(kp, "page_tags", unlabelled)
    # The rehome's own check reads the labels back, and says so...
    with pytest.raises(FoldInError, match="not in project:hal0 with its scope"):
        asyncio.run(rehome(box, owner="claude-code").rehome())
    # ...and compat, whose read rule is the authority, shows none of them.
    _row, key = store.add_client("claude-code", created_by="t")
    roots = _client(box).get(_url(HAL0 + "/tree"), headers=_auth(key)).json()["roots"]
    assert sorted(_names(roots)) == sorted([*STANDARD_PAGES, "Initiatives"])
