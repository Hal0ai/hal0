"""File operations under the decision model's hal0-owned directories (#3757).

Every write the ``memory-decision`` component makes under
``/var/lib/hal0/memory/hindsight-decision`` or the engine's Hugging Face
cache goes through here. The directories belong to ``hal0``, so a process
running as root must never write into them itself: a compromised ``hal0``
account could plant a symlink and have root write through it. A root caller
therefore runs these operations in a child that has dropped to ``hal0``
(``python -m hal0.memory.decision_fs <op> ...``, see
:func:`hal0.memory.decision_component._fs`); any other caller runs them
in-process. Either way the operations themselves refuse symlinks:

* ``write``: the body goes to a fresh ``O_CREAT|O_EXCL|O_NOFOLLOW`` temp name
  in the same directory, then ``os.replace`` onto the target, which replaces a
  symlink at the target rather than writing through it. A symlinked parent
  directory is refused.
* ``rename`` / ``rmtree`` act on the link itself, never its target.
"""

from __future__ import annotations

import os
import secrets
import shutil
import sys
from pathlib import Path


class UnsafePath(OSError):
    """A path component the operation refuses to follow."""


def _check_parent(path: Path) -> None:
    parent = path.parent
    if parent.is_symlink():
        raise UnsafePath(f"refusing a symlinked directory: {parent}")
    if not parent.is_dir():
        raise UnsafePath(f"not a directory: {parent}")


def write_bytes(path: Path, body: bytes, mode: int = 0o644) -> None:
    """Atomically replace ``path`` with ``body`` without following any symlink."""
    path = Path(path)
    _check_parent(path)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        os.chmod(tmp, mode, follow_symlinks=False)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_bytes(path: Path) -> bytes:
    """Read ``path``, refusing a symlink at the final component."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        return f.read()


def mkdir(path: Path) -> None:
    path = Path(path)
    if path.is_symlink():
        raise UnsafePath(f"refusing a symlinked directory: {path}")
    path.mkdir(parents=True, exist_ok=True)


def rename(src: Path, dst: Path) -> None:
    _check_parent(Path(dst))
    os.rename(src, dst)


def rmtree(path: Path) -> None:
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def copy(src: Path, dst: Path) -> None:
    write_bytes(Path(dst), read_bytes(Path(src)))


def retarget(built: Path, final: Path) -> None:
    """Rewrite the build-aside path to the final one in ``bin/`` launchers."""
    old, new = str(built), str(final)
    bin_dir = Path(built) / "bin"
    if bin_dir.is_symlink():
        raise UnsafePath(f"refusing a symlinked directory: {bin_dir}")
    for script in bin_dir.iterdir():
        if script.is_symlink() or not script.is_file():
            continue
        try:
            text = read_bytes(script).decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if old in text:
            mode = script.stat(follow_symlinks=False).st_mode & 0o7777
            write_bytes(script, text.replace(old, new).encode("utf-8"), mode)


def main(argv: list[str]) -> int:
    op, *args = argv
    if op == "write":
        write_bytes(Path(args[0]), sys.stdin.buffer.read(), int(args[1], 8))
    elif op == "mkdir":
        mkdir(Path(args[0]))
    elif op == "rename":
        rename(Path(args[0]), Path(args[1]))
    elif op == "rmtree":
        rmtree(Path(args[0]))
    elif op == "copy":
        copy(Path(args[0]), Path(args[1]))
    elif op == "retarget":
        retarget(Path(args[0]), Path(args[1]))
    else:
        print(f"decision_fs: unknown op {op!r}", file=sys.stderr)
        return 64
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the root path
    try:
        sys.exit(main(sys.argv[1:]))
    except OSError as exc:
        print(f"decision_fs: {exc}", file=sys.stderr)
        sys.exit(1)
