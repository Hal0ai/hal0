"""#2662: the hal0-admin ``slot_list`` payload must stay small enough for Hermes.

Hermes 0.19.0 truncates any MCP tool result over its per-result budget
(``budget_for_context_window(65536)`` = 39,321 chars) to a 1,500-char preview
when no sandbox exists (every ``hermes -z`` turn). The old payload was ~2.9 KB
per slot as Hermes sees it (pretty-printed text + ``structuredContent`` +
``resolved_command`` + ``image``), so 14+ slots crossed the cap and the model
answered from the preview. The list now carries roster fields only; full
detail stays on ``slot_status``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue

# Hermes' inline budget for the 65,536-ctx anchor, and the tighter bound the
# 30-slot roster must meet so growth has headroom.
HERMES_BUDGET = 39_321
ROSTER_BOUND = 20_000

_DIGEST = "sha256:" + "ab12" * 16


def _full_slot(i: int, *, state: str = "ready") -> dict[str, Any]:
    """A slot dict shaped like the real /api/slots row (verbose fields included)."""
    return {
        "name": f"slot-{i}",
        "id": i,
        "state": state,
        "status": state,
        "kind": "local",
        "type": "llm",
        "port": 8000 + i,
        "model_id": f"org/model-{i}-Q4_K_M",
        "models": [f"org/model-{i}-Q4_K_M"],
        "model_default": f"org/model-{i}-Q4_K_M",
        "backend": "llamacpp",
        "declared_backend": "vulkan",
        "profile": "balanced",
        "tune_effective": {
            "ctx_size": 8192,
            "enable_thinking": False,
            "reasoning_effort": "low",
            "reasoning_budget": -1,
            "vision": "off",
            "speculation": "auto",
            "sources": {"ctx_size": "slot", "enable_thinking": "profile-flags"},
            "reasoning_effort_unavailable": None,
        },
        "runtime": "container",
        "container_status": "running",
        "container_health": True,
        "image_status": "present",
        "last_used_at": 1760000000.0,
        "image": f"ghcr.io/hal0ai/hal0-runner-vulkan:1.4.0@{_DIGEST}",
        "actual_image": f"ghcr.io/hal0ai/hal0-runner-vulkan:1.4.0@{_DIGEST}",
        "resolved_command": [
            "podman",
            "run",
            "--rm",
            *[f"--flag-{n}=value-{n}-padding-padding" for n in range(40)],
            "llama-server",
        ],
        "metadata": {"backend": "llamacpp", "tuning": {"ctx": 65536, "ngl": 99, "k": "v" * 80}},
        "metrics": {"tokens_per_s": 41.2, "queue": 0, "history": list(range(30))},
        "llamacpp_args": ["--ctx-size", "65536", "--n-gpu-layers", "99"],
        "pinned": False,
        "autoload": True,
        "priority": 5,
        "idle_timeout_s": 600,
    }


@pytest.fixture
def queue() -> ApprovalQueue:
    return ApprovalQueue()


@pytest.fixture
def rest_payload(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import httpx

    box: dict[str, Any] = {"payload": []}

    class _Resp:
        status_code = 200
        text = ""

        def json(self) -> Any:
            return box["payload"]

    class _Client:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, *a: Any, **k: Any) -> _Resp:
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return box


def _hermes_visible_chars(result: Any) -> int:
    """Chars Hermes counts: every text block plus structuredContent if present."""
    if isinstance(result, CallToolResult):
        content, structured = result.content, result.structuredContent
    else:
        content, structured = result
    total = sum(len(c.text) for c in content if isinstance(c, TextContent))
    if structured is not None:
        total += len(json.dumps(structured))
    return total


async def _call(queue: ApprovalQueue, tool: str, args: dict[str, Any]) -> Any:
    server = admin.build_server(approval_queue=queue, base_url="http://t")
    return await server.call_tool(tool, {"args": args})


@pytest.mark.asyncio
async def test_30_slot_roster_fits_hermes_budget_with_headroom(
    queue: ApprovalQueue, rest_payload: dict[str, Any]
) -> None:
    rest_payload["payload"] = [_full_slot(i) for i in range(30)]
    result = await _call(queue, "slot_list", {})
    size = _hermes_visible_chars(result)
    assert size < ROSTER_BOUND, f"30-slot slot_list is {size} chars as Hermes sees it"
    assert size < HERMES_BUDGET


@pytest.mark.asyncio
async def test_payload_is_carried_once_not_text_plus_structured(
    queue: ApprovalQueue, rest_payload: dict[str, Any]
) -> None:
    rest_payload["payload"] = [_full_slot(i) for i in range(3)]
    result = await _call(queue, "slot_list", {})
    assert isinstance(result, CallToolResult)
    assert result.structuredContent is None
    assert len(result.content) == 1
    body = json.loads(result.content[0].text)
    assert body["count"] == 3
    assert body["slots"][0]["tune_effective"] == _full_slot(0)["tune_effective"]
    assert [s["name"] for s in body["slots"]] == ["slot-0", "slot-1", "slot-2"]
    # compact JSON, not indent=2
    assert "\n" not in result.content[0].text


@pytest.mark.asyncio
async def test_list_rows_keep_roster_fields_and_drop_detail(
    queue: ApprovalQueue, rest_payload: dict[str, Any]
) -> None:
    rest_payload["payload"] = [_full_slot(1), _full_slot(2, state="offline")]
    result = await _call(queue, "slot_list", {})
    rows = json.loads(result.content[0].text)["slots"]
    row = rows[0]
    for key in ("name", "id", "state", "model_id", "backend", "port", "container_health"):
        assert key in row, key
    assert row["model_id"] == "org/model-1-Q4_K_M"
    assert row["loaded"] is True
    assert rows[1]["loaded"] is False
    for key in (
        "resolved_command",
        "image",
        "actual_image",
        "metadata",
        "metrics",
        "llamacpp_args",
    ):
        assert key not in row, key
    # the digest never reaches the list; only the tag does
    assert _DIGEST not in result.content[0].text
    assert row["image_tag"] == "1.4.0"


@pytest.mark.asyncio
async def test_slot_status_stays_full(queue: ApprovalQueue, rest_payload: dict[str, Any]) -> None:
    full = _full_slot(7)
    rest_payload["payload"] = full
    result = await _call(queue, "slot_status", {"name": "slot-7"})
    _content, structured = result
    assert structured["resolved_command"] == full["resolved_command"]
    assert structured["image"] == full["image"]
    assert structured["metadata"] == full["metadata"]


def test_compact_slot_row_tolerates_sparse_and_remote_rows() -> None:
    assert admin._compact_slot_row({"name": "primary"}) == {"name": "primary"}
    row = admin._compact_slot_row(
        {"name": "or", "kind": "remote", "state": "ready", "image": None, "provider": "openrouter"}
    )
    assert row["kind"] == "remote" and row["loaded"] is True and "image_tag" not in row
