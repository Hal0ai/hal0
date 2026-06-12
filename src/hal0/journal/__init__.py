"""Journal helpers (issue #323, epic #322).

The dashboard journal panel reads hal0 EventBus events through
:mod:`hal0.api.routes.journal`; slot containers log to journald via
their ``hal0-slot@*`` units and are read uniformly from there. This
module keeps the shared time helper.
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["now_iso"]


def now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with microsecond precision."""
    return datetime.now(UTC).isoformat()
