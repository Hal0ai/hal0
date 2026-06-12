"""Atomic ``api.env`` secret store shared by the providers + secrets routers.

``/etc/hal0/api.env`` is the systemd ``EnvironmentFile=`` for ``hal0-api``;
it holds provider credentials (``POST /api/providers/{name}/credentials``)
and operator-managed secrets (``/api/secrets``). Both surfaces need the
same atomic, mode-0600 upsert/delete posture so a partial write never
leaves a reader (systemd, the running registry) staring at a truncated
file.

This module factors that writer out of :mod:`hal0.api.routes.providers`
(where it lived first) so the secrets router doesn't duplicate the
tmp-file + ``os.replace`` dance. Every path here is given by the caller
(``hal0.config.paths.etc() / "api.env"`` in production, a
``HAL0_HOME``-relative path under tests) — this module never resolves
paths itself.

Values are double-quoted with embedded quotes/backslashes escaped so
systemd's ``EnvironmentFile=`` parser sees the literal secret verbatim.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def _escape(value: str) -> str:
    """Escape a secret for a double-quoted ``EnvironmentFile`` value.

    systemd treats a double-quoted value as a single token with the outer
    quotes stripped; backslash + double-quote must be escaped so the
    secret round-trips byte-for-byte.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _line_targets_key(line: str, key: str) -> bool:
    """True when ``line`` sets ``key`` (plain or commented out)."""
    stripped = line.lstrip()
    return (
        stripped.startswith(f"{key}=")
        or stripped.startswith(f"# {key}=")
        or stripped.startswith(f"#{key}=")
    )


def _atomic_write(api_env: Path, text: str) -> None:
    """Write ``text`` to ``api_env`` atomically with mode 0600.

    tmp-file in the same directory + ``os.replace`` so a concurrent
    reader never sees a partial file; ``0600`` because this file holds
    secrets.
    """
    api_env.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{api_env.name}.",
        suffix=".tmp",
        dir=str(api_env.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_str, 0o600)
        os.replace(tmp_str, api_env)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_str)
        raise


def upsert_env_value(api_env: Path, key: str, value: str) -> None:
    """Upsert ``key="<escaped-value>"`` in ``api_env`` atomically.

    If a line for ``key`` exists (commented or not) it is replaced in
    place; otherwise the line is appended. Raises :class:`OSError` on any
    read/write failure — callers wrap it in their own error envelope.
    """
    existing = ""
    with contextlib.suppress(FileNotFoundError):
        existing = api_env.read_text(encoding="utf-8")

    new_line = f'{key}="{_escape(value)}"\n'
    lines = existing.splitlines(keepends=True) if existing else []
    rewritten: list[str] = []
    replaced = False
    for line in lines:
        if _line_targets_key(line, key):
            if not replaced:
                rewritten.append(new_line)
                replaced = True
            continue
        rewritten.append(line)
    if not replaced:
        if rewritten and not rewritten[-1].endswith("\n"):
            rewritten.append("\n")
        rewritten.append(new_line)

    _atomic_write(api_env, "".join(rewritten))


def delete_env_value(api_env: Path, key: str) -> bool:
    """Remove every line setting ``key`` from ``api_env`` atomically.

    Returns ``True`` if at least one line was removed, ``False`` if the
    key wasn't present (so the caller can decide whether that's a 404).
    Missing file → ``False`` (nothing to delete). Raises :class:`OSError`
    only on a genuine write failure.
    """
    try:
        existing = api_env.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False

    lines = existing.splitlines(keepends=True)
    kept = [line for line in lines if not _line_targets_key(line, key)]
    if len(kept) == len(lines):
        return False
    _atomic_write(api_env, "".join(kept))
    return True


def list_env_keys(api_env: Path) -> list[str]:
    """Return the sorted, de-duplicated set of keys set in ``api_env``.

    Only uncommented ``KEY=...`` lines count; commented-out lines and
    blank lines are skipped. Never returns values. Missing file → ``[]``.
    """
    try:
        existing = api_env.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    keys: set[str] = set()
    for line in existing.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        head, sep, _ = stripped.partition("=")
        if sep and head:
            keys.add(head.strip())
    return sorted(keys)


__all__ = [
    "delete_env_value",
    "list_env_keys",
    "upsert_env_value",
]
