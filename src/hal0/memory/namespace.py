"""Namespace resolution — shared by the MCP + REST surfaces.

The MCP server (:mod:`hal0.mcp.memory`) and the REST shims
(:mod:`hal0.api.routes.memory`) both translate caller-supplied
``dataset`` + identity context into the effective dataset name.
Keeping that logic in one place ensures the two surfaces can't drift —
issue #317 surfaced exactly that kind of drift, where the REST handler
hardcoded ``"shared"`` while the MCP dispatcher correctly honored
``private:<client_id>`` promotion.

The rule:

  - Writes default to ``"shared"``.
  - Callers in "private mode" promote to ``private:<client_id>`` for
    writes; ``--private`` wins over an explicit body ``dataset`` field
    so a private-mode client can't smuggle data into ``shared``.
  - Private-mode reads expand to ``[shared, private:<client_id>]`` so
    a caller sees their own scoped items alongside the shared bucket
    without having to opt in per-call.
  - Requesting ``private`` without an authenticated ``client_id`` is
    a usage error — the namespace promotion has no identity to scope to.
  - The namespace set is CLOSED (spec §3 table): ``shared`` | ``agents``
    | ``project:<id>`` | the caller's own ``private:<client_id>``.
    Free-form names used to pass through verbatim, which let any caller
    read/write arbitrary engine banks (and made the items undeletable
    through the id-scoped delete sweep). Writes to unknown namespaces
    now raise; reads *degrade* — an unaddressable entry is dropped from a
    list that still names at least one addressable namespace, so a
    multi-namespace read keeps working instead of erroring.
  - A read request that names namespaces and resolves to NONE of them
    fails CLOSED (#1451). Dropping the last entry used to yield ``[]``,
    which every downstream ``requested or [DEFAULT_DATASET]`` read back
    as "nothing requested" and expanded into the default shared sweep —
    so an approval-gated ``memory_delete`` scoped to a nonexistent bank
    executed against ``shared``, and reads scoped exclusively to banks
    the caller may not address returned shared rows. ``[]`` means *no
    banks*; only ``None`` may expand to the default.

This module is intentionally tiny: pure functions + the
``MemoryNamespaceError`` sentinel. The wrapper-level enforcement
(rejecting cross-client writes, intersecting read scopes) still lives
in the active provider — this layer is for transport-side resolution.
"""

from __future__ import annotations

import re

DEFAULT_DATASET = "shared"
AGENTS_DATASET = "agents"
PRIVATE_PREFIX = "private:"
PROJECT_PREFIX = "project:"

# Sentinel the MCP/REST identity resolvers emit for an absent/malformed
# ``X-hal0-Agent`` header. It is NOT a real identity: a private write under it
# must be rejected, not mis-scoped into a ``private:anonymous`` bank. See
# ``mcp_mount.client_id_resolver`` whose contract delegates that rejection here.
ANONYMOUS_CLIENT_ID = "anonymous"

# Spec §3 namespace grammar — the scoped suffix after ``project:`` follows
# the same identity rules as agent ids: alnum + ``-`` + ``_``,
# ≤64 chars, so bank names derived from it stay path-traversal-free.
_SCOPED_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


class MemoryNamespaceError(ValueError):
    """Raised when namespace resolution can't be satisfied (e.g. private
    requested without an authenticated client_id, or an unknown
    namespace on a write)."""


def is_known_namespace(name: str, *, client_id: str | None = None) -> bool:
    """Spec §3 table membership: ``shared`` | ``agents`` | ``project:<id>``
    | the caller's own ``private:<client_id>``."""
    if name in (DEFAULT_DATASET, AGENTS_DATASET):
        return True
    if name.startswith(PROJECT_PREFIX):
        return bool(_SCOPED_ID_PATTERN.match(name[len(PROJECT_PREFIX) :]))
    if name.startswith(PRIVATE_PREFIX):
        return client_id is not None and name == f"{PRIVATE_PREFIX}{client_id}"
    return False


def resolve_write_dataset(
    requested: str | None,
    *,
    private: bool,
    client_id: str | None,
) -> str:
    """Translate a write request into the effective dataset name.

    Mirrors :func:`hal0.mcp.memory._resolve_dataset` (which delegates
    here) — the rule described in the module docstring applies:

      - ``private=True`` → ``private:<client_id>`` (raises if no
        ``client_id`` is available).
      - ``requested`` is ``None`` / empty → :data:`DEFAULT_DATASET`.
      - ``requested`` starts with ``private:`` and ``private=False``
        → ``MemoryNamespaceError``. PR #366 review hardening: a
        non-private caller must not be able to address the private
        namespace by passing the prefix in the body — the toggle is
        the only path in. Surfaces as 400 at the transport layer
        instead of silently being forwarded to the wrapper.
      - ``requested`` outside the spec §3 namespace table →
        ``MemoryNamespaceError`` (closed-set hardening; see module
        docstring).
    """
    if private:
        if not client_id or client_id == ANONYMOUS_CLIENT_ID:
            raise MemoryNamespaceError("private namespace requires an authenticated client_id")
        return f"{PRIVATE_PREFIX}{client_id}"
    if requested is None or not requested.strip():
        return DEFAULT_DATASET
    if requested.startswith(PRIVATE_PREFIX):
        raise MemoryNamespaceError(
            "non-private callers cannot address the private namespace by name; "
            "send X-hal0-Private: 1 (REST) or private=true (MCP) instead"
        )
    if not is_known_namespace(requested, client_id=client_id):
        raise MemoryNamespaceError(
            f"unknown namespace {requested!r}; writes accept 'shared', 'agents', "
            "or 'project:<id>' (private goes through the private-mode toggle)"
        )
    return requested


def resolve_read_datasets(
    requested: str | list[str] | None,
    *,
    private: bool,
    client_id: str | None,
) -> str | list[str]:
    """Translate a read request into the effective dataset filter.

    Mirrors the read branch from :func:`hal0.mcp.memory._memory_search`:

      - ``requested`` already a non-empty list → filtered against the
        spec §3 namespace table (unknown / foreign-private entries are
        dropped — the provider applies the same rule, this keeps the
        contract visible at the front door). If **every** entry is
        dropped the call raises ``MemoryNamespaceError`` rather than
        returning ``[]``: an empty filter result is indistinguishable
        from "unscoped" to every downstream ``or DEFAULT_DATASET``, and
        that collapse is #1451 (an approved foreign-bank delete executing
        against ``shared``). Partial drops still degrade.
      - ``requested`` an empty list → no namespace was named at all, so it
        is treated exactly like ``None`` (below), not as a rejection.
      - ``requested`` empty/``None`` + ``private`` + ``client_id`` →
        expand to ``[shared, private:<client_id>]`` per §3.
      - ``requested`` empty/``None`` otherwise → :data:`DEFAULT_DATASET`.
      - ``requested`` non-empty string → resolved via
        :func:`resolve_write_dataset` (same rule applies; e.g. an explicit
        ``shared`` from a private-mode client still gets promoted —
        consistent with the write side).
      - ``private=True`` with no authenticated ``client_id`` → always
        ``MemoryNamespaceError``, regardless of ``requested`` shape — same
        guard :func:`resolve_write_dataset` applies. Without this, an
        unauthenticated private-mode caller with no explicit ``requested``
        fell through the empty/``None`` branch's ``private and client_id``
        check straight to the "not private at all" default (``shared``),
        silently degrading a claimed-private read into an unscoped shared
        one instead of refusing it (#1669).
    """
    if private and (not client_id or client_id == ANONYMOUS_CLIENT_ID):
        raise MemoryNamespaceError("private namespace requires an authenticated client_id")
    if isinstance(requested, list) and requested:
        names = [str(d) for d in requested]
        kept = [d for d in names if is_known_namespace(d, client_id=client_id)]
        if not kept:
            raise MemoryNamespaceError(
                "no addressable namespace in the requested scope "
                f"{names!r}; reads accept 'shared', 'agents', 'project:<id>', "
                "or your own 'private:<client_id>'"
            )
        return kept
    if requested is None or isinstance(requested, list) or not requested.strip():
        if private:
            return [DEFAULT_DATASET, f"{PRIVATE_PREFIX}{client_id}"]
        return DEFAULT_DATASET
    return resolve_write_dataset(requested, private=private, client_id=client_id)


__all__ = [
    "AGENTS_DATASET",
    "DEFAULT_DATASET",
    "PRIVATE_PREFIX",
    "PROJECT_PREFIX",
    "MemoryNamespaceError",
    "is_known_namespace",
    "resolve_read_datasets",
    "resolve_write_dataset",
]
