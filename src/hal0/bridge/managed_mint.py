"""Mint or rotate a hal0-managed bridge client from a root CLI, as the hal0 user.

A root-run CLI (``sudo hal0 agent install pi``, ``sudo hal0 update component
pi``) cannot mint a managed client itself: :func:`hal0.bridge.managed.
ensure_managed_bridge_client` mints only as the ``hal0`` service account, so
``bridge.db`` is never left with a root-owned row or file hal0-api cannot write.
The CLI therefore re-runs this module as ``hal0`` (``runuser``/``setpriv``/
``sudo``, :func:`hal0.system.seam.as_hal0_argv`) and reads one JSON line from
its stdout::

    python -m hal0.bridge.managed_mint <name> <created_by> [--rotate]
    {"status": "minted" | "rotated" | "existing" | "foreign" | "revoked"
               | "disabled" | "error", "key": "<plaintext>" | null, "detail": "..."}

The key travels only over that pipe: never in argv, the environment or a file.
``--rotate`` replaces the key of a client this module may manage (same
``created_by``); a client someone else made is never touched (``foreign``).
The client gets the ``memory`` scope (``agents`` is always granted with it,
:func:`hal0.bridge.keys.normalize_scopes`).
"""

from __future__ import annotations

import json
import sys
from typing import Any

#: The scopes a managed coding-agent client is given.
MANAGED_SCOPES: tuple[str, ...] = ("memory",)


def mint(name: str, created_by: str, *, rotate: bool, store: Any = None) -> dict[str, Any]:
    """The answer :func:`main` prints. Never raises."""
    from hal0.bridge.managed import ManagedClientRefused, ensure_managed_bridge_client

    try:
        from hal0.config.loader import load_hal0_config

        if not load_hal0_config().bridge.enabled:
            return {"status": "disabled", "key": None, "detail": "[bridge] enabled is false"}
        if store is None:
            from hal0.bridge.store import BridgeStore
            from hal0.config.paths import bridge_db

            store = BridgeStore(bridge_db())
            store.open()
        row, minted = ensure_managed_bridge_client(
            name, scopes=MANAGED_SCOPES, created_by=created_by, store=store
        )
        if row is None:
            return {"status": "error", "key": None, "detail": "not running as the hal0 user"}
        if minted:
            return {"status": "minted", "key": minted, "detail": ""}
        if row.revoked_at is not None:
            return {
                "status": "revoked",
                "key": None,
                "detail": f"bridge client {name!r} is revoked",
            }
        if not rotate:
            return {"status": "existing", "key": None, "detail": ""}
        _row, key = store.rotate_client(name)
        return {"status": "rotated", "key": key, "detail": ""}
    except ManagedClientRefused as exc:
        return {"status": "foreign", "key": None, "detail": str(exc)}
    except Exception as exc:
        return {"status": "error", "key": None, "detail": f"{type(exc).__name__}: {exc}"}


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--rotate"]
    if len(args) != 2:
        print(
            "usage: python -m hal0.bridge.managed_mint <name> <created_by> [--rotate]",
            file=sys.stderr,
        )
        return 2
    answer = mint(args[0], args[1], rotate="--rotate" in argv)
    sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the driver
    raise SystemExit(main(sys.argv[1:]))
