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

Also home to :func:`redact_log_line` — the TEXT-scanning counterpart
used on free-text journald lines (``/api/logs``, ``/api/logs/stream``,
the MCP ``logs_tail``/``slot_logs`` tools) where there's no key/value
structure to walk. See that function's docstring for details.
"""

from __future__ import annotations

import re
from typing import Any, Final

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

# Plain sentinel for masked values. Exposed via __all__ so a future
# caller can grep logs / fixtures for the exact token.
MASK: Final[str] = "***REDACTED***"


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
    lines; contrast with :func:`redact_config`, which walks structured
    dict/list trees by key name.
    """

    def _sub(match: re.Match[str]) -> str:
        groups = match.groupdict()
        for prefix_group in ("prefix_auth", "prefix_env", "prefix_client_id", "prefix_key"):
            if groups[prefix_group] is not None:
                return f"{groups[prefix_group]}{MASK}"
        return f"{groups['prefix_bearer']}{MASK}"

    return LOG_SECRET_RE.sub(_sub, line)


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
    "redact_value",
]
