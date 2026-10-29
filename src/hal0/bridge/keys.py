"""Bridge client key format (ADR-0043).

A bridge key reads ``hal0bk_<name>.<token>``. ``<name>`` is the client's
name, embedded so the auth layer can look the client row up directly instead
of scanning every stored hash. ``<token>`` is ``secrets.token_urlsafe(32)``.
The separator is a literal ``.`` because the urlsafe alphabet includes ``_``
and ``-`` but never ``.``, so the first ``.`` after the prefix always ends the
name.

Only the sha256 hex digest of the whole key string is stored; the plaintext
is shown once at mint time and cannot be recovered.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Iterable

KEY_PREFIX = "hal0bk_"

# Lowercase, starts with a letter or digit, at most 32 characters. No ``.``,
# so a name can never swallow the key's separator.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# What a bridge client may be granted. ``agents`` (delegate to Hermes) is held
# by every client; ``memory`` reaches the memory MCP server; ``admin`` reaches
# the hal0 admin MCP server.
SCOPES: frozenset[str] = frozenset({"agents", "memory", "admin"})

# Names no client may take. A bridge principal's memory identity is the bare
# client name, so a client called ``hermes`` would read and write the bundled
# Hermes agent's private memory namespace, and ``shared`` or ``anonymous``
# would alias the namespaces memory already treats specially. ``admin`` is the
# name the operator's own credential reports on ``GET /api/bridge/me``;
# ``hal0`` and ``hal0-cli`` are hal0's own callers. ``hermes__<profile>`` is a
# Hermes profile's namespace.
RESERVED_CLIENT_NAMES: frozenset[str] = frozenset(
    {"hermes", "pi", "admin", "anonymous", "hal0", "hal0-cli", "shared"}
)
RESERVED_CLIENT_PREFIXES: tuple[str, ...] = ("hermes__",)


def valid_name(name: str) -> bool:
    """Return True when ``name`` is an acceptable bridge client name."""
    return NAME_RE.fullmatch(name) is not None


def is_reserved_name(name: str) -> bool:
    """Return True when ``name`` is one no bridge client may take (ADR-0043)."""
    return name in RESERVED_CLIENT_NAMES or name.startswith(RESERVED_CLIENT_PREFIXES)


def hash_key(key: str) -> str:
    """Return the sha256 hex digest of the whole key string."""
    return hashlib.sha256(key.encode()).hexdigest()


def mint(name: str) -> tuple[str, str]:
    """Create a key for ``name``; return ``(plaintext, sha256 hex)``.

    Raises ``ValueError`` when ``name`` fails :func:`valid_name`.
    """
    if not valid_name(name):
        raise ValueError(f"invalid bridge client name {name!r}")
    plain = f"{KEY_PREFIX}{name}.{secrets.token_urlsafe(32)}"
    return plain, hash_key(plain)


def name_of(key: str) -> str | None:
    """Return the client name embedded in ``key``, or None when the shape is wrong.

    Shape is ``hal0bk_<name>.<token>`` with a :func:`valid_name` head. This
    checks shape only; whether the key is genuine is decided by comparing
    :func:`hash_key` against the stored digest.
    """
    if not key.startswith(KEY_PREFIX):
        return None
    head, sep, _token = key[len(KEY_PREFIX) :].partition(".")
    if not sep or not valid_name(head):
        return None
    return head


def normalize_scopes(values: Iterable[str]) -> frozenset[str]:
    """Return ``values`` as a canonical scope set that always includes ``agents``.

    Each value is stripped and lowercased. Raises ``ValueError`` naming the
    first scope that is not in :data:`SCOPES`.
    """
    scopes = {"agents"}
    for value in values:
        scope = value.strip().lower()
        if scope not in SCOPES:
            raise ValueError(f"unknown bridge scope {scope!r}")
        scopes.add(scope)
    return frozenset(scopes)
