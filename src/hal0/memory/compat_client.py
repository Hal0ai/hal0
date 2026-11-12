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

import base64
import contextlib
import json
import os
import stat
from collections.abc import Callable
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


def is_managed_section(section: dict[str, Any], client: str) -> bool:
    """Whether hal0 may rewrite this ``harnesses.<harness>`` section: its
    ``apiUrl`` is absent or hal0's endpoint, and its ``apiToken`` is absent or
    a key of bridge client ``client`` (:func:`hal0.bridge.keys.name_of`).
    Anything else was set by someone else and is left alone."""
    from hal0.bridge.keys import name_of

    url = section.get("apiUrl")
    token = section.get("apiToken")
    return (
        url in (None, "") or (isinstance(url, str) and url.rstrip("/").endswith(COMPAT_PATH))
    ) and (token in (None, "") or (isinstance(token, str) and name_of(token) == client))


class UnsafeConfigPath(OSError):
    """The client config, or its temporary file, is not one this process may
    write: it resolves outside the home directory, is not a regular file,
    belongs to someone other than this user or the home's owner, or (as root)
    sits in a root-owned home under a directory someone else controls."""


def _allowed_owners(home: Path) -> set[int]:
    owners = {os.geteuid()}
    with contextlib.suppress(OSError):
        owners.add(os.stat(home).st_uid)
    return owners


def checked_config_path(path: Path, home: Path) -> Path:
    """``path`` resolved, refused (:class:`UnsafeConfigPath`) when unsafe to write.

    The resolved file must lie under ``home`` (a symlink into a dotfiles
    checkout in the home is fine; one leaving it is not) and, when it exists,
    be a regular file owned by this process's euid or by the home's owner.
    This check alone does not stop a home's owner from swapping a directory
    for a symlink after it ran, so a root process never acts on its answer in
    a home it does not own: :func:`write_client_config` and
    :func:`read_client_config` do that work as the home's owner instead.
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


# ── root in another user's home ────────────────────────────────────────────
#
# A root process that writes into a directory tree another user controls can
# be raced: the owner swaps a directory in the path for a symlink between the
# check and the write, and O_NOFOLLOW covers only the last component. So root
# never does. In a home owned by someone else, the read or write runs in a
# forked child that has dropped to the home owner's uid and gid: whatever
# that user swaps in, the child can reach only what the user could anyway,
# and the files it creates are the user's. In a root-owned home root works
# directly, and only when every directory from the home down to the file is
# root's and writable by nobody else.


def _is_root() -> bool:
    """Module-level seam for tests (they cannot run as root)."""
    return os.geteuid() == 0


def _home_owner(home: Path) -> tuple[int, int]:
    st = os.stat(home)
    return st.st_uid, st.st_gid


def _run_as_owner(uid: int, gid: int, fn: Callable[[], bytes]) -> bytes:
    """``fn()`` run in a forked child dropped to ``uid``/``gid``; its bytes.

    The child sheds root's supplementary groups, then the gid, then the uid,
    runs ``fn`` and exits; the parent reads the answer from a pipe and waits.
    An ``OSError`` in the child is raised here with the same errno (as
    :class:`UnsafeConfigPath` when it was one); anything else as ``OSError``.
    Nothing is imported in the child: another thread may hold the import lock.
    Module-level seam for tests."""
    rfd, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - the child's coverage is not collected
        code = 1
        try:
            os.close(rfd)
            try:
                if os.geteuid() == 0:
                    os.setgroups([])
                os.setgid(gid)
                os.setuid(uid)
                out = {"ok": True, "data": base64.b64encode(fn()).decode("ascii")}
            except BaseException as exc:
                out = {
                    "ok": False,
                    "errno": getattr(exc, "errno", None),
                    "unsafe": isinstance(exc, UnsafeConfigPath),
                    "msg": str(exc) or type(exc).__name__,
                }
            payload = json.dumps(out).encode("utf-8")
            while payload:
                payload = payload[os.write(wfd, payload) :]
            code = 0
        finally:
            os._exit(code)
    os.close(wfd)
    chunks: list[bytes] = []
    try:
        while chunk := os.read(rfd, 65536):
            chunks.append(chunk)
    finally:
        os.close(rfd)
        _pid, status = os.waitpid(pid, 0)
    try:
        answer = json.loads(b"".join(chunks) or b"{}")
    except ValueError:
        answer = {}
    if os.waitstatus_to_exitcode(status) != 0 or not isinstance(answer, dict) or not answer:
        raise OSError(f"the write as uid {uid} did not finish")
    if not answer.get("ok"):
        msg = str(answer.get("msg") or "failed")
        if answer.get("unsafe"):
            raise UnsafeConfigPath(msg)
        err = answer.get("errno")
        raise OSError(err, msg) if isinstance(err, int) else OSError(msg)
    return base64.b64decode(answer.get("data") or "")


def _require_root_tree(home: Path, directory: Path) -> None:
    """Refuse (:class:`UnsafeConfigPath`) unless every existing directory from
    ``home`` down to ``directory`` is root's, not a symlink, and writable by
    nobody else. Both are resolved and ``directory`` lies under ``home``."""
    parts = directory.relative_to(home).parts
    current = home
    for part in (None, *parts):
        if part is not None:
            current = current / part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            return
        if (
            not stat.S_ISDIR(st.st_mode)
            or st.st_uid != 0
            or stat.S_IMODE(st.st_mode) & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise UnsafeConfigPath(
                f"{current} is not a root-owned directory only root can write; root does "
                "not write into a directory another user controls. Not written"
            )


def _as_home_owner(home: Path, path: Path, fn: Callable[[], bytes]) -> bytes:
    """``fn()`` where it is safe for this process to touch ``path``: directly
    unless this is root in a home owned by someone else, where it runs as that
    owner (:func:`_run_as_owner`). Root in a root-owned home also needs a
    root-only tree (:func:`_require_root_tree`)."""
    if not _is_root():
        return fn()
    uid, gid = _home_owner(home)
    if uid != 0:
        return _run_as_owner(uid, gid, fn)
    target = checked_config_path(path, home)
    _require_root_tree(home.resolve(), target.parent)
    return fn()


def _write_here(path: Path, home: Path, body: bytes, max_mode: int | None) -> bytes:
    """The write itself, as whoever this process is. See :func:`write_client_config`."""
    target = checked_config_path(path, home)
    try:
        st: os.stat_result | None = os.stat(target)
    except FileNotFoundError:
        st = None
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    try:
        tst = os.lstat(tmp)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(tst.st_mode) or tst.st_uid not in _allowed_owners(home):
            raise UnsafeConfigPath(f"{tmp} is in the way and is not ours to remove; not written")
        os.unlink(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        view = memoryview(body)
        while view:
            view = view[os.write(fd, view) :]
        if st is None:
            os.fchmod(fd, 0o600)
        else:
            mode = stat.S_IMODE(st.st_mode)
            os.fchmod(fd, mode & max_mode if max_mode is not None else mode)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    os.close(fd)
    os.replace(tmp, target)
    with contextlib.suppress(OSError):
        dfd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    return b""


def write_client_config(
    path: Path, data: dict[str, Any], *, home: Path, max_mode: int | None = None
) -> None:
    """Write the shared client config atomically. Raises ``OSError``.

    The target is :func:`checked_config_path`; a symlink is written through
    (the link stays). The temporary file beside it is created with
    ``O_CREAT | O_EXCL | O_NOFOLLOW`` at 0600 and fsynced before the rename,
    after removing a stale one only when it is a regular file of an allowed
    owner. An existing file keeps its mode (narrowed to ``max_mode`` when
    given, never widened); a new one is 0600.

    Root writes only in a home it owns, with a root-only directory tree; in
    anyone else's home the whole write runs as the home's owner
    (:func:`_as_home_owner`), so a new file is theirs. Keys keep their order:
    the file is shared and is not reordered.
    """
    body = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    _as_home_owner(home, path, lambda: _write_here(path, home, body, max_mode))


def _read_here(path: Path, home: Path) -> bytes:
    target = checked_config_path(path, home)
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UnsafeConfigPath(f"{target} is not a regular file; not read")
        chunks: list[bytes] = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)


def read_client_config(path: Path, home: Path) -> dict[str, Any] | None:
    """The client config at ``path`` as an object, or ``None`` when it is
    missing, unreadable, not a JSON object, or fails :func:`checked_config_path`.
    Read as the home's owner when root is in someone else's home."""
    try:
        raw = _as_home_owner(home, path, lambda: _read_here(path, home))
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
