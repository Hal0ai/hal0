"""Atomic writer for ``openwebui.env``.

write_env_atomic() is the one way hal0 writes an environment file.  It uses a
tmpfile in the same directory as the target, then os.replace() for an atomic
POSIX rename.  If the process dies mid-write, the prior file is left intact.
Its only production caller is ``hal0.openwebui.env_writer``.

This is the Tier 1 fix for the non-atomic env write bug identified in
haloai lib/slots.py:551-622.  See PLAN.md §5 Tier 1.

File format.  ``hal0-openwebui.service`` hands the file to ``podman run
--env-file``, so the format is podman's: one ``KEY=value`` per line, the value
written byte for byte.  podman keeps quotes and backslashes literally and
reads a ``#`` only at the start of a line, so a value is never quoted or
escaped here; quoting it for systemd (as this module once did) put the quote
characters into the container's environment (#3045).  A value that holds a
line break or NUL cannot be one line of such a file and is refused.

systemd reads the same file through ``EnvironmentFile=``, but only to expand
``HAL0_OWUI_BIND_HOST`` into ``podman run -p``.  Its parser is not line
oriented: an opening quote stays open across newlines and a trailing ``\\``
continues the value onto the next line.  A verbatim value that sorts ahead of
the bind host and holds either could swallow that line, and the unit's
``0.0.0.0`` default would then publish Open WebUI on the LAN of a box set to
loopback.  Two things close that, and neither is enough alone:

* The bind host is written as the first value line, ahead of every value an
  operator can influence, and its value must be a plain IP address or
  hostname, a string both parsers read identically.
* systemd takes ``\\r`` as a line break and podman does not, and when systemd
  sees a name twice the later assignment wins.  A name or value holding ``\\r``
  would let the text after it read to systemd as a second
  ``HAL0_OWUI_BIND_HOST=`` below the first, while podman still sees one
  variable.  So a value with a line break or NUL, and a name that is empty or
  holds a line break, NUL or ``=``, are refused.

The first-line placement and the host check apply only to a dict that has the
bind host: a file without it carries nothing to hide, and the unit's own
default stands.

Usage::

    from hal0.config.env import write_env_atomic

    write_env_atomic(
        hal0.config.paths.openwebui_env(),
        {"HAL0_OWUI_BIND_HOST": "127.0.0.1", "WEBUI_NAME": "hal0"},
    )
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import re
import tempfile
from pathlib import Path

#: The one variable systemd reads from ``openwebui.env``: the address the unit
#: publishes Open WebUI on.  Mirrored by ``hal0.openwebui.env_writer``, which
#: cannot import this module's package without its init (see that module).
BIND_HOST_KEY = "HAL0_OWUI_BIND_HOST"

_IP_CHARS = re.compile(r"[0-9A-Fa-f:.]+")
_HOSTNAME_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _is_plain_host(value: str) -> bool:
    """Whether *value* is an IP address or hostname with nothing else in it.

    No whitespace, quote, backslash, ``#``, ``;``, ``%`` (so no IPv6 zone id),
    bracket or port: nothing either parser could read differently, and
    nothing that is not an address the unit can hand to ``podman -p``.
    """
    if _IP_CHARS.fullmatch(value):
        try:
            ipaddress.ip_address(value)
        except ValueError:
            pass
        else:
            return True
    if len(value) > 253:
        return False
    labels = value.split(".")
    # A name whose last label is all digits is a malformed IPv4 address.
    return all(_HOSTNAME_LABEL.fullmatch(label) for label in labels) and not labels[-1].isdigit()


#: Default header for a file regenerated wholesale on every write, so "do not
#: edit" is literally true for it.  ``openwebui.env`` passes its own, because
#: since #1514 it is merged rather than regenerated.
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
                  written in sorted order for deterministic diffs, except
                  ``HAL0_OWUI_BIND_HOST``, which comes first.  Values are
                  written verbatim, in podman's ``--env-file`` format.
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
        ValueError: If a value contains a line break or NUL, a key is empty or
                    contains a line break, NUL or ``=``, or the value of
                    ``HAL0_OWUI_BIND_HOST`` is not a plain IP address or
                    hostname.  Raised while the lines are built, before the
                    tmpfile exists, so the file at *path* is left untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [*(header if header is not None else _SLOT_HEADER), ""]
    # The bind host goes first: systemd's parser can lose a line that follows a
    # value with an unclosed quote or a trailing backslash (module docstring).
    keys = sorted(env_dict, key=lambda k: (k != BIND_HOST_KEY, k))
    for key in keys:
        value = env_dict[key]
        if not isinstance(value, str):
            raise TypeError(f"env value for {key!r} must be str, got {type(value).__name__}")
        if not key or any(c in key for c in "\n\r\0="):
            raise ValueError(f"env key {key!r} cannot be empty or contain a line break, NUL or '='")
        if any(c in value for c in "\n\r\0"):
            raise ValueError(f"env value for {key!r} cannot contain a line break or NUL")
        if key == BIND_HOST_KEY and not _is_plain_host(value):
            raise ValueError(f"{key} must be a plain IP address or hostname, got {value!r}")
        lines.append(f"{key}={value}")
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
