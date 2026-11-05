"""The dashboard's restart result type mirrors the live backend response."""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hal0.api.routes.updater import restart_drifted


@pytest.mark.asyncio
async def test_restart_slots_result_matches_ui_type() -> None:
    sm = SimpleNamespace(
        list=AsyncMock(return_value=[SimpleNamespace(name="chat"), SimpleNamespace(name="brain")]),
        compute_config_drift=AsyncMock(
            side_effect=[
                {"drifted": True, "diffs": []},
                {"drifted": True, "diffs": [], "render_error": "bad config"},
            ]
        ),
        restart=AsyncMock(side_effect=RuntimeError("unit failed")),
    )
    result = await restart_drifted(sm)
    source = Path("ui/src/api/hooks/useUpdates.ts").read_text()
    match = re.search(r"export interface RestartSlotsResult\s*\{(.*?)\n\}", source, re.S)
    assert match
    fields = dict(re.findall(r"^\s*(\w+):\s*(.+)$", match.group(1), re.M))
    assert set(fields) == set(result)
    assert fields == {
        "restarted": "string[]",
        "failed": "Array<{ slot: string; error: string }>",
        "skipped_busy": "string[]",
        "skipped_render_error": "string[]",
        "count": "number",
    }
    assert result == {
        "restarted": [],
        "failed": [{"slot": "chat", "error": "unit failed"}],
        "skipped_busy": [],
        "skipped_render_error": ["brain"],
        "count": 0,
    }
