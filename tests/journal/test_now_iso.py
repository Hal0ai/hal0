"""hal0.journal — the shared time helper (the module's remaining surface).

Phase E (#687) removed the WS log-ring bridge; slot containers log to
journald via their ``hal0-slot@*`` units and are read uniformly from
there (``api/routes/journal``). Only ``now_iso`` remains here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hal0.journal import now_iso


def test_now_iso_is_utc_iso8601() -> None:
    stamp = now_iso()
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_now_iso_is_roughly_now() -> None:
    before = datetime.now(UTC)
    parsed = datetime.fromisoformat(now_iso())
    after = datetime.now(UTC)
    assert before <= parsed <= after


def test_journal_exports_only_now_iso() -> None:
    """The bridge surface is gone — pin the module's public API."""
    import hal0.journal as journal

    assert journal.__all__ == ["now_iso"]
