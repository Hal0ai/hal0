"""R4 H1 regression — ``_collect_chat_slots`` filter against real LXC payloads.

The bug: the pre-PR-1 filter checked ``_slot_kind`` first, which read
``kind`` before ``type``. The live ``/api/slots`` payload tags every slot
with ``kind="local"`` (deployment shape) and ``type="llm"`` (capability),
so the kind-first check rejected 100% of real chat slots. The rendered
``model_aliases:`` block never appeared and Hermes only ever saw the
primary upstream's single model in ``/v1/models``.

Fixtures captured from LXC 105 on 2026-05-28 (live state + two derived
scenarios). See ``tests/fixtures/slots_*.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.agents import hermes_provision as hp

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("fixture", "expected_aliases"),
    [
        ("slots_cold.json", []),
        ("slots_primary_ready.json", ["primary"]),
        ("slots_all_ready.json", ["agent-hermes", "primary"]),
    ],
)
def test_collect_chat_slots_against_real_lxc_payload(
    fixture: str, expected_aliases: list[str]
) -> None:
    """For each scenario, ``_collect_chat_slots`` returns exactly the
    expected aliases — and never an embed/rerank/stt/tts slot."""
    slots = _load(fixture)
    collected = hp._collect_chat_slots(slots)
    got_aliases = sorted(s["alias"] for s in collected)
    assert got_aliases == sorted(expected_aliases), (
        f"{fixture}: expected {expected_aliases}, got {got_aliases}"
    )

    # Every collected slot must carry a non-empty model_id + backend_url
    # — that's what the config render template grabs.
    for entry in collected:
        assert entry["model_id"], f"{fixture}: empty model_id in {entry}"
        assert entry["backend_url"], f"{fixture}: empty backend_url in {entry}"


def test_collect_chat_slots_skips_non_llm_capabilities() -> None:
    """Embed/rerank/stt/tts slots must never appear in chat aliases even
    when ready. Real /api/slots flips ``state=ready`` for every loaded
    slot regardless of type — the type filter is the only guard."""
    slots = _load("slots_all_ready.json")
    # Force every slot ready including non-chat capabilities.
    for s in slots:
        s["state"] = "ready"
        s["status"] = "ready"
        s["lemonade_state"] = "loaded"
    collected = hp._collect_chat_slots(slots)
    aliases = {s["alias"] for s in collected}
    assert "embed" not in aliases
    assert "rerank" not in aliases
    assert "stt" not in aliases
    assert "tts" not in aliases
    # Only the two llm slots remain.
    assert aliases == {"primary", "agent-hermes"}


def test_collect_chat_slots_skips_non_ready_llm_slots() -> None:
    """Even ``type==llm`` slots must be ``_is_ready`` to be advertised."""
    slots = _load("slots_cold.json")
    # Every slot is offline → no aliases.
    assert hp._collect_chat_slots(slots) == []

    # Flip just primary to a non-ready intermediate state — still excluded.
    for s in slots:
        if s["name"] == "primary":
            s["state"] = "starting"
            s["status"] = "starting"
    assert hp._collect_chat_slots(slots) == []
