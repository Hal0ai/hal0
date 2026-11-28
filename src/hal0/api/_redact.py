"""Shared config-echo redaction (issue #553).

A single helper that scrubs sensitive values from any config dict before
it leaves the API. The trigger is a regex on the KEY NAME (not the
value) — we never look at the value, only decide whether to mask it.

Sensitive key pattern (case-insensitive)::

    SECRET|TOKEN|PASSWORD|PASS|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|
    ENCRYPTION[_-]?KEY|SALT|_KEY$|^KEY$

For a sensitive-keyed value the helper returns::

    {"value": "***REDACTED***", "set": <bool>}

where ``set`` is True iff the real value is non-empty / non-None — the
UI uses that to render the slot as "configured" or "unset" without ever
seeing the secret. Non-sensitive keys pass through unchanged. Lists
and nested dicts are walked recursively so an arbitrary config tree is
fully scrubbed in one pass.

Wired into every config-echoing endpoint (``/api/settings``,
``/api/config/models``, ``/api/upstreams``, …). See
``tests/api/test_redact.py`` for the contract.

The TEXT-scanning counterpart — :func:`redact_log_line`, used on free-text
journald lines (``/api/logs``, ``/api/logs/stream``, the MCP
``logs_tail``/``slot_logs`` tools), event messages, and durable audit rows —
now lives in the dependency-free :mod:`hal0.redaction` so layers *below* the
API package (``hal0.events``, ``hal0.activity``) can reach it without an
import cycle (#1523). It is re-exported here unchanged, so every existing
``from hal0.api._redact import redact_log_line`` keeps working.
"""

from __future__ import annotations

from hal0.redaction import (
    LOG_SECRET_RE,
    MASK,
    SENSITIVE_KEY_RE,
    is_sensitive_key,
    is_token_count,
    redact_config,
    redact_log_line,
    redact_text_tree,
    redact_value,
)

# The key-name pattern lives in hal0.redaction now (so hal0.bridge can use it without importing
# this package); the old name stays for the mirrors that cite it (installer/lib/failure-report.sh,
# doctor_bundle).
_SENSITIVE_RE = SENSITIVE_KEY_RE


__all__ = [
    "LOG_SECRET_RE",
    "MASK",
    "is_sensitive_key",
    "is_token_count",
    "redact_config",
    "redact_log_line",
    "redact_text_tree",
    "redact_value",
]
