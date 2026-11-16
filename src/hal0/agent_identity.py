"""Agent identity keys: the credential behind a first-party ``X-hal0-Agent`` claim (#3569).

``X-hal0-Agent`` names the memory identity a caller acts as, and that name is
also the owner of ``visibility:private`` notes. A header is a claim, not a
credential, so on its own it is an audit label only: a caller that sends
``X-hal0-Agent: hermes`` and nothing else reads and writes no private memory.

hal0's own agents (the Hermes profiles and Pi) back the claim with an identity
key, sent as ``X-hal0-Agent-Key``. The key for agent ``<id>`` is::

    hal0ak_ + base64url(HMAC-SHA256(secret, "hal0-agent-identity/v1\\0" + <id>))

where ``secret`` is 32 random bytes in ``/var/lib/hal0/agents/identity.bin``
(``hal0``-owned, mode 0600). The key is derived, never stored: provisioning,
the hal0-api startup sweep and a repair all compute the same value, so writing
it again never rotates it, and verifying it is one HMAC with no store lookup.
Deleting the file revokes every agent key at once; the next write of a key
creates a new secret.

Remote agents use a bridge key instead (ADR-0043), whose name is its identity.
The names hal0's own agents use are reserved there, and only those names get an
identity key (:func:`key_for` and :func:`verify` refuse any other), so the two
never meet. The secret is trusted only as a regular 0600 file owned by root or
the service user, and only those two may create it; a refused secret is logged.

This module sits below ``hal0.api`` and imports only config paths and the
perms table. NEVER log or echo a key or the secret.
"""

from __future__ import annotations

import base64
import contextlib
import grp
import hashlib
import hmac
import os
import pwd
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Final

import structlog

log = structlog.get_logger("hal0.agent_identity")

#: The request header that carries an agent identity key.
AGENT_KEY_HEADER: Final[str] = "X-hal0-Agent-Key"
#: The request header that carries the claimed agent identity.
AGENT_HEADER: Final[str] = "X-hal0-Agent"
#: Prefix of every agent identity key, so a leaked one is recognisable.
KEY_PREFIX: Final[str] = "hal0ak_"

_DOMAIN: Final[bytes] = b"hal0-agent-identity/v1\x00"
_SECRET_ENV: Final[str] = "HAL0_AGENT_IDENTITY_SECRET_PATH"
_SECRET_NAME: Final[str] = "identity.bin"

_cache: tuple[str, object, bytes] | None = None


def secret_path() -> Path:
    """Where the identity secret lives (``$HAL0_AGENT_IDENTITY_SECRET_PATH`` overrides)."""
    override = os.environ.get(_SECRET_ENV, "").strip()
    if override:
        return Path(override)
    from hal0.config import paths

    return paths.var_lib() / "agents" / _SECRET_NAME


def _service_uid() -> int | None:
    """The ``hal0`` service account's uid, or ``None`` where no such account exists."""
    try:
        return pwd.getpwnam("hal0").pw_uid
    except KeyError:
        return None


def _service_ids() -> tuple[int, int] | None:
    """``(uid, gid)`` of the ``hal0`` service account (its ``hal0`` group when there is one)."""
    try:
        account = pwd.getpwnam("hal0")
    except KeyError:
        return None
    try:
        gid = grp.getgrnam("hal0").gr_gid
    except KeyError:
        gid = account.pw_gid
    return account.pw_uid, gid


def _trusted_owners() -> set[int]:
    """Who may own the secret: root and the service user.

    On a box with no ``hal0`` account (a development checkout, CI) there is no
    hal0-api to lock out, so the current user is accepted too.
    """
    service = _service_uid()
    return {0, service} if service is not None else {0, os.geteuid(), os.getuid()}


def _may_create() -> bool:
    """Only root or the service user may mint the secret (#3569 review, S3).

    Anyone else (an operator in the ``hal0`` group running the CLI) would leave
    a file hal0-api cannot read, which locks every bundled agent out of its
    private memory. With no ``hal0`` account on the box, see :func:`_trusted_owners`.
    """
    euid = os.geteuid()
    service = _service_uid()
    return euid == 0 or (service is None or euid == service)


#: The last refusal logged, so a bad secret is reported once, not per request.
_refused: tuple[str, object] | None = None


def _refuse(path: Path, reason: str, marker: object) -> None:
    global _refused
    if _refused == (str(path), marker):
        return
    _refused = (str(path), marker)
    log.error(
        "hal0.agent_identity.secret_refused",
        path=str(path),
        reason=reason,
        remedy=(
            "every bundled agent's private memory is refused until this is fixed: give the "
            f"file to the hal0 user with mode 0600, or delete {path} so hal0-api writes a "
            "new one and re-keys the agents on its next start"
        ),
    )


def _read_secret(path: Path) -> bytes | None:
    """The secret at ``path``, or ``None`` when it is missing or not trustworthy.

    Opened without following a link. Refused, with one error log line per
    state (#3569 review, S1 and S2), when it is not a regular file, is owned by
    anyone but root or the service user, is readable or writable by group or
    others, is shorter than 32 bytes, or cannot be read. A missing file is not
    an error: no key was ever handed out.
    """
    global _cache
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _refuse(path, f"cannot open it ({type(exc).__name__}: {exc.strerror})", exc.errno)
        return None
    try:
        st = os.fstat(fd)
        marker = (st.st_ino, st.st_mtime_ns, st.st_size, st.st_uid, st.st_mode)
        cached = _cache
        if cached is not None and cached[:2] == (str(path), marker):
            return cached[2]
        if not stat.S_ISREG(st.st_mode):
            _refuse(path, "it is not a regular file", marker)
            return None
        if st.st_uid not in _trusted_owners():
            _refuse(path, f"it is owned by uid {st.st_uid}, not root or hal0", marker)
            return None
        if st.st_mode & 0o077:
            _refuse(path, f"its mode is {stat.S_IMODE(st.st_mode):04o}, not 0600", marker)
            return None
        try:
            data = os.read(fd, 4096)
        except OSError as exc:
            _refuse(path, f"cannot read it ({exc.strerror})", marker)
            return None
        if len(data) < 32:
            _refuse(path, f"it holds {len(data)} bytes, fewer than 32", marker)
            return None
    finally:
        os.close(fd)
    _cache = (str(path), marker, data)
    return data


def _create_secret(path: Path) -> bytes | None:
    """Write a new secret at ``path``, or ``None`` when this process may not.

    Only root or the service user creates it (:func:`_may_create`). The service
    user creates the directory too; root creates the file only inside an
    existing directory. Root hands the file to the ``hal0`` account whenever it
    exists, and the directory too when it is root-owned: ``installer/agents/pi.sh``
    runs ``mkdir -p`` there as root, so on a box that installs Pi before Hermes the
    directory is root's, and a secret handed to the directory's owner would be one
    hal0-api refuses (#3569 verification). Without a ``hal0`` account the file
    goes to the directory's owner.
    """
    if not _may_create():
        return None
    is_root = os.geteuid() == 0
    parent = path.parent
    try:
        if not parent.is_dir():
            if is_root:
                return None
            from hal0.install.perms import AGENTS_DIR_MODE, ensure_shared_dir

            ensure_shared_dir(parent, mode=AGENTS_DIR_MODE)
        owner = parent.stat()
        target = (owner.st_uid, owner.st_gid)
        service = _service_ids() if is_root else None
        if service is not None:
            target = service
            if owner.st_uid == 0:
                # The hal0 user must be able to open the secret and to create
                # one after a revoke; the declared table mode stays (0711).
                os.chown(parent, *service, follow_symlinks=False)
        elif is_root and owner.st_uid not in _trusted_owners():
            return None
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
    except OSError:
        return None
    tmp = Path(tmp_name)
    secret = secrets.token_bytes(32)
    try:
        os.fchmod(fd, 0o600)
        if is_root:
            os.fchown(fd, *target)
        os.write(fd, secret)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        # Never replace a secret another process wrote meanwhile: that would
        # revoke the keys it already handed out.
        os.link(tmp, path)
    except FileExistsError:
        return _read_secret(path)
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(OSError):
            tmp.unlink()
    return _read_secret(path)


def _secret(*, create: bool) -> bytes | None:
    path = secret_path()
    found = _read_secret(path)
    if found is None and create:
        found = _create_secret(path)
    return found


def _derive(secret: bytes, agent_id: str) -> str:
    digest = hmac.new(secret, _DOMAIN + agent_id.encode("utf-8"), hashlib.sha256).digest()
    return KEY_PREFIX + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def key_for(agent_id: str) -> str | None:
    """The identity key for ``agent_id``, creating the secret when this process may.

    ``None`` when no secret is readable and none can be created here (an
    unprivileged CLI, or root before ``/var/lib/hal0/agents`` exists): the
    caller then sends the bare header, and the next run by hal0-api or root
    fills the key in.
    """
    if not agent_id or not _keyable(agent_id):
        return None
    secret = _secret(create=True)
    return _derive(secret, agent_id) if secret is not None else None


def _keyable(agent_id: str) -> bool:
    """Only the names hal0 reserves for its own agents get an identity key (#3569 review, S5).

    Every other name is one a bridge client may hold, and a bridge client's
    identity comes from its bridge key alone; an identity key for it would open
    that client's private memory on REST, where bridge keys are refused.
    """
    from hal0.bridge.keys import is_reserved_name

    return agent_id != "anonymous" and is_reserved_name(agent_id)


def verify(agent_id: str, presented: str | None) -> bool:
    """True when ``presented`` is the identity key for ``agent_id``.

    Never creates the secret: with none on disk no key was ever handed out.
    """
    if not agent_id or not presented or not presented.startswith(KEY_PREFIX):
        return False
    if not _keyable(agent_id):
        return False
    secret = _secret(create=False)
    if secret is None:
        return False
    return hmac.compare_digest(_derive(secret, agent_id).encode(), presented.strip().encode())


#: What :func:`judge_claim` makes of a well-formed ``X-hal0-Agent`` value.
#: ``verified``: the key matches, the name is the caller's memory identity.
#: ``reserved``: no matching key, and the name is one hal0's own callers use;
#: the request is refused with 403 ``bridge.client_name_reserved``.
#: ``unverified``: no matching key; the name is an audit label only and the
#: caller's memory identity is ``anonymous``.
CLAIM_VERIFIED: Final[str] = "verified"
CLAIM_RESERVED: Final[str] = "reserved"
CLAIM_UNVERIFIED: Final[str] = "unverified"

#: The code a reserved name claimed without its key is refused with: the one
#: a bridge key under a reserved name gets (``hal0.api.auth``, #3560).
RESERVED_CLAIM_CODE: Final[str] = "bridge.client_name_reserved"
RESERVED_CLAIM_MESSAGE: Final[str] = (
    "X-hal0-Agent names an identity hal0 reserves for its own agents, and the request "
    "carries no matching X-hal0-Agent-Key; a remote agent uses a bridge key "
    "(`hal0 bridge add <name>`), and a bundled agent gets its key back with "
    "`hal0 agent reprovision hermes --repair` or `sudo hal0 update component pi`"
)


def judge_claim(agent_id: str, presented: str | None) -> str:
    """Judge a well-formed ``X-hal0-Agent`` value against the ``X-hal0-Agent-Key`` sent.

    ``anonymous`` is the absence of an identity, not a claim, so it is never
    ``reserved``. Every other name on the bridge's reserved list
    (:func:`hal0.bridge.keys.is_reserved_name`: ``hermes``, ``hermes__*``,
    ``pi``, ``hal0*``, ``unknown``, ...) needs its key.
    """
    if verify(agent_id, presented):
        return CLAIM_VERIFIED
    from hal0.bridge.keys import is_reserved_name

    if agent_id != "anonymous" and is_reserved_name(agent_id):
        return CLAIM_RESERVED
    return CLAIM_UNVERIFIED


def identity_headers(agent_id: str) -> dict[str, str]:
    """``X-hal0-Agent`` for ``agent_id``, plus its key when one can be derived here.

    A reserved name with no derivable key (an unprivileged CLI) gets no header
    at all: hal0 would refuse the bare claim with 403, and without it the
    request still reaches shared memory as ``anonymous``. Only for requests to
    hal0's own API: never put these on a request to a third-party server.
    """
    key = key_for(agent_id)
    if key:
        return {AGENT_HEADER: agent_id, AGENT_KEY_HEADER: key}
    if judge_claim(agent_id, None) == CLAIM_RESERVED:
        return {}
    return {AGENT_HEADER: agent_id}


__all__ = [
    "AGENT_HEADER",
    "AGENT_KEY_HEADER",
    "CLAIM_RESERVED",
    "CLAIM_UNVERIFIED",
    "CLAIM_VERIFIED",
    "KEY_PREFIX",
    "RESERVED_CLAIM_CODE",
    "RESERVED_CLAIM_MESSAGE",
    "identity_headers",
    "judge_claim",
    "key_for",
    "secret_path",
    "verify",
]
