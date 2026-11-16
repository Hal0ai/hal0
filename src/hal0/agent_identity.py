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
The names hal0's own agents use are reserved there, so the two never meet.

This module sits below ``hal0.api`` and imports only config paths and the
perms table. NEVER log or echo a key or the secret.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import os
import secrets
import tempfile
from pathlib import Path
from typing import Final

#: The request header that carries an agent identity key.
AGENT_KEY_HEADER: Final[str] = "X-hal0-Agent-Key"
#: The request header that carries the claimed agent identity.
AGENT_HEADER: Final[str] = "X-hal0-Agent"
#: Prefix of every agent identity key, so a leaked one is recognisable.
KEY_PREFIX: Final[str] = "hal0ak_"

_DOMAIN: Final[bytes] = b"hal0-agent-identity/v1\x00"
_SECRET_ENV: Final[str] = "HAL0_AGENT_IDENTITY_SECRET_PATH"
_SECRET_NAME: Final[str] = "identity.bin"

_cache: tuple[str, int, int, bytes] | None = None


def secret_path() -> Path:
    """Where the identity secret lives (``$HAL0_AGENT_IDENTITY_SECRET_PATH`` overrides)."""
    override = os.environ.get(_SECRET_ENV, "").strip()
    if override:
        return Path(override)
    from hal0.config import paths

    return paths.var_lib() / "agents" / _SECRET_NAME


def _read_secret(path: Path) -> bytes | None:
    global _cache
    try:
        st = path.stat()
    except OSError:
        return None
    cached = _cache
    if cached is not None and cached[:3] == (str(path), st.st_mtime_ns, st.st_size):
        return cached[3]
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 32:
        return None
    _cache = (str(path), st.st_mtime_ns, st.st_size, data)
    return data


def _create_secret(path: Path) -> bytes | None:
    """Write a new secret at ``path``, or ``None`` when this process may not.

    The service user (or any non-root caller that can write the directory, a
    test's ``HAL0_HOME``) creates the directory and the file. Root creates the
    file only inside an existing directory and hands it to that directory's
    owner, so ``hal0-api`` can always read what a root provisioning run wrote.
    """
    is_root = os.geteuid() == 0
    parent = path.parent
    try:
        if not parent.is_dir():
            if is_root:
                return None
            from hal0.install.perms import AGENTS_DIR_MODE, ensure_shared_dir

            ensure_shared_dir(parent, mode=AGENTS_DIR_MODE)
        owner = parent.stat()
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
    except OSError:
        return None
    tmp = Path(tmp_name)
    secret = secrets.token_bytes(32)
    try:
        os.fchmod(fd, 0o600)
        if is_root:
            os.fchown(fd, owner.st_uid, owner.st_gid)
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
    if not agent_id:
        return None
    secret = _secret(create=True)
    return _derive(secret, agent_id) if secret is not None else None


def verify(agent_id: str, presented: str | None) -> bool:
    """True when ``presented`` is the identity key for ``agent_id``.

    Never creates the secret: with none on disk no key was ever handed out.
    """
    if not agent_id or not presented or not presented.startswith(KEY_PREFIX):
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
