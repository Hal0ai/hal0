"""Hermes release builder: one release directory per build, behind one lock.

This module implements ADR-0032 decisions 4, 6, 7, 10 and 11, as amended by
ADR-0034 decisions 1, 3 and 4. Every path that builds or repairs the Hermes
venv comes through :func:`ensure_release`:

* ``hermes_provision._phase_install`` (install, bootstrap, reprovision,
  ``--repair``, and ``hal0 agent upgrade hermes``);
* ``components.hermes_arm.converge_hermes`` (``hal0 update``);
* ``updater.repair_hermes_mcp_client`` (``hal0 update``).

:func:`ensure_release` takes the builder lock, decides whether a build is
needed (:func:`build_trigger`), builds a fresh release
(:func:`build_release`), activates it with a flip-back on a failed re-verify
(:func:`activate_release`), writes the ``venv.pin`` stamp from
``release.json``, and prunes everything but ``current`` and ``previous``
(:func:`prune_releases`). Nothing ever writes into a release once its
``release.json`` exists, so a repair is a fresh release of the same ref.

Builds run as the ``hal0`` user (§7.4). ``git`` and ``uv`` run with the
HOME-sanitised environment the provisioner already uses, uv's cache under
``/var/lib/hal0/.cache/uv`` and managed interpreters under
``/var/lib/hal0/hermes/python``, both inside the hal0-owned release tree. uv is the copy that ships in hal0's own venv
(:func:`find_uv`).
"""

from __future__ import annotations

import contextlib
import datetime
import errno
import fcntl
import functools
import json
import os
import re
import shutil
import subprocess  # nosec B404 — fixed git / uv / systemctl argv
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from hal0.agents import hermes_platforms as hpl
from hal0.agents import hermes_provision as hp
from hal0.agents.hermes_layout import GATEWAY_UNIT_PATH, release_root_for

log = structlog.get_logger(__name__)

RELEASE_JSON = "release.json"
#: ADR-0032 decision 11 fixes these names for the release the first migration
#: makes from the pre-layout venv: ``releases/legacy-<sha12>/`` and
#: ``"legacy": true`` in its ``release.json``.
_LEGACY_KEY = "legacy"  # scar-ok: release.json key fixed by ADR-0032 decision 11
_LEGACY_PREFIX = "legacy-"  # scar-ok: release directory prefix fixed by ADR-0032 decision 11
AGENT_UNIT = "hal0-agent@hermes.service"
GATEWAY_UNIT = "hermes-gateway.service"
HERMES_UNITS: tuple[str, ...] = (AGENT_UNIT, GATEWAY_UNIT)

#: How long every recorded unit must stay active, with no new restart, after
#: an activation starts it (ADR-0032 decision 7, step 7).
DEFAULT_SETTLE_S = 10.0

#: Ceiling for one build subprocess (a dependency install or the UI build).
_STEP_TIMEOUT_S = 1800.0

#: The operator remedy every failed build or activation names.
REPAIR_REMEDY = "sudo hal0 agent reprovision hermes --repair"

#: Offline-safe release check (ADR-0034 decision 3). ``hermes --version`` makes
#: a network update check at newer refs, so it is not used.
_IMPORT_CHECK = "import hermes_cli.main, hermes_cli.web_server"

#: Upstream's own dashboard build entry point, present at 3ef6bbd2 and at
#: v0.22.2 (``hermes_cli.main._build_web_ui``). Returns a bool.
_WEB_BUILD_SNIPPET = (
    "import sys\n"
    "from pathlib import Path\n"
    "from hermes_cli.main import _build_web_ui\n"
    "sys.exit(0 if _build_web_ui(Path(sys.argv[1]), fatal=True) else 1)\n"
)

_REQ_RE = re.compile(
    r"^hermes-agent\s*(?:\[(?P<extras>[^\]]*)\])?\s*@\s*git\+(?P<url>https?://\S+?)@(?P<ref>[0-9A-Za-z._+-]+)\s*$"
)

Runner = Callable[..., subprocess.CompletedProcess]


# ── Errors ───────────────────────────────────────────────────────────────────


class ReleaseError(RuntimeError):
    """A builder step failed. ``step`` names it for the operator."""

    def __init__(self, step: str, message: str) -> None:
        self.step = step
        super().__init__(f"{step}: {message}")


class HermesBusy(RuntimeError):
    """Another process holds the builder lock. ``holder`` is its record."""

    def __init__(self, holder: Mapping[str, Any]) -> None:
        self.holder = dict(holder)
        super().__init__(f"Hermes builder busy: {describe_holder(self.holder)}")


def describe_holder(holder: Mapping[str, Any]) -> str:
    """``pid 123 (converge, since 2026-…Z)`` from a lock record."""
    pid = holder.get("pid", "?")
    caller = holder.get("caller", "?")
    since = holder.get("started_at", "?")
    return f"pid {pid} ({caller}, since {since})"


# ── Layout ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Layout:
    """The release tree that belongs to one venv link."""

    venv_link: Path
    root: Path

    @classmethod
    def for_venv(cls, venv: Path | str) -> Layout:
        venv = Path(venv)
        return cls(venv_link=venv, root=release_root_for(venv))

    @property
    def lock(self) -> Path:
        return self.root / ".lock"

    @property
    def mirror(self) -> Path:
        return self.root / "mirror.git"

    @property
    def releases(self) -> Path:
        return self.root / "releases"

    @property
    def current(self) -> Path:
        return self.root / "current"

    @property
    def previous(self) -> Path:
        return self.root / "previous"

    @property
    def venv_target(self) -> Path:
        return self.current / "venv"

    @property
    def python_dir(self) -> Path:
        """uv-managed interpreters (``UV_PYTHON_INSTALL_DIR``)."""
        return self.root / "python"

    @property
    def uv_cache(self) -> Path:
        return self.root / ".cache" / "uv"

    @property
    def tools(self) -> Path:
        """Upstream's package-manager store (``HERMES_RUNTIME_DIR``).

        The v0.22.2 web build fetches its pinned Node through ``pm`` (Node
        v26.7.0 per upstream ``pm/lock.json``). Without the override ``pm``
        puts it in ``$HERMES_HOME/tools`` (``pm/environments.py:211-239`` at
        d923a5eb), outside the release tree. Shared by every release so a
        build does not download Node again.
        """
        return self.root / "tools"


@dataclass(frozen=True)
class Release:
    """One release directory and its ``release.json``."""

    id: str
    path: Path
    data: dict[str, Any]

    @property
    def ref(self) -> str:
        return str(self.data.get("ref") or "")

    @property
    def legacy(self) -> bool:  # scar-ok: ADR-0032 decision 11 names the "legacy release"
        return bool(self.data.get(_LEGACY_KEY))

    @property
    def python_minor(self) -> str | None:
        value = self.data.get("python_minor")
        return str(value) if value else None

    @property
    def venv(self) -> Path:
        return self.path / "venv"

    @property
    def src(self) -> Path:
        return self.path / "src"


def read_release(path: Path) -> Release | None:
    """The release at ``path``, or ``None`` when it is incomplete or unreadable."""
    try:
        data = json.loads((path / RELEASE_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return Release(id=path.name, path=path, data=data)


def _link_release_id(link: Path) -> str | None:
    """The release id a ``current``/``previous`` link names, or ``None``."""
    if not link.is_symlink():
        return None
    try:
        return Path(os.readlink(link)).name or None
    except OSError:
        return None


def live_release(layout: Layout) -> Release | None:
    """The release ``current`` names, when it is complete."""
    rid = _link_release_id(layout.current)
    return read_release(layout.releases / rid) if rid else None


def _swap_link(link: Path, target: str | Path) -> None:
    """Point ``link`` at ``target`` atomically: symlink a temp name, rename it over."""
    tmp = link.with_name(f".{link.name}.tmp-{os.getpid()}")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    os.symlink(str(target), tmp)
    os.replace(tmp, link)


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _utc_stamp() -> str:
    return datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%dT%H%M%SZ")


def _utc_iso() -> str:
    return datetime.datetime.now(tz=datetime.UTC).isoformat().replace("+00:00", "Z")


def _new_release_id(layout: Layout, prefix: str, stamp: str) -> str:
    """``<prefix>-<stamp>``, suffixed when a release with that id exists."""
    base = f"{prefix}-{stamp}"
    rid, n = base, 1
    while (layout.releases / rid).exists():
        rid = f"{base}-{n}"
        n += 1
    return rid


# ── The pin ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Pin:
    """URL, extras and ref read from the single requirements line (decision 3)."""

    url: str
    extras: tuple[str, ...]
    ref: str


def read_pin(requirements: Path | None = None) -> Pin:
    """Parse ``installer/agents/hermes/requirements.txt`` without handing it to pip."""
    path = Path(requirements) if requirements is not None else hp.HERMES_REQUIREMENTS
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseError("pin", f"cannot read {path}: {exc}") from exc
    try:
        line = hp._hermes_requirement_line(text)
    except ValueError as exc:
        raise ReleaseError("pin", f"{path}: {exc}") from exc
    m = _REQ_RE.match(line)
    if m is None:
        raise ReleaseError(
            "pin", f"{path}: expected `hermes-agent[extras] @ git+<url>@<sha>`, got {line!r}"
        )
    extras = tuple(e.strip() for e in (m.group("extras") or "").split(",") if e.strip())
    return Pin(url=m.group("url"), extras=extras, ref=m.group("ref"))


def require_vetted(ref: str) -> str:
    """Return ``ref`` when it is a full 40-hex commit in ``VETTED_HERMES_REFS``."""
    if not hp._HERMES_SHA_RE.fullmatch(ref or ""):
        raise ReleaseError(
            "pin", f"{ref!r} is not a full 40-hex commit; tags, branches and versions are refused"
        )
    if ref not in hp.VETTED_HERMES_REFS:
        raise ReleaseError("pin", f"{ref} is not in VETTED_HERMES_REFS")
    return ref


def live_ref(layout: Layout | None = None) -> str | None:
    """The ref of the live release, or None when there is none or it records no ref."""
    lay = layout if layout is not None else Layout.for_venv(hp.HERMES_VENV_DEFAULT)
    try:
        live = live_release(lay)
    except OSError:
        return None
    return live.ref or None if live is not None else None


def refuse_downgrade(ref: str, *, live: str | None) -> None:
    """Raise ``ReleaseError`` when building ``ref`` would run an older vetted release
    over the ``state.db`` the newer live one has written (upstream #140060).

    Only two vetted refs can be ordered; an unknown or absent live ref passes.
    """
    versions = hp.VETTED_HERMES_VERSIONS
    if live is None or live == ref or live not in versions or ref not in versions:
        return
    if versions[ref] < versions[live]:
        want = ".".join(map(str, versions[ref]))
        have = ".".join(map(str, versions[live]))
        raise ReleaseError(
            "downgrade",
            f"{ref[:12]} is v{want}, older than the live v{have} ({live[:12]}): a downgrade "
            "over the state.db the newer release has written is refused (upstream refuses "
            "it too, and hal0's release switch keeps only config.yaml). A manual rollback "
            "path is tracked in #2805",
        )


def target_minor(ref: str | None = None) -> str:
    """The interpreter minor a release of ``ref`` is built on.

    The newest supported minor the ref's ``requires-python`` admits
    (``hermes_provision.hermes_target_minor``): 3.12 for 3ef6bbd2, which caps
    below 3.14; 3.14 for a ref with no record. Raises :class:`ReleaseError`
    when the ref admits no supported minor.
    """
    try:
        return hp.hermes_target_minor(ref)
    except ValueError as exc:
        raise ReleaseError("python", f"{(ref or '?')[:12]}: {exc}") from exc


def _checkout_requires_python(src: Path) -> str | None:
    """``[project].requires-python`` of the checkout, or ``None``."""
    import tomllib

    try:
        data = tomllib.loads((src / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    value = (data.get("project") or {}).get("requires-python")
    return value if isinstance(value, str) and value.strip() else None


# ── Tools ────────────────────────────────────────────────────────────────────


def _checkout_extras(src: Path) -> set[str] | None:
    """``[project.optional-dependencies]`` names of the checkout, or ``None``."""
    import tomllib

    try:
        with (src / "pyproject.toml").open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return None
    extras = (data.get("project") or {}).get("optional-dependencies")
    return set(extras) if isinstance(extras, dict) else None


def configured_extras(
    hermes_home: Path | str | None = None, secrets_env: Path | str | None = None
) -> tuple[str, ...]:
    """The platform extras this box's Hermes configuration needs (hermes_platforms).

    Defaults to the live ``HERMES_HOME`` and the gateway's secrets vault. Root
    reads the vault; a ``hal0`` caller (hal0-api's converge pass) gets the
    names of its set keys from the ``hal0-agentenv secret-keys`` seam.
    """
    home = Path(hermes_home) if hermes_home is not None else hp.HERMES_HOME_DEFAULT
    vault = Path(secrets_env) if secrets_env is not None else hp.HERMES_SECRETS_ENV
    seam = hp._HAL0_AGENTENV if os.geteuid() != 0 else None
    return hpl.configured_platform_extras(home, vault, seam=seam)


def release_extras(
    pin_extras: tuple[str, ...], platform_extras: tuple[str, ...] | list[str], src: Path
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The pin's extras plus the platform extras the checkout defines.

    Returns ``(all extras, platform extras applied)``. A platform extra the
    checkout's pyproject does not declare is dropped: ``uv export --extra``
    would refuse it, and a ref that lacks it cannot satisfy it anyway.
    """
    defined = _checkout_extras(src)
    applied = tuple(
        sorted(
            e
            for e in set(platform_extras)
            if e not in pin_extras and (defined is None or e in defined)
        )
    )
    return (*pin_extras, *applied), applied


def find_uv(
    *,
    executable: str | Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """The uv that ships in hal0's own venv, then uv on ``PATH``.

    ADR-0034 decision 2: uv is a pinned dependency of hal0, so it sits next to
    the running interpreter (``Path(sys.executable).parent / "uv"``). ``PATH``
    is only a fallback for a dev checkout run from a venv without it.
    """
    sibling = Path(executable or sys.executable).parent / "uv"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return which("uv")


_NEUTRAL_CWD = "/"


def _run(
    argv: list[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = _STEP_TIMEOUT_S,
) -> subprocess.CompletedProcess:
    # uv discovers ``uv.toml`` from the cwd upward, independent of HOME. A
    # ``sudo`` caller's cwd is ``/root``, so an unpinned run died on
    # ``/root/uv.toml`` as hal0 (#2883). Start from ``/`` unless told otherwise.
    return subprocess.run(  # nosec B603 — argv built from fixed tool paths
        [str(a) for a in argv],
        cwd=str(cwd) if cwd is not None else _NEUTRAL_CWD,
        env=dict(env) if env is not None else None,
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
    )


def _tail(text: str | None, limit: int = 600) -> str:
    return (text or "").strip()[-limit:]


def _must(run: Runner, step: str, argv: list[Any], **kw: Any) -> subprocess.CompletedProcess:
    """Run one builder command; raise :class:`ReleaseError` naming ``step`` on failure."""
    try:
        proc = run([str(a) for a in argv], **kw)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(step, str(exc)) from exc
    if proc.returncode != 0:
        err = _tail(proc.stderr) or _tail(proc.stdout)
        raise ReleaseError(
            step, f"`{' '.join(str(a) for a in argv[:4])} …` exited {proc.returncode}: {err}"
        )
    return proc


def build_env(*, hermes_home: Path | None = None, layout: Layout | None = None) -> dict[str, str]:
    """HOME-sanitised environment for git and uv (O15), with uv's paths pinned.

    uv's interpreters and cache live inside the release tree, which the hal0
    user owns, never in ``/var/lib/hal0/python`` or ``/var/lib/hal0/.cache/uv``:
    both can be root-owned from an earlier root-run uv, and builds run as hal0.
    """
    python_dir = layout.python_dir if layout is not None else hp.UV_PYTHON_INSTALL_DIR
    cache_dir = layout.uv_cache if layout is not None else hp.UV_CACHE_DIR
    env = hp._hal0_subprocess_env(
        UV_CACHE_DIR=str(cache_dir),
        UV_PYTHON_INSTALL_DIR=str(python_dir),
        UV_NO_PROGRESS="1",
        GIT_TERMINAL_PROMPT="0",
    )
    env.pop("VIRTUAL_ENV", None)
    if layout is not None:
        # Upstream's own build and CI scripts point pm at a store this way
        # (scripts/ci/setup_toolchain.py, scripts/build/launchers.py).
        env["HERMES_RUNTIME_DIR"] = str(layout.tools)
    if hermes_home is not None:
        env["HERMES_HOME"] = str(hermes_home)
    return env


def _lazy_installs_disabled(hermes_home: str | None) -> bool:
    """True when Hermes's ``security.allow_lazy_installs`` is false.

    v0.22.2's web build asks ``pm`` for Node and its npm dependencies, and
    with lazy installs off it passes ``--no-install`` and fails
    (hermes_cli/source_build.py:70-80, pm/install.py:164-191 at d923a5eb).
    Upstream's dashboard build takes no flag to override the setting, so
    hal0 checks it first and names the fix instead of failing deep in npm.
    """
    if not hermes_home:
        return False
    path = Path(hermes_home) / "config.yaml"
    try:
        data = hp._parse_yaml_config(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    security = (data or {}).get("security")
    return isinstance(security, dict) and security.get("allow_lazy_installs") is False


# ── The lock ─────────────────────────────────────────────────────────────────


def _read_holder(fd: int) -> dict[str, Any]:
    try:
        raw = os.pread(fd, 4096, 0)
        data = json.loads(raw.decode("utf-8") or "{}")
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_lock_holder(layout: Layout) -> dict[str, Any]:
    """The advisory holder record in the lock file (may name a dead holder)."""
    try:
        fd = os.open(layout.lock, os.O_RDONLY)
    except OSError:
        return {}
    try:
        return _read_holder(fd)
    finally:
        os.close(fd)


def builder_lock_free(layout: Layout) -> tuple[bool, dict[str, Any]]:
    """Whether the builder lock is free right now, and the holder record if not.

    A probe: it takes the ``flock`` without blocking and drops it again, and it
    never rewrites the holder record, so a stale record from a finished holder
    is left as it was. When the lock is taken the record comes back for the
    caller to show, but it is advisory and may name a dead holder, as with
    :func:`read_lock_holder`.

    The answer is a snapshot, not a reservation. For the instant the probe
    holds ``LOCK_EX``, a concurrent ``builder_lock(wait=False)`` raises
    :class:`HermesBusy` with a stale or empty holder record, and the lock can
    be taken by someone else the moment the probe returns ``(True, {})``.

    Raises:
        OSError: when the release root or the lock file cannot be created or
            opened read-write (for instance ``PermissionError`` when the caller
            cannot write there, or the file was created by root).
    """
    layout.root.mkdir(parents=True, exist_ok=True)
    fd = os.open(layout.lock, os.O_RDWR | os.O_CREAT, 0o664)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise
            return False, _read_holder(fd)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True, {}
    finally:
        os.close(fd)


@contextlib.contextmanager
def builder_lock(
    layout: Layout,
    *,
    caller: str,
    wait: bool,
    on_wait: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[None]:
    """Hold the exclusive builder ``flock`` (ADR-0032 decision 6).

    ``wait=False`` raises :class:`HermesBusy` with the holder's record when the
    lock is taken. ``wait=True`` calls ``on_wait`` with that record once, then
    blocks. Once held, the record is rewritten with this process's pid, caller
    and start time; ``flock`` itself cannot name its holder.
    """
    layout.root.mkdir(parents=True, exist_ok=True)
    fd = os.open(layout.lock, os.O_RDWR | os.O_CREAT, 0o664)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise
            holder = _read_holder(fd)
            if not wait:
                raise HermesBusy(holder) from None
            if on_wait is not None:
                on_wait(holder)
            fcntl.flock(fd, fcntl.LOCK_EX)
        record = {"pid": os.getpid(), "caller": caller, "started_at": _utc_iso()}
        os.ftruncate(fd, 0)
        os.pwrite(fd, (json.dumps(record) + "\n").encode("utf-8"), 0)
        yield
    finally:
        os.close(fd)


# ── Build ────────────────────────────────────────────────────────────────────


def _pyvenv_cfg(venv: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def _minor_of(version: str | None) -> str | None:
    m = re.match(r"(\d+)\.(\d+)", version or "")
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _venv_minor(venv: Path) -> str | None:
    cfg = _pyvenv_cfg(venv)
    return _minor_of(cfg.get("version_info") or cfg.get("version"))


def _ensure_mirror(layout: Layout, url: str, *, run: Runner, env: Mapping[str, str]) -> None:
    """Create the bare mirror as a blobless partial clone of ``url``.

    The mirror fetches tags (ADR-0034 decision 3), and with full history and
    every blob that is about 1.1 GB for hermes-agent. A ``blob:none`` filter
    keeps commits, trees and tags (all ``git describe`` needs) and fetches
    only the blobs a release's ``git worktree add`` checks out: measured at
    about 200 MB for 3ef6bbd2 with every tag.
    """
    m = layout.mirror
    if not (m / "HEAD").is_file():
        _must(run, "mirror", ["git", "init", "--bare", "--quiet", m], env=env)
    # A mirror whose first setup died after `git init` has HEAD but no origin
    # (every later fetch would fail with "No such remote 'origin'"), so the
    # remote and its partial-clone config are (re)applied whenever origin is
    # missing, not only on a fresh init.
    try:
        has_origin = (
            run(["git", "-C", str(m), "remote", "get-url", "origin"], env=env).returncode == 0
        )
    except (OSError, subprocess.SubprocessError):
        has_origin = False
    if has_origin:
        _must(run, "mirror", ["git", "-C", m, "remote", "set-url", "origin", url], env=env)
    else:
        _must(run, "mirror", ["git", "-C", m, "remote", "add", "origin", url], env=env)
    _must(run, "mirror", ["git", "-C", m, "config", "remote.origin.promisor", "true"], env=env)
    _must(
        run,
        "mirror",
        ["git", "-C", m, "config", "remote.origin.partialclonefilter", "blob:none"],
        env=env,
    )


def lock_serves(lock: Path, minor: str | None) -> bool:
    """Whether ``lock`` exists and its ``requires-python`` admits ``minor``.

    3ef6bbd2's lock says ``>=3.11, <3.14``, so it cannot serve the 3.14 venv;
    v0.22.2's covers 3.14 only. ``minor=None`` accepts any lock. An unreadable
    or unparsable ``requires-python`` is treated as not serving.
    """
    if not lock.is_file():
        return False
    if minor is None:
        return True
    try:
        head = lock.read_text(encoding="utf-8")[:4096]
    except (OSError, UnicodeDecodeError):
        return False
    m = re.search(r'^requires-python\s*=\s*"([^"]*)"', head, re.MULTILINE)
    if m is None:
        return False
    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    try:
        return SpecifierSet(m.group(1)).contains(f"{minor}.0", prereleases=True)
    except InvalidSpecifier:
        return False


def install_command_lines(
    *,
    uv: str,
    src: Path,
    venv: Path,
    extras: tuple[str, ...],
    export_file: Path,
    minor: str | None = None,
    exclude_newer: str | None = None,
) -> list[list[str]]:
    """The dependency-install commands for one release (ADR-0034 decision 1).

    With a ``uv.lock`` in the checkout whose ``requires-python`` admits the
    venv's ``minor``: export the lock with hashes, sync it into the venv with
    ``--require-hashes``, then install the project itself with ``--no-deps``.
    Otherwise (no lock, or a lock that cannot serve this minor, such as
    3ef6bbd2's on 3.14): ADR-0032's ``uv pip install -e "<src>[extras]"``.
    """
    python = str(venv / "bin" / "python")
    if lock_serves(src / "uv.lock", minor):
        export = [uv, "export", "--frozen", "--project", str(src)]
        for extra in extras:
            export += ["--extra", extra]
        export += ["--no-emit-project", "--output-file", str(export_file)]
        return [
            export,
            [uv, "pip", "sync", "--python", python, "--require-hashes", str(export_file)],
            [uv, "pip", "install", "--python", python, "--no-deps", "-e", str(src)],
        ]
    spec = f"{src}[{','.join(extras)}]" if extras else str(src)
    argv = [uv, "pip", "install", "--python", python]
    if exclude_newer:
        # Reproducible resolve: nothing uploaded after the ref's commit date.
        argv += ["--exclude-newer", exclude_newer]
    return [[*argv, "-e", spec]]


def build_web_ui(src: Path, venv: Path, *, run: Runner, env: Mapping[str, str]) -> str:
    """Build the dashboard into ``src/hermes_cli/web_dist``; return ``built``/``absent``.

    ADR-0034 decision 4 builds the UI once per release with upstream's own
    build. Both 3ef6bbd2 and v0.22.2 expose ``hermes_cli.main._build_web_ui``,
    so the step is the same at both refs; what differs is what that function
    needs. v0.22.2 ships ``scripts/build/web.mjs`` and fetches its own pinned
    Node, so a failure there fails the release. 3ef6bbd2 has no build script
    and needs a system npm, which hal0 never required, so a failure there
    records ``absent`` with a warning and the agent keeps building at start, as
    it does today.
    """
    web = src / "web"
    dist_index = src / "hermes_cli" / "web_dist" / "index.html"
    if not (web / "package.json").is_file():
        return "absent"
    ships_build_script = (src / "scripts" / "build" / "web.mjs").is_file()
    if ships_build_script and _lazy_installs_disabled(env.get("HERMES_HOME")):
        raise ReleaseError(
            "web-build",
            "security.allow_lazy_installs is false in "
            f"{env.get('HERMES_HOME')}/config.yaml, so Hermes's dashboard build cannot "
            "fetch its pinned Node and npm packages. Remove that setting (and any "
            "`security.allow_lazy_installs: false` in /etc/hal0/agents/hermes/overrides.yaml), "
            "then run `sudo hal0 agent reprovision hermes`",
        )
    error = ""
    try:
        proc = run(
            [str(venv / "bin" / "python"), "-I", "-c", _WEB_BUILD_SNIPPET, str(web)],
            cwd=src,
            env=env,
        )
        ok = proc.returncode == 0
        if not ok:
            error = _tail(proc.stderr) or _tail(proc.stdout)
    except (OSError, subprocess.SubprocessError) as exc:
        ok, error = False, str(exc)
    if ok and dist_index.is_file():
        return "built"
    if ships_build_script:
        raise ReleaseError("web-build", error or f"no {dist_index} after the build")
    log.warning("hermes_release.web_build_skipped", src=str(src), error=error[:300])
    return "absent"


def verify_release(
    venv: Path,
    *,
    run: Runner,
    probe: Callable[[Path], Mapping[str, Any]],
    env: Mapping[str, str],
    src: Path | None = None,
    ref: str | None = None,
) -> None:
    """Decision 6's release checks, offline-safe per ADR-0034 decision 3."""
    if src is not None and ref is not None:
        head = _must(run, "verify-ref", ["git", "-C", src, "rev-parse", "HEAD"], env=env)
        if head.stdout.strip() != ref:
            raise ReleaseError(
                "verify-ref", f"checkout is at {head.stdout.strip()}, expected {ref}"
            )
    python = venv / "bin" / "python"
    _must(run, "verify-import", [python, "-I", "-c", _IMPORT_CHECK], env=env, timeout=120)
    _must(run, "verify-help", [venv / "bin" / "hermes", "--help"], env=env, timeout=120)
    try:
        verdict = probe(python)
    except Exception as exc:  # a probe that cannot run fails the check
        raise ReleaseError("verify-mcp", str(exc)) from exc
    if not verdict.get("ok"):
        raise ReleaseError("verify-mcp", str(verdict.get("error") or verdict))


def _default_probe(python: Path) -> Mapping[str, Any]:
    return hp._probe_mcp_client(python, None, agent_id="hermes")


def _remove_release(layout: Layout, rdir: Path, *, run: Runner, env: Mapping[str, str]) -> None:
    """Delete one release: its worktree registration first, then the directory."""
    if (rdir / "src").exists() and (layout.mirror / "HEAD").is_file():
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            run(
                [
                    "git",
                    "-C",
                    str(layout.mirror),
                    "worktree",
                    "remove",
                    "--force",
                    str(rdir / "src"),
                ],
                env=env,
            )
    shutil.rmtree(rdir, ignore_errors=True)
    if (layout.mirror / "HEAD").is_file():
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            run(["git", "-C", str(layout.mirror), "worktree", "prune"], env=env)


def build_release(
    ref: str,
    *,
    layout: Layout,
    pin: Pin,
    python: str,
    uv: str,
    run: Runner = _run,
    probe: Callable[[Path], Mapping[str, Any]] = _default_probe,
    hermes_home: Path | None = None,
    stamp: Callable[[], str] = _utc_stamp,
    minor: str | None = None,
    platform_extras: tuple[str, ...] | list[str] = (),
) -> Release:
    """Build and verify a new release of ``ref``; ``release.json`` is written last.

    ``platform_extras`` are the gateway platforms' upstream extras
    (:mod:`hal0.agents.hermes_platforms`). They install only through the lock,
    hash-checked, next to the pin's extras; the no-lock fallback, which
    resolves unhashed from PyPI, installs the pin's extras alone.

    ``minor`` is the interpreter minor ``python`` was resolved for (from the
    vetted ``requires-python`` record). The checkout's own ``requires-python``
    must lead to the same minor, or the build stops before installing: hal0
    never installs a ref on an interpreter its metadata excludes.

    A failure removes the half-built directory and raises
    :class:`ReleaseError`; the live release is untouched.
    """
    env = build_env(hermes_home=hermes_home, layout=layout)
    layout.releases.mkdir(parents=True, exist_ok=True)
    rid = _new_release_id(layout, ref[:12], stamp())
    rdir = layout.releases / rid
    src, venv = rdir / "src", rdir / "venv"
    rdir.mkdir()
    try:
        _ensure_mirror(layout, pin.url, run=run, env=env)
        _must(
            run,
            "fetch",
            [
                "git",
                "-C",
                layout.mirror,
                "fetch",
                "--quiet",
                "--filter=blob:none",
                "--tags",
                "--force",
                "origin",
                ref,
            ],
            env=env,
        )
        _must(
            run,
            "worktree",
            ["git", "-C", layout.mirror, "worktree", "add", "--detach", src, ref],
            env=env,
        )
        requires_python = _checkout_requires_python(src)
        try:
            checkout_minor = hp.hermes_target_minor(ref, requires_python)
        except ValueError as exc:
            raise ReleaseError("python", f"{ref[:12]}: {exc}") from exc
        if minor is not None and checkout_minor != minor:
            raise ReleaseError(
                "python",
                f"{ref[:12]} declares requires-python {requires_python!r} (minor "
                f"{checkout_minor}), but its vetted record says {minor}; fix "
                "VETTED_HERMES_REQUIRES_PYTHON",
            )
        minor = checkout_minor
        _must(run, "venv", [uv, "venv", "--quiet", "--python", python, venv], env=env)
        locked = lock_serves(src / "uv.lock", minor)
        exclude_newer = None
        if not locked:
            # The no-lock fallback resolves from PyPI; pin the resolve to the
            # ref's commit date so a rebuild of the same ref is reproducible.
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                proc = run(["git", "-C", str(src), "log", "-1", "--format=%cI", ref], env=env)
                if proc.returncode == 0:
                    exclude_newer = proc.stdout.strip() or None
        export_file = rdir / "requirements.lock.txt"
        extras, applied = (
            release_extras(pin.extras, platform_extras, src) if locked else (pin.extras, ())
        )
        for argv in install_command_lines(
            uv=uv,
            src=src,
            venv=venv,
            extras=extras,
            export_file=export_file,
            minor=minor,
            exclude_newer=exclude_newer,
        ):
            _must(run, "install", argv, env=env)
        web_dist = build_web_ui(src, venv, run=run, env=env)
        # Never against the live HERMES_HOME: `hermes --help` runs upstream's
        # boot maintenance (hermes_cli/main.py:3545-3549 -> boot_bootstrap ->
        # post_update.step_migrate_config at d923a5eb), which migrates and
        # rewrites config.yaml. A build must leave the live home alone; the
        # migration belongs to activation, after the flip-back copy.
        verify_home = rdir / VERIFY_HOME_DIR
        verify_home.mkdir()
        verify_release(
            venv,
            run=run,
            probe=probe,
            env={**env, "HERMES_HOME": str(verify_home)},
            src=src,
            ref=ref,
        )
        shutil.rmtree(verify_home, ignore_errors=True)
        describe = None
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            proc = run(
                ["git", "-C", str(src), "describe", "--tags", "--match", "v[0-9]*", "--abbrev=0"],
                env=env,
            )
            if proc.returncode == 0:
                describe = proc.stdout.strip() or None
        cfg = _pyvenv_cfg(venv)
        data = {
            "id": rid,
            "ref": ref,
            "describe": describe,
            "url": pin.url,
            "extras": list(extras),
            # The extras beyond the pin's: the gateway platforms' and those
            # carried from a lazily installed generation (hermes_platforms).
            # The next rebuild carries them again.
            "platform_extras": list(applied),
            "lock": locked,
            # Which install path ran: "lock" (uv export --frozen + pip sync
            # --require-hashes + --no-deps -e) or "editable" (-e "<src>[extras]").
            "install": "lock" if locked else "editable",
            "lock_present": (src / "uv.lock").is_file(),
            "requires_python": requires_python,
            "exclude_newer": exclude_newer,
            "python": python,
            "python_minor": _minor_of(cfg.get("version_info") or cfg.get("version")),
            "web_dist": web_dist,
            "hal0_version": hp._hal0_version_string(),
            "built_at": _utc_iso(),
            _LEGACY_KEY: False,
        }
        _atomic_json(rdir / RELEASE_JSON, data)
    except BaseException:
        _remove_release(layout, rdir, run=run, env=env)
        raise
    log.info("hermes_release.built", release=rid, ref=ref, describe=describe, web_dist=web_dist)
    return Release(id=rid, path=rdir, data=data)


# ── Units ────────────────────────────────────────────────────────────────────


class SystemdUnits:
    """Read, stop and start the two Hermes units (ADR-0032 decision 7).

    Reads use ``systemctl show``, which needs no privilege. Stop and start go
    through ``hal0-systemctl`` (``stop-agent``/``start-agent``,
    ``svc-stop``/``svc-start``) as ``hal0``, or plain ``systemctl`` as root:
    the same split ``converge_hermes`` always made. The seam's allow-list is
    unchanged; every verb used here already exists.
    """

    def __init__(self, *, run: Runner | None = None, use_seam: bool | None = None) -> None:
        self._run = run if run is not None else subprocess.run
        self._use_seam = (os.geteuid() != 0) if use_seam is None else use_seam

    def _show(self, unit: str) -> dict[str, str]:
        try:
            proc = self._run(
                ["systemctl", "show", "-p", "ActiveState", "-p", "NRestarts", unit],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return {}
        out: dict[str, str] = {}
        for line in (getattr(proc, "stdout", "") or "").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                out[key.strip()] = value.strip()
        return out

    def active(self, unit: str) -> bool:
        return self._show(unit).get("ActiveState") == "active"

    def enabled_state(self, unit: str) -> str:
        """``systemctl is-enabled`` (no privilege needed); ``unknown`` on error."""
        try:
            proc = self._run(
                ["systemctl", "is-enabled", unit],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        out = (getattr(proc, "stdout", "") or "").strip()
        return out.splitlines()[0] if out else "unknown"

    def restarts(self, unit: str) -> int | None:
        try:
            return int(self._show(unit).get("NRestarts", ""))
        except ValueError:
            return None

    def _argv(self, verb: str, unit: str) -> list[str]:
        if not self._use_seam:
            return ["systemctl", verb, unit]
        from hal0.system.seam import SEAM_BIN

        if unit == AGENT_UNIT:
            return ["sudo", "-n", SEAM_BIN, f"{verb}-agent", "hermes"]
        return ["sudo", "-n", SEAM_BIN, f"svc-{verb}", "hermes-gateway"]

    def _act(self, verb: str, unit: str) -> bool:
        argv = self._argv(verb, unit)
        try:
            proc = self._run(argv, capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("hermes_release.unit_failed", verb=verb, unit=unit, error=str(exc))
            return False
        if getattr(proc, "returncode", 1) != 0:
            log.warning(
                "hermes_release.unit_failed",
                verb=verb,
                unit=unit,
                rc=getattr(proc, "returncode", None),
                stderr=_tail(getattr(proc, "stderr", ""), 300),
            )
            return False
        return True

    def stop(self, unit: str) -> bool:
        return self._act("stop", unit)

    def start(self, unit: str) -> bool:
        return self._act("start", unit)


class NoUnits:
    """A unit controller that sees nothing running and touches nothing."""

    def active(self, unit: str) -> bool:
        return False

    def restarts(self, unit: str) -> int | None:
        return None

    def stop(self, unit: str) -> bool:
        return True

    def start(self, unit: str) -> bool:
        return True


# ── Gateway unit heal ────────────────────────────────────────────────────────

#: What a heal that rewrote the unit or turned linger on reports.
GATEWAY_HEALED_NOTE = "Hermes gateway unit regenerated for v0.22 (linger on for hal0)"
#: What an operator runs when the heal could not.
GATEWAY_REGEN_REMEDY = "sudo hal0 agent install hermes"


@dataclass
class GatewayHeal:
    """What :func:`heal_gateway_unit` found and did."""

    reason: str | None = None
    action: str | None = None
    message: str | None = None
    error: str | None = None

    @property
    def note(self) -> str | None:
        """The line ``hal0 update`` shows for this heal, or ``None``."""
        if self.error is not None:
            return (
                f"Hermes gateway unit not regenerated ({self.reason}): {self.error}. "
                f"Run `{GATEWAY_REGEN_REMEDY}` once."
            )
        if self.action == "regenerated":
            return GATEWAY_HEALED_NOTE
        return None


def heal_gateway_unit(
    layout: Layout,
    *,
    gateway_unit: Path = GATEWAY_UNIT_PATH,
    job_id: str | None = None,
    seam_factory: Callable[[], Any] | None = None,
    regen_reason: Callable[[], str | None] | None = None,
) -> GatewayHeal:
    """Regenerate a stale gateway unit through ``hal0-update hermes-gateway`` (#2615, #2894).

    Only once ``current`` is v0.22-shape: its checkout has the
    ``hermes_bootstrap`` module hal0's launcher imports. The cheap
    unprivileged probe (:func:`hal0.system.linger.gateway_unit_regen_reason`)
    runs first, so a box whose unit is current with linger on, or whose
    gateway is off, never calls sudo. The root verb takes no arguments,
    re-checks everything itself, renders the unit from fixed paths, validates
    it and never runs or reads the Hermes release (ADR-0034 decision 6).
    Never raises: a failure lands in :attr:`GatewayHeal.error`.
    """
    from hal0.system import linger

    try:
        if not (layout.current / "src" / "hermes_bootstrap.py").is_file():
            return GatewayHeal()
        if regen_reason is not None:
            reason = regen_reason()
        else:
            reason = linger.gateway_unit_regen_reason(
                unit=str(gateway_unit),
                in_use=lambda: linger.gateway_in_use(unit_dir=str(gateway_unit.parent)),
            )
    except Exception as exc:  # the probe is read-only; never fail the caller
        log.warning("components.hermes_gateway_probe_failed", job_id=job_id, error=str(exc))
        return GatewayHeal()
    if reason is None:
        return GatewayHeal()
    try:
        if seam_factory is None:
            from hal0.updater.privileged import UpdateSeam

            seam = UpdateSeam(job_id=job_id)
        else:
            seam = seam_factory()
        outcome = seam.hermes_gateway()
    except Exception as exc:  # non-fatal by design: the note names the remedy
        message = str(getattr(exc, "message", "") or exc)
        log.warning(
            "components.hermes_gateway_regen_failed",
            job_id=job_id,
            reason=reason,
            error=message,
            remedy=GATEWAY_REGEN_REMEDY,
        )
        return GatewayHeal(reason=reason, error=message)
    outcome = outcome if isinstance(outcome, dict) else {}
    heal = GatewayHeal(reason=reason, action=outcome.get("action"), message=outcome.get("message"))
    log.info(
        "components.hermes_gateway_regen",
        job_id=job_id,
        reason=reason,
        action=heal.action,
        message=heal.message,
    )
    return heal


def _default_gateway_heal(gateway_unit: Path) -> Callable[[Layout], str | None]:
    def heal(layout: Layout) -> str | None:
        return heal_gateway_unit(layout, gateway_unit=gateway_unit).note

    return heal


# ── Activation ───────────────────────────────────────────────────────────────


@dataclass
class Activation:
    ok: bool
    release: Release
    outgoing: Release | None = None
    error: str | None = None
    flipped_back: bool = False
    migrated_legacy: bool = False
    restarted: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _read_stamp_ref(stamp_file: Path | None) -> str:
    if stamp_file is None:
        return "unknown"
    try:
        value = stamp_file.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    return value or "unknown"


def _prepare_legacy_release(layout: Layout, *, stamp_file: Path | None) -> Release:
    """Create the pre-layout release dir with its ``release.json``; no venv yet.

    ``release.json`` is written before the venv moves in, so a builder that
    dies between the two leaves a complete release record rather than an
    incomplete directory the next lock holder would delete with the venv in it.
    """
    ref = _read_stamp_ref(stamp_file)
    cfg = _pyvenv_cfg(layout.venv_link)
    layout.releases.mkdir(parents=True, exist_ok=True)
    rid = f"{_LEGACY_PREFIX}{ref[:12]}"
    n = 1
    while (layout.releases / rid).exists():
        rid = f"{_LEGACY_PREFIX}{ref[:12]}-{n}"
        n += 1
    rdir = layout.releases / rid
    rdir.mkdir()
    home = cfg.get("home")
    version = cfg.get("version_info") or cfg.get("version")
    minor = _minor_of(version)
    python = str(Path(home) / f"python{minor}") if home and minor else None
    data = {
        "id": rid,
        _LEGACY_KEY: True,
        "ref": ref,
        "describe": None,
        "python": python,
        "python_home": home,
        "python_version": version,
        "python_minor": minor,
        "web_dist": "absent",
        "hal0_version": hp._hal0_version_string(),
        "built_at": _utc_iso(),
    }
    _atomic_json(rdir / RELEASE_JSON, data)
    return Release(id=rid, path=rdir, data=data)


FAILED_MARKER = ".activation-failed"

#: The units an activation stopped, written before the first stop and removed
#: once they are started again. A builder that dies in between leaves it, and
#: the next lock holder starts those units (:func:`restart_stranded_units`).
STOPPED_UNITS = ".stopped-units"


def _record_stopped_units(layout: Layout, units: list[str]) -> None:
    with contextlib.suppress(OSError):
        _atomic_json(layout.root / STOPPED_UNITS, {"units": units, "at": _utc_iso()})


def _clear_stopped_units(layout: Layout) -> None:
    with contextlib.suppress(FileNotFoundError):
        (layout.root / STOPPED_UNITS).unlink()


_OFF_STATES = frozenset({"disabled", "masked", "masked-runtime"})


def complete_releases(layout: Layout) -> list[Release]:
    """Every release with a ``release.json`` and a venv interpreter, oldest first."""
    if not layout.releases.is_dir():
        return []
    out = []
    for rdir in sorted(layout.releases.iterdir()):
        release = read_release(rdir) if rdir.is_dir() else None
        if release is not None and (release.venv / "bin" / "python").exists():
            out.append(release)
    return out


def _adopt_orphaned_pre_layout_release(layout: Layout) -> bool:
    """Make the pre-layout release ``current`` when a migration died mid-switch.

    A crash after the venv moved into the pre-layout release ``venv/`` but
    before the first ``current`` swap leaves no ``venvs/hermes``, no
    ``current``, and the only working venv inside that release. Adopting it as
    ``current`` (and relinking) restores the host as it was before the
    migration; the pre-layout trigger then retries the migration normally.
    Only a pre-layout release is adopted: any other complete release was
    never activated and verified.
    """
    if layout.current.is_symlink() or layout.venv_link.exists():
        return False
    candidates = [r for r in complete_releases(layout) if r.data.get(_LEGACY_KEY)]
    if not candidates:
        return False
    adopted = candidates[-1]
    try:
        _swap_link(layout.current, Path("releases") / adopted.id)
    except OSError as exc:
        log.warning("hermes_release.adopt_failed", release=adopted.id, error=str(exc))
        return False
    log.warning("hermes_release.adopted_orphaned_release", release=adopted.id)
    return True


def restart_stranded_units(layout: Layout, units: Any) -> list[str]:
    """Start units a dead builder stopped and never started again; return them."""
    try:
        data = json.loads((layout.root / STOPPED_UNITS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names = (
        [u for u in (data.get("units") or []) if u in HERMES_UNITS]
        if isinstance(data, dict)
        else []
    )
    # #2823: never start a unit the operator has since disabled or masked,
    # even one the dead builder had recorded as running.
    enabled_state = getattr(units, "enabled_state", None)
    started: list[str] = []
    for unit in names:
        if callable(enabled_state) and enabled_state(unit) in _OFF_STATES:
            log.info("hermes_release.stranded_unit_left_off", unit=unit)
            continue
        if not units.active(unit):
            units.start(unit)
        started.append(unit)
    _clear_stopped_units(layout)
    names = started
    if names:
        log.warning("hermes_release.restarted_stranded_units", units=names)
    return names


def repair_venv_link(layout: Layout) -> bool:
    """Point ``venvs/hermes`` back at ``current/venv`` when it is missing or wrong.

    A builder that died between the pre-layout rename and the link swap
    leaves the path missing. The live release is fine, so no rebuild is
    needed. A real directory (a pre-layout venv) is never touched here.
    Returns ``True`` when the link was rewritten.
    """
    link = layout.venv_link
    if link.exists() and not link.is_symlink():
        return False
    if live_release(layout) is None and not _adopt_orphaned_pre_layout_release(layout):
        return False
    try:
        if link.is_symlink() and Path(os.readlink(link)) == layout.venv_target:
            return False
        link.parent.mkdir(parents=True, exist_ok=True)
        _swap_link(link, layout.venv_target)
    except OSError as exc:
        log.warning("hermes_release.venv_link_repair_failed", error=str(exc))
        return False
    log.warning("hermes_release.venv_link_repaired", link=str(link))
    return True


def _write_failed_marker(layout: Layout, release_id: str) -> None:
    """Record that ``release_id`` is live but failed its re-verify.

    A release is never written after its ``release.json`` exists, so the
    marker lives at the tree root. :func:`build_trigger` rebuilds while it
    names the live release; a successful activation removes it.
    """
    with contextlib.suppress(OSError):
        (layout.root / FAILED_MARKER).write_text(release_id + "\n", encoding="utf-8")


def _clear_failed_marker(layout: Layout) -> None:
    with contextlib.suppress(FileNotFoundError):
        (layout.root / FAILED_MARKER).unlink()


#: Throwaway HERMES_HOME inside a release being built, for the checks that
#: start the Hermes CLI (see build_release). Removed once they pass.
VERIFY_HOME_DIR = ".verify-home"


def _config_copy_name(new_id: str) -> str:
    return f"config.yaml.before-{new_id}"


def _restore_config(outgoing: Release, new_id: str, hermes_home: Path) -> bool:
    copy = outgoing.path / _config_copy_name(new_id)
    if not copy.is_file():
        return False
    try:
        shutil.copy2(copy, hermes_home / "config.yaml")
    except OSError as exc:
        log.warning("hermes_release.config_restore_failed", error=str(exc))
        return False
    return True


def _config_migrate(layout: Layout, *, run: Runner, hermes_home: Path) -> str | None:
    """``hermes config migrate`` from the new release; a warning string on failure."""
    if not (hermes_home / "config.yaml").is_file():
        return None
    env = build_env(hermes_home=hermes_home, layout=layout)
    try:
        proc = run(
            [str(layout.venv_link / "bin" / "hermes"), "config", "migrate"], env=env, timeout=300
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"hermes config migrate failed: {exc}"
    if proc.returncode != 0:
        return f"hermes config migrate exited {proc.returncode}: {_tail(proc.stderr, 300)}"
    return None


def _units_settled(
    units: Any,
    recorded: list[str],
    baseline: dict[str, int | None],
    settle_s: float,
    sleep: Callable[[float], None],
) -> str | None:
    if not recorded:
        return None
    sleep(settle_s)
    for unit in recorded:
        if not units.active(unit):
            return f"{unit} is not active after {settle_s:g}s"
        after = units.restarts(unit)
        if baseline.get(unit) is not None and after is not None and after != baseline[unit]:
            return f"{unit} restarted during the {settle_s:g}s settle period"
    return None


def _restore_link(link: Path, release_id: str | None) -> None:
    if release_id:
        _swap_link(link, Path("releases") / release_id)
    else:
        with contextlib.suppress(FileNotFoundError):
            link.unlink()


def _undo_switch(
    layout: Layout,
    old_current: str | None,
    old_previous: str | None,
    old_venv: Release | None,
) -> None:
    """Put links (and a half-migrated pre-layout venv) back after a failed switch."""
    with contextlib.suppress(OSError):
        _restore_link(layout.current, old_current)
        _restore_link(layout.previous, old_previous)
    if old_venv is None:
        return
    with contextlib.suppress(OSError):
        if old_venv.venv.is_dir() and not layout.venv_link.is_dir():
            if layout.venv_link.is_symlink():
                layout.venv_link.unlink()
            os.rename(old_venv.venv, layout.venv_link)
        if not old_venv.venv.exists():
            shutil.rmtree(old_venv.path, ignore_errors=True)


def activate_release(
    new: Release,
    *,
    layout: Layout,
    units: Any,
    hermes_home: Path,
    run: Runner = _run,
    probe: Callable[[Path], Mapping[str, Any]] = _default_probe,
    settle_s: float = DEFAULT_SETTLE_S,
    stamp_file: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    heal_gateway: Callable[[], str | None] | None = None,
) -> Activation:
    """Stop, switch, migrate, start, re-verify; flip back on failure (decision 7).

    Handles the first migration too (decision 11): when ``venvs/hermes`` is
    still a real directory, it moves into the pre-layout release ``venv/``
    while both units are stopped, and becomes the flip-back target.

    Only units that were active before the switch are stopped and started, so
    a gateway the operator stopped or disabled is never started (#2823).

    ``heal_gateway`` runs after the switch, while the units are still
    stopped and before any is started or re-verified: a gateway unit that
    does not run hal0's launcher (upstream's release-local shim, #2894)
    would otherwise fail every start on the new release and force a
    flip-back. It returns a note for the outcome's warnings, or ``None``.
    """
    recorded = [u for u in HERMES_UNITS if units.active(u)]
    if recorded:
        _record_stopped_units(layout, recorded)
    try:
        return _activate(
            new,
            recorded=recorded,
            layout=layout,
            units=units,
            hermes_home=hermes_home,
            run=run,
            probe=probe,
            settle_s=settle_s,
            stamp_file=stamp_file,
            sleep=sleep,
            heal_gateway=heal_gateway,
        )
    finally:
        _clear_stopped_units(layout)


def _activate(
    new: Release,
    *,
    recorded: list[str],
    layout: Layout,
    units: Any,
    hermes_home: Path,
    run: Runner,
    probe: Callable[[Path], Mapping[str, Any]],
    settle_s: float,
    stamp_file: Path | None,
    sleep: Callable[[float], None],
    heal_gateway: Callable[[], str | None] | None = None,
) -> Activation:
    stopped: list[str] = []
    for unit in recorded:
        if not units.stop(unit):
            for done in stopped:
                units.start(done)
            return Activation(
                ok=False, release=new, error=f"could not stop {unit}; nothing switched"
            )
        stopped.append(unit)

    migrating = layout.venv_link.exists() and not layout.venv_link.is_symlink()
    old_current = _link_release_id(layout.current)
    old_previous = _link_release_id(layout.previous)
    outgoing: Release | None = None
    try:
        if migrating:
            outgoing = _prepare_legacy_release(layout, stamp_file=stamp_file)
        else:
            outgoing = live_release(layout)

        config = hermes_home / "config.yaml"
        if outgoing is not None and config.is_file():
            try:
                shutil.copy2(config, outgoing.path / _config_copy_name(new.id))
            except OSError as exc:
                log.warning("hermes_release.config_copy_failed", error=str(exc))

        if migrating:
            assert outgoing is not None
            os.rename(layout.venv_link, outgoing.venv)
        _swap_link(layout.current, Path("releases") / new.id)
        if outgoing is not None:
            _swap_link(layout.previous, Path("releases") / outgoing.id)
        if (
            not layout.venv_link.is_symlink()
            or Path(os.readlink(layout.venv_link)) != layout.venv_target
        ):
            layout.venv_link.parent.mkdir(parents=True, exist_ok=True)
            _swap_link(layout.venv_link, layout.venv_target)
    except OSError as exc:
        _undo_switch(layout, old_current, old_previous, outgoing if migrating else None)
        for unit in recorded:
            units.start(unit)
        log.warning("hermes_release.switch_failed", release=new.id, error=str(exc))
        return Activation(ok=False, release=new, error=f"switch failed, nothing activated: {exc}")

    result = Activation(ok=True, release=new, outgoing=outgoing, migrated_legacy=migrating)
    warning = _config_migrate(layout, run=run, hermes_home=hermes_home)
    if warning:
        result.warnings.append(warning)
    # The units are still stopped: hal0's must-have keys (sessions.auto_prune:
    # false first of all) go on disk before the new release first starts.
    # `hal0 update` reaches Hermes only through this activation, never
    # through the provisioning overlay.
    try:
        hp.apply_release_config_invariants(hermes_home)
    except Exception as exc:  # a broken config.yaml must not start the release
        error = f"could not write hal0's keys to {hermes_home / 'config.yaml'}: {exc}"
        log.warning("hermes_release.config_invariants_failed", release=new.id, error=error)
        result.ok = False
        result.error = error
        if outgoing is None:
            _write_failed_marker(layout, new.id)
        else:
            _flip_back(
                result,
                error,
                layout=layout,
                outgoing=outgoing,
                old_previous=old_previous,
                new_id=new.id,
                hermes_home=hermes_home,
                units=units,
                recorded=recorded,
                stop_first=False,
            )
        return result

    # #2894: put hal0's launcher in the gateway unit before the new release's
    # first start. Through the root verb (`hal0-update hermes-gateway`), which
    # never starts a unit that is stopped, as both are here.
    if heal_gateway is not None:
        try:
            note = heal_gateway()
        except Exception as exc:  # the heal reports its own failures; belt and braces
            log.warning("hermes_release.gateway_heal_crashed", release=new.id, error=str(exc))
            note = None
        if note:
            result.warnings.append(note)

    for unit in recorded:
        units.start(unit)
    baseline = {unit: units.restarts(unit) for unit in recorded}
    result.restarted = list(recorded)

    error: str | None = None
    try:
        verify_release(
            layout.venv_link,
            run=run,
            probe=probe,
            env=build_env(hermes_home=hermes_home, layout=layout),
        )
    except ReleaseError as exc:
        error = str(exc)
    if error is None:
        error = _units_settled(units, recorded, baseline, settle_s, sleep)
    if error is None:
        return result

    log.warning("hermes_release.reverify_failed", release=new.id, error=error)
    result.ok = False
    result.error = error
    if outgoing is None:
        # A first install has nothing to flip back to: the release stays live
        # and the failure is reported. The marker makes the next update or
        # provision rebuild it (build_trigger) instead of calling it converged.
        _write_failed_marker(layout, new.id)
        return result
    _flip_back(
        result,
        error,
        layout=layout,
        outgoing=outgoing,
        old_previous=old_previous,
        new_id=new.id,
        hermes_home=hermes_home,
        units=units,
        recorded=recorded,
        stop_first=True,
    )
    return result


def _flip_back(
    result: Activation,
    error: str,
    *,
    layout: Layout,
    outgoing: Release,
    old_previous: str | None,
    new_id: str,
    hermes_home: Path,
    units: Any,
    recorded: list[str],
    stop_first: bool,
) -> None:
    """Point ``current`` back at ``outgoing``, restore its config.yaml, restart its units."""
    if stop_first:
        for unit in recorded:
            units.stop(unit)
    try:
        _swap_link(layout.current, Path("releases") / outgoing.id)
        _restore_link(layout.previous, old_previous)
        result.flipped_back = True
    except OSError as exc:
        result.error = f"{error}; flip-back failed: {exc}"
        _write_failed_marker(layout, new_id)
    finally:
        copy = outgoing.path / _config_copy_name(new_id)
        had_config = copy.exists() or (hermes_home / "config.yaml").exists()
        restored = result.flipped_back and _restore_config(outgoing, new_id, hermes_home)
        if result.flipped_back and not restored and had_config:
            result.warnings.append(
                f"config.yaml was not restored from {copy}; it may carry the failed "
                "release's `hermes config migrate` changes"
            )
        for unit in recorded:
            units.start(unit)


# ── Retention ────────────────────────────────────────────────────────────────


def _read_unit(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _configured_python() -> str:
    value = os.environ.get("HAL0_HERMES_PYTHON") or ""
    if value:
        return value
    try:
        text = hp.HERMES_PYTHON_ENV.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return text.strip().removeprefix("HAL0_HERMES_PYTHON=")


def _names_release(text: str, rdir: Path) -> bool:
    """Whether ``text`` names ``rdir`` as a path (followed by ``/``, space, quote or EOL)."""
    return re.search(re.escape(str(rdir)) + r"(?=[/\s'\"]|$)", text, re.MULTILINE) is not None


def prune_releases(
    layout: Layout,
    *,
    run: Runner = _run,
    uv: str | None = None,
    gateway_unit: Path = GATEWAY_UNIT_PATH,
    retired_minors: tuple[str, ...] = ("3.12",),
) -> list[str]:
    """Keep ``current`` and ``previous``; delete the rest (decision 10).

    A release the gateway unit still names is kept, with a warning naming the
    ``sudo hal0 agent install hermes`` remedy. Returns the warnings.
    """
    env = build_env(layout=layout)
    warnings: list[str] = []
    keep = {r for r in (_link_release_id(layout.current), _link_release_id(layout.previous)) if r}
    unit_text = _read_unit(gateway_unit)
    if layout.releases.is_dir():
        for rdir in sorted(layout.releases.iterdir()):
            if not rdir.is_dir() or rdir.name in keep:
                continue
            if _names_release(unit_text, layout.releases / rdir.name):
                warnings.append(
                    f"kept release {rdir.name}: {gateway_unit} still names it — run "
                    "`sudo hal0 agent install hermes` to regenerate and normalise the unit"
                )
                keep.add(rdir.name)
                continue
            _remove_release(layout, rdir, run=run, env=env)
    if uv is not None:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            run([uv, "cache", "prune"], env=env, timeout=600)
        managed = layout.python_dir
        kept = [read_release(layout.releases / rid) for rid in keep]
        configured = _configured_python()
        live = live_release(layout)
        live_minor = live.python_minor if live is not None else None
        for minor in retired_minors:
            if minor == live_minor:
                continue
            if not any(managed.glob(f"cpython-{minor}*")):
                continue
            in_use = any(
                r is not None
                and r.python_minor == minor
                and str(r.data.get("python") or r.data.get("python_home") or "").startswith(
                    str(managed)
                )
                for r in kept
            )
            # A configured HAL0_HERMES_PYTHON pointing at an interpreter of
            # this minor in the managed tree keeps it; one on the target
            # minor does not.
            if in_use or configured.startswith(str(managed / f"cpython-{minor}")):
                continue
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                run([uv, "python", "uninstall", minor], env=env, timeout=300)
    return warnings


def sweep_incomplete(layout: Layout, *, run: Runner = _run) -> list[str]:
    """Delete releases without ``release.json`` (a dead builder's leftovers).

    Only a lock holder calls this, so an incomplete release seen here is not
    being built by anyone.
    """
    removed: list[str] = []
    if not layout.releases.is_dir():
        return removed
    keep = {r for r in (_link_release_id(layout.current), _link_release_id(layout.previous)) if r}
    env = build_env(layout=layout)
    for rdir in sorted(layout.releases.iterdir()):
        if rdir.is_dir() and rdir.name not in keep and not (rdir / RELEASE_JSON).exists():
            _remove_release(layout, rdir, run=run, env=env)
            removed.append(rdir.name)
    return removed


# ── The one entry point ──────────────────────────────────────────────────────


def build_trigger(
    layout: Layout,
    ref: str,
    minor: str,
    platform_extras: tuple[str, ...] | list[str] = (),
    *,
    hermes_home: Path | str | None = None,
) -> str | None:
    """Why a build is needed, or ``None`` (ADR-0032 decision 6's three triggers).

    A fourth (ADR-0034 decision 1's amendment): a configured platform's extra
    (``platform_extras``) is missing from the live venv. Packages an operator
    installed by hand count as present. Only a lock-built release is judged,
    since only the lock path installs platform extras, and only extras its
    checkout declares, or the trigger would fire on every update.

    A fifth: Hermes boots the live release into a dependency generation its
    own lazy installer committed under ``HERMES_HOME/installs``
    (:func:`hermes_platforms.committed_generation`), so what runs is not the
    verified release venv. A rebuild gets a new checkout path, hence a new
    install key with no generation, and carries the generation's platform SDKs.
    """
    if layout.venv_link.exists() and not layout.venv_link.is_symlink():
        return "pre-layout venv: venvs/hermes is a real directory"
    live = live_release(layout)
    if live is None:
        return "no live release"
    link = layout.venv_link
    if not link.is_symlink() or Path(os.readlink(link)) != layout.venv_target:
        return "venvs/hermes does not point at current/venv"
    if live.legacy:  # scar-ok: ADR-0032 decision 11 names the "legacy release"
        return "the pre-layout release is live"
    try:
        failed = (layout.root / FAILED_MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        failed = ""
    if failed and failed == live.id:
        return f"the live release {live.id} failed its last re-verify"
    if live.ref != ref:
        return f"live ref {live.ref[:12]} differs from target {ref[:12]}"
    if live.python_minor != minor:
        return f"live interpreter {live.python_minor} differs from target {minor}"
    home = Path(hermes_home) if hermes_home is not None else hp.HERMES_HOME_DEFAULT
    generation = hpl.committed_generation(home, live.src)
    if generation is not None:
        return (
            f"Hermes runs the live release from its own lazily installed environment "
            f"{generation}, not from the release venv"
        )
    if platform_extras and live.data.get("lock") is True:
        defined = _checkout_extras(live.src)
        wanted = [e for e in platform_extras if defined is None or e in defined]
        missing = hpl.missing_extras(live.venv, wanted)
        if missing:
            return f"configured platform extras missing from the live release: {', '.join(missing)}"
    return None


@dataclass
class ReleaseOutcome:
    """What :func:`ensure_release` did.

    ``status`` is ``converged`` (nothing to do), ``activated``, ``busy``,
    ``offline``, ``build_failed`` or ``activation_failed``.
    """

    status: str
    reason: str | None = None
    ref: str | None = None
    release_id: str | None = None
    previous_id: str | None = None
    error: str | None = None
    remedy: str | None = None
    holder: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    flipped_back: bool = False
    migrated_legacy: bool = False

    @property
    def ok(self) -> bool:
        return self.status in ("converged", "activated")

    @property
    def changed(self) -> bool:
        return self.status == "activated"


def offline_refusal(reason: str, ref: str, url: str) -> str:
    """The ``--offline`` refusal, naming the first step that needs the network."""
    return (
        f"--offline: a Hermes build is needed ({reason}), but its first step, fetch "
        f"(git fetch of {ref[:12]} from {url} into mirror.git), needs the network, "
        "as do the dependency install from PyPI and the dashboard build. Nothing was "
        "changed; the live release, if any, keeps running. Re-run without --offline."
    )


def write_stamp(release: Release, stamp_file: Path | None) -> str | None:
    """Write the ``venv.pin`` stamp from ``release.json``; a warning on failure."""
    if stamp_file is None:
        return None
    try:
        stamp_file.parent.mkdir(parents=True, exist_ok=True)
        stamp_file.write_text(release.ref[:12] + "\n", encoding="utf-8")
    except OSError as exc:
        return f"venv.pin stamp not written: {exc}"
    return None


def _default_stamp_file() -> Path:
    from hal0.components.hermes_arm import stamp_path

    return stamp_path()


def _drop_unlive(layout: Layout, new: Release, *, run: Runner, gateway_unit: Path) -> None:
    if _link_release_id(layout.current) == new.id or _link_release_id(layout.previous) == new.id:
        return
    if _names_release(_read_unit(gateway_unit), layout.releases / new.id):
        return
    _remove_release(layout, new.path, run=run, env=build_env(layout=layout))


def _migration_blocked(layout: Layout) -> str | None:
    """Why the first migration cannot move the pre-layout venv, or ``None``.

    ``rename()`` of a directory to a new parent needs write permission on the
    directory itself (its ``..`` entry changes) and on both parents. A venv
    left root-owned by a provision from before §7.4 would therefore fail the
    switch on every update, after a full build; refuse before building.
    """
    link = layout.venv_link
    if not link.is_dir() or link.is_symlink():
        return None
    for path in (link, link.parent):
        if not os.access(path, os.W_OK):
            return (
                f"the pre-layout venv cannot be moved into the release layout: {path} is "
                "not writable by the builder (a root-owned venv from an earlier provision)"
            )
    return None


def _hal0_user_exists() -> bool:
    import pwd

    try:
        pwd.getpwnam("hal0")
    except KeyError:
        return False
    return True


def _ensure_as_hal0(**kw: Any) -> ReleaseOutcome:
    """Run :func:`ensure_release` as the hal0 user and parse its outcome."""
    payload = json.dumps({k: (str(v) if isinstance(v, Path) else v) for k, v in kw.items()})
    inner = [
        "env",
        "-u",
        "HERMES_HOME",
        f"HOME={hp._hal0_service_home()}",
        sys.executable,
        "-m",
        "hal0.agents.hermes_release",
        payload,
    ]
    if shutil.which("runuser"):
        cmd = ["runuser", "-u", "hal0", "--", *inner]
    elif shutil.which("setpriv"):
        cmd = ["setpriv", "--reuid", "hal0", "--regid", "hal0", "--init-groups", "--", *inner]
    else:
        cmd = ["sudo", "-n", "-u", "hal0", "--", *inner]
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, our own module
            cmd,
            stdout=subprocess.PIPE,
            text=True,
            check=False,
            timeout=6 * _STEP_TIMEOUT_S,
            # #2883: sudo leaves root's cwd (/root, 0700) on the child, and uv
            # opens ./uv.toml from its cwd upward, so every uv the child runs
            # (venv, python find) died on "Permission denied". Also keeps the
            # caller's cwd off the child's sys.path[0] (python -m).
            cwd="/",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ReleaseOutcome(
            status="build_failed", error=f"drop to hal0: {exc}", remedy=REPAIR_REMEDY
        )
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and "status" in data:
            fields = {f for f in ReleaseOutcome.__dataclass_fields__}
            return ReleaseOutcome(**{k: v for k, v in data.items() if k in fields})
    return ReleaseOutcome(
        status="build_failed",
        error=f"the hal0 builder child exited {proc.returncode} without an outcome",
        remedy=REPAIR_REMEDY,
    )


def ensure_release(
    *,
    caller: str,
    venv: Path | str | None = None,
    requirements: Path | None = None,
    ref: str | None = None,
    force: bool = False,
    reason: str | None = None,
    wait: bool = True,
    on_wait: Callable[[dict[str, Any]], None] | None = None,
    offline: bool = False,
    hermes_home: Path | str | None = None,
    units: Any = None,
    run: Runner = _run,
    probe: Callable[[Path], Mapping[str, Any]] = _default_probe,
    python_resolver: Callable[[str], str | None] | None = None,
    uv: str | None = None,
    stamp_file: Path | None = None,
    gateway_unit: Path = GATEWAY_UNIT_PATH,
    settle_s: float = DEFAULT_SETTLE_S,
    sleep: Callable[[float], None] = time.sleep,
    drop_root: bool = True,
    platform_extras: list[str] | tuple[str, ...] | None = None,
    secrets_env: Path | str | None = None,
    heal_gateway: Callable[[Layout], str | None] | None = None,
) -> ReleaseOutcome:
    """Converge the live Hermes release onto ``ref`` (default: the pin).

    ``force`` builds even when no trigger applies (``--repair``, a failed MCP
    client probe, ``hal0 agent upgrade``). ``wait=False`` returns ``busy``
    instead of waiting for the lock.

    Builds run as ``hal0`` (§7.4). A root caller (``sudo hal0 update`` reaching
    the converge or repair pass) re-runs this function as ``hal0`` in a child
    process instead of building root-owned releases the next hal0 build could
    neither update nor prune.

    ``platform_extras`` defaults to :func:`configured_extras`, read here before
    any drop to hal0, because only root can read the gateway's secrets vault.
    A build installs them together with the extras the live venv already has
    (:func:`hermes_platforms.installed_platform_extras`), so a rebuild never
    drops a platform SDK the box was running with.

    ``heal_gateway`` runs inside activation, before the new release's units
    start (#2894). It defaults to :func:`heal_gateway_unit` on
    ``gateway_unit`` whenever real units are controlled; :class:`NoUnits`
    (nothing is started) skips it.
    """
    if platform_extras is None:
        platform_extras = configured_extras(hermes_home, secrets_env)
    platform_extras = list(platform_extras)
    if drop_root and os.geteuid() == 0 and _hal0_user_exists():
        return _ensure_as_hal0(
            caller=caller,
            venv=venv,
            requirements=requirements,
            ref=ref,
            force=force,
            reason=reason,
            wait=wait,
            offline=offline,
            hermes_home=hermes_home,
            no_units=isinstance(units, NoUnits),
            print_wait=on_wait is not None,
            platform_extras=platform_extras,
        )
    layout = Layout.for_venv(venv if venv is not None else hp.HERMES_VENV_DEFAULT)
    home = Path(hermes_home) if hermes_home is not None else hp.HERMES_HOME_DEFAULT
    try:
        pin = read_pin(requirements)
        target = require_vetted(ref or pin.ref)
        # Every builder (install, update, upgrade --to, bootstrap --ref) passes
        # here, so this is where an older vetted ref over a newer release's
        # state.db is refused (upstream #140060, manual rollback in #2805).
        refuse_downgrade(target, live=live_ref(layout))
    except ReleaseError as exc:
        return ReleaseOutcome(status="build_failed", error=str(exc), remedy=REPAIR_REMEDY)
    stamp = stamp_file if stamp_file is not None else _default_stamp_file()
    units = units if units is not None else SystemdUnits()
    if heal_gateway is None and not isinstance(units, NoUnits):
        heal_gateway = _default_gateway_heal(gateway_unit)

    try:
        with builder_lock(layout, caller=caller, wait=wait, on_wait=on_wait):
            sweep_incomplete(layout, run=run)
            # Relink first, so units a dead builder stopped start on a
            # resolvable venvs/hermes.
            relinked = repair_venv_link(layout)
            stranded = restart_stranded_units(layout, units)
            try:
                minor = target_minor(target)
            except ReleaseError as exc:
                return ReleaseOutcome(
                    status="build_failed", ref=target, error=str(exc), remedy=REPAIR_REMEDY
                )
            trigger = build_trigger(layout, target, minor, platform_extras, hermes_home=home)
            if trigger is None and not force:
                notes = []
                if relinked:
                    notes.append("venvs/hermes was missing or wrong; relinked to current/venv")
                if stranded:
                    notes.append(
                        f"restarted units a dead builder left stopped: {', '.join(stranded)}"
                    )
                return ReleaseOutcome(status="converged", ref=target, warnings=notes)
            why = trigger or reason or "forced rebuild"
            if offline:
                return ReleaseOutcome(
                    status="offline",
                    reason=why,
                    ref=target,
                    error=offline_refusal(why, target, pin.url),
                )
            blocked = _migration_blocked(layout)
            if blocked:
                return ReleaseOutcome(
                    status="build_failed",
                    reason=why,
                    ref=target,
                    error=blocked,
                    remedy="sudo hal0 doctor perms --fix, or sudo hal0 agent reprovision hermes",
                )
            resolver = (
                python_resolver
                if python_resolver is not None
                else (lambda m: hp._ensure_supported_python(minor=m))
            )
            try:
                python = resolver(minor)
            except (RuntimeError, ValueError, OSError) as exc:
                python, py_error = None, str(exc)
            else:
                py_error = hp._python_range_error(minor)
            if not python:
                return ReleaseOutcome(
                    status="build_failed",
                    reason=why,
                    ref=target,
                    error=f"python: {py_error}",
                    remedy="sudo hal0 doctor perms --fix, then " + REPAIR_REMEDY,
                )
            uv_bin = uv or find_uv()
            if uv_bin is None:
                return ReleaseOutcome(
                    status="build_failed",
                    reason=why,
                    ref=target,
                    error="uv: not found next to the running interpreter or on PATH",
                    remedy="reinstall hal0 (uv ships in its venv), then " + REPAIR_REMEDY,
                )
            # Carried into the new release: the live venv's platform SDKs (the
            # pre-layout venv's too), whether a lazy install or the operator put
            # them there; the extras the live release itself carried; and every
            # extra upstream recorded for a lazily installed generation (a TTS
            # engine or search provider too). Without the last, a rebuild drops
            # the feature, its next use installs it lazily again, and every
            # update rebuilds once more (#2876).
            carried: set[str] = set()
            if layout.venv_link.exists():
                carried.update(hpl.installed_platform_extras(layout.venv_link))
            live = live_release(layout)
            if live is not None:
                recorded = live.data.get("platform_extras")
                if isinstance(recorded, list):
                    carried.update(e for e in recorded if isinstance(e, str))
                generation = hpl.committed_generation(home, live.src)
                if generation is not None:
                    carried.update(hpl.installed_platform_extras(generation))
                    carried.update(hpl.generation_extras(home, live.src))
            build_extras = sorted({*platform_extras, *carried} - hpl.NEVER_CARRY)
            log.info(
                "hermes_release.build_start",
                caller=caller,
                ref=target,
                reason=why,
                platform_extras=build_extras,
            )
            try:
                new = build_release(
                    target,
                    layout=layout,
                    pin=pin,
                    python=python,
                    uv=uv_bin,
                    run=run,
                    probe=probe,
                    hermes_home=home,
                    minor=minor,
                    platform_extras=build_extras,
                )
            except (ReleaseError, OSError) as exc:
                step = getattr(exc, "step", "build")
                log.warning("hermes_release.build_failed", caller=caller, step=step, error=str(exc))
                return ReleaseOutcome(
                    status="build_failed",
                    reason=why,
                    ref=target,
                    error=str(exc),
                    remedy="sudo hal0 doctor perms --fix, then " + REPAIR_REMEDY,
                )
            try:
                act = activate_release(
                    new,
                    layout=layout,
                    units=units,
                    hermes_home=home,
                    run=run,
                    probe=probe,
                    settle_s=settle_s,
                    stamp_file=stamp,
                    sleep=sleep,
                    heal_gateway=(
                        functools.partial(heal_gateway, layout) if heal_gateway else None
                    ),
                )
            except OSError as exc:
                log.warning("hermes_release.activation_crashed", release=new.id, error=str(exc))
                _drop_unlive(layout, new, run=run, gateway_unit=gateway_unit)
                return ReleaseOutcome(
                    status="activation_failed",
                    reason=why,
                    ref=target,
                    release_id=new.id,
                    error=f"activation: {exc}",
                    remedy=REPAIR_REMEDY,
                )
            out = ReleaseOutcome(
                status="activated" if act.ok else "activation_failed",
                reason=why,
                ref=target,
                release_id=new.id,
                previous_id=act.outgoing.id if act.outgoing else None,
                error=act.error,
                remedy=None if act.ok else REPAIR_REMEDY,
                warnings=list(act.warnings),
                flipped_back=act.flipped_back,
                migrated_legacy=act.migrated_legacy,
            )
            if act.flipped_back and act.outgoing is not None:
                lacking = hpl.missing_extras(act.outgoing.venv, platform_extras)
                if lacking:
                    out.warnings.append(
                        f"the flipped-back release {act.outgoing.id} lacks the configured "
                        f"platform extras {', '.join(lacking)}; the next update rebuilds it"
                    )
            if act.ok:
                _clear_failed_marker(layout)
                warning = write_stamp(new, stamp)
                if warning:
                    out.warnings.append(warning)
                out.warnings += prune_releases(
                    layout, run=run, uv=uv_bin, gateway_unit=gateway_unit
                )
            else:
                # A failed release that is not live (flipped back, never
                # switched, or a stop that failed) is dropped unless the
                # gateway unit names it; otherwise every failed update would
                # leave another release behind.
                _drop_unlive(layout, new, run=run, gateway_unit=gateway_unit)
            log.info(
                "hermes_release.activation",
                caller=caller,
                release=new.id,
                status=out.status,
                error=out.error,
            )
            return out
    except HermesBusy as exc:
        return ReleaseOutcome(status="busy", ref=target, holder=exc.holder, error=str(exc))


def main(argv: list[str] | None = None) -> int:
    """``python -m hal0.agents.hermes_release '<json kwargs>'`` (the hal0 child)."""
    import dataclasses

    args = list(sys.argv[1:] if argv is None else argv)
    kw = json.loads(args[0]) if args else {}
    no_units = bool(kw.pop("no_units", False))
    print_wait = bool(kw.pop("print_wait", False))

    def _on_wait(holder: dict[str, Any]) -> None:
        # stderr: the parent captures only stdout (the outcome line).
        print(
            f"waiting for the Hermes builder lock, held by {describe_holder(holder)} …",
            file=sys.stderr,
            flush=True,
        )

    out = ensure_release(
        units=NoUnits() if no_units else None,
        drop_root=False,
        on_wait=_on_wait if print_wait else None,
        **kw,
    )
    print(json.dumps(dataclasses.asdict(out)), flush=True)
    return 0 if out.ok else 1


if __name__ == "__main__":  # pragma: no cover — exercised through main()
    raise SystemExit(main())
