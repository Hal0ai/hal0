"""Check, mint or rotate a hal0-managed bridge client from a root CLI, as the hal0 user.

A root-run CLI (``sudo hal0 agent install pi``, ``sudo hal0 update component
pi``) cannot mint a managed client itself: :func:`hal0.bridge.managed.
ensure_managed_bridge_client` mints only as the ``hal0`` service account, so
``bridge.db`` is never left with a root-owned row or file hal0-api cannot write.
The CLI therefore re-runs this module as ``hal0`` (``runuser``/``setpriv``/
``sudo``, :func:`hal0.system.seam.as_hal0_argv`)::

    python -m hal0.bridge.managed_mint <name> <created_by> [--rotate] [--keys-on-stdin]

With ``--keys-on-stdin`` it first reads ``{"keys": ["<candidate>", ...]}`` from
stdin: the keys the caller already holds. It prints one JSON line::

    {"status": "valid" | "minted" | "rotated" | "existing" | "foreign"
               | "revoked" | "disabled" | "error",
     "index": <int, for "valid">, "key": "<plaintext>" | null, "detail": "..."}

Whether a held key is still good is decided here, against the store
(:meth:`hal0.bridge.store.BridgeStore.verify_key`), and never from an HTTP
answer: hal0-api also answers 401 for a valid key when it cannot read
``bridge.db`` or was started before ``[bridge] enabled`` was switched on.
``valid`` names the first candidate the store accepts by its index, so a key
is never echoed back. A new key travels only over the stdout pipe; candidates
only over stdin. Neither is put in argv, the environment, a file or a log.

``--rotate`` replaces the key of an active client this module may manage
(same ``created_by``) when none of the candidates verifies; without it such a
client answers ``existing`` and nothing changes. A client someone else made
is never touched (``foreign``).

The client gets the ``memory`` scope, and with it ``agents``, which every
bridge client holds (:func:`hal0.bridge.keys.normalize_scopes`). So whoever
can read the file the key is written to can also start and steer Hermes runs
through ``/mcp/agents``, not only read and write memory. The key is stored
0600 in the installing user's home for that reason.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any

#: The scopes a managed coding-agent client is given (``agents`` comes with them).
MANAGED_SCOPES: tuple[str, ...] = ("memory",)


def mint(
    name: str,
    created_by: str,
    *,
    rotate: bool,
    candidates: Sequence[str] = (),
    store: Any = None,
) -> dict[str, Any]:
    """The answer :func:`main` prints. Never raises.

    Order: the bridge switched off or the store unreadable answers without
    touching anything; a client of ``name`` made by someone else is
    ``foreign`` and a revoked one ``revoked``; a candidate the store verifies
    for ``name`` is ``valid``; no client is ``minted``; an active client with
    no verifying candidate is ``rotated`` under ``rotate``, else ``existing``.
    """
    from hal0.bridge import keys
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
        row = store.get_client(name)
        if row is not None and row.created_by != created_by:
            raise ManagedClientRefused(
                f"bridge client {name!r} was created by {row.created_by!r}, not {created_by!r}"
            )
        if row is not None and row.revoked_at is not None:
            return {
                "status": "revoked",
                "key": None,
                "detail": f"bridge client {name!r} is revoked",
            }
        if row is not None:
            for index, candidate in enumerate(candidates):
                if (
                    isinstance(candidate, str)
                    and keys.name_of(candidate) == name
                    and store.verify_key(candidate) is not None
                ):
                    return {"status": "valid", "index": index, "key": None, "detail": ""}
        row, minted = ensure_managed_bridge_client(
            name, scopes=MANAGED_SCOPES, created_by=created_by, store=store
        )
        if row is None:
            return {"status": "error", "key": None, "detail": "not running as the hal0 user"}
        if minted:
            return {"status": "minted", "key": minted, "detail": ""}
        if not rotate:
            return {
                "status": "existing",
                "key": None,
                "detail": f"bridge client {name!r} exists and no key held here verifies",
            }
        _row, key = store.rotate_client(name)
        return {"status": "rotated", "key": key, "detail": ""}
    except ManagedClientRefused as exc:
        return {"status": "foreign", "key": None, "detail": str(exc)}
    except Exception as exc:
        return {"status": "error", "key": None, "detail": f"{type(exc).__name__}: {exc}"}


def _read_candidates(stream: Any) -> list[str] | None:
    """``{"keys": [...]}`` from ``stream``; ``None`` when it is not that shape."""
    try:
        payload = json.loads(stream.read() or "{}")
    except ValueError:
        return None
    held = payload.get("keys", []) if isinstance(payload, dict) else None
    if not isinstance(held, list) or not all(isinstance(k, str) for k in held):
        return None
    return held


def main(argv: list[str]) -> int:
    flags = {"--rotate", "--keys-on-stdin"}
    args = [a for a in argv if a not in flags]
    if len(args) != 2:
        print(
            "usage: python -m hal0.bridge.managed_mint <name> <created_by> "
            "[--rotate] [--keys-on-stdin]",
            file=sys.stderr,
        )
        return 2
    candidates: list[str] = []
    if "--keys-on-stdin" in argv:
        held = _read_candidates(sys.stdin)
        if held is None:
            sys.stdout.write(
                json.dumps(
                    {"status": "error", "key": None, "detail": 'stdin is not {"keys": [...]}'}
                )
                + "\n"
            )
            return 0
        candidates = held
    answer = mint(args[0], args[1], rotate="--rotate" in argv, candidates=candidates)
    sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the driver
    raise SystemExit(main(sys.argv[1:]))
