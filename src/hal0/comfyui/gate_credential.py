"""The ComfyUI GPU gate's own credential (#3058).

The ``hal0_gpu_gate`` node inside the img container calls three hal0-api
routes before a prompt runs: ``GET /api/comfyui/arbiter``,
``POST /api/comfyui/prompt-handoff`` and ``POST /api/comfyui/switchover``,
and reports a prompt it refused to ``POST /api/comfyui/gate-refusal``.
They are ADMIN routes, so a box that enforces auth (``hal0 auth require on``),
or one bound to a specific address with an admin key set, refuses the gate
without a credential. The gate never gets the admin key. hal0 mints a token
for it instead, and :mod:`hal0.api.auth` admits that token on those four
requests and nothing else; the switch handler also holds it to a plain switch
into image mode.

The token lives in its own directory, ``<var_lib>/comfyui-gate/`` (0700, the
token file 0600, both owned by the service user), apart from every directory
the container mounts read-write. The img slot bind-mounts that directory
read-only and tells the gate where the file is; the gate reads it on every
request, and hal0-api reads it on every request that presents a token. The
directory is mounted rather than the file so a replaced token reaches a
running container.

NEVER log or echo the token.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
import secrets
import stat
import urllib.parse
from pathlib import Path

from hal0.config import paths
from hal0.security import nofollow

#: The token file's name inside :func:`token_dir`.
TOKEN_FILE_NAME = "token"

#: ``secrets.token_urlsafe(32)``: 43 characters of ``[A-Za-z0-9_-]``. A file
#: holding anything else is treated as no token and replaced on the next mint.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")

#: The service account that runs hal0-api and must be able to read the token.
_SERVICE_USER = "hal0"


def token_dir() -> Path:
    """The directory holding the gate token, mounted read-only into the img container."""
    return paths.var_lib() / "comfyui-gate"


def token_path() -> Path:
    """The gate token file."""
    return token_dir() / TOKEN_FILE_NAME


def _owner() -> tuple[int, int]:
    """Who owns the token directory and file.

    hal0-api writes them as itself. A root writer (the CLI building the img
    unit) hands them to the ``hal0`` service account so hal0-api can still
    read the token, or keeps them root's on a box without that account.
    """
    if os.geteuid() != 0:
        return os.geteuid(), os.getegid()
    import grp
    import pwd

    try:
        uid = pwd.getpwnam(_SERVICE_USER).pw_uid
    except KeyError:
        return 0, 0
    try:
        gid = grp.getgrnam(_SERVICE_USER).gr_gid
    except KeyError:
        gid = pwd.getpwnam(_SERVICE_USER).pw_gid
    return uid, gid


def read_token() -> str | None:
    """The current gate token, or ``None`` when there is none or it is unusable.

    The file is read without following a link (:mod:`hal0.security.nofollow`);
    a missing, unreadable or malformed file reads as no token.
    """
    try:
        text = nofollow.read_text(token_path())
    except (OSError, UnicodeDecodeError):
        return None
    if text is None:
        return None
    token = text.strip()
    return token if _TOKEN_RE.fullmatch(token) else None


def _private(owner: tuple[int, int]) -> bool:
    """True iff the token file is a regular file only ``owner`` can read."""
    try:
        st = os.lstat(token_path())
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not st.st_mode & 0o077 and st.st_uid == owner[0]


def ensure_token() -> str:
    """The gate token, minted and written first when there is no usable one.

    The directory is re-tightened on every call. A token others could have
    read (its mode widened, or another owner) is replaced, not kept.
    Raises :class:`OSError` when the directory or file cannot be written.
    """
    owner = _owner()
    # The state root exists on an installed box; a HAL0_HOME sandbox may not have it yet.
    paths.var_lib().mkdir(parents=True, exist_ok=True)
    nofollow.ensure_dir(token_dir(), mode=0o700, owner=owner, create=True, expect="adopt")
    current = read_token()
    if current is not None and _private(owner):
        return current
    token = secrets.token_urlsafe(32)
    nofollow.write_file(token_path(), token + "\n", mode=0o600, owner=owner, create_dirs=False)
    return token


def verify(candidate: str) -> bool:
    """True iff ``candidate`` is the current gate token (constant-time compare)."""
    token = read_token()
    if token is None or not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), token.encode("utf-8"))


def credential_present() -> bool:
    """True iff a usable gate token is on disk for the img container to mount."""
    return read_token() is not None


def names_an_address(url: str) -> bool:
    """True iff *url* names its host by IP address (or ``localhost``), not by a name.

    The gate speaks plain HTTP and resolves a name on every request, so it sends
    its token only to such a URL (``_direct_host`` in the ``hal0_gpu_gate``
    asset, which cannot import hal0: a test holds the two together). A token on
    disk is of no use to a gate that is given a host name.
    """
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    if host == "localhost":
        return True
    try:
        ipaddress.ip_address(host or "")
    except ValueError:
        return False
    return True


__all__ = [
    "TOKEN_FILE_NAME",
    "credential_present",
    "ensure_token",
    "names_an_address",
    "read_token",
    "token_dir",
    "token_path",
    "verify",
]
