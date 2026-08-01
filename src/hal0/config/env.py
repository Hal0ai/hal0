"""Atomic environment file writer.

write_env_atomic() is the only correct way to write slot .env files and
the openwebui.env file in hal0.  It uses a tmpfile in the same directory
as the target, then os.replace() for an atomic POSIX rename.  If the
process dies mid-write, the prior file is left intact.

This is the Tier 1 fix for the non-atomic env write bug identified in
haloai lib/slots.py:551-622.  See PLAN.md §5 Tier 1.

Usage::

    from hal0.config.env import write_env_atomic

    write_env_atomic(
        hal0.config.paths.slot_data_dir("primary") / "slot.env",
        {"HAL0_MODEL_PATH": "/var/lib/hal0/models/...", "HAL0_PORT": "8081"},
    )
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

_QUOTE_CHARS = frozenset(" \t\n\r#$\"'\\")


def _quote_value(value: str) -> str:
    """Quote a value if it contains shell-special characters.

    Uses double-quotes and escapes embedded double-quotes and backslashes.
    systemd EnvironmentFile syntax is followed: values with spaces must
    be double-quoted; systemd strips the outer quotes.
    """
    if not value or any(c in _QUOTE_CHARS for c in value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


#: Header for slot env files — regenerated wholesale on every slot load, so
#: "do not edit" is literally true for them.
_SLOT_HEADER: tuple[str, ...] = (
    "# hal0 slot environment — written by hal0.config.env.write_env_atomic",
    "# Do not edit manually; changes will be overwritten on next slot load.",
)


def write_env_atomic(
    path: Path | str,
    env_dict: dict[str, str],
    header: tuple[str, ...] | list[str] | None = None,
) -> None:
    """Write an environment file atomically.

    Writes the key=value pairs in env_dict to a temporary file in the same
    directory as *path*, then renames it into place via os.replace().  The
    rename is atomic on POSIX filesystems when src and dst are on the same
    mount; because the tmpfile is created in the same directory, this
    constraint is always satisfied.

    If the function raises (e.g. disk full), the original file at *path*
    is left untouched.  The orphaned tmpfile (if any) is cleaned up in the
    finally block.

    Args:
        path:     Destination path for the env file.
        env_dict: Mapping of variable names to string values.  Keys are
                  written in sorted order for deterministic diffs.
                  Values are quoted if they contain shell-special characters.
        header:   Comment lines written above the values.  Defaults to the
                  slot-env header.  ``openwebui.env`` passes its own,
                  because since #1514 that file is merged rather than
                  regenerated — telling its operator that edits "will be
                  overwritten on next slot load" was wrong on both counts.

    Raises:
        OSError: If the directory doesn't exist, or disk full, or
                 the rename fails for a filesystem reason.
        TypeError: If path is not str or Path, or values in env_dict are
                   not strings.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [*(header if header is not None else _SLOT_HEADER), ""]
    for key in sorted(env_dict.keys()):
        value = env_dict[key]
        if not isinstance(value, str):
            raise TypeError(f"env value for {key!r} must be str, got {type(value).__name__}")
        lines.append(f"{key}={_quote_value(value)}")
    lines.append("")  # trailing newline

    content = "\n".join(lines)

    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(
            prefix=".hal0-env-",
            suffix=".tmp",
            dir=path.parent,
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            # fdopen took ownership; close the raw fd only if fdopen failed
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, path)
        tmp_path = None  # rename succeeded; don't clean up in finally
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
