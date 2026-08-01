"""Shared config-echo redaction (issue #553).

A single helper that scrubs sensitive values from any config dict before
it leaves the API. The trigger is a regex on the KEY NAME (not the
value) — we never look at the value, only decide whether to mask it.

Sensitive key pattern (case-insensitive)::

    SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT

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

import re
from typing import Any, Final

from hal0.redaction import (
    LOG_SECRET_RE,
    MASK,
    redact_log_line,
    redact_text_tree,
)

# Match by key NAME. Case-insensitive. ``(?:...)`` non-capturing group;
# alternation ordered longest-token-first so e.g. ``PRIVATE_KEY`` wins
# over ``PASS`` when both could match. ``re.search`` (not ``match``) so
# the pattern triggers anywhere in the key — ``TOKEN`` matches
# ``TOKENIZER_ID`` and ``HF_TOKEN`` alike, which is the conservative
# (over-redact) behaviour the spec asks for.
_SENSITIVE_RE: Final[re.Pattern[str]] = re.compile(
    # The trailing ``_KEY$``/``^KEY$`` alternative catches credential names
    # that end in a bare KEY suffix — hal0's OWN auth keys
    # (HAL0_ADMIN_KEY / HAL0_CLIENT_KEY) matched none of the older
    # substrings and leaked VERBATIM into shareable doctor bundles
    # (halo150 O9). Anchored as a suffix (not a bare ``KEY`` substring) so
    # non-secret settings like KEY_ROTATION_DAYS or KEYBOARD_LAYOUT don't
    # over-mask.
    r"(?i)(?:SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT|_KEY$|^KEY$)"
)

# ``MASK`` is defined once in hal0.redaction and re-exported above — both
# strategies must mask to the SAME sentinel or a caller grepping for it
# would find only half the redactions.


def is_sensitive_key(key: str) -> bool:
    """True if ``key`` (the *name*, not the value) matches a sensitive pattern.

    Case-insensitive substring search over the regex alternation. Safe to
    call on untrusted / user-supplied strings — it only ever returns a
    bool, never echoes the value.
    """
    return bool(_SENSITIVE_RE.search(key))


def redact_value(value: Any) -> dict[str, Any]:
    """Project a sensitive *value* into the masked ``{value, set}`` shape.

    ``set`` is True for any non-empty / non-None value (including 0 and
    False — those are still "configured"). Empty string and None are
    treated as "unset" so the UI can render the slot as empty.
    """
    is_set = value is not None and value != ""
    return {"value": MASK, "set": bool(is_set)}


# ── Line-level secret redaction (moved from hal0.mcp.admin) ─────────────────
#
# redact_config()/is_sensitive_key() above scrub STRUCTURED dict/list
# trees by key NAME. Free-text log lines (journald output) have no key
# shape to inspect, so this second helper scans the line's TEXT for
# known secret-bearing shapes instead. It originated in hal0.mcp.admin
# (security review MED-1, logs_tail) and lives here — not there — so
# hal0.api.routes.logs (a REST route mounted on every install) can reuse
# it without importing hal0.mcp.admin, which hard-fails at import time
# when the optional ``mcp`` SDK isn't installed (see that module's
# "Fail-fast import" docstring section); pulling that dependency into
# the core /api/logs route would break every install that hasn't opted
# into the MCP admin server. hal0.mcp.admin now imports
# ``redact_log_line`` from here so logs_tail and /api/logs share one
# behaviour (api-logs-redact, surfaced by the SEC-mcp-clientid lane).
#
def redact_config(config: Any) -> Any:
    """Recursively scrub sensitive-keyed values from a config tree.

    - ``dict``: walk keys. For a sensitive key, replace its value with
      :func:`redact_value`'s ``{value, set}`` projection. For a nested
      dict or list value, recurse. Otherwise pass through.
    - ``list``: recurse element-by-element (so a list of dicts is fully
      scrubbed). Lists of scalars pass through as-is — only keyed
      containers can hide a sensitive name.
    - scalar: return as-is.

    Pure — does not mutate the input. Returns a new dict / list at each
    level it walks; scalars are shared.
    """
    if isinstance(config, dict):
        out: dict[str, Any] = {}
        for k, v in config.items():
            if is_sensitive_key(k):
                out[k] = redact_value(v)
            elif isinstance(v, (dict, list)):
                out[k] = redact_config(v)
            else:
                out[k] = v
        return out
    if isinstance(config, list):
        return [redact_config(item) for item in config]
    return config


__all__ = [
    "LOG_SECRET_RE",
    "MASK",
    "is_sensitive_key",
    "redact_config",
    "redact_log_line",
    "redact_text_tree",
    "redact_value",
]
