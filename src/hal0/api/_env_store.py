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
import stat
import tempfile
import threading
from collections.abc import Iterator, MutableMapping
from pathlib import Path

from hal0.config import paths

# Every character ``str.splitlines()`` treats as a line boundary. The file
# is re-read with ``.splitlines()`` (see ``list_env_keys`` / the upsert
# rewrite), so any of these inside a value could split the stored line into
# a phantom ``KEY=value`` entry on round-trip — even the higher-codepoint
# ones (NEL/LS/PS) that a ``< 0x20`` byte check misses. We refuse them all.
_LINE_BREAK_CHARS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")

#: Names that steer how a process loads code or resolves programs rather than
#: carry a credential (#2936). ``api.env`` is hal0-api's own
#: ``EnvironmentFile=`` and every write also lands in the live
#: ``os.environ``, so each child hal0-api spawns (model pulls, the Hermes
#: provisioner, update and converge helpers) inherits the value, and hal0-api
#: itself starts with it after a restart: ``LD_PRELOAD`` runs arbitrary code
#: as ``hal0``, a wrong ``PATH`` or ``PYTHONPATH`` wedges the daemon. The sudo
#: seam resets the environment, so none of this reaches root.
PROCESS_CONTROL_ENV_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "SHELL",
        "IFS",
        "ENV",
        "BASH_ENV",
        "NODE_OPTIONS",
        "PERL5OPT",
        "RUBYOPT",
        "GCONV_PATH",
        "PERL5LIB",
        "NODE_PATH",
        # bash runs ``PS4`` (command substitution included) on every xtrace
        # line, and SHELLOPTS/BASHOPTS can switch xtrace on in a child shell.
        "SHELLOPTS",
        "BASHOPTS",
        "PS4",
        # Redirects where hal0's Hermes helpers read and write its .env.
        "HERMES_HOME",
        # Python's ssl, httpx, curl and Node write every TLS session key to the
        # named file, so a passive capture decrypts upstream tokens (#2997).
        "SSLKEYLOGFILE",
        # Set to 0, turns off certificate checks for every Node child (the Hermes
        # web build, npm): simpler than swapping the CA bundle #2941 guards (#2999).
        "NODE_TLS_REJECT_UNAUTHORIZED",
    }
)
#: Prefix families with the same effect: the dynamic loader (``LD_*``,
#: ``DYLD_*``), the interpreters and installers hal0's children run
#: (``PYTHON*``, ``UV_*``, ``PIP_*``, ``NPM_CONFIG_*`` for the Hermes web
#: build's npm), git (``GIT_SSH_COMMAND`` and the rest), OpenSSL
#: (``OPENSSL_CONF`` and its module and engine directories) and the XDG base
#: directories those tools read their configuration, data and caches from.
PROCESS_CONTROL_ENV_PREFIXES = (
    "LD_",
    "DYLD_",
    "PYTHON",
    "UV_",
    "PIP_",
    "NPM_CONFIG_",
    "GIT_",
    # OPENSSL_CONF loads an engine or provider shared object into every OpenSSL
    # user; OPENSSL_MODULES and OPENSSL_ENGINES pick where those load from (#2998).
    "OPENSSL_",
    # The XDG base directories relocate the config, data and cache trees of
    # git, pip, uv and npm, sidestepping the HOME/GIT_/PIP_/UV_ denies (#3000):
    # XDG_CONFIG_HOME holds git's core.sshCommand/core.fsmonitor and pip/uv
    # config, XDG_CONFIG_DIRS pip's site config, XDG_DATA_HOME the uv-managed
    # Pythons the Hermes build runs, XDG_CACHE_HOME the wheels uv and pip reuse.
    "XDG_",
)

#: Suffix of the proxy variables (#3006): ``HTTP_PROXY``, ``HTTPS_PROXY``,
#: ``ALL_PROXY``, ``NO_PROXY`` and their lower-case spellings. httpx, requests,
#: git, curl, uv and pip all honour them, so an entry routes hal0-api's
#: outbound traffic, bearer tokens on plain-http upstreams included, through a
#: proxy chosen by whoever wrote it. requests and urllib read any
#: ``<scheme>_proxy``, hence a suffix rather than a list. A box behind a proxy
#: sets these by editing ``api.env`` as root; no hal0 writer stores them.
PROXY_ENV_SUFFIX = "_PROXY"

#: Hugging Face Hub variables that choose where the ``hf`` CLI and
#: ``huggingface_hub`` send requests, the bearer token with them, or where
#: they read cached weights (#3017). The ComfyUI fetch runs ``hf download``
#: with hal0-api's environment plus ``HF_TOKEN``, so ``HF_ENDPOINT`` hands the
#: token to another host and lets it choose the weights that are later loaded;
#: a cache directory hal0 does not own serves planted files the same way.
#: ``HF_TOKEN`` (the credential) and ``HF_HOME`` (documented) stay allowed.
HF_HUB_REDIRECT_ENV_NAMES = frozenset(
    {
        "HF_ENDPOINT",
        "HF_INFERENCE_ENDPOINT",
        "HUGGINGFACE_CO_STAGING",
        "HF_TOKEN_PATH",
        "HF_ASSETS_CACHE",
        "HUGGINGFACE_ASSETS_CACHE",
    }
)
#: ``HF_HUB_CACHE``, ``HF_HUB_OFFLINE`` and the rest; the older spelling
#: ``HUGGINGFACE_HUB_CACHE``; the Xet transfer settings (``HF_XET_CACHE``).
HF_HUB_REDIRECT_ENV_PREFIXES = ("HF_HUB_", "HUGGINGFACE_HUB_", "HF_XET_")

#: Names that choose which CA bundle outbound TLS clients trust (#2941). A
#: bundle the ``hal0`` user can write lets a man-in-the-middle read every
#: token hal0 sends upstream (HF, provider keys), so these are stored only as
#: a path hal0 cannot modify (:func:`tls_trust_violation`) -- not denied
#: outright, because a corporate-proxy box needs a custom CA.
TLS_TRUST_ENV_NAMES = frozenset(
    {
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
    }
)

#: The uid that must own a trusted path. Module state only so tests can stand
#: in for root; production code never changes it.
_TRUSTED_UID = 0
#: Directory at and above which a path is taken as trusted without inspection.
#: ``None`` checks every component from ``/``. Tests point it at ``tmp_path``.
_TRUST_ANCHOR: Path | None = None

# A key ``upsert_env_value`` will write: an env-var name and nothing else, so a
# caller can never smuggle a line break (and a second variable) in the key.
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_process_control_env(key: str) -> bool:
    """True when ``key`` changes how processes run, not what they authenticate with."""
    name = key.strip().upper()
    return (
        name in PROCESS_CONTROL_ENV_NAMES
        or name.startswith(PROCESS_CONTROL_ENV_PREFIXES)
        or is_proxy_env(name)
        or is_hf_hub_redirect_env(name)
    )


def is_hf_hub_redirect_env(key: str) -> bool:
    """True when ``key`` moves Hugging Face Hub traffic or its cache (``HF_ENDPOINT``, ...) (#3017)."""
    name = key.strip().upper()
    return name in HF_HUB_REDIRECT_ENV_NAMES or name.startswith(HF_HUB_REDIRECT_ENV_PREFIXES)


def is_proxy_env(key: str) -> bool:
    """True when ``key`` is a proxy variable (``HTTP_PROXY``, ``no_proxy``, ...) (#3006)."""
    return key.strip().upper().endswith(PROXY_ENV_SUFFIX)


def is_tls_trust_env(key: str) -> bool:
    """True when ``key`` selects the CA bundle or directory TLS clients trust."""
    return key.strip().upper() in TLS_TRUST_ENV_NAMES


#: Bounds on what one TLS trust value may make the check do: it runs in a
#: worker thread, but a hostile value must not buy unbounded work.
_TLS_MAX_VALUE_CHARS = 4096
_TLS_MAX_LIST_ENTRIES = 16
_TLS_MAX_SYMLINK_HOPS = 40
_TLS_MAX_DIR_ENTRIES = 2000


class TlsTrustError(ValueError):
    """A TLS trust variable's value is not a path hal0 cannot rewrite (#2941)."""

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"{key!r} is a TLS trust setting and cannot be stored: {reason}")
        self.key = key
        self.reason = reason


# CodeQL note: `os.lstat`, `os.readlink` and `os.scandir` below report
# py/path-injection (the rule this repo already carries standing alerts for).
# The operator-supplied path is the whole point: the check inspects metadata
# (owner, mode, type) to decide whether to store it, and never opens, reads,
# creates or modifies it, so a hostile value can at worst make the check say no.
def _node_violation(node: Path, st: os.stat_result) -> str | None:
    if st.st_uid != _TRUSTED_UID:
        return "the path, a symlink hop or a parent directory is not root-owned"
    if not stat.S_ISLNK(st.st_mode) and st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "the path or a parent directory is group- or world-writable"
    return None


def _walk_trusted(value: str) -> tuple[Path | None, str | None]:
    """Resolve absolute ``value`` by hand, vetting every component and symlink hop.

    Each component, ``/`` included, is ``lstat``-ed: it must be owned by the
    trusted uid and, unless it is a symlink (whose own mode is meaningless),
    not writable by group or others. A symlink is read and its target spliced
    into the remaining path (absolute targets restart from ``/``), so every
    intermediate hop is vetted, not only the first link and the final target:
    whoever can rewrite any hop can repoint the whole path. Returns the
    resolved path or the refusal reason.
    """
    anchor = _TRUST_ANCHOR
    skipped = {anchor, *anchor.parents} if anchor is not None else set()

    def vet(node: Path) -> tuple[os.stat_result | None, str | None]:
        try:
            st = os.lstat(node)
        except (OSError, ValueError):
            return None, "the path does not exist or cannot be inspected"
        if node in skipped:
            return st, None
        return st, _node_violation(node, st)

    cur = Path("/")
    _, reason = vet(cur)
    if reason is not None:
        return None, reason
    parts = list(Path(value).parts[1:])
    hops = 0
    while parts:
        name = parts.pop(0)
        if name in ("", "."):
            continue
        if name == "..":
            cur = cur.parent
            continue
        nxt = cur / name
        st, reason = vet(nxt)
        if reason is not None:
            return None, reason
        assert st is not None
        if stat.S_ISLNK(st.st_mode):
            hops += 1
            if hops > _TLS_MAX_SYMLINK_HOPS:
                return None, "too many symlink hops"
            try:
                target = Path(os.readlink(nxt))
            except OSError:
                return None, "the path cannot be inspected"
            if target.is_absolute():
                cur = Path("/")
                parts = [*target.parts[1:], *parts]
            else:
                parts = [*target.parts, *parts]
            continue
        cur = nxt
    return cur, None


def _tls_path_violation(value: str, *, want_dir: bool, any_type: bool = False) -> str | None:
    if not value or value != value.strip() or "\x00" in value:
        return "the value must be an absolute path"
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        return "the value must be an absolute path with no '..' component"
    resolved, reason = _walk_trusted(value)
    if reason is not None or resolved is None:
        return reason or "the path does not exist"
    try:
        st = os.lstat(resolved)
    except OSError:
        return "the path does not exist or cannot be inspected"
    if any_type:
        return None
    if not (stat.S_ISDIR(st.st_mode) if want_dir else stat.S_ISREG(st.st_mode)):
        return "the path must be a directory" if want_dir else "the path must be a regular file"
    if not want_dir:
        return None
    # A directory is only as trustworthy as what is in it: vet every entry
    # (hash-named symlinks included) the same way.
    try:
        with os.scandir(resolved) as it:
            for count, entry in enumerate(it, 1):
                if count > _TLS_MAX_DIR_ENTRIES:
                    return "the directory holds too many entries to vet"
                reason = _tls_path_violation(
                    str(resolved / entry.name), want_dir=False, any_type=True
                )
                if reason is not None:
                    return f"a directory entry fails the check: {reason}"
    except OSError:
        return "the directory cannot be listed"
    return None


def tls_trust_violation(key: str, value: str) -> str | None:
    """Why ``value`` may not be stored under TLS trust variable ``key``, or None (#2941).

    Accepted: an absolute path to an existing root-owned file (a directory for
    ``SSL_CERT_DIR``, where a ``:`` list is accepted if every entry qualifies),
    not group- or world-writable, under parents that are root-owned and not
    group- or world-writable. Symlinks are followed hop by hop and every hop,
    its directory and the final target must pass; for ``SSL_CERT_DIR`` so must
    each entry of the directory. Any other key returns None. The reason never
    repeats the value. Mode and ownership only: ACLs, mount options and file
    attributes are not inspected. Blocking filesystem work: call it from a
    thread inside the event loop.
    """
    name = key.strip().upper()
    if name not in TLS_TRUST_ENV_NAMES:
        return None
    if len(value) > _TLS_MAX_VALUE_CHARS:
        return "the value is too long"
    if name == "SSL_CERT_DIR":
        entries = value.split(":")
        if len(entries) > _TLS_MAX_LIST_ENTRIES:
            return "the list has too many entries"
        for entry in entries:
            reason = _tls_path_violation(entry, want_dir=True)
            if reason is not None:
                return reason
        return None
    return _tls_path_violation(value, want_dir=False)


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
