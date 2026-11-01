"""#2700 — the boot brain lane leaves exactly one identity card per agent.

The lane reaches memory through the in-process MCP dispatcher
(``_boot_mcp_memory_call``), whose ``memory_search`` answers ``{"results": ...}``
with one entry per extracted FACT. The phases used to look for ``{"items": ...}``,
so on every hal0-api boot they found no prior card, deleted nothing, and added
another byte-identical one. These run the two phases repeatedly through the REAL
dispatcher against a bank that behaves like the engine (search is per-fact, delete
removes whole documents), so the shape contract between the two sides is pinned
end to end rather than by a hand-written fake of one side.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

import pytest
from fastapi import FastAPI

import hal0.agents.hermes_provision as hp
from hal0.agents.personas import BRAIN_PROFILE_AGENT_ID
from hal0.api import _boot_mcp_memory_call

_FACTS_PER_DOCUMENT = 3


class _CardBank:
    """Document store with the engine's search/delete semantics.

    ``search`` returns one hit per FACT of every tag-matching document (each
    carrying the document's id and metadata), the way the Hindsight provider
    maps recall results; ``delete`` drops whole documents by id.
    """

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.add_count = 0

    async def add(self, **kwargs: Any) -> dict[str, Any]:
        self.add_count += 1
        doc_id = kwargs.get("document_id") or f"doc-{self.add_count}"
        self.docs[doc_id] = kwargs
        return {"id": doc_id, "timestamp": "now"}

    async def search(self, *, tags: list[str], **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {"id": doc_id, "text": f"fact {n}", "metadata": doc["metadata"]}
            for doc_id, doc in self.docs.items()
            if set(tags) <= set(doc["tags"])
            for n in range(_FACTS_PER_DOCUMENT)
        ]

    async def delete(self, ids: list[str], **_kwargs: Any) -> dict[str, int]:
        removed = [i for i in dict.fromkeys(ids) if self.docs.pop(i, None) is not None]
        return {"deleted": len(removed)}

    def cards_for(self, agent_id: str) -> list[str]:
        return [i for i, d in self.docs.items() if d["metadata"].get("agent_id") == agent_id]


@pytest.mark.asyncio
async def test_repeated_boots_keep_one_identity_card_per_agent() -> None:
    bank = _CardBank()
    app = FastAPI()
    app.state.memory_provider = bank
    loop = asyncio.get_running_loop()
    io = hp.InstallIO(mcp_memory_call=functools.partial(_boot_mcp_memory_call, app, loop))
    state = hp.BootstrapState(agent_id="hermes-agent")
    ctx = hp._StepCtx(state=state, io=io)

    for boot in range(1, 4):
        hermes = await asyncio.to_thread(hp._phase_namespace_register, ctx)
        brain = await asyncio.to_thread(hp._phase_brain_profile_seed, ctx)

        assert hermes.details["registered"] is True, hermes.details
        assert brain.details["registered"] is True, brain.details
        # First boot has nothing to replace; every later boot must find the prior card.
        assert hermes.details["refreshed_existing"] is (boot > 1)
        assert brain.details["refreshed_existing"] is (boot > 1)
        assert len(bank.cards_for(state.agent_id)) == 1, f"boot {boot}"
        assert len(bank.cards_for(BRAIN_PROFILE_AGENT_ID)) == 1, f"boot {boot}"

    assert len(bank.docs) == 2
