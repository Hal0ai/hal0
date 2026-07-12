"""Unified-bank memory model — [memory] unified_bank.

Pins the unified routing contract (one ``shared`` bank, tag-based
visibility) plus the always-on write-path quality (agent tag, timestamp,
context, session-derived document id) and the legacy multi-bank
back-compat path (``unified_bank=False``).
"""

from __future__ import annotations

import pytest

from hal0.memory.hindsight_provider import HindsightProvider


class RecordingClient:
    """Captures the full retain/recall payload for assertions."""

    def __init__(self) -> None:
        self.retained: list[dict] = []
        self.recalled: list[dict] = []
        self._facts_by_bank: dict[str, list[dict]] = {}

    async def retain(
        self,
        *,
        bank_id,
        content,
        document_id,
        context=None,
        metadata=None,
        tags=None,
        timestamp=None,
    ):
        self.retained.append(
            {
                "bank_id": bank_id,
                "content": content,
                "document_id": document_id,
                "context": context,
                "metadata": dict(metadata or {}),
                "tags": list(tags or []),
                "timestamp": timestamp,
            }
        )
        self._facts_by_bank.setdefault(bank_id, []).append(
            {"document_id": document_id, "text": content, "tags": list(tags or [])}
        )
        return {"operation_id": "op-1"}

    async def recall(
        self, *, bank_id, query, types=None, max_tokens=4096, tags=None, tags_match=None
    ):
        self.recalled.append({"bank_id": bank_id, "tags": tags, "tags_match": tags_match})
        return {"results": list(self._facts_by_bank.get(bank_id, []))}

    async def list_memories(self, *, bank_id, limit=50, offset=0, types=None, query=None):
        raw = self._facts_by_bank.get(bank_id, [])
        return {
            "items": [{"id": f["document_id"], "text": f["text"], "tags": f["tags"]} for f in raw]
        }


def _unified(client_id: str = "hermes") -> HindsightProvider:
    return HindsightProvider(client=RecordingClient(), client_id=client_id, unified_bank=True)


# ── Write routing ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unified_private_write_lands_in_shared_with_visibility_tag():
    """The private toggle (front door resolves ``private:<agent>``) no longer
    forks a bank — the write lands in ``shared`` and is stamped
    ``visibility:private`` + ``agent:<id>``."""
    p = _unified()
    await p.add("secret", dataset="private:hermes", client_id="hermes")
    rec = p._client.retained[0]
    assert rec["bank_id"] == "shared"
    assert "visibility:private" in rec["tags"]
    assert "agent:hermes" in rec["tags"]


@pytest.mark.asyncio
async def test_unified_shared_write_gets_agent_tag_no_visibility():
    p = _unified()
    await p.add("public", dataset="shared", client_id="hermes")
    rec = p._client.retained[0]
    assert rec["bank_id"] == "shared"
    assert "agent:hermes" in rec["tags"]
    assert "visibility:private" not in rec["tags"]


@pytest.mark.asyncio
async def test_unified_preserves_caller_tags_and_does_not_double_stamp_agent():
    p = _unified()
    await p.add("x", dataset="shared", client_id="hermes", tags=["topic:cats", "agent:override"])
    rec = p._client.retained[0]
    assert "topic:cats" in rec["tags"]
    # Caller already supplied an agent: tag — server must not add a second.
    assert [t for t in rec["tags"] if t.startswith("agent:")] == ["agent:override"]


@pytest.mark.asyncio
async def test_agent_tag_falls_back_to_unknown_for_anonymous():
    p = HindsightProvider(client=RecordingClient(), unified_bank=True)
    # No client_id, source is the front-door sentinel "anonymous".
    await p.add("x", dataset="shared", source="anonymous")
    rec = p._client.retained[0]
    assert "agent:unknown" in rec["tags"]


# ── agents registry namespace stays separate in unified mode (ADR-0011 §6) ────


@pytest.mark.asyncio
async def test_unified_agents_namespace_writes_to_agents_bank():
    """The ``agents`` federated registry is NOT chat memory — unified mode must
    leave it routing to its own ``agents`` bank, not collapse it into shared."""
    p = _unified()
    await p.add("identity card", dataset="agents", client_id="hermes")
    rec = p._client.retained[0]
    assert rec["bank_id"] == "agents"


@pytest.mark.asyncio
async def test_unified_agents_namespace_reads_from_agents_bank():
    p = _unified()
    await p.recall("who", dataset="agents", client_id="hermes")
    banks = {c["bank_id"] for c in p._client.recalled}
    assert banks == {"agents"}


@pytest.mark.asyncio
async def test_unified_agents_list_targets_agents_bank():
    p = _unified()
    await p.add("card", dataset="agents", client_id="hermes")
    out = await p.list_items(dataset="agents", client_id="hermes")
    assert {i["dataset"] for i in out["items"]} == {"agents"}


@pytest.mark.asyncio
async def test_unified_mixed_read_keeps_agents_alongside_collapsed_shared():
    """An explicit multi-scope read (agents + a chat namespace) keeps agents as
    its own bank while the chat namespace collapses to shared."""
    p = _unified()
    banks = set(p._allowed_namespaces(["agents", "private:hermes", "project:x"], "hermes"))
    assert banks == {"agents", "shared"}


# ── Write-path quality (mode-independent) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_add_always_sends_timestamp_and_synthesized_context():
    p = _unified()
    await p.add("x", dataset="shared", client_id="hermes")
    rec = p._client.retained[0]
    assert rec["timestamp"] is not None and "T" in rec["timestamp"]
    assert rec["context"] == "hermes conversation turn"


@pytest.mark.asyncio
async def test_add_honors_caller_timestamp_and_context():
    p = _unified()
    await p.add(
        "x",
        dataset="shared",
        client_id="hermes",
        metadata={"timestamp": "2020-01-01T00:00:00+00:00", "context": "onboarding"},
    )
    rec = p._client.retained[0]
    assert rec["timestamp"] == "2020-01-01T00:00:00+00:00"
    assert rec["context"] == "onboarding"
    # timestamp/context are consumed, not echoed into metadata.
    assert "timestamp" not in rec["metadata"]
    assert "context" not in rec["metadata"]


@pytest.mark.asyncio
async def test_session_id_makes_document_id_deterministic():
    p = _unified()
    r1 = await p.add("turn 1", dataset="shared", client_id="hermes", metadata={"session_id": "s7"})
    r2 = await p.add("turn 2", dataset="shared", client_id="hermes", metadata={"session_id": "s7"})
    assert r1["id"] == r2["id"] == "hermes:s7"


@pytest.mark.asyncio
async def test_explicit_document_id_wins_over_session():
    p = _unified()
    r = await p.add(
        "x",
        dataset="shared",
        client_id="hermes",
        document_id="doc-9",
        metadata={"session_id": "s7"},
    )
    assert r["id"] == "doc-9"


# ── Recall (single-bank in unified mode) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_unified_recall_is_single_bank_no_fanout():
    p = _unified()
    # Even a private-mode read (front door → [shared, private:hermes]) collapses.
    await p.recall("q", dataset=["shared", "private:hermes"], client_id="hermes")
    banks = {c["bank_id"] for c in p._client.recalled}
    assert banks == {"shared"}


@pytest.mark.asyncio
async def test_recall_forwards_tags_match_when_set():
    p = _unified()
    await p.recall("q", dataset="shared", client_id="hermes", tags=["t"], tags_match="all")
    assert p._client.recalled[0]["tags_match"] == "all"


@pytest.mark.asyncio
async def test_recall_omits_tags_match_when_unset():
    p = _unified()
    await p.recall("q", dataset="shared", client_id="hermes")
    # None → not forwarded (fake would still record it as None; assert default).
    assert p._client.recalled[0]["tags_match"] is None


# ── Back-compat: unified_bank=False keeps the legacy multi-bank model ─────────


@pytest.mark.asyncio
async def test_legacy_multibank_private_write_forks_private_bank():
    p = HindsightProvider(client=RecordingClient(), client_id="hermes", unified_bank=False)
    await p.add("secret", dataset="private:hermes", client_id="hermes")
    rec = p._client.retained[0]
    assert rec["bank_id"] == "private__hermes"
    # Agent + visibility tags are stamped in both modes (in legacy the private
    # BANK is the primary mechanism; the tag is a harmless, uniform marker).
    assert "agent:hermes" in rec["tags"]
    assert "visibility:private" in rec["tags"]


@pytest.mark.asyncio
async def test_legacy_multibank_recall_fans_out():
    client = RecordingClient()
    p = HindsightProvider(client=client, client_id="hermes", unified_bank=False)
    await p.recall("q", dataset="shared", client_id="hermes")
    banks = {c["bank_id"] for c in client.recalled}
    assert banks == {"shared", "private__hermes"}


@pytest.mark.asyncio
async def test_default_construction_is_legacy_multibank():
    """Directly-constructed providers default to legacy multi-bank so existing
    call sites/tests are unaffected; the live TRUE default rides on config."""
    p = HindsightProvider(client=RecordingClient(), client_id="hermes")
    await p.add("x", dataset="private:hermes", client_id="hermes")
    assert p._client.retained[0]["bank_id"] == "private__hermes"


# ── Config default ────────────────────────────────────────────────────────────


def test_config_default_unified_bank_true():
    from hal0.config.schema import MemoryConfig

    assert MemoryConfig().unified_bank is True
