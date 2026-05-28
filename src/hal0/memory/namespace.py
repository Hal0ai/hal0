"""ADR-0005 §3 namespace resolution — shared by the MCP + REST surfaces.

The MCP server (:mod:`hal0.mcp.memory`) and the REST shims
(:mod:`hal0.api.routes.memory`) both translate caller-supplied
``dataset`` + identity context into the effective Cognee dataset name.
Keeping that logic in one place ensures the two surfaces can't drift —
issue #317 surfaced exactly that kind of drift, where the REST handler
hardcoded ``"shared"`` while the MCP dispatcher correctly honored
``private:<client_id>`` promotion.

The rule (ADR-0005 §3):

  - Writes default to ``"shared"``.
  - Callers in "private mode" promote to ``private:<client_id>`` for
    writes; ``--private`` wins over an explicit body ``dataset`` field
    so a private-mode client can't smuggle data into ``shared``.
  - Private-mode reads expand to ``[shared, private:<client_id>]`` so
    a caller sees their own scoped items alongside the shared bucket
    without having to opt in per-call.
  - Requesting ``private`` without an authenticated ``client_id`` is
    a usage error — the namespace promotion has no identity to scope to.

This module is intentionally tiny: just two pure functions + the
``MemoryNamespaceError`` sentinel. The wrapper-level enforcement
(rejecting cross-client writes, intersecting read scopes) still lives
in :mod:`hal0.memory.cognee_wrapper` — this layer is for transport-side
resolution only.
"""

from __future__ import annotations

DEFAULT_DATASET = "shared"
PRIVATE_PREFIX = "private:"


class MemoryNamespaceError(ValueError):
    """Raised when namespace resolution can't be satisfied (e.g. private
    requested without an authenticated client_id)."""


def resolve_write_dataset(
    requested: str | None,
    *,
    private: bool,
    client_id: str | None,
) -> str:
    """Translate a write request into the effective Cognee dataset name.

    Mirrors :func:`hal0.mcp.memory._resolve_dataset` (which delegates
    here) — the docstring rule from ADR-0005 §3 applies:

      - ``private=True`` → ``private:<client_id>`` (raises if no
        ``client_id`` is available).
      - ``requested`` is ``None`` / empty → :data:`DEFAULT_DATASET`.
      - ``requested`` starts with ``private:`` and ``private=False``
        → ``MemoryNamespaceError``. PR #366 review hardening: a
        non-private caller must not be able to address the private
        namespace by passing the prefix in the body — the toggle is
        the only path in. Surfaces as 400 at the transport layer
        instead of silently being forwarded to the wrapper.
      - Otherwise the requested string is passed through verbatim.
    """
    if private:
        if not client_id:
            raise MemoryNamespaceError("private namespace requires an authenticated client_id")
        return f"{PRIVATE_PREFIX}{client_id}"
    if requested is None or not requested.strip():
        return DEFAULT_DATASET
    if requested.startswith(PRIVATE_PREFIX):
        raise MemoryNamespaceError(
            "non-private callers cannot address the private namespace by name; "
            "send X-hal0-Private: 1 (REST) or private=true (MCP) instead"
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

      - ``requested`` already a list → pass through (caller knows what
        they want; the wrapper still intersects against the read scope).
      - ``requested`` empty/``None`` + ``private`` + ``client_id`` →
        expand to ``[shared, private:<client_id>]`` per §3.
      - ``requested`` empty/``None`` otherwise → :data:`DEFAULT_DATASET`.
      - ``requested`` non-empty string → resolved via
        :func:`resolve_write_dataset` (same rule applies; e.g. an explicit
        ``shared`` from a private-mode client still gets promoted —
        consistent with the write side).
    """
    if isinstance(requested, list):
        return [str(d) for d in requested]
    if requested is None or (isinstance(requested, str) and not requested.strip()):
        if private and client_id:
            return [DEFAULT_DATASET, f"{PRIVATE_PREFIX}{client_id}"]
        return DEFAULT_DATASET
    return resolve_write_dataset(requested, private=private, client_id=client_id)


__all__ = [
    "DEFAULT_DATASET",
    "PRIVATE_PREFIX",
    "MemoryNamespaceError",
    "resolve_read_datasets",
    "resolve_write_dataset",
]
