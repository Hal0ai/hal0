"""MCP memory_recall tool (P2)."""

from __future__ import annotations

import pytest

from hal0.mcp.memory import TRUSTED_IN_PROCESS, make_dispatcher
from tests.memory.fakes import FakeMemoryProvider


class RecallProvider(FakeMemoryProvider):
    async def recall(
        self,
        query,
        *,
        types=None,
        max_tokens=4096,
        dataset="shared",
        tags=None,
        tags_match=None,
        client_id=None,
    ):
        return [
            {
                "id": "d1",
                "text": "from-recall",
                "timestamp": "t",
                "dataset": "shared",
                "tags": [],
                "source": None,
                "metadata": {},
                "score": None,
            }
        ]


@pytest.mark.asyncio
async def test_memory_recall_tool_dispatches():
    dispatch = make_dispatcher(RecallProvider(client_id="alice"), approval_queue=TRUSTED_IN_PROCESS)
    out = await dispatch("memory_recall", {"query": "hi", "max_tokens": 512})
    assert out["status"] == "ok"
    assert out["results"][0]["text"] == "from-recall"
