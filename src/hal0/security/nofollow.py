"""Privileged filesystem writes that never follow a link the service account planted (#2975).

``/var/lib/hal0`` and ``/etc/hal0`` are ``hal0``-owned ``2775`` with no sticky
bit, so the ``hal0`` account can rename anything directly inside them
(``.hermes``, ``secrets/``, ``agents/``) and put a symlink, or a tree it owns,
in its place. A root writer that resolves its target by pathname then writes,
creates, chowns or chmods inside whatever the link names.

Every function here resolves its directory from ``/`` one component at a
time, relative to the previous directory's descriptor. A component is opened
``O_DIRECTORY|O_NOFOLLOW`` unless the directory that holds it is trusted:
owned by a trusted uid (:func:`_trusted_uids`) and writable by nobody else.
Only a trusted directory's entries can be followed, because nobody else can
have put a link there. Below the first untrusted directory, a symlink anywhere
on the way is refused with :class:`UnsafePathError`, and every later step
(``mkdir``, the temp file, the rename, ``fchown``/``fchmod``) acts on the
descriptor or relative to it, never on a pathname.

``require_trusted_dir`` additionally demands that the final directory itself
be trusted, for root-only targets such as ``secrets/agents/``: a real
directory the service account owns is refused just like a link.
"""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
from collections.abc import Iterator
from pathlib import Path

__all__ = [
    "UnsafePathError",
    "chmod",
    "chown",
    "chown_tree",
    "open_dir",
    "read_text",
    "write_file",
]

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC


class UnsafePathError(OSError):
    """A component on the way to a privileged write is a symlink or untrusted."""

    def __init__(self, path: Path | str, reason: str) -> None:
        super().__init__(errno.ELOOP, f"refusing {path}: {reason}")
        self.path = str(path)
        self.reason = reason


def _trusted_uids() -> frozenset[int]:
    """Owners whose directories may be followed: root, or whoever runs this."""
    return frozenset({0, os.geteuid()})


def _is_trusted(st: os.stat_result) -> bool:
    return st.st_uid in _trusted_uids() and not st.st_mode & 0o022


def _components(path: Path | str) -> tuple[str, ...]:
    # abspath normalises ``..`` lexically; nothing is resolved through a link.
    return Path(os.path.abspath(path)).parts[1:]


def open_dir(
    path: Path | str,
    *,
    create: bool = False,
    mkdir_mode: int | None = None,
    require_trusted_dir: bool = False,
) -> int:
    """Descriptor of directory ``path``, walked from ``/`` as the module describes.

    ``create`` makes missing components with ``mkdir`` relative to their
    parent's descriptor. A created component gets exactly ``mkdir_mode``
    (``fchmod``, so the umask cannot narrow it) when given, else the umask
    default; an existing one is never re-moded. The caller closes the
    descriptor. Raises :class:`UnsafePathError` for a symlinked or, with
    ``require_trusted_dir``, untrusted directory, and ``OSError`` otherwise.
    """
    fd = os.open("/", _DIR_FLAGS)
    try:
        walked = Path("/")
        for name in _components(path):
            walked = walked / name
            follow = _is_trusted(os.fstat(fd))
            flags = _DIR_FLAGS if follow else _DIR_FLAGS | os.O_NOFOLLOW
            created = False
            try:
                nfd = os.open(name, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, 0o700 if mkdir_mode is not None else 0o777, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
                nfd = _open_child(fd, name, walked, _DIR_FLAGS | os.O_NOFOLLOW)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UnsafePathError(walked, "a symlink or not a directory") from exc
                raise
            os.close(fd)
            fd = nfd
            if created and mkdir_mode is not None:
                os.fchmod(fd, mkdir_mode)
        if require_trusted_dir and not _is_trusted(os.fstat(fd)):
            raise UnsafePathError(path, "directory is not owned by root alone")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_child(dir_fd: int, name: str, path: Path, flags: int) -> int:
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafePathError(path, "a symlink or not a directory") from exc
        raise


@contextlib.contextmanager
def _parent(path: Path, **kwargs: object) -> Iterator[int]:
    fd = open_dir(path.parent, **kwargs)  # type: ignore[arg-type]
    try:
        yield fd
    finally:
        os.close(fd)


def read_text(path: Path | str, *, require_trusted_dir: bool = False) -> str | None:
    """Text of regular file ``path``, or ``None`` when it is absent.

    The file is opened ``O_NOFOLLOW|O_NONBLOCK`` relative to its directory, so
    a planted symlink (or FIFO) is refused instead of read as root. Raises
    :class:`UnsafePathError` for a link or a non-regular file.
    """
    path = Path(path)
    try:
        with _parent(path, require_trusted_dir=require_trusted_dir) as dfd:
            try:
                fd = os.open(path.name, _FILE_READ_FLAGS, dir_fd=dfd)
            except FileNotFoundError:
                return None
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise UnsafePathError(path, "a symlink") from exc
                raise
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise UnsafePathError(path, "not a regular file")
        return fh.read().decode("utf-8")


def write_file(
    path: Path | str,
    content: str | bytes,
    *,
    mode: int | None = None,
    owner: tuple[int, int] | None = None,
    create_dirs: bool = True,
    mkdir_mode: int | None = None,
    require_trusted_dir: bool = False,
) -> None:
    """Atomically replace ``path`` with ``content`` without following any planted link.

    The temp file gets a random name created ``O_CREAT|O_EXCL|O_NOFOLLOW``
    relative to the directory descriptor, is written, ``fsync``-ed, given its
    mode and owner with ``fchmod``/``fchown``, and renamed over ``path``
    through the same descriptor. The mode is set before a byte is written.

    ``mode`` defaults to the replaced file's mode, else the umask default.
    ``owner`` defaults, for a root caller, to the replaced file's owner, so a
    root repair does not leave a ``hal0`` file root-owned, and failing to keep
    it fails the write. A pinned ``owner`` (``(0, 0)`` for a root-only file)
    is best-effort, as the pathname ``chown`` it replaces was: a root caller's
    new file is already root's.
    The replaced file is looked at with ``lstat``: a symlink there donates
    nothing and is replaced as a link, never written through.
    """
    path = Path(path)
    data = content.encode("utf-8") if isinstance(content, str) else content
    with _parent(
        path,
        create=create_dirs,
        mkdir_mode=mkdir_mode,
        require_trusted_dir=require_trusted_dir,
    ) as dfd:
        pinned = owner is not None
        with contextlib.suppress(FileNotFoundError):
            st = os.stat(path.name, dir_fd=dfd, follow_symlinks=False)
            if stat.S_ISREG(st.st_mode):
                if mode is None:
                    mode = stat.S_IMODE(st.st_mode)
                if owner is None and os.geteuid() == 0:
                    owner = (st.st_uid, st.st_gid)
        tmp = f".{path.name}.{secrets.token_hex(8)}"
        # 0o600 until fchmod below; 0o666 masked by the umask when no mode is known.
        initial = 0o600 if mode is not None else 0o666
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            initial,
            dir_fd=dfd,
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                if owner is not None and os.geteuid() == 0:
                    with contextlib.suppress(*((PermissionError,) if pinned else ())):
                        os.fchown(fh.fileno(), *owner)
                if mode is not None:
                    os.fchmod(fh.fileno(), mode)
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=dfd)
            raise


def _lstat_not_link(dfd: int, path: Path) -> os.stat_result:
    st = os.stat(path.name, dir_fd=dfd, follow_symlinks=False)
    if stat.S_ISLNK(st.st_mode):
        raise UnsafePathError(path, "a symlink")
    return st


def chown(path: Path | str, uid: int, gid: int) -> None:
    """Re-own ``path`` itself; a symlink on the way or at the end is refused.

    ``fchownat(AT_SYMLINK_NOFOLLOW)`` relative to the directory descriptor: a
    link swapped in after the check is re-owned itself, never its target.
    """
    path = Path(path)
    with _parent(path) as dfd:
        _lstat_not_link(dfd, path)
        os.chown(path.name, uid, gid, dir_fd=dfd, follow_symlinks=False)


def chmod(path: Path | str, mode: int) -> None:
    """``fchmod`` ``path`` itself; a symlink on the way or at the end is refused.

    Linux has no ``lchmod``, so the mode goes through a descriptor opened
    ``O_NOFOLLOW``: the entry itself for a file or directory, an ``O_PATH``
    handle (changed through ``/proc/self/fd``) for anything else.
    """
    path = Path(path)
    with _parent(path) as dfd:
        st = _lstat_not_link(dfd, path)
        if stat.S_ISDIR(st.st_mode):
            flags = _DIR_FLAGS | os.O_NOFOLLOW
        elif stat.S_ISREG(st.st_mode):
            flags = _FILE_READ_FLAGS
        elif hasattr(os, "O_PATH"):
            flags = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
        else:
            raise UnsafePathError(path, "not a regular file or directory")
        try:
            fd = os.open(path.name, flags, dir_fd=dfd)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise UnsafePathError(path, "replaced while it was opened") from exc
            raise
        try:
            if flags & getattr(os, "O_PATH", 0):
                if stat.S_ISLNK(os.fstat(fd).st_mode):
                    raise UnsafePathError(path, "replaced by a symlink")
                os.chmod(f"/proc/self/fd/{fd}", mode)
            else:
                os.fchmod(fd, mode)
        finally:
            os.close(fd)


def chown_tree(root: Path | str, uid: int, gid: int) -> None:
    """``chown -R`` that never leaves the tree: links are re-owned, never followed.

    ``os.fwalk`` does not descend through a symlinked directory, and every
    entry is re-owned with ``fchownat(AT_SYMLINK_NOFOLLOW)`` relative to the
    descriptor of the directory that holds it.
    """
    root = Path(root)
    with _parent(root) as dfd:
        os.chown(root.name, uid, gid, dir_fd=dfd, follow_symlinks=False)
        top = _open_child(dfd, root.name, root, _DIR_FLAGS | os.O_NOFOLLOW)
    try:
        for _dirpath, dirnames, filenames, dirfd in os.fwalk(".", dir_fd=top):
            for name in (*dirnames, *filenames):
                with contextlib.suppress(FileNotFoundError):
                    os.chown(name, uid, gid, dir_fd=dirfd, follow_symlinks=False)
    finally:
        os.close(top)
