"""Dependency-free secret redaction primitives (issues #553, #1523).

This module is the *bottom* of the redaction stack: it imports nothing from
hal0 and may therefore be used from any layer, including ``hal0.events`` and
``hal0.activity``, which sit below the API package and cannot import
:mod:`hal0.api._redact` without a cycle.

Two complementary strategies live here:

``redact_log_line``
    TEXT scanning. For free-text where there is no key/value structure to
    walk — journald lines, exception strings, event messages. Matches on
    the *shape* of the secret (``Authorization: Bearer …``,
    ``HAL0_BEARER_TOKEN=…``, a long ``client_id=…``, ``<NAME>_KEY=…``) and
    destroys only the token body, leaving the prefix so an operator reading
    a redacted log still sees a secret WAS present.

``redact_text_tree``
    The same text scan applied recursively to every string in a structured
    value, without touching keys, types, or shape. Structured ``data``
    blobs are half text: ``{"error": "HTTPStatusError: Bearer tok…"}`` hides
    a secret under an entirely innocent key name, so key-name redaction
    alone (:func:`hal0.api._redact.redact_config`) cannot see it.

Key-NAME redaction (``is_sensitive_key`` / ``redact_config`` /
``redact_value``) stays in :mod:`hal0.api._redact`, which re-exports
everything here so existing importers keep working unchanged.
"""

from __future__ import annotations

import re
from typing import Any, Final

# Plain sentinel for masked values. Exposed so a caller can grep logs /
# fixtures for the exact token.
MASK: Final[str] = "***REDACTED***"

# Compiled once at import time. Each alternative ends with a
# ``(?P<...>...)`` capture of just the secret token; ``redact_log_line``
# rewrites that token to :data:`MASK` while leaving the surrounding
# ``Authorization:``, ``Bearer``, ``HAL0_BEARER_TOKEN=``, ``client_id=``,
# or ``<NAME>_KEY=``/``KEY=`` prefix in place so an operator reading a
# redacted log still sees a secret WAS present. Case-insensitive; the
# explicit alternatives are ordered most-to-least specific so the
# precise header form wins over the bare ``Bearer`` fallback (Python's
# ``re`` alternation is leftmost-wins inside a single match).
#
# The ``client_id=`` alternative is length-gated (16+ chars) so it
# doesn't mask the short, non-secret labels client_id legitimately takes
# (``anonymous``, the 12-hex-char hash). The ``<NAME>_KEY=``/``KEY=``
# alternative mirrors is_sensitive_key's ``_KEY$``/``^KEY$`` suffix rule
# so hal0's own admin/client keys (HAL0_ADMIN_KEY, HAL0_CLIENT_KEY, ...)
# are caught if one is ever stamped into a log line verbatim, not just
# in structured config — same conservative "over-redact" posture as
# is_sensitive_key.
LOG_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix_auth>Authorization:\s*Bearer\s+)(?P<auth_token>\S+)"
    r"|(?P<prefix_env>HAL0_BEARER_TOKEN=)(?P<env_token>\S+)"
    r"|(?P<prefix_bearer>Bearer\s+)(?P<bearer_token>[A-Za-z0-9_\-\.]+)"
    r"|(?P<prefix_client_id>client_id=)(?P<client_id_token>[A-Za-z0-9_\-\.]{16,})"
    r"|(?P<prefix_key>\b(?:[A-Za-z][A-Za-z0-9_]*_KEY|KEY)=)(?P<key_token>\S+)",
    re.IGNORECASE,
)


def redact_log_line(line: str) -> str:
    """Replace Bearer / HAL0_BEARER_TOKEN / long client_id / ``*_KEY=``
    secrets in ``line`` with :data:`MASK`.

    The prefix is preserved so an operator reading a redacted log still
    sees that an Authorization header (or client_id / ``*_KEY`` field)
    was present — only the token body is destroyed. For free-text log
    lines; contrast with ``redact_config``, which walks structured
    dict/list trees by key name.

    Idempotent: :data:`MASK` contains no character that any alternative
    matches, so re-running over already-redacted text is a no-op. That
    matters because the same string can cross more than one seam
    (emit-time redaction, then again at the durable write).
    """

    def _sub(match: re.Match[str]) -> str:
        groups = match.groupdict()
        for prefix_group in ("prefix_auth", "prefix_env", "prefix_client_id", "prefix_key"):
            if groups[prefix_group] is not None:
                return f"{groups[prefix_group]}{MASK}"
        return f"{groups['prefix_bearer']}{MASK}"

    return LOG_SECRET_RE.sub(_sub, line)


def redact_text_tree(value: Any) -> Any:
    """Apply :func:`redact_log_line` to every string inside ``value``.

    Walks dicts (values only — keys are structural and never carry the
    secret body), lists, and tuples; returns scalars untouched. Shape,
    types, key names, and ordering are preserved exactly, because
    consumers route on structured fields: ``data["slot"]`` picks the slot
    a journal entry belongs to, ``data["model"]`` the activity target.
    A redaction that reshaped those would break routing rather than
    protect it.

    Pure — does not mutate the input. Complements key-name redaction
    rather than replacing it: this catches a secret hiding in the VALUE
    under an innocent key, that one catches a secret whose KEY names it.
    """
    if isinstance(value, str):
        return redact_log_line(value)
    if isinstance(value, dict):
        return {k: redact_text_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_text_tree(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_text_tree(v) for v in value)
    return value


__all__ = [
    "LOG_SECRET_RE",
    "MASK",
    "redact_log_line",
    "redact_text_tree",
]
