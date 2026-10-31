"""RAG-safe context assembly from the active memory provider.

This module is intentionally independent of Open WebUI.  It turns Hindsight's
fact recall plus response-level raw chunks into a small, provenance-carrying
context envelope.  The caller cannot select a bank: automatic chat recall is
always the public ``shared`` namespace and uses an anonymous identity.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _clean_text(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    text = _CONTROL_RE.sub("", text).strip()
    # Open WebUI serialises retrieved text inside <source> blocks.  Memory is
    # untrusted evidence and must not be able to terminate or spoof one.
    return re.sub(
        r"<(\s*/?\s*source\b[^>]*)>",
        lambda match: f"&lt;{match.group(1)}&gt;",
        text,
        flags=re.IGNORECASE,
    )


def _tokens(text: str) -> int:
    """Cheap, deterministic upper-ish estimate used only for output caps."""
    return max(1, math.ceil(len(text) / 4)) if text else 0


def _id_piece(value: Any) -> str:
    raw = str(value or "unknown")
    cleaned = _SAFE_ID_RE.sub("-", raw).strip("-.")
    if cleaned and len(cleaned) <= 80:
        return cleaned
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:20]


def _metadata(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _source_for(chunk: dict[str, Any], related: list[dict[str, Any]]) -> str:
    candidates: list[Any] = []
    meta = _metadata(chunk.get("metadata"))
    candidates.extend(
        (chunk.get("source"), meta.get("source"), meta.get("name"), meta.get("title"))
    )
    for fact in related:
        fact_meta = _metadata(fact.get("metadata"))
        candidates.extend(
            (
                fact.get("source"),
                fact_meta.get("source"),
                fact_meta.get("name"),
                fact_meta.get("title"),
            )
        )
    for value in candidates:
        clean = _clean_text(value)
        if clean:
            return clean[:240]
    return "Hindsight memory"


def _as_ids(value: Any) -> set[str]:
    if value is None:
        return set()
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return {str(item) for item in values if item is not None and str(item)}


def _related_facts(
    chunk_key: str, chunk: dict[str, Any], facts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    chunk_ids = _as_ids(chunk.get("id") or chunk_key)
    document_ids = _as_ids(chunk.get("document_id"))
    fact_ids = _as_ids(chunk.get("fact_id") or chunk.get("fact_ids"))
    related: list[dict[str, Any]] = []
    for fact in facts:
        if (
            chunk_ids & _as_ids(fact.get("chunk_id"))
            or document_ids & _as_ids(fact.get("document_id") or fact.get("id"))
            or fact_ids & _as_ids(fact.get("fact_id"))
        ):
            related.append(fact)
    return related


def _fact_wire(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": fact.get("fact_id") or fact.get("id"),
        "document_id": fact.get("document_id") or fact.get("id"),
        "text": _clean_text(fact.get("text")),
        "source": _source_for({}, [fact]),
        "kind": fact.get("kind") or "fact",
        "type": fact.get("type"),
        "score": fact.get("score") if isinstance(fact.get("score"), (int, float)) else None,
    }


async def build_memory_context(
    provider: Any,
    query: str,
    *,
    fact_tokens: int = 1024,
    chunk_tokens: int = 1536,
    max_sources: int = 8,
    budget: str = "low",
    prefer_observations: bool = True,
    min_scores: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Recall shared memory and return raw-chunk-first RAG contexts.

    ``provider.recall`` remains list-compatible across engines.  Hindsight
    adds ``.chunks``; degraded providers simply produce fact fallbacks.
    Chunks must join to a visible recalled fact even when a provider forgets
    to pre-filter its enrichment, keeping this boundary fail closed.
    """

    recalled = await provider.recall(
        query=query,
        max_tokens=fact_tokens,
        dataset="shared",
        budget=budget,
        prefer_observations=prefer_observations,
        include={"chunks": {"max_tokens": chunk_tokens}},
        min_scores=min_scores,
        client_id="anonymous",
    )
    facts = [item for item in recalled if isinstance(item, dict)]
    chunks = getattr(recalled, "chunks", None)
    chunks = chunks if isinstance(chunks, dict) else {}

    candidates: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    for key, raw in chunks.items():
        if not isinstance(raw, dict):
            continue
        related = _related_facts(str(key), raw, facts)
        if not related:
            continue
        text = _clean_text(raw.get("text") or raw.get("content"))
        fingerprint = " ".join(text.lower().split())
        if not text or fingerprint in seen_text:
            continue
        seen_text.add(fingerprint)
        document_id = (
            raw.get("document_id") or related[0].get("document_id") or related[0].get("id")
        )
        chunk_id = raw.get("id") or key
        scores = [f.get("score") for f in related if isinstance(f.get("score"), (int, float))]
        candidates.append(
            {
                "id": f"hs:{_id_piece(document_id)}:{_id_piece(chunk_id)}",
                "text": text,
                "title": _source_for(raw, related),
                "source": _source_for(raw, related),
                "kind": "chunk",
                "document_id": document_id,
                "chunk_id": chunk_id,
                "fact_ids": [f.get("fact_id") for f in related if f.get("fact_id")],
                "score": max(scores) if scores else None,
            }
        )

    candidates.sort(
        key=lambda item: (item.get("score") is None, -float(item.get("score") or 0.0), item["id"])
    )
    contexts: list[dict[str, Any]] = []
    remaining = chunk_tokens
    truncated = False
    for candidate in candidates[:max_sources]:
        needed = _tokens(candidate["text"])
        if needed > remaining:
            if remaining <= 0:
                truncated = True
                break
            candidate = dict(candidate)
            candidate["text"] = candidate["text"][: remaining * 4].rstrip()
            truncated = True
            needed = _tokens(candidate["text"])
        contexts.append(candidate)
        remaining -= needed
        if remaining <= 0:
            truncated = len(candidates) > len(contexts)
            break

    # Hindsight can be degraded or an older engine can omit chunks.  Facts
    # still provide useful shared-memory context and preserve the endpoint's
    # engine-neutral behavior.
    if not contexts:
        for fact in facts[:max_sources]:
            wire = _fact_wire(fact)
            if not wire["text"]:
                continue
            needed = _tokens(wire["text"])
            if needed > remaining:
                if remaining <= 0:
                    truncated = True
                    break
                wire["text"] = wire["text"][: remaining * 4].rstrip()
                truncated = True
                needed = _tokens(wire["text"])
            document_id = wire["document_id"]
            fact_id = wire["id"]
            wire.update(
                {
                    "id": f"hs:{_id_piece(document_id)}:{_id_piece(fact_id)}",
                    "title": wire.pop("source"),
                    "source": _source_for({}, [fact]),
                    "fact_ids": [fact_id] if fact_id else [],
                    "chunk_id": None,
                }
            )
            contexts.append(wire)
            remaining -= needed

    return {
        "engine": "hindsight" if chunks or hasattr(recalled, "chunks") else "memory",
        "contexts": contexts,
        "facts": [_fact_wire(fact) for fact in facts[:max_sources]],
        "truncated": truncated or len(candidates) > len(contexts),
    }
