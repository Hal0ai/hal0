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
import fcntl
import os
import re
import tempfile
import threading
from collections.abc import Iterator, MutableMapping
from pathlib import Path

from hal0.config import paths

# The name and value checks live in hal0.config.env_guard, outside this package, so
# the CLI can use them without importing hal0.api (#4062). Re-exported for the
# callers that take them from here.
from hal0.config.env_guard import (
    HF_HUB_REDIRECT_ENV_NAMES,
    HF_HUB_REDIRECT_ENV_PREFIXES,
    PROCESS_CONTROL_ENV_NAMES,
    PROCESS_CONTROL_ENV_PREFIXES,
    PROXY_ENV_SUFFIX,
    TLS_TRUST_ENV_NAMES,
    TlsTrustError,
    is_hf_hub_redirect_env,
    is_process_control_env,
    is_proxy_env,
    is_tls_trust_env,
    tls_trust_violation,
)

# Every character ``str.splitlines()`` treats as a line boundary. The file
# is re-read with ``.splitlines()`` (see ``list_env_keys`` / the upsert
# rewrite), so any of these inside a value could split the stored line into
# a phantom ``KEY=value`` entry on round-trip — even the higher-codepoint
# ones (NEL/LS/PS) that a ``< 0x20`` byte check misses. We refuse them all.
_LINE_BREAK_CHARS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")


# A key ``upsert_env_value`` will write: an env-var name and nothing else, so a
# caller can never smuggle a line break (and a second variable) in the key.
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


_API_ENV_THREAD_LOCK = threading.RLock()


@contextlib.contextmanager
def api_env_lock(api_env: Path) -> Iterator[None]:
    """Serialise read-modify-write cycles on ``api_env``.

    The routes call the writers from worker threads, so a module lock keeps two
    saves in this process from both reading the old file and the second rename
    dropping the first's key. An ``flock`` on a sidecar file in the same
    directory extends that to other holders of the file (``oauth/store.py``,
    ``service_identity.rotate_api_env_key``, a second hal0 process). The
    sidecar is the lock, not ``api.env`` itself, because every write replaces
    ``api.env``'s inode. If the sidecar cannot be opened the thread lock still
    holds.

    The sidecar lives in ``/etc/hal0``, which the ``hal0`` account can write,
    while ``hal0 auth reset-key`` takes this lock as root. It is therefore
    opened through :func:`hal0.config.locking._open_lock_file`: ``O_NOFOLLOW``,
    an ``S_ISREG`` check on the descriptor, and an ownership fix-up only on the
    file this call created. A planted symlink or FIFO is refused (and locking
    degrades to the thread lock) rather than followed and chowned.
    """
    from hal0.config.locking import _open_lock_file

    with _API_ENV_THREAD_LOCK:
        fh = None
        try:
            api_env.parent.mkdir(parents=True, exist_ok=True)
            fh = _open_lock_file(api_env.parent / f".{api_env.name}.lock", api_env)
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            if fh is not None:
                fh.close()
            fh = None
        try:
            yield
        finally:
            if fh is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()


def _atomic_write(api_env: Path, text: str) -> None:
    """Write ``text`` to ``api_env`` atomically at :data:`paths.API_ENV_MODE`.

    tmp-file in the same directory + ``os.replace`` so a concurrent reader
    never sees a partial file; owner-only because this file holds secrets.

    The mode rides on the tmp file and lands with the rename, so this also
    *repairs* an api.env that a previous installer re-run (or the pre-#1466
    perms table) left world-readable — the mode is not inherited from the
    file being replaced.
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
        os.chmod(tmp_str, paths.API_ENV_MODE)
        os.replace(tmp_str, api_env)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_str)
        raise


def upsert_env_value(
    api_env: Path,
    key: str,
    value: str,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Upsert ``key="<escaped-value>"`` in ``api_env`` atomically.

    If a line for ``key`` exists (commented or not) it is replaced in
    place; otherwise the line is appended. Raises :class:`OSError` on any
    read/write failure — callers wrap it in their own error envelope.

    Writer-level guard (defense-in-depth): a line-break character in
    ``value`` would terminate the quoted env-file line and let everything
    after it parse as a *new* ``KEY=value`` entry — i.e. arbitrary env-var
    injection into api.env. We escape backslash + double-quote (so the
    secret round-trips), but escaping can't neutralise a raw line break, so
    we refuse the whole ``str.splitlines()`` set outright (not just
    ``\\n``/``\\r`` — also NEL/LS/PS, which the file's own ``.splitlines()``
    re-read would split on). Route-level validation should reject these
    before reaching here; this guard ensures no future caller can
    reintroduce the hole.

    The same goes for the key: a loader or interpreter variable
    (:func:`is_process_control_env`) is refused here as well as at the
    routes, so no writer of this file can plant one (#2936). Deleting such a
    line stays allowed, so an existing one can be removed. A TLS trust
    variable (:func:`tls_trust_violation`) is refused unless its value is a
    root-owned path hal0 cannot write (#2941).

    ``environ`` (normally ``os.environ``) is updated inside the same locked
    section as the write, so a concurrent :func:`delete_env_value` cannot leave
    the live process env and the persisted file disagreeing.
    """
    if not _ENV_KEY_RE.match(key):
        raise ValueError("env key must be an env-var name (letters, digits, underscores)")
    if is_process_control_env(key):
        raise ValueError(f"{key!r} controls how processes run and cannot be stored in api.env")
    if _LINE_BREAK_CHARS.intersection(value):
        raise ValueError("env value must not contain line-break characters")
    tls_reason = tls_trust_violation(key, value)
    if tls_reason is not None:
        raise TlsTrustError(key, tls_reason)

    with api_env_lock(api_env):
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
        if environ is not None:
            environ[key] = value


def delete_env_value(
    api_env: Path,
    key: str,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> bool:
    """Remove every line setting ``key`` from ``api_env`` atomically.

    Returns ``True`` if at least one line was removed, ``False`` if the
    key wasn't present (so the caller can decide whether that's a 404).
    Missing file → ``False`` (nothing to delete). Raises :class:`OSError`
    only on a genuine write failure.

    ``environ`` (normally ``os.environ``) loses ``key`` inside the same locked
    section, whether or not the file held it, unless the write fails.
    """
    with api_env_lock(api_env):
        try:
            existing = api_env.read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = None

        removed = False
        if existing is not None:
            lines = existing.splitlines(keepends=True)
            kept = [line for line in lines if not _line_targets_key(line, key)]
            if len(kept) != len(lines):
                _atomic_write(api_env, "".join(kept))
                removed = True
        if environ is not None:
            environ.pop(key, None)
        return removed


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
    "HF_HUB_REDIRECT_ENV_NAMES",
    "HF_HUB_REDIRECT_ENV_PREFIXES",
    "PROCESS_CONTROL_ENV_NAMES",
    "PROCESS_CONTROL_ENV_PREFIXES",
    "PROXY_ENV_SUFFIX",
    "TLS_TRUST_ENV_NAMES",
    "TlsTrustError",
    "api_env_lock",
    "delete_env_value",
    "is_hf_hub_redirect_env",
    "is_process_control_env",
    "is_proxy_env",
    "is_tls_trust_env",
    "list_env_keys",
    "tls_trust_violation",
    "upsert_env_value",
]
