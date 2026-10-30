"""``model_list`` / ``capability_list`` expose and forward their filter args (#3161)."""

from __future__ import annotations

from typing import Any

import pytest

from hal0.mcp import admin


def test_model_list_schema_exposes_filter_params() -> None:
    schema = admin.tool_param_schema("model_list")
    props = schema["properties"]
    assert set(props) >= {
        "installed",
        "type",
        "capability",
        "runs_on",
        "q",
        "fields",
        "limit",
        "offset",
    }
    assert props["installed"]["type"] == "boolean"
    assert props["limit"]["type"] == "integer"
    assert props["offset"]["type"] == "integer"
    for name in ("type", "capability", "runs_on", "q", "fields"):
        assert props[name]["type"] == "string", name
    assert schema["required"] == []


def test_capability_list_schema_exposes_section_and_summary() -> None:
    props = admin.tool_param_schema("capability_list")["properties"]
    assert set(props) == {"section", "summary"}
    assert props["section"]["type"] == "string"
    assert props["summary"]["type"] == "boolean"


def test_descriptions_steer_agents_to_the_narrow_args() -> None:
    model = admin.TOOL_DESCRIPTIONS["model_list"]
    assert "large" in model and "fields" in model
    cap = admin.TOOL_DESCRIPTIONS["capability_list"]
    assert "large" in cap and "section" in cap and "summary" in cap


@pytest.mark.asyncio
async def test_model_list_args_reach_rest_as_query_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def _fake_rest(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"models": []}

    monkeypatch.setattr(admin, "_call_rest", _fake_rest)

    await admin._execute_tool(
        tool="model_list",
        args={"installed": True, "fields": "id,type"},
        bearer="t",
        base_url="http://t",
        memory_dispatcher=None,
    )

    assert seen["method"] == "GET"
    assert seen["url"] == "http://t/api/models"
    assert seen["payload"] == {"installed": True, "fields": "id,type"}


@pytest.mark.asyncio
async def test_capability_list_args_reach_rest_as_query_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def _fake_rest(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(admin, "_call_rest", _fake_rest)

    await admin._execute_tool(
        tool="capability_list",
        args={"section": "catalogs", "summary": True},
        bearer="t",
        base_url="http://t",
        memory_dispatcher=None,
    )

    assert seen["url"] == "http://t/api/capabilities"
    assert seen["payload"] == {"section": "catalogs", "summary": True}
