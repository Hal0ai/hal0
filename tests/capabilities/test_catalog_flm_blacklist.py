"""Regression for the FLM broken-tag blacklist.

When the FLM toolbox bundles a model whose chat decoder is known-broken
upstream (current case: ``qwen3:0.6b`` returning HTTP 500 with
``invalid UTF-8 byte`` on any non-ASCII output), the dashboard picker
must not surface it. The slot would otherwise reach ``state=ready`` —
the health probe with ``max_tokens=1`` doesn't trigger the upstream
crash — and a real chat would 500, which is a trap for the operator.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.capabilities import catalog


@pytest.fixture
def flm_entries() -> list[dict[str, Any]]:
    """Three FLM-served rows: a broken tag + a good chat + a good embed."""
    return [
        {
            "tag": "qwen3:0.6b",
            "size_bytes": 600_000_000,
            "footprint_gb": 0.66,
            "capabilities": ["chat"],
            "installed": True,
        },
        {
            "tag": "gemma3:1b",
            "size_bytes": 1_000_000_000,
            "footprint_gb": 1.2,
            "capabilities": ["chat"],
            "installed": True,
        },
        {
            "tag": "embed-gemma:300m",
            "size_bytes": 300_000_000,
            "footprint_gb": 0.4,
            "capabilities": ["embed"],
            "installed": False,
        },
    ]


def test_broken_tag_hidden_from_chat_rows(
    monkeypatch: pytest.MonkeyPatch, flm_entries: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr("hal0.providers.flm.flm_served_models", lambda: flm_entries)
    rows = catalog._flm_rows_for_capability("chat")

    ids = {row["id"] for row in rows}
    assert "qwen3:0.6b" not in ids, (
        "blacklist failure: qwen3:0.6b must not surface in the chat picker "
        "(see _FLM_BROKEN_TAGS in catalog.py)"
    )
    assert "gemma3:1b" in ids, "non-broken chat tags must still surface"


def test_broken_tag_set_is_documented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each entry must carry a reason — that's the next reader's recheck cue."""
    for tag, reason in catalog._FLM_BROKEN_TAGS.items():
        assert tag and ":" in tag, f"entry {tag!r} is not an FLM tag"
        assert reason and len(reason) > 10, (
            f"entry {tag!r} has no reason — the next reader can't tell "
            f"whether the blacklist still applies after a toolbox bump"
        )
