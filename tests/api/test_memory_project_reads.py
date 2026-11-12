"""The ``project`` field on reads, through REST and MCP (ADR-0058 decision 7b).

A read with no ``project`` (or ``"*"``) covers the whole bank; a name narrows
it to that project plus global memory. The field is passed to the provider
only when the caller sent one. Also here: the list route's ``?bank=`` alias
for the two-bank private bank, and the reflect route's mental-model exclusion
taken from the bank the reflect reads.

Run targeted:
    .venv/bin/python -m pytest tests/api/test_memory_project_reads.py -q
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes
from hal0.mcp import memory as memory_mcp
from hal0.memory.hindsight_provider import HindsightProvider

from ..memory.test_two_bank_layout import ME, PUBLIC, TWO, provider, seeded


async def _rest(
    prov: Any, method: str, path: str, *, headers: dict[str, str] | None = None, **kw: Any
) -> httpx.Response:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory")
    app.state.memory_provider = prov
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        return await client.request(
            method,
            f"http://test/api/memory{path}",
            headers={"X-hal0-Agent": ME, **(headers or {})},
            **kw,
        )


def _mcp(prov: Any, *, private: bool = False):
    return memory_mcp.make_dispatcher(
        prov, client_id_resolver=lambda: ME, private_resolver=lambda: private
    )


def _texts(items: Any) -> set[str]:
    return {item["text"] for item in items}


class _Recorder:
    """A provider double that records the keywords each read was called with."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def search(self, **kw: Any) -> list[Any]:
        self.calls.append(("search", kw))
        return []

    async def recall(self, **kw: Any) -> list[Any]:
        self.calls.append(("recall", kw))
        return []

    async def list_items(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("list_items", kw))
        return {"items": [], "next_cursor": None}

    async def reflect(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("reflect", kw))
        return {"text": "x"}


# ── REST ──────────────────────────────────────────────────────────────────────

ROUTES = [
    ("search", "POST", "/search", {"query": "q"}),
    ("recall", "POST", "/recall", {"query": "q"}),
    ("reflect", "POST", "/reflect", {"query": "q"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method_name", "verb", "path", "body"), ROUTES)
async def test_rest_reads_pass_the_project_only_when_sent(method_name, verb, path, body) -> None:
    prov = _Recorder()
    assert (await _rest(prov, verb, path, json=body)).status_code == 200
    assert (await _rest(prov, verb, path, json={**body, "project": " atlas "})).status_code == 200
    assert (await _rest(prov, verb, path, json={**body, "project": "*"})).status_code == 200
    plain, named, every = (kw for name, kw in prov.calls if name == method_name)
    assert "project" not in plain
    assert named["project"] == "atlas"
    assert every["project"] == "*"


@pytest.mark.asyncio
async def test_rest_list_passes_the_project_from_the_query() -> None:
    prov = _Recorder()
    assert (await _rest(prov, "GET", "/list")).status_code == 200
    assert (await _rest(prov, "GET", "/list", params={"project": "atlas"})).status_code == 200
    plain, named = (kw for _name, kw in prov.calls)
    assert "project" not in plain and named["project"] == "atlas"


@pytest.mark.asyncio
@pytest.mark.parametrize(("_name", "verb", "path", "body"), ROUTES)
@pytest.mark.parametrize("project", [7, ["atlas"], {"name": "atlas"}, True])
async def test_rest_reads_refuse_a_project_that_is_not_a_string(
    _name, verb, path, body, project
) -> None:
    resp = await _rest(_Recorder(), verb, path, json={**body, "project": project})
    assert resp.status_code == 400
    assert "'project' must be a string" in resp.text


@pytest.mark.asyncio
async def test_rest_recall_reads_one_project_or_every_project() -> None:
    _engine, prov = seeded(TWO)
    whole = await _rest(prov, "POST", "/recall", json={"query": "q"})
    narrowed = await _rest(prov, "POST", "/recall", json={"query": "q", "project": "p"})
    assert _texts(whole.json()["items"]) == PUBLIC | {"private own"}
    assert _texts(narrowed.json()["items"]) == {"untagged", "global", "project P"}


@pytest.mark.asyncio
async def test_rest_list_and_search_read_one_project() -> None:
    _engine, prov = seeded(TWO)
    listed = await _rest(prov, "GET", "/list", params={"project": "q"})
    found = await _rest(prov, "POST", "/search", json={"query": "q", "project": "q"})
    assert _texts(listed.json()["items"]) == {"untagged", "global", "project Q"}
    assert _texts(found.json()["items"]) == {"untagged", "global", "project Q"}


@pytest.mark.asyncio
async def test_rest_add_with_a_project_is_readable_by_a_shared_recall() -> None:
    _engine, prov = seeded(TWO)
    added = await _rest(prov, "POST", "/add", json={"text": "atlas note", "project": "atlas"})
    assert added.status_code == 200, added.text
    out = await _rest(prov, "POST", "/recall", json={"query": "q"})
    assert "atlas note" in _texts(out.json()["items"])
    narrowed = await _rest(prov, "POST", "/recall", json={"query": "q", "project": "atlas"})
    assert _texts(narrowed.json()["items"]) == {"untagged", "global", "atlas note"}


@pytest.mark.asyncio
async def test_the_list_alias_answers_for_the_callers_own_private_bank() -> None:
    _engine, prov = seeded(TWO)
    private = {"X-hal0-Private": "1"}
    resp = await _rest(prov, "GET", "/list", params={"bank": "hal0:private"}, headers=private)
    assert resp.status_code == 200, resp.text
    assert _texts(resp.json()["items"]) == {"private own"}
    shared = await _rest(prov, "GET", "/list", params={"bank": "hal0:shared"})
    assert _texts(shared.json()["items"]) == PUBLIC | {"private own"}
    # Without the private toggle the alias is no door into a private namespace.
    refused = await _rest(prov, "GET", "/list", params={"bank": "hal0:private"})
    assert refused.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "bank", "excluded"),
    [
        ({}, "hal0:shared", False),
        ({"X-hal0-Private": "1"}, "hal0:private", True),
    ],
)
async def test_the_reflect_route_leaves_mental_models_out_where_the_bank_needs_it(
    headers, bank, excluded
) -> None:
    engine, prov = seeded(TWO)
    resp = await _rest(prov, "POST", "/reflect", json={"query": "q"}, headers=headers)
    assert resp.status_code == 200, resp.text
    (call,) = engine.reflects
    assert (call["bank_id"], call["exclude_mental_models"]) == (bank, excluded)


@pytest.mark.asyncio
async def test_the_reflect_route_still_leaves_mental_models_out_on_a_legacy_unified_bank() -> None:
    engine, prov = seeded("legacy_unified")
    await _rest(prov, "POST", "/reflect", json={"query": "q"})
    assert engine.reflects[0]["exclude_mental_models"] is True


@pytest.mark.asyncio
async def test_a_provider_that_cannot_say_is_treated_as_sharing_a_bank() -> None:
    prov = _Recorder()
    await _rest(prov, "POST", "/reflect", json={"query": "q"})
    assert prov.calls[0][1]["exclude_mental_models"] is True


# ── MCP ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["memory_search", "memory_recall", "memory_reflect"])
async def test_mcp_reads_pass_the_project_only_when_sent(tool) -> None:
    prov = _Recorder()
    dispatch = _mcp(prov)
    assert (await dispatch(tool, {"query": "q"}))["status"] == "ok"
    assert (await dispatch(tool, {"query": "q", "project": "atlas"}))["status"] == "ok"
    plain, named = (kw for _name, kw in prov.calls)
    assert "project" not in plain and named["project"] == "atlas"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["memory_search", "memory_recall", "memory_reflect"])
async def test_mcp_reads_refuse_a_project_that_is_not_a_string(tool) -> None:
    out = await _mcp(_Recorder())(tool, {"query": "q", "project": ["atlas"]})
    assert out["status"] == "error" and out["error"]["code"] == "mcp.memory_schema"


@pytest.mark.parametrize("tool", ["memory_search", "memory_recall", "memory_reflect"])
def test_the_mcp_read_tools_advertise_the_project_parameter(tool) -> None:
    definition = memory_mcp._memory_tool_definitions()[tool]
    assert "project" in definition.parameters["properties"]
    assert "project" not in definition.parameters.get("required", [])
    assert "project" in definition.description


@pytest.mark.asyncio
async def test_mcp_recall_reads_one_project_or_every_project() -> None:
    _engine, prov = seeded(TWO)
    dispatch = _mcp(prov)
    whole = await dispatch("memory_recall", {"query": "q"})
    narrowed = await dispatch("memory_recall", {"query": "q", "project": "p"})
    assert _texts(whole["results"]) == PUBLIC | {"private own"}
    assert _texts(narrowed["results"]) == {"untagged", "global", "project P"}


@pytest.mark.asyncio
async def test_mcp_add_with_a_project_is_readable_by_mcp_recall() -> None:
    engine = seeded(TWO)[0]
    prov: HindsightProvider = provider(TWO, engine)
    dispatch = _mcp(prov)
    added = await dispatch("memory_add", {"text": "atlas note", "project": "atlas"})
    assert added["status"] == "ok", added
    assert "project:atlas" in engine.retained[0]["tags"]
    out = await dispatch("memory_recall", {"query": "q", "project": "atlas"})
    assert _texts(out["results"]) == {"untagged", "global", "atlas note"}
