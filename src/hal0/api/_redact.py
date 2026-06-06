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
    r"(?i)(?:SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT)"
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
    "MASK",
    "is_sensitive_key",
    "redact_config",
    "redact_value",
]
