"""Secret redaction on the journal / events / activity pipes (issue #1523).

``/api/logs`` has scrubbed free-text journald lines through
:func:`hal0.redaction.redact_log_line` since the SEC-mcp-clientid review.
The sibling pipes never got the same treatment: ``EventBus`` messages flow
verbatim into ``/api/events``, ``/api/journal``, and — via the AuditStore
sink — the durable ``activity.db``. Producers that stringify an exception
(``f"{type(exc).__name__}: {exc}"`` in ``hal0.registry.pull``) can therefore
put a bearer token or ``*_KEY=`` value at rest in a database served over an
unauthenticated LAN endpoint.

These tests pin the seam at the two write boundaries:

  * :func:`hal0.events.make_event` — the single choke point every event
    passes through before it reaches the ring, subscribers, or the sink.
  * :meth:`hal0.activity.AuditStore.record` — the direct-write path used by
    ``record_action`` for API mutations, which never goes through the bus.

The existing key-name redaction on ``before``/``after`` blobs is orthogonal
and must keep working; text redaction is additive.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from hal0.activity import AuditStore
from hal0.events import make_event
from hal0.redaction import MASK

# A representative secret of each shape LOG_SECRET_RE knows about, paired
# with the fragment that must NOT survive redaction.
SECRET_LINES: list[tuple[str, str]] = [
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9-supersecret", "eyJhbGciOiJIUzI1NiJ9-supersecret"),
    ("pull failed: HAL0_BEARER_TOKEN=tok_live_abcdefghijklmnop", "tok_live_abcdefghijklmnop"),
    ("GET /v1/models?client_id=abcdefghijklmnopqrstuvwxyz", "abcdefghijklmnopqrstuvwxyz"),
    ("env HF_TOKEN_KEY=hf_QQQQwwwweeeerrrrtttt rejected", "hf_QQQQwwwweeeerrrrtttt"),
]


@pytest.fixture()
def store(tmp_path: Path) -> AuditStore:
    s = AuditStore(tmp_path / "activity.db", retention_days=30, max_rows=None)
    s.init_schema()
    return s


# ── make_event: the shared emit-boundary seam ────────────────────────────────


@pytest.mark.parametrize(("line", "secret"), SECRET_LINES)
def test_make_event_redacts_secrets_in_message(line: str, secret: str) -> None:
    """A secret in an event message never reaches the ring, /api/events, or the sink."""
    event = make_event(1, type="pull.failed", severity="error", source="registry", message=line)
    assert secret not in event["message"]
    assert MASK in event["message"]


@pytest.mark.parametrize(("line", "secret"), SECRET_LINES)
def test_make_event_redacts_secrets_in_data_strings(line: str, secret: str) -> None:
    """Structured ``data`` string values are scrubbed too — /api/journal spreads
    the whole ``data`` dict into the served entry."""
    event = make_event(
        1,
        type="pull.failed",
        severity="error",
        source="registry",
        message="pull failed",
        data={"error": line, "nested": {"detail": line}, "items": [line]},
    )
    assert secret not in str(event["data"])
    assert MASK in event["data"]["error"]
    assert MASK in event["data"]["nested"]["detail"]
    assert MASK in event["data"]["items"][0]


def test_make_event_preserves_non_secret_data_shape() -> None:
    """Redaction must not change types, keys, or ordering — consumers read
    ``data.slot`` / ``data.model`` for routing."""
    event = make_event(
        7,
        type="slot.state",
        severity="info",
        source="slot:agent",
        message="slot agent is ready",
        data={"slot": "agent", "port": 8082, "ready": True, "tps": 41.5, "tags": ["a", "b"]},
    )
    assert event["message"] == "slot agent is ready"
    assert event["data"] == {
        "slot": "agent",
        "port": 8082,
        "ready": True,
        "tps": 41.5,
        "tags": ["a", "b"],
    }
    assert event["id"] == 7
    assert event["type"] == "slot.state"


def test_make_event_redaction_is_idempotent() -> None:
    """Re-redacting an already-masked message is a no-op (defence in depth
    means the same text may pass more than one seam)."""
    once = make_event(1, type="t", severity="info", source="s", message="Bearer abcdef123456")
    twice = make_event(2, type="t", severity="info", source="s", message=once["message"])
    assert once["message"] == twice["message"]


# ── AuditStore: the durable direct-write path ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(("line", "secret"), SECRET_LINES)
async def test_record_redacts_message_at_rest(store: AuditStore, line: str, secret: str) -> None:
    """``record_action``'s free-text message is scrubbed before it hits sqlite."""
    await store.record(kind="action", category="model", action="model.pull", message=line)
    rows = store.query(limit=10)
    assert secret not in rows[0]["message"]
    assert MASK in rows[0]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("line", "secret"), SECRET_LINES)
async def test_record_redacts_error_at_rest(store: AuditStore, line: str, secret: str) -> None:
    """``audit_action`` stores the stringified exception in ``error`` — the
    exact field ``hal0.registry.pull`` fills with ``f"{type(exc).__name__}: {exc}"``."""
    await store.record(
        kind="action", category="model", action="model.pull", outcome="error", error=line
    )
    rows = store.query(limit=10)
    assert secret not in (rows[0]["error"] or "")
    assert MASK in (rows[0]["error"] or "")


@pytest.mark.asyncio
async def test_record_redacts_secret_text_inside_after_blob(store: AuditStore) -> None:
    """Key-name redaction misses a secret embedded in a free-text VALUE under a
    harmless key — text redaction must cover it."""
    await store.record(
        kind="action",
        category="model",
        action="model.pull",
        after={"stderr": "curl -H 'Authorization: Bearer tok_abcdef123456' failed"},
    )
    rows = store.query(limit=10)
    assert "tok_abcdef123456" not in str(rows[0]["after"])


@pytest.mark.asyncio
async def test_record_keeps_key_name_redaction(store: AuditStore) -> None:
    """The pre-existing key-name masking on before/after must not regress."""
    await store.record(
        kind="action",
        category="provider",
        action="upstream.create",
        after={"name": "openrouter", "api_key": "sk-or-v1-plaintext"},
    )
    rows = store.query(limit=10)
    assert "sk-or-v1-plaintext" not in str(rows[0]["after"])
    assert json.loads(rows[0]["after"])["name"] == "openrouter"


@pytest.mark.asyncio
async def test_secret_never_reaches_the_sqlite_file(store: AuditStore, tmp_path: Path) -> None:
    """End-to-end: grep the raw database file — nothing readable survives."""
    secret = "tok_live_never_at_rest_0001"
    await store.record(
        kind="action",
        category="model",
        action="model.pull",
        message=f"pull failed: HAL0_BEARER_TOKEN={secret}",
        error=f"HTTPStatusError: Bearer {secret}",
        after={"stderr": f"HAL0_BEARER_TOKEN={secret}"},
    )
    conn = sqlite3.connect(store.db_path)
    try:
        blob = "".join(str(r) for r in conn.execute("SELECT * FROM audit").fetchall())
    finally:
        conn.close()
    assert secret not in blob


@pytest.mark.asyncio
async def test_record_event_redacts_via_the_shared_seam(store: AuditStore) -> None:
    """An event mirrored into the durable store carries no secret either."""
    event = make_event(
        1,
        type="pull.failed",
        severity="error",
        source="registry",
        message="HTTPStatusError: Bearer tok_abcdef123456",
        data={"error": "HAL0_BEARER_TOKEN=tok_abcdef123456"},
    )
    await store.record_event(event)
    rows = store.query(limit=10)
    assert "tok_abcdef123456" not in str(rows[0])
