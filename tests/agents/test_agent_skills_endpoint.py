"""HTTP tests for ``GET /api/agents/skills`` (v0.3 PR-11).

Pins the static skills catalog the dashboard SidebarAgentBlock renders.
Validates:

* 200 always — the catalog is hardcoded, no failure mode.
* Shape: ``{skills, groups, total, source, note}``.
* ``source == "static"`` so future swaps to a live registry can flip
  the field name without breaking callers that read it.
* Each ``skills[*]`` row carries ``name`` / ``description`` /
  ``category`` / ``source`` and the ``source`` value matches a key in
  ``groups``.
* The bundled hal0 MCP servers (``hal0-admin`` + ``hal0-memory``) ship
  with the documented tool sets (slot_*, model_*, memory_*).

These tests also serve as the contract for the v0.4 swap to a
live hermes ``tools/list`` query: the body shape must stay
backwards-compatible.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0.api.agents import skills as skills_route


def test_skills_endpoint_returns_200_with_required_fields(client: TestClient) -> None:
    r = client.get("/api/agents/skills")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) >= {"skills", "groups", "total", "source", "note"}


def test_skills_total_matches_skills_length(client: TestClient) -> None:
    r = client.get("/api/agents/skills")
    body = r.json()
    assert body["total"] == len(body["skills"])


def test_skills_groups_counts_match_skill_sources(client: TestClient) -> None:
    r = client.get("/api/agents/skills")
    body = r.json()
    rebuilt: dict[str, int] = {}
    for row in body["skills"]:
        rebuilt[row["source"]] = rebuilt.get(row["source"], 0) + 1
    assert rebuilt == body["groups"]


def test_skills_each_row_has_required_keys(client: TestClient) -> None:
    r = client.get("/api/agents/skills")
    body = r.json()
    required = {"name", "description", "category", "source"}
    for row in body["skills"]:
        assert required <= set(row.keys()), row


def test_skills_source_field_is_static_for_v0_3(client: TestClient) -> None:
    """v0.4 may flip this to ``"hermes-runtime"`` — pinning the value
    here forces an intentional change rather than a silent drift."""
    r = client.get("/api/agents/skills")
    body = r.json()
    assert body["source"] == "static"


def test_skills_includes_hermes_core_tools(client: TestClient) -> None:
    """The vendored upstream catalog must include the canonical 4-tool
    set (read/write/edit/bash) — pi-mono's "minimal-by-design" floor."""
    r = client.get("/api/agents/skills")
    body = r.json()
    names = {row["name"] for row in body["skills"] if row["source"] == "hermes-core"}
    assert {"read", "write", "edit", "bash"} <= names


def test_skills_includes_hal0_admin_tools(client: TestClient) -> None:
    """hal0-admin MCP must advertise slot/model/hardware/log subsets."""
    r = client.get("/api/agents/skills")
    body = r.json()
    names = {row["name"] for row in body["skills"] if row["source"] == "hal0-admin"}
    assert "slot_list" in names
    assert "slot_swap" in names
    assert "model_swap" in names
    assert "hardware_probe" in names
    assert "log_tail" in names


def test_skills_includes_hal0_memory_tools(client: TestClient) -> None:
    """hal0-memory MCP must advertise add/search/list/delete."""
    r = client.get("/api/agents/skills")
    body = r.json()
    names = {row["name"] for row in body["skills"] if row["source"] == "hal0-memory"}
    assert {"memory_add", "memory_search", "memory_list", "memory_delete"} <= names


def test_skills_gated_tools_documented_in_description(client: TestClient) -> None:
    """ADR-0004 two-tier-scope tools (model_pull, slot_delete,
    config_write, memory_delete) should be self-describing as gated so
    a UI hint without checking the policy file is accurate."""
    r = client.get("/api/agents/skills")
    body = r.json()
    by_name = {row["name"]: row for row in body["skills"]}
    assert "Gated" in by_name["model_pull"]["description"]
    assert "Gated" in by_name["slot_delete"]["description"]
    assert "Gated" in by_name["config_write"]["description"]
    # memory_delete describes its conditional gate.
    assert "Gated" in by_name["memory_delete"]["description"]


def test_skills_no_duplicate_names_within_source(client: TestClient) -> None:
    r = client.get("/api/agents/skills")
    body = r.json()
    seen: set[tuple[str, str]] = set()
    for row in body["skills"]:
        key = (row["source"], row["name"])
        assert key not in seen, f"duplicate skill {key}"
        seen.add(key)


def test_skills_module_constants_match_response(client: TestClient) -> None:
    """Belt-and-braces: the route should pull from the documented
    constants. If somebody re-implements the response in the handler,
    this test forces them to update both."""
    r = client.get("/api/agents/skills")
    body = r.json()
    expected_total = len(skills_route.HERMES_TOOL_CATALOG) + len(skills_route.HAL0_MCP_TOOL_CATALOG)
    assert body["total"] == expected_total
