"""Bridge clients that hal0 itself mints for a companion (Open WebUI, later the brain).

A companion needs a bridge key in a file hal0 writes, so hal0-api mints the
client rather than asking the operator to. The mint is deliberately narrow:

* only the ``hal0`` service account may mint (a root caller such as the image-model
  installer renders what exists and never creates ``bridge.db`` rows, which would
  leave a root-owned file hal0-api cannot write);
* the row is stamped ``created_by`` with a ``system:`` value, and a row of that name
  with another ``created_by`` is never adopted: an operator who already made a client
  called ``openwebui`` keeps it, and hal0 renders nothing from it.

This module imports only the bridge store and keys and the service-user predicate, so
it stays below ``hal0.api`` in the import graph (``tests/bridge/test_import_graph.py``).
The store is passed in by the caller, which owns the process-wide singleton.
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog

from hal0.bridge.store import BridgeStore, BridgeStoreError, ClientRow
from hal0.system.seam import is_hal0_service_user

log = structlog.get_logger("hal0.bridge.managed")


class ManagedClientRefused(Exception):
    """The named client exists but is not hal0's to manage (another ``created_by``)."""


def ensure_managed_bridge_client(
    name: str,
    *,
    scopes: Iterable[str],
    created_by: str,
    store: BridgeStore,
    may_mint: bool | None = None,
) -> tuple[ClientRow | None, str | None]:
    """Return ``(row, plaintext)`` for the managed client ``name``.

    ``may_mint`` overrides the service-user check (tests; the arm passes its injected predicate).
    ``plaintext`` is set only when this call minted the row. ``(None, None)`` when
    the caller is not the ``hal0`` service account and no row exists (nothing is
    created). Raises :class:`ManagedClientRefused` when a row exists with another
    ``created_by``. Store errors propagate.
    """
    row = store.get_client(name)
    if row is not None:
        if row.created_by != created_by:
            raise ManagedClientRefused(
                f"bridge client {name!r} was created by {row.created_by!r}, not {created_by!r}"
            )
        return row, None
    if not (is_hal0_service_user() if may_mint is None else may_mint):
        return None, None
    try:
        row, plain = store.add_client(name, created_by, scopes=scopes)
    except BridgeStoreError as exc:
        if exc.code != "bridge.client_exists":  # lost a race to another minter: adopt theirs
            raise
        row = store.get_client(name)
        if row is None or row.created_by != created_by:
            raise ManagedClientRefused(f"bridge client {name!r} is not managed") from exc
        return row, None
    log.info("bridge.managed_client_minted", name=name, created_by=created_by)
    return row, plain
