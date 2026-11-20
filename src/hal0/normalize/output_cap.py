"""A per-request output cap a client asks for with a header (#3804).

Some OpenAI-compatible clients can add fixed headers to every request but have
no setting for a default ``max_tokens``. The Hermes agent hal0 bundles is one:
its main loop sends no output limit to a ``custom`` endpoint, so one call can
decode until the slot's context window is full. Such a client sends
``X-hal0-Max-Tokens: <n>`` and hal0 bounds the request body before dispatch.

The header only ever lowers a limit. A request that already asks for fewer
tokens keeps its own value, so auxiliary calls with small budgets are
untouched. It is not a credential and grants nothing: a caller that sends it
restricts only its own request.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = ["MAX_TOKENS_HEADER", "apply_output_cap", "parse_output_cap"]

#: Request header carrying the cap, a positive decimal integer.
MAX_TOKENS_HEADER: Final[str] = "X-hal0-Max-Tokens"

#: The two OpenAI chat fields that limit generated tokens.
_LIMIT_FIELDS: Final[tuple[str, ...]] = ("max_completion_tokens", "max_tokens")

#: Longest header value read. Anything longer is not a token count.
_MAX_DIGITS: Final[int] = 9


def parse_output_cap(raw: str | None) -> int | None:
    """The cap in ``raw``, or ``None`` when it is absent or not a positive integer."""
    if raw is None:
        return None
    text = raw.strip()
    if not text.isascii() or not text.isdigit() or len(text) > _MAX_DIGITS:
        return None
    value = int(text)
    return value if value > 0 else None


def _within(value: Any, cap: int) -> bool:
    """True when ``value`` is already a usable limit no larger than ``cap``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return 0 < value <= cap


def apply_output_cap(body: dict[str, Any], cap: int) -> dict[str, Any]:
    """Return ``body`` with its output limit bounded at ``cap``.

    Each limit field the body sets is lowered to ``cap`` unless it is already a
    positive number at or under it (``-1``, which llama-server reads as "no
    limit", is replaced). A body that sets neither field gains ``max_tokens``.
    The same object comes back when nothing had to change.
    """
    changes: dict[str, Any] = {}
    limited = False
    for field in _LIMIT_FIELDS:
        value = body.get(field)
        if value is None:
            continue
        limited = True
        if not _within(value, cap):
            changes[field] = cap
    if not limited:
        changes["max_tokens"] = cap
    return {**body, **changes} if changes else body
