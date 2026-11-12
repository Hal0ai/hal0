"""Client-side helpers for hal0's engine-compatible memory endpoint.

``/api/memory/compat`` (:mod:`hal0.api.routes.memory_compat`, ADR-0058
decision 4b) is where hal0 points a Hindsight coding-agents client: the Pi
driver (``harnesses.pi`` in ``~/.hindsight/coding-agent.json``) and
``hal0 memory agents install`` (``harnesses.<harness>``). Both write the
address from :func:`compat_url` and write the shared file through
:func:`write_client_config`.

Whether a stored key is still good is decided against the bridge store, never
from an HTTP answer: hal0-api also answers 401 for a valid key when it cannot
read ``bridge.db``, or when ``[bridge] enabled`` was switched on after it
started. :func:`compat_accepts` is therefore advisory: ``False`` may only stop
a caller from writing a key it was handed, never make one replace a key.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from pathlib import Path
from typing import Any

#: The endpoint's prefix on hal0-api.
COMPAT_PATH = "/api/memory/compat"


def compat_url(api_base: str) -> str:
    """The ``apiUrl`` a client uses: hal0-api's origin plus :data:`COMPAT_PATH`."""
    return f"{api_base.rstrip('/')}{COMPAT_PATH}"


def compat_accepts(url: str, token: str, *, timeout: float = 5.0) -> bool | None:
    """Whether the endpoint at ``url`` accepts ``token`` as a memory client.

    ``True`` on a 200 from ``<url>/health`` (the route is BRIDGE class: it
    needs a bridge key with the ``memory`` scope, or the admin credential),
    ``False`` on 401 or 403, ``None`` when that cannot be told (hal0-api or
    the engine down, any other answer). A 401 is not proof the key is dead
    (see the module docstring), so no caller rotates a key on ``False``.
    """
    import httpx

    try:
        resp = httpx.get(
            f"{url}/health", headers={"Authorization": f"Bearer {token}"}, timeout=timeout
        )
    except httpx.HTTPError:
        return None
    if resp.status_code == 200:
        return True
    if resp.status_code in (401, 403):
        return False
    return None


class UnsafeConfigPath(OSError):
    """The client config, or its temporary file, is not one this process may
    write: it resolves outside the home directory, is not a regular file, or
    belongs to someone other than this user or the home's owner."""


def _allowed_owners(home: Path) -> set[int]:
    owners = {os.geteuid()}
    with contextlib.suppress(OSError):
        owners.add(os.stat(home).st_uid)
    return owners


def checked_config_path(path: Path, home: Path) -> Path:
    """``path`` resolved, refused (:class:`UnsafeConfigPath`) when unsafe to write.

    The resolved file must lie under ``home`` (a symlink into a dotfiles
    checkout in the home is fine; one leaving it is not) and, when it exists,
    be a regular file owned by this process's euid or by the home's owner. A
    root process must not be steered into rewriting a file elsewhere.
    """
    home_r = home.resolve()
    target = path.resolve()
    if not target.is_relative_to(home_r):
        raise UnsafeConfigPath(f"{path} resolves to {target}, outside {home_r}; not written")
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return target
    if not stat.S_ISREG(st.st_mode):
        raise UnsafeConfigPath(f"{target} is not a regular file; not written")
    if st.st_uid not in _allowed_owners(home):
        raise UnsafeConfigPath(
            f"{target} belongs to uid {st.st_uid}, not this user or the home's owner; not written"
        )
    return target


def write_client_config(
    path: Path, data: dict[str, Any], *, home: Path, max_mode: int | None = None
) -> None:
    """Write the shared client config atomically. Raises ``OSError``.

    The target is :func:`checked_config_path`; a symlink is written through
    (the link stays). The temporary file beside it is created with
    ``O_CREAT | O_EXCL | O_NOFOLLOW`` at 0600, after removing a stale one only
    when it is a regular file of an allowed owner. An existing file keeps its
    owner and mode (narrowed to ``max_mode`` when given, never widened); a new
    one is 0600. Keys keep their order: the file is shared and is not reordered.
    """
    target = checked_config_path(path, home)
    try:
        st: os.stat_result | None = os.stat(target)
    except FileNotFoundError:
        st = None
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    try:
        tst = os.lstat(tmp)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(tst.st_mode) or tst.st_uid not in _allowed_owners(home):
            raise UnsafeConfigPath(f"{tmp} is in the way and is not ours to remove; not written")
        os.unlink(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
        if st is None:
            os.fchmod(fd, 0o600)
        else:
            mode = stat.S_IMODE(st.st_mode)
            os.fchmod(fd, mode & max_mode if max_mode is not None else mode)
            # Run as root over another user's file: hand it back to its owner.
            with contextlib.suppress(PermissionError):
                os.fchown(fd, st.st_uid, st.st_gid)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    os.close(fd)
    os.replace(tmp, target)


def read_client_config(path: Path, home: Path) -> dict[str, Any] | None:
    """The client config at ``path`` as an object, or ``None`` when it is
    missing, unreadable, not a JSON object, or fails :func:`checked_config_path`."""
    try:
        target = checked_config_path(path, home)
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
