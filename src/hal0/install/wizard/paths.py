"""The checks on a typed path: the model folder and the file a copy is saved to.

Standard library only, so every flow module can use it. The folder check looks
at the filesystem; the rest reads only the text.
"""

from __future__ import annotations

import json
import os
import posixpath
import stat

_COPY_MAX_BYTES = 2**20  # an answers document is far smaller than this


def _clean_path(text: str, what: str, *, percent: bool = False) -> tuple[str, str | None]:
    """The trimmed, normalised absolute path and the reason it cannot be used, or None."""
    path = text.strip()
    if not path:
        return path, f"{what} is required"
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return path, f"{what} has a control character"
    if percent and "%" in path:
        return path, f"{what} must not contain %"
    if not path.startswith("/"):
        return path, f"{what} must start with /"
    return posixpath.normpath(path), None


def validate_folder(text: str) -> tuple[str, str | None]:
    """The cleaned folder path and the reason it cannot be used, or None."""
    path, error = _clean_path(text, "the folder", percent=True)
    if error is not None:
        return path, error
    ancestor = path
    while not os.path.exists(ancestor) and ancestor != "/":
        ancestor = posixpath.dirname(ancestor)
    if not os.path.isdir(ancestor):
        return path, f"{ancestor} is not a folder"
    return path, None


def _is_answers_document(path: str) -> bool:
    """Whether a regular file loads as an answers document; never raises."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY)
    except OSError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(_COPY_MAX_BYTES + 1)
        if len(raw) > _COPY_MAX_BYTES:
            return False
        data = json.loads(raw.decode("utf-8"))
        meta = data.get("meta") if isinstance(data, dict) else None
        return "version" in data and isinstance(meta, dict) and "saved_by" in meta
    except Exception:
        return False
    finally:
        os.close(fd)


def check_copy(text: str, confirmed: object) -> tuple[str, str | None]:
    """The path a copy of the answers may be saved to.

    An existing file is replaced only when it is the copy confirmed earlier in
    this run or an earlier answers document; anything else is left alone.
    """
    path, error = _clean_path(text, "the file path")
    if error is not None:
        return path, error
    if os.path.islink(path):
        return path, f"{path} is a symlink"
    if os.path.isdir(path):
        return path, f"{path} is a folder"
    if not os.path.isdir(posixpath.dirname(path)):
        return path, "the folder for this file does not exist"
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return path, None
    except OSError:
        mode = 0
    if stat.S_ISREG(mode) and (path == confirmed or _is_answers_document(path)):
        return path, None
    return path, f"{path} already exists; choose another name"
