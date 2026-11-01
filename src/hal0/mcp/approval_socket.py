"""The root CLI's path to an approval decision (ADR-0046).

``hal0 agent approvals approve|deny`` must work for root and must not be
forgeable by a process running as ``hal0``, the account the bundled agent and
hal0-api share. Everything ``hal0`` can read, it can present, so no stored
credential qualifies: not the admin key, not a token file, not a public key in
a tree ``hal0`` can write.

So this asks the kernel. hal0-api listens on a unix socket
(:func:`hal0.config.paths.approval_socket`) and reads each connection's
``SO_PEERCRED``: the UID the kernel recorded when the peer called ``connect``.
A peer that is not root is refused before a byte of its request is read.
Nothing is stored or provisioned, so there is nothing to steal or migrate, and
no unit file changes.

A same-UID process can unlink the socket and listen in its place. That can make
the CLI report a false outcome or fail; it cannot produce a decision, because
only the real listener holds the queue. It is one of the things a process with
the ``hal0`` account can do, which ADR-0002 already records.

The protocol is one JSON line each way::

    -> {"action": "approve", "id": "<approval id>"}
    <- {"status": 200, "approval": {...}}
    <- {"status": 404, "error": {"code": "approvals.not_found", "message": "..."}}

``status`` carries the numbers the REST routes answer with, so the two surfaces
read the same.

The module is importable without :mod:`hal0.api`, because the CLI imports it and
importing the API builds the app.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import struct
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final

import structlog

from hal0.config import paths

log = structlog.get_logger(__name__)

#: The only peer allowed to decide.
ROOT_UID: Final[int] = 0

#: Audit actor of a decision made through this socket.
ACTOR: Final[str] = "cli:root"

#: A request is one action and one id; anything longer is not a request.
_MAX_REQUEST_BYTES: Final[int] = 1024
_READ_TIMEOUT_S: Final[float] = 5.0
_DISCARD_TIMEOUT_S: Final[float] = 0.2

#: How long the CLI waits for a reply. ``approve`` awaits the gated tool itself,
#: and a slot load can take minutes. The decision stands if the wait runs out.
CLIENT_TIMEOUT_S: Final[float] = 300.0

_ACTIONS: Final[frozenset[str]] = frozenset({"approve", "deny"})


class ApprovalSocketUnavailable(Exception):
    """The CLI could not reach hal0-api's decision socket, or got no usable reply."""


def peer_uid(sock: Any) -> int | None:
    """The UID the kernel recorded for this unix-socket peer, else ``None``.

    ``None`` on a platform without ``SO_PEERCRED`` and on any read failure; the
    caller refuses it, so a box that cannot answer the question fails closed.
    """
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None or sock is None:
        return None
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
    except (OSError, struct.error):
        return None
    return int(uid)


def _error(status: int, code: str, message: str) -> dict[str, Any]:
    return {"status": status, "error": {"code": code, "message": message}}


async def _decide(app: Any, action: str, approval_id: str) -> dict[str, Any]:
    """Run one decision against the app's queue, audited like the REST route."""
    from hal0.activity import ActionRecorder, audit_action

    queue = getattr(app.state, "approval_queue", None)
    if queue is None:
        return _error(503, "approvals.unavailable", "approval queue not initialised")

    decide = queue.approve if action == "approve" else queue.deny

    async def _run(rec: ActionRecorder) -> dict[str, Any]:
        result: dict[str, Any] = await decide(approval_id)
        rec.after = {"id": approval_id, "state": result.get("state")}
        return {"status": 200, "approval": result}

    try:
        store = getattr(app.state, "audit", None)
        if store is None:
            return await _run(ActionRecorder(target=approval_id))
        async with audit_action(
            store,
            category="approval",
            action=f"approval.{action}",
            target=approval_id,
            actor=ACTOR,
        ) as rec:
            return await _run(rec)
    except KeyError:
        return _error(404, "approvals.not_found", f"approval {approval_id!r} not found")
    except ValueError as exc:
        return _error(409, "approvals.already_resolved", str(exc))


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str] | None:
    """The ``(action, id)`` of a well-formed request, else ``None``."""
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=_READ_TIMEOUT_S)
        body = json.loads(line)
    except (TimeoutError, ValueError, OSError):
        return None
    if not isinstance(body, dict):
        return None
    action, approval_id = body.get("action"), body.get("id")
    if not isinstance(action, str) or action not in _ACTIONS:
        return None
    if not isinstance(approval_id, str) or not approval_id:
        return None
    return action, approval_id


async def _discard_input(reader: asyncio.StreamReader) -> None:
    """Read what a peer sent and throw it away, so a refusal reaches it.

    Closing a unix socket with unread input resets the connection, which loses
    the reply on its way out. Bounded: a peer that never finishes sending costs
    this connection a moment, then gets its reply anyway.
    """
    with contextlib.suppress(TimeoutError, OSError, ValueError):
        async with asyncio.timeout(_DISCARD_TIMEOUT_S):
            while await reader.read(_MAX_REQUEST_BYTES):
                pass


def _connection_handler(
    app: Any, decider_uid: int
) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]:
    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            uid = peer_uid(writer.get_extra_info("socket"))
            if uid != decider_uid:
                # Checked before the request is read: a refused peer gets to
                # send nothing that is ever parsed.
                log.warning("hal0.approvals.socket_refused", peer_uid=uid)
                await _discard_input(reader)
                reply = _error(403, "auth.forbidden", "approval decisions need root on this box")
            else:
                request = await _read_request(reader)
                if request is None:
                    await _discard_input(reader)
                    reply = _error(400, "approvals.bad_request", "expected one JSON request line")
                else:
                    reply = await _decide(app, *request)
            writer.write(json.dumps(reply, default=str).encode("utf-8") + b"\n")
            await writer.drain()
        except OSError:
            # The CLI went away (its wait ran out). The decision still stands
            # and the audit row still records it.
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    return _handle


async def start_server(
    app: Any, *, path: Path | None = None, decider_uid: int = ROOT_UID
) -> Callable[[], Awaitable[None]]:
    """Listen for the root CLI; return the callable that stops listening.

    Never raises: a box where the socket cannot be bound (an unwritable state
    directory in a dev run) still boots, the dashboard still decides, and only
    the CLI reports the socket missing. ``decider_uid`` exists for the tests,
    which do not run as root; production callers leave it at :data:`ROOT_UID`.
    """
    target = path if path is not None else paths.approval_socket()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        server = await asyncio.start_unix_server(
            _connection_handler(app, decider_uid),
            path=str(target),
            limit=_MAX_REQUEST_BYTES,
        )
        # Root reaches a 0600 socket regardless of owner, and nobody else who
        # could reach it would pass the peer check anyway.
        os.chmod(target, 0o600)
    except (OSError, ValueError) as exc:
        log.warning("hal0.approvals.socket_unavailable", path=str(target), error=str(exc))

        async def _nothing_to_stop() -> None:
            return None

        return _nothing_to_stop

    async def _stop() -> None:
        server.close()
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)

    return _stop


def send_decision(
    action: str,
    approval_id: str,
    *,
    path: Path | None = None,
    timeout: float = CLIENT_TIMEOUT_S,
) -> dict[str, Any]:
    """Send one decision and return the reply (the CLI's side, blocking)."""
    target = path if path is not None else paths.approval_socket()
    request = json.dumps({"action": action, "id": approval_id}).encode("utf-8") + b"\n"
    chunks: list[bytes] = []
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(target))
            sock.sendall(request)
            sock.shutdown(socket.SHUT_WR)
            while chunk := sock.recv(65536):
                chunks.append(chunk)
    except TimeoutError as exc:
        raise ApprovalSocketUnavailable(
            f"no reply within {timeout:.0f}s; the decision may still be running, "
            "so check `hal0 agent approvals list` and the activity log"
        ) from exc
    except OSError as exc:
        raise ApprovalSocketUnavailable(
            f"cannot reach hal0-api's decision socket at {target} ({exc}); "
            "is hal0-api running on this box?"
        ) from exc
    try:
        reply = json.loads(b"".join(chunks))
    except ValueError as exc:
        raise ApprovalSocketUnavailable("hal0-api's decision socket sent no valid reply") from exc
    if not isinstance(reply, dict):
        raise ApprovalSocketUnavailable("hal0-api's decision socket sent no valid reply")
    return reply


__all__ = [
    "ACTOR",
    "CLIENT_TIMEOUT_S",
    "ROOT_UID",
    "ApprovalSocketUnavailable",
    "peer_uid",
    "send_decision",
    "start_server",
]
