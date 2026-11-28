"""The ``memory-decision`` component: the local Hindsight decision model server (#3757).

``[memory.decision]`` with ``provider = "local"`` sends the engine's decision
calls (consolidation triage, reflect sufficiency, page delta gating) to
``hs-decide-1-flash`` served on CPU by ``hal0-hs-decide``. Nothing installed
that server before this module, so the lane was off on every box that had not
built a venv by hand.

This module is the single owner of the pins (model revision, PyTorch index,
exact package versions) and of the build. It is opt-in: the component
converges only when the operator asks (``hal0 memory decision install`` or
``hal0 update component memory-decision``); a plain ``hal0 update``
re-converges it only on a box where hal0 installed it.

Layout (all ``hal0``-owned, under ``${var_lib}/memory``):

* ``hindsight-decision/.venv`` — the server's venv. ``.venv/hal0-build.json``
  records which pins built it; the venv is rebuilt when they change.
* ``hindsight-decision/hs_decide_serve.py`` — the server script, copied from
  ``packaging/hindsight-decision`` on every converge.
* ``hindsight/hf-cache/hub/models--hindsight-ai--hs-decide-1-flash/snapshots/<rev>``
  — the pinned model snapshot, in the engine's own Hugging Face cache. Its
  ``serving/`` package is installed into the venv, so it is code from the
  Hugging Face repo that runs as ``hal0``; the full-sha revision pin is what
  fixes which code that is.

Install states (:func:`install_state`): ``not-installed`` (no unit file),
``unmanaged`` (a unit but no hal0 build stamp, e.g. a hand install) and
``managed``. The API, the CLI, the doctor row and the update table all read
this one function.

A converge is one transaction over the venv, the server script and the unit:
the replacement venv is built at ``.venv.new`` while the live one serves, and
any failure after the first change puts all three back (the previous unit is
re-rendered through the seam), restarts the previous server and checks its
``/health``. With no previous install a failure leaves no unit and the lane
off. One converge at a time: a second one finds the lock held and returns
``busy``. The whole converge is bounded by :data:`CONVERGE_BUDGET_S`.

Privileges: hal0-api runs this as the ``hal0`` user. The unit file and the
service verbs go through :class:`hal0.system.seam.SystemCtlSeam`:
``write-hs-decide-unit`` renders the unit root-side from four validated
numbers (it takes no unit text), ``remove-hs-decide-unit`` deletes that one
literal path, and ``svc-<verb> hs-decide`` reaches only
``hal0-hs-decide.service``. Run as root (``sudo hal0 memory decision
install``, or the installer's update pass), the build steps AND every file
write under the hal0-owned directories drop to ``hal0``
(:mod:`hal0.memory.decision_fs`); root itself only writes the unit.

Never raises for operational failures: every entry point returns a status
dict.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import structlog

from hal0.config.paths import var_lib
from hal0.memory import decision_fs
from hal0.system.seam import (
    DECISION_UNIT_BACKUP_SUFFIX,
    DECISION_UNIT_NAME,
    DECISION_UNIT_PATH,
    SeamTimeout,
    SystemCtlSeam,
    as_hal0_argv,
    hal0_service_account,
    render_decision_unit,
)

log = structlog.get_logger(__name__)

# ── Pins: the one place they live ─────────────────────────────────────────────

#: Hugging Face repo of the decision model.
MODEL_REPO = "hindsight-ai/hs-decide-1-flash"

#: The model revision every box converges on. A full commit sha, so the
#: snapshot directory name is this exact string, and so is the ``serving/``
#: code installed from it. 2026-11-09 revision; the one the unit shipped
#: before #3757 (``b9a782058d…``) no longer matches a pull.
MODEL_REVISION = "e2c789026dfd1f7608cafb2ef6a9bb31397af802"

#: CPU-only PyTorch wheels. The torch wheel alone comes from this index
#: (``--index-url`` with ``--no-deps``): the index mirrors only some versions
#: of torch's dependencies, so they are resolved from PyPI by the next step,
#: at the versions the constraints file pins.
TORCH_INDEX_URL = "https://download.pytorch.org/whl/cpu"
#: The version the measured hand install ran (the CPU wheel is ``2.15.0+cpu``).
TORCH_VERSION = "2.15.0"

#: Top-level packages installed from PyPI after torch. torch 2.15.0 and
#: transformers 5.23.0 are the measured hand install; the rest are what
#: resolves with them. setuptools builds the model's ``serving/`` package
#: without build isolation, so it comes from this pinned set too.
PACKAGES: tuple[str, ...] = (
    "transformers==5.23.0",
    "fastapi==0.163.1",
    "uvicorn==0.54.0",
    "accelerate==1.16.0",
    "huggingface-hub==2.4.0",
    "pydantic==2.14.3",
    "setuptools==84.2.1",
)

#: Every package of the venv, transitive ones included, at an exact version
#: (``uv pip compile`` output; the file header has the command). Applied as a
#: constraints file to every pip step, and hashed into :func:`pinned_label`.
CONSTRAINTS_PATH = Path(__file__).with_name("decision_constraints.txt")

#: pip runs with these on every call: no prompts, no user or site config,
#: no environment-variable configuration.
_PIP_FLAGS = ("--isolated", "--no-input", "--disable-pip-version-check")

#: Environment names a build child may inherit; everything else is dropped
#: (no HF_TOKEN, no PIP_*, no credentials).
_ENV_ALLOW = (
    "PATH",
    "LANG",
    "LC_ALL",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
)

#: Modules the built venv must import before it is swapped in.
_IMPORT_CHECK = "import torch, transformers, fastapi, uvicorn, accelerate, hindsight_decision"

#: Prints the running interpreter's ``major.minor``.
_PYTHON_VERSION_PROBE = "import sys; print('%d.%d' % sys.version_info[:2])"
_PYTHON_VERSION_RE = re.compile(r"^[0-9]{1,2}\.[0-9]{1,3}$")

#: What a torch version probe may print: digits and dots, an optional local tag.
_TORCH_VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+){1,3}(\+[a-z0-9]{1,16})?$")

# ── Budgets ──────────────────────────────────────────────────────────────────

#: The whole converge (build, pull, start, health) is bounded by this. Every
#: step's own timeout is clipped to what is left of it.
CONVERGE_BUDGET_S = 5400.0
#: What a waiting CLI allows: the converge, then the engine restart that
#: re-points it (a few seam calls of at most 120 s each), with headroom.
#: A failure past the budget still gets this long to put the previous set back.
ROLLBACK_BUDGET_S = 900.0
CLI_WAIT_S = CONVERGE_BUDGET_S + ROLLBACK_BUDGET_S + 900.0

_VENV_TIMEOUT_S = 180.0
_PIP_TIMEOUT_S = 1800.0
_PULL_TIMEOUT_S = 1800.0
_PROBE_TIMEOUT_S = 120.0
_FS_TIMEOUT_S = 300.0
_SYSTEMCTL_TIMEOUT_S = 120.0
#: Model load on a cold page cache plus uvicorn start.
_HEALTH_POLL_TOTAL_S = 240.0
_HEALTH_POLL_STEP_S = 3.0
#: Venv ~0.7 GB + model ~1.5 GB, with headroom for the build-aside copy.
_MIN_FREE_BYTES = 4 * 1024**3

Runner = Callable[..., subprocess.CompletedProcess]
HttpGet = Callable[[str], dict[str, Any] | None]
#: What a seam call raises operationally (a stale wrapper, a sudo refusal, a wedge).
_SEAM_ERRORS = (OSError, ValueError, subprocess.SubprocessError, SeamTimeout)

STATUS_COMMAND = "hal0 memory decision status"
INSTALL_COMMAND = "hal0 memory decision install"
UNMANAGED_NOTE = (
    "an unmanaged decision server (hal0-hs-decide without a hal0 build stamp) was found; "
    f"`{INSTALL_COMMAND}` will adopt it"
)

NOT_INSTALLED = "not-installed"
UNMANAGED = "unmanaged"
MANAGED = "managed"


class _Abort(Exception):
    """A converge step failed; carries the operator-facing reason."""


#: The deadline of the converge running in this process, so every seam and
#: file call can clip its own timeout to it (:func:`_clip`).
_ACTIVE_DEADLINE: _Deadline | None = None


def _clip(timeout_s: float) -> float:
    """``timeout_s`` held to the running converge's deadline (never below 1 s)."""
    deadline = _ACTIVE_DEADLINE
    if deadline is None:
        return timeout_s
    return max(1.0, min(timeout_s, deadline.left()))


class _Deadline:
    def __init__(self, budget_s: float) -> None:
        self._end = time.monotonic() + budget_s

    def left(self) -> float:
        return self._end - time.monotonic()

    def clip(self, timeout_s: float) -> float:
        left = self._end - time.monotonic()
        if left <= 0:
            raise _Abort(f"the converge used up its {CONVERGE_BUDGET_S:.0f}s budget")
        return min(timeout_s, left)


# ── Paths ────────────────────────────────────────────────────────────────────


def decision_dir() -> Path:
    return var_lib() / "memory" / "hindsight-decision"


def venv_dir() -> Path:
    return decision_dir() / ".venv"


def server_script_path() -> Path:
    return decision_dir() / "hs_decide_serve.py"


def hf_home() -> Path:
    """The engine's Hugging Face cache; the model lives beside its embedders."""
    return var_lib() / "memory" / "hindsight" / "hf-cache"


def model_cache_dir() -> Path:
    return hf_home() / "hub" / f"models--{MODEL_REPO.replace('/', '--')}"


def snapshot_dir() -> Path:
    return model_cache_dir() / "snapshots" / MODEL_REVISION


def _build_stamp_path(venv: Path) -> Path:
    return venv / "hal0-build.json"


def packaged_server_script() -> Path:
    """``packaging/hindsight-decision/hs_decide_serve.py`` in the running tree.

    A dev checkout resolves it from the repo; a production install from the
    release tree under :func:`hal0.config.paths.usr_lib`, which ships
    ``packaging/`` (the :func:`hal0.agents.manager.installer_script_path`
    convention).
    """
    from hal0.config.paths import usr_lib

    rel = Path("packaging") / "hindsight-decision" / "hs_decide_serve.py"
    editable = Path(__file__).resolve().parents[3] / rel
    return editable if editable.is_file() else usr_lib() / rel


# ── Identity ─────────────────────────────────────────────────────────────────


def _pins_digest() -> str:
    """Hash of every pin: the index, torch, the top level, the full constraints set."""
    material = "\n".join((TORCH_INDEX_URL, f"torch=={TORCH_VERSION}", *PACKAGES, MODEL_REVISION))
    full = CONSTRAINTS_PATH.read_bytes()
    return hashlib.sha256(material.encode("utf-8") + b"\0" + full).hexdigest()[:8]


def pinned_label() -> str:
    """``<rev12> (pins <digest>)``; changes whenever any pin does."""
    return f"{MODEL_REVISION[:12]} (pins {_pins_digest()})"


def _read_build_stamp(venv: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(decision_fs.read_bytes(_build_stamp_path(venv)))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _python_version(runner: Runner, py: str) -> str | None:
    """``major.minor`` of the interpreter at ``py``, or ``None`` when it does not run."""
    try:
        proc = _run(runner, [py, "-c", _PYTHON_VERSION_PROBE], timeout=_clip(_PROBE_TIMEOUT_S))
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout.decode("utf-8", "replace") if isinstance(proc.stdout, bytes) else proc.stdout
    got = (out or "").strip()
    # The probe ran in a venv hal0's account owns: its output is checked.
    return got if proc.returncode == 0 and _PYTHON_VERSION_RE.match(got) else None


def _pyvenv_cfg_version(venv: Path) -> str | None:
    """``major.minor`` from the ``version`` line ``python -m venv`` wrote, if any."""
    try:
        text = decision_fs.read_bytes(venv / "pyvenv.cfg").decode("utf-8", "replace")
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "version":
            got = ".".join(value.strip().split(".")[:2])
            return got if _PYTHON_VERSION_RE.match(got) else None
    return None


def _runnable_file(path: Path) -> bool:
    """Whether ``path`` is a file this account may execute; ``True`` when that cannot be told."""
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return True


def _venv_python_check(
    runner: Runner, venv: Path, stamp: dict[str, Any] | None
) -> tuple[str | None, bool]:
    """Why a managed venv cannot run on the interpreter it now resolves to, and whether that is proven.

    A venv is tied to the ``major.minor`` it was built with: its packages sit
    under ``lib/pythonX.Y``. When the system Python moves (a distribution
    release upgrade), ``bin/python`` follows it and every import fails (#3960).
    The build stamp records the version; a stamp from before it did has no
    ``python`` key, and ``pyvenv.cfg`` answers for it, so an existing install
    is rebuilt only when its interpreter really changed.

    Any reason is enough to rebuild. Only a proven one lets a rollback discard
    the old venv: both versions were read and differ, or the interpreter is
    missing or not executable. A version probe that merely failed (a timeout,
    the drop to hal0's account refused) says nothing about the venv.
    """
    recorded = (stamp or {}).get("python")
    built = recorded if isinstance(recorded, str) and recorded else _pyvenv_cfg_version(venv)
    py = venv / "bin" / "python"
    now = _python_version(runner, str(py))
    if now is None:
        return "its Python interpreter does not run", not _runnable_file(py)
    if built is None:
        return "the Python version it was built with is not recorded", False
    if built != now:
        return f"it was built with Python {built} and its interpreter is now Python {now}", True
    return None, False


def _venv_python_problem(runner: Runner, venv: Path, stamp: dict[str, Any] | None) -> str | None:
    """The reason from :func:`_venv_python_check`, or ``None`` when the venv can run."""
    return _venv_python_check(runner, venv, stamp)[0]


def install_state(
    *, venv: Path | None = None, unit_path: Path | None = None
) -> tuple[str, str | None]:
    """``(state, label)`` — the one answer every surface gives (#3757).

    * ``not-installed``: no ``hal0-hs-decide`` unit file. The engine drop-in
      keys on the same file (``extraction_env.decision_unit_installed``).
    * ``unmanaged``: a unit, but no venv with a hal0 build stamp (a hand
      install). It serves; ``install`` adopts it; ``hal0 update`` leaves it.
    * ``managed``: hal0 built it; ``label`` is the pins it was built with.
    """
    unit = unit_path or DECISION_UNIT_PATH
    try:
        if not unit.is_file():
            return NOT_INSTALLED, None
    except OSError:
        return NOT_INSTALLED, None
    venv = venv or venv_dir()
    try:
        has_python = (venv / "bin" / "python").exists()
    except OSError:
        has_python = False
    stamp = _read_build_stamp(venv) if has_python else None
    label = stamp.get("label") if stamp else None
    if isinstance(label, str) and label:
        return MANAGED, label
    return UNMANAGED, None


def installed_label(*, venv: Path | None = None, unit_path: Path | None = None) -> str | None:
    """The registry's installed probe: the pins label, ``"unmanaged"``, or ``None``."""
    state, label = install_state(venv=venv, unit_path=unit_path)
    if state == MANAGED:
        return label
    return UNMANAGED if state == UNMANAGED else None


def component_installed() -> bool:
    """True when a decision server unit is installed (managed or not)."""
    return install_state()[0] != NOT_INSTALLED


# ── Running as hal0 ──────────────────────────────────────────────────────────


def _dropping() -> bool:
    """True when this process is root and must act on hal0's files as hal0."""
    return os.geteuid() == 0 and hal0_service_account() is not None


def _as_hal0(argv: list[str]) -> list[str]:
    return as_hal0_argv(argv) if _dropping() else argv


def _run(
    runner: Runner,
    argv: list[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {"capture_output": True, "timeout": timeout, "env": _child_env(env)}
    if input_bytes is None:
        kwargs["text"] = True
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = input_bytes
    return runner(_as_hal0(argv), **kwargs)


def _child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The only environment a build child sees: :data:`_ENV_ALLOW`, HOME, ``extra``."""
    from hal0.system.seam import hal0_service_home

    env = {k: os.environ[k] for k in _ENV_ALLOW if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = hal0_service_home() if _dropping() else os.environ.get("HOME", "/")
    env["PIP_CONFIG_FILE"] = os.devnull
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env.update(extra or {})
    return env


def _fs(runner: Runner, op: str, *args: str, body: bytes | None = None, mode: int = 0o644) -> None:
    """One :mod:`hal0.memory.decision_fs` operation, as hal0 when this is root."""
    if not _dropping():
        if op == "write":
            decision_fs.write_bytes(Path(args[0]), body or b"", mode)
        else:
            getattr(decision_fs, op)(*(Path(a) for a in args))
        return
    argv = [sys.executable, "-m", "hal0.memory.decision_fs", op, *args]
    if op == "write":
        argv.append(oct(mode)[2:])
    proc = _run(runner, argv, timeout=_clip(_FS_TIMEOUT_S), input_bytes=body or b"")
    if proc.returncode != 0:
        err = (
            proc.stderr.decode("utf-8", "replace")
            if isinstance(proc.stderr, bytes)
            else proc.stderr
        )
        raise OSError(f"{op} {' '.join(args)} as hal0 failed: {(err or '').strip()}")


def _tail(proc: subprocess.CompletedProcess) -> str:
    def _s(v: Any) -> str:
        return v.decode("utf-8", "replace") if isinstance(v, bytes) else (v or "")

    text = (_s(proc.stderr) + "\n" + _s(proc.stdout)).strip()
    return text[-600:] if text else f"exit {proc.returncode}"


def _http_get_json(url: str) -> dict[str, Any] | None:
    from hal0.service_identity import open_url

    try:
        with open_url(url, timeout=5.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _failed(error: str, remedy: str, **extra: Any) -> dict[str, Any]:
    log.warning("memory.decision_component_failed", error=error)
    return {"status": "build_failed", "error": error, "remedy": remedy, **extra}


def _seam_error(exc: BaseException) -> str:
    from hal0.memory.extraction_env import _detail, _stale_wrapper_hint

    return f"{_detail(exc)}{_stale_wrapper_hint(exc)}"


def _decision_config() -> Any:
    from hal0.config.loader import load_hal0_config

    return load_hal0_config().memory.decision


@contextlib.contextmanager
def _converge_lock(runner: Runner) -> Iterator[bool]:
    """Exclusive, non-blocking lock on :func:`decision_dir`; yields False when held.

    ``flock`` on the directory itself (opened ``O_NOFOLLOW``), so the lock
    creates no file anywhere.
    """
    _fs(runner, "mkdir", str(decision_dir()))
    fd = os.open(decision_dir(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                yield False
                return
            raise
        yield True
    finally:
        os.close(fd)


def _busy() -> dict[str, Any]:
    return {
        "status": "busy",
        "error": "another memory-decision converge is running; wait for it, then retry",
    }


# ── Config: provider writes ──────────────────────────────────────────────────


class Hal0TomlUnreadable(Exception):
    """hal0.toml exists but cannot be read safely; nothing was written."""


#: hal0.toml is a small text file; anything bigger is not one.
_HAL0_TOML_MAX_BYTES = 1024 * 1024


def _read_hal0_toml(target: Path) -> tuple[dict[str, Any], os.stat_result | None]:
    """``(raw, stat)`` of hal0.toml, ``({}, None)`` only when it does not exist.

    ``/etc/hal0`` is writable by the hal0 account, so a root caller must not
    follow a link, block on a FIFO or copy a foreign file's owner: the file is
    opened ``O_NOFOLLOW|O_NONBLOCK``, must be a regular file under a size cap,
    and the owner and mode come from that descriptor. Any other failure raises
    :class:`Hal0TomlUnreadable`.
    """
    import stat as stat_mod
    import tomllib

    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        raise Hal0TomlUnreadable(f"{target}: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat_mod.S_ISREG(st.st_mode):
            raise Hal0TomlUnreadable(f"{target} is not a regular file")
        if st.st_size > _HAL0_TOML_MAX_BYTES:
            raise Hal0TomlUnreadable(f"{target} is larger than {_HAL0_TOML_MAX_BYTES} bytes")
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
            if sum(map(len, chunks)) > _HAL0_TOML_MAX_BYTES:
                raise Hal0TomlUnreadable(f"{target} grew past {_HAL0_TOML_MAX_BYTES} bytes")
    except OSError as exc:
        raise Hal0TomlUnreadable(f"{target}: {exc}") from exc
    finally:
        os.close(fd)
    try:
        raw = tomllib.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise Hal0TomlUnreadable(f"{target}: {exc}") from exc
    return raw, st


def set_provider(provider: str, *, path: Path | None = None) -> bool:
    """Write ``[memory.decision].provider`` (and ``enabled = true`` for ``local``).

    A raw read-modify-write under the hal0.toml lock, so nothing else in the
    file changes and every other default stays unwritten. Keeps the file's
    mode and owner. True when the file changed. A file that exists but cannot
    be read safely raises :class:`Hal0TomlUnreadable` and is left untouched;
    only a missing file starts from empty.
    """
    import stat as stat_mod

    from hal0.config import paths
    from hal0.config.loader import hal0_config_file_lock, write_toml_atomic

    target = path or paths.hal0_toml()
    with hal0_config_file_lock(target):
        raw, st = _read_hal0_toml(target)
        memory = raw.setdefault("memory", {})
        decision = memory.setdefault("decision", {}) if isinstance(memory, dict) else None
        if not isinstance(decision, dict):
            raise Hal0TomlUnreadable(f"{target}: [memory.decision] is not a table")
        wanted = {"provider": provider, **({"enabled": True} if provider == "local" else {})}
        if all(decision.get(k) == v for k, v in wanted.items()):
            return False
        decision.update(wanted)
        if st is not None:
            write_toml_atomic(
                target, raw, mode=stat_mod.S_IMODE(st.st_mode), owner=(st.st_uid, st.st_gid)
            )
        else:
            write_toml_atomic(target, raw)
    log.info("memory.decision_provider_set", provider=provider)
    return True


# ── Build ────────────────────────────────────────────────────────────────────


def _interpreter() -> str:
    import shutil

    return shutil.which("python3") or sys.executable


def _build_venv(runner: Runner, new: Path, final: Path, deadline: _Deadline) -> None:
    """Build the server venv at ``new``; raises :class:`_Abort` with the reason.

    Every network fetch is one of: the PyTorch CPU index (torch only), PyPI
    (:data:`PACKAGES`), and Hugging Face at :data:`MODEL_REVISION`.
    """
    _fs(runner, "rmtree", str(new))
    py = str(new / "bin" / "python")
    constraints = str(CONSTRAINTS_PATH)
    pip = [py, "-m", "pip", "install", *_PIP_FLAGS, "-c", constraints]

    def step(argv: list[str], what: str, budget: float, **kw: Any) -> subprocess.CompletedProcess:
        try:
            proc = _run(runner, argv, timeout=deadline.clip(budget), **kw)
        except (OSError, subprocess.SubprocessError) as exc:
            raise _Abort(f"{what} failed: {exc}") from exc
        if proc.returncode != 0:
            raise _Abort(f"{what} failed: {_tail(proc)}")
        return proc

    step([_interpreter(), "-m", "venv", str(new)], "python3 -m venv", _VENV_TIMEOUT_S)
    python = _python_version(runner, py)
    if python is None:
        raise _Abort("the new venv's Python interpreter does not run")
    step(
        # --no-deps: the CPU index does not carry the pinned versions of torch's
        # dependencies (filelock, sympy, ...); the PyPI step below installs them.
        [*pip, "--no-deps", f"torch=={TORCH_VERSION}", "--index-url", TORCH_INDEX_URL],
        "installing CPU torch",
        _PIP_TIMEOUT_S,
    )
    proc = step(
        [py, "-c", "import importlib.metadata as m; print(m.version('torch'))"],
        "probing torch",
        _PROBE_TIMEOUT_S,
    )
    got = (proc.stdout or "").strip()
    # The probe ran in a venv hal0 controls: its output is checked, never reused.
    if not _TORCH_VERSION_RE.match(got) or got.split("+", 1)[0] != TORCH_VERSION:
        raise _Abort(f"torch probe reported {got[:40]!r}, not {TORCH_VERSION}")
    # torch is named again so pip resolves its dependencies from PyPI; the
    # installed CPU wheel already satisfies the requirement and the constraint.
    step(
        [*pip, f"torch=={TORCH_VERSION}", *PACKAGES],
        "installing the pinned packages",
        _PIP_TIMEOUT_S,
    )
    _pull_model(runner, py, deadline)
    step(
        # --no-deps: the package declares pydantic>=2.12, transformers>=5.16.1
        # and huggingface-hub>=1.0 at this revision, all already in the pinned set.
        [*pip, "--no-build-isolation", "--no-deps", str(snapshot_dir() / "serving")],
        "installing the model's serving package",
        _PIP_TIMEOUT_S,
    )
    step([py, "-c", _IMPORT_CHECK], "the built venv's import check", _PROBE_TIMEOUT_S)
    _fs(runner, "retarget", str(new), str(final))
    stamp = {
        "label": pinned_label(),
        "model_revision": MODEL_REVISION,
        "torch": TORCH_VERSION,
        "packages": list(PACKAGES),
        "python": python,
        "built_at": time.time(),
    }
    _fs(runner, "write", str(_build_stamp_path(new)), body=json.dumps(stamp).encode("utf-8"))


def _pull_model(runner: Runner, py: str, deadline: _Deadline) -> None:
    """Fetch :data:`MODEL_REVISION` into :func:`hf_home`."""
    code = (
        "import sys\n"
        "from huggingface_hub import snapshot_download\n"
        "print(snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2]))\n"
    )
    _fs(runner, "mkdir", str(hf_home()))
    try:
        proc = _run(
            runner,
            [py, "-c", code, MODEL_REPO, MODEL_REVISION],
            timeout=deadline.clip(_PULL_TIMEOUT_S),
            env={"HF_HOME": str(hf_home())},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _Abort(f"pulling {MODEL_REPO} failed: {exc}") from exc
    if proc.returncode != 0:
        raise _Abort(f"pulling {MODEL_REPO}@{MODEL_REVISION[:12]} failed: {_tail(proc)}")
    if not (snapshot_dir() / "serving").is_dir():
        raise _Abort(f"the {MODEL_REPO} snapshot at {snapshot_dir()} has no serving/ package")


# ── Service ──────────────────────────────────────────────────────────────────


def _svc(seam: SystemCtlSeam, verb: str, *, check: bool = True) -> None:
    seam.systemctl(
        "systemctl", verb, DECISION_UNIT_NAME, check=check, timeout=_clip(_SYSTEMCTL_TIMEOUT_S)
    )


def unit_active(seam: SystemCtlSeam | None = None, *, unit_path: Path | None = None) -> bool | None:
    """``systemctl is-active hal0-hs-decide``; ``None`` when systemd cannot be asked.

    ``False`` without asking when the unit file is not installed.
    """
    if not (unit_path or DECISION_UNIT_PATH).is_file():
        return False
    seam = seam if seam is not None else SystemCtlSeam()
    try:
        proc = seam.systemctl(
            "systemctl", "is-active", DECISION_UNIT_NAME, check=False, timeout=10.0
        )
    except Exception:
        return None
    state = (proc.stdout or "").strip()
    if proc.returncode == 0:
        return True
    return False if state else None


def _poll_health(
    http_get: HttpGet, port: int, total_s: float, sleep: Callable[[float], None]
) -> bool:
    url = f"http://127.0.0.1:{port}/health"
    # Counted, not clock-bound, so an injected ``sleep`` bounds the loop too.
    for attempt in range(int(total_s // _HEALTH_POLL_STEP_S) + 1):
        if attempt:
            sleep(_HEALTH_POLL_STEP_S)
        body = http_get(url)
        if body is not None and body.get("status") == "ok":
            return True
    return False


#: How far the server's window sits above the engine's request budget, in
#: eighths (10/8 = 1.25). The engine packs a decision request up to
#: ``max_request_tokens`` by its own estimate; the server counts with the
#: model's tokenizer and answers 400 ``max_tokens_exceeded`` to anything over
#: its window. With the two equal, requests the engine sized at the budget
#: arrived up to 11% over it (4558 counted against 4096) and were refused. A
#: refused prune makes reflect keep every piece of evidence it retrieved, which
#: turned one mental-model refresh into dozens of full-context model calls
#: (#3849).
_WINDOW_HEADROOM_EIGHTHS = 10

#: The seam's upper bound for ``--max-tokens`` (``_check_decision_unit_params``).
_WINDOW_MAX = 32768


def server_window(max_request_tokens: int) -> int:
    """The ``--max-tokens`` window for an engine budget of ``max_request_tokens``.

    A quarter above the budget, held to the seam's bound. At the very top of
    the setting's range the bound leaves less headroom than that.
    """
    return min(_WINDOW_MAX, -(-int(max_request_tokens) * _WINDOW_HEADROOM_EIGHTHS // 8))


def _unit_params(decision: Any) -> dict[str, Any]:
    return {
        "revision": MODEL_REVISION,
        "threads": int(decision.threads),
        "port": int(decision.port),
        "max_tokens": server_window(decision.max_request_tokens),
    }


_UNIT_MODEL_RE = re.compile(r"/snapshots/([0-9a-f]{40})\b")
_UNIT_PORT_RE = re.compile(r"--port[ =]([0-9]{1,5})\b")
_UNIT_TOKENS_RE = re.compile(r"--max-tokens[ =]([0-9]{1,6})\b")
_UNIT_THREADS_RE = re.compile(r"(?:--threads[ =]|HS_DECIDE_THREADS=)([0-9]{1,3})\b")


def parse_unit_params(text: str) -> dict[str, Any] | None:
    """The four render values of an installed unit, or ``None`` when unreadable.

    Reads hal0's own template and the shape of a hand-written unit alike;
    used to put the previous unit back through the seam after a failure.
    """
    found = [
        r.search(text) for r in (_UNIT_MODEL_RE, _UNIT_THREADS_RE, _UNIT_PORT_RE, _UNIT_TOKENS_RE)
    ]
    if found[0] is None or found[2] is None:
        return None
    params = {
        "revision": found[0].group(1),
        "threads": int(found[1].group(1)) if found[1] else 8,
        "port": int(found[2].group(1)),
        "max_tokens": int(found[3].group(1)) if found[3] else 8192,
    }
    try:
        render_decision_unit(**params)
    except ValueError:
        return None
    return params


def _read_unit(unit_path: Path) -> str | None:
    try:
        return unit_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_unit(seam: SystemCtlSeam, params: dict[str, Any], unit_path: Path) -> bool:
    """Write the unit when it differs; True when it changed. Raises seam errors."""
    if _read_unit(unit_path) == render_decision_unit(**params):
        return False
    seam.write_decision_unit(**params, path=unit_path, timeout=_clip(_SYSTEMCTL_TIMEOUT_S))
    seam.systemctl("systemctl", "daemon-reload", check=True, timeout=_clip(_SYSTEMCTL_TIMEOUT_S))
    return True


def _teardown_unit(seam: SystemCtlSeam, unit_path: Path) -> str | None:
    """Stop, disable and delete the unit; ``None`` or the first error."""
    error: str | None = None
    for verb in ("stop", "disable"):
        try:
            _svc(seam, verb, check=False)
        except _SEAM_ERRORS as exc:
            error = error or f"systemctl {verb} {DECISION_UNIT_NAME}: {_seam_error(exc)}"
    try:
        seam.remove_decision_unit(path=unit_path, timeout=_clip(_SYSTEMCTL_TIMEOUT_S))
        seam.systemctl(
            "systemctl", "daemon-reload", check=False, timeout=_clip(_SYSTEMCTL_TIMEOUT_S)
        )
    except _SEAM_ERRORS as exc:
        error = error or f"could not remove {unit_path}: {_seam_error(exc)}"
    return error


def _last_good_memory() -> Any:
    """The ``[memory]`` config as it loads now, or ``None`` when it does not."""
    try:
        from hal0.config.loader import load_hal0_config

        return load_hal0_config().memory
    except Exception:
        return None


def _reconcile_engine(seam: SystemCtlSeam, fallback: Any = None) -> dict[str, Any]:
    """Re-render the engine's showcase drop-in so it points at (or off) the server.

    When hal0.toml no longer loads, ``fallback`` (the config loaded earlier in
    this converge) is rendered instead, so the engine still stops using a
    server that is gone.
    """
    try:
        from hal0.memory.extraction_env import apply_showcase_dropin, reconcile_showcase_dropin

        result = reconcile_showcase_dropin(seam=seam)
        if (
            fallback is not None
            and result.get("status") == "skipped"
            and "hal0.toml unreadable" in str(result.get("reason"))
        ):
            applied = apply_showcase_dropin(
                models=fallback.models,
                decision=fallback.decision,
                lanes=fallback.lanes,
                graph=fallback.graph,
                seam=seam,
            )
            result = {
                "status": "failed" if applied.get("error") else "applied",
                "from_last_good_config": True,
                **applied,
            }
        return result
    except Exception as exc:  # never fail the component on the engine pass
        log.warning("memory.decision_engine_reconcile_failed", error=str(exc))
        return {"status": "failed", "error": str(exc)}


def _set_provider_safely(provider: str) -> str | None:
    try:
        set_provider(provider)
    except Hal0TomlUnreadable as exc:
        log.warning("memory.decision_provider_write_refused", provider=provider, error=str(exc))
        return (
            f"hal0.toml could not be read: {exc}; nothing was changed "
            f'([memory.decision].provider was not set to "{provider}")'
        )
    except Exception as exc:
        log.warning("memory.decision_provider_write_failed", provider=provider, error=str(exc))
        return f'could not set [memory.decision].provider = "{provider}": {exc}'
    return None


# ── Converge ─────────────────────────────────────────────────────────────────


class _Txn:
    """What this converge changed, so a failure can put all of it back."""

    def __init__(self, unit_path: Path, prev_unit: str | None) -> None:
        self.unit_path = unit_path
        self.prev_unit = prev_unit
        self.prev_params = parse_unit_params(prev_unit) if prev_unit else None
        self.venv_swapped = False
        #: Why the venv this converge started with was rebuilt for its
        #: interpreter, when it was (:func:`_venv_python_check`).
        self.prev_venv_problem: str | None = None
        #: Whether that reason is proven. A rollback never restores a venv
        #: proven dead; one whose probe merely failed is restored as usual.
        self.prev_venv_dead = False
        self.script_changed = False
        self.script_had_prev = False
        self.unit_changed = False
        #: ``[memory]`` as loaded at the start, for an engine re-render when
        #: hal0.toml stops loading mid-converge.
        self.last_good: Any = None


def converge_memory_decision(
    *,
    job_id: str | None = None,
    apply: bool = True,
    install: bool = False,
    action: str | None = None,
    purge_model: bool = False,
    seam: SystemCtlSeam | None = None,
    runner: Runner | None = None,
    http_get: HttpGet | None = None,
    sleep: Callable[[float], None] = time.sleep,
    unit_path: Path | None = None,
    budget_s: float | None = None,
) -> dict[str, Any]:
    """Converge (or, with ``action="remove"``, remove) the decision server.

    * ``apply=False`` — diagnose only.
    * not installed and not ``install`` — ``skipped``: opt-in, so a plain
      ``hal0 update`` never installs it; an ``unmanaged`` server is left alone.
    * otherwise build the venv if its pins changed or its Python interpreter
      is no longer the one it was built with, sync the server script,
      render the unit from ``[memory.decision]``, enable and (re)start it,
      wait for ``/health``; then, when asked by name (``install``), set
      ``provider = "local"``; then re-render the engine drop-in.

    ``seam``/``runner``/``http_get``/``sleep``/``unit_path`` are test seams.
    """
    seam = seam if seam is not None else SystemCtlSeam()
    runner = runner if runner is not None else subprocess.run
    http_get = http_get if http_get is not None else _http_get_json
    unit_path = unit_path or DECISION_UNIT_PATH

    state, label = install_state(unit_path=unit_path)
    pinned = pinned_label()
    if action != "remove" and not install:
        if state == NOT_INSTALLED:
            return {"status": "skipped", "reason": f"not installed (opt-in: {INSTALL_COMMAND})"}
        if state == UNMANAGED:
            return {"status": UNMANAGED, "note": UNMANAGED_NOTE}
        if not apply:
            if label != pinned:
                return {"status": "stale"}
            venv = venv_dir()
            problem = _venv_python_problem(runner, venv, _read_build_stamp(venv))
            return {"status": "stale", "reason": problem} if problem else {"status": "converged"}
    elif not apply:
        return {"status": "skipped", "reason": "diagnose only"}

    try:
        with _converge_lock(runner) as acquired:
            if not acquired:
                return _busy()
            global _ACTIVE_DEADLINE
            deadline = _Deadline(budget_s if budget_s is not None else CONVERGE_BUDGET_S)
            _ACTIVE_DEADLINE = deadline
            try:
                if action == "remove":
                    return _remove(seam, runner, unit_path, purge_model)
                return _converge(
                    seam,
                    runner,
                    http_get,
                    sleep,
                    unit_path,
                    state=state,
                    label=label,
                    install=install,
                    job_id=job_id,
                    deadline=deadline,
                )
            finally:
                _ACTIVE_DEADLINE = None
    except OSError as exc:
        return _failed(f"cannot prepare {decision_dir()}: {exc}", "check the directory, then retry")


def _converge(
    seam: SystemCtlSeam,
    runner: Runner,
    http_get: HttpGet,
    sleep: Callable[[float], None],
    unit_path: Path,
    *,
    state: str,
    label: str | None,
    install: bool,
    job_id: str | None,
    deadline: _Deadline,
) -> dict[str, Any]:
    pinned = pinned_label()
    try:
        decision = _decision_config()
    except Exception as exc:
        return _failed(
            f"hal0.toml could not be read: {exc}; nothing was changed", "fix hal0.toml, then retry"
        )

    txn = _Txn(unit_path, _read_unit(unit_path))
    txn.last_good = _last_good_memory()
    hand_copy: Path | None = None
    if state == UNMANAGED and txn.prev_unit is not None:
        # Adopting a hand install replaces the operator's unit with hal0's
        # template; write-hs-decide-unit keeps the operator's file, root-owned,
        # at a fixed path the first time (a rollback re-renders the template
        # from the old unit's values rather than restoring the file verbatim).
        hand_copy = unit_path.with_name(unit_path.name + DECISION_UNIT_BACKUP_SUFFIX)
    venv = venv_dir()
    new = venv.with_name(".venv.new")
    prev = venv.with_name(".venv.prev")
    script = server_script_path()
    script_prev = script.with_name(script.name + ".prev")
    if state == MANAGED:
        txn.prev_venv_problem, txn.prev_venv_dead = _venv_python_check(
            runner, venv, _read_build_stamp(venv)
        )
    needs_build = state != MANAGED or label != pinned or txn.prev_venv_problem is not None
    if state == UNMANAGED:
        stamp = _read_build_stamp(venv)
        needs_build = not stamp or stamp.get("label") != pinned
    remedy_build = (
        f"check network access to PyPI, {TORCH_INDEX_URL} and huggingface.co, then "
        f"retry with: {INSTALL_COMMAND}"
    )

    if needs_build:
        import shutil

        free = shutil.disk_usage(decision_dir()).free
        if free < _MIN_FREE_BYTES:
            return _failed(
                f"{free // 1024**2} MiB free under {decision_dir()}; the build needs about "
                f"{_MIN_FREE_BYTES // 1024**3} GiB",
                "free disk space, then retry",
            )
        log.info(
            "memory.decision_build_start",
            job_id=job_id,
            pinned=pinned,
            reason=txn.prev_venv_problem,
        )
        try:
            _build_venv(runner, new, venv, deadline)
        except (_Abort, OSError) as exc:
            with contextlib.suppress(OSError):
                _fs(runner, "rmtree", str(new))
            return _failed(str(exc), remedy_build, lane="unchanged" if txn.prev_unit else "off")

    # From here on every change is recorded on ``txn`` and undone on failure.
    try:
        body = decision_fs.read_bytes(packaged_server_script())
        # A symlink in the script's place is never read or backed up: the
        # write below replaces the link itself.
        current: bytes | None = None
        if not script.is_symlink() and script.exists():
            current = decision_fs.read_bytes(script)
        if current != body:
            if current is not None:
                _fs(runner, "copy", str(script), str(script_prev))
                txn.script_had_prev = True
            _fs(runner, "write", str(script), body=body)
            txn.script_changed = True

        if needs_build:
            if txn.prev_unit is not None:
                with contextlib.suppress(*_SEAM_ERRORS):
                    _svc(seam, "stop", check=False)
            _fs(runner, "rmtree", str(prev))
            if venv.exists() or venv.is_symlink():
                _fs(runner, "rename", str(venv), str(prev))
            _fs(runner, "rename", str(new), str(venv))
            txn.venv_swapped = True

        params = _unit_params(decision)
        txn.unit_changed = _write_unit(seam, params, unit_path)
        _svc(seam, "enable")
        if txn.venv_swapped or txn.unit_changed or txn.script_changed:
            _svc(seam, "restart")
        else:
            _svc(seam, "start")
        if not _poll_health(
            http_get, int(params["port"]), min(_HEALTH_POLL_TOTAL_S, deadline.clip(1e9)), sleep
        ):
            raise _Abort(
                f"{DECISION_UNIT_NAME} did not answer http://127.0.0.1:{params['port']}/health "
                f"within {_HEALTH_POLL_TOTAL_S:.0f}s"
            )
    except (_Abort, OSError, *_SEAM_ERRORS) as exc:
        error = str(exc) if isinstance(exc, _Abort | OSError) else _seam_error(exc)
        rolled = _roll_back(seam, runner, http_get, sleep, txn, error)
        if hand_copy is not None:
            rolled["hand_unit_copy"] = str(hand_copy)
        return rolled

    with contextlib.suppress(OSError):
        _fs(runner, "rmtree", str(prev))
        _fs(runner, "rmtree", str(script_prev))
    result: dict[str, Any] = {
        "status": "installed"
        if state == NOT_INSTALLED
        else ("upgraded" if needs_build else "converged"),
        "to": pinned,
        "port": params["port"],
    }
    if state == MANAGED and label != pinned:
        result["from"] = label
    if txn.prev_venv_problem:
        result["rebuilt_because"] = txn.prev_venv_problem
    if state == UNMANAGED:
        result["adopted"] = True
        if hand_copy is not None:
            result["hand_unit_copy"] = str(hand_copy)
            result["note"] = (
                "adopted a hand-installed server: its unit was replaced by hal0's; "
                f"the previous file is kept at {hand_copy}"
            )
    if install and (decision.provider != "local" or not decision.enabled):
        error = _set_provider_safely("local")
        if error:
            result["note"] = f"{error}; turn the lane on with `hal0 memory decision enable`"
    elif not install and (decision.provider != "local" or not decision.enabled):
        result["note"] = "the decision lane is off; `hal0 memory decision enable` turns it on"
    result["engine"] = _reconcile_engine(seam, txn.last_good)
    log.info("memory.decision_converged", job_id=job_id, status=result["status"])
    return result


def _roll_back(
    seam: SystemCtlSeam,
    runner: Runner,
    http_get: HttpGet,
    sleep: Callable[[float], None],
    txn: _Txn,
    error: str,
) -> dict[str, Any]:
    """Put venv, script and unit back to the previous working set, and say how it went."""
    global _ACTIVE_DEADLINE
    # A failure at the budget's edge still gets time to put things back.
    _ACTIVE_DEADLINE = _Deadline(ROLLBACK_BUDGET_S)
    remedy = f"check `journalctl -u {DECISION_UNIT_NAME}` and `{STATUS_COMMAND}`, then retry"
    venv = venv_dir()
    prev = venv.with_name(".venv.prev")
    script = server_script_path()
    script_prev = script.with_name(script.name + ".prev")
    problems: list[str] = []

    if txn.prev_unit is None:
        # No previous install: no unit, lane off, nothing left enabled.
        teardown = _teardown_unit(seam, txn.unit_path)
        if teardown:
            problems.append(teardown)
        provider_error = _set_provider_safely("off")
        if provider_error:
            problems.append(provider_error)
        engine = _reconcile_engine(seam, txn.last_good)
        return _failed(error, remedy, lane="off", restore_problems=problems or None, engine=engine)

    dead = txn.prev_venv_problem if txn.prev_venv_dead else None
    if dead and txn.venv_swapped:
        # The venv this converge replaced cannot run (#3960): putting it back
        # and waiting on /health again would restore a server that is known
        # dead. The new build passed its import check and stays, with the
        # script and unit written for it.
        with contextlib.suppress(OSError):
            _fs(runner, "rmtree", str(prev))
        with contextlib.suppress(OSError):
            _fs(runner, "rmtree", str(script_prev))
        return _failed(
            f"{error}; rollback: the previous venv was not restored ({dead}); "
            "the new build stays in place",
            remedy,
            restored=False,
        )

    try:
        if txn.venv_swapped and prev.exists():
            _fs(runner, "rmtree", str(venv))
            _fs(runner, "rename", str(prev), str(venv))
        if txn.script_changed:
            if txn.script_had_prev:
                _fs(runner, "rename", str(script_prev), str(script))
            else:
                _fs(runner, "rmtree", str(script))
    except OSError as exc:
        problems.append(f"restoring files failed: {exc}")
    if txn.unit_changed:
        if txn.prev_params is None:
            problems.append("the previous unit could not be read back, so the new one stays")
        else:
            try:
                seam.write_decision_unit(
                    **txn.prev_params, path=txn.unit_path, timeout=_clip(_SYSTEMCTL_TIMEOUT_S)
                )
                seam.systemctl(
                    "systemctl", "daemon-reload", check=True, timeout=_clip(_SYSTEMCTL_TIMEOUT_S)
                )
            except _SEAM_ERRORS as exc:
                problems.append(f"restoring the previous unit failed: {_seam_error(exc)}")
    if dead:
        # The swap never happened, so the venv in place is the one that cannot
        # run: there is no server to restart or wait for.
        problems.append(f"the venv in place cannot run ({dead})")
    else:
        try:
            _svc(seam, "restart")
        except _SEAM_ERRORS as exc:
            problems.append(f"restarting the previous server failed: {_seam_error(exc)}")
        port = int((txn.prev_params or {}).get("port") or 0)
        healthy = bool(port) and _poll_health(http_get, port, _HEALTH_POLL_TOTAL_S, sleep)
        if not healthy:
            problems.append("the restored server does not answer /health")
    if problems:
        return _failed(f"{error}; rollback: {'; '.join(problems)}", remedy, restored=False)
    return {"status": "rolled_back", "error": error, "remedy": remedy, "restored": True}


def _remove(
    seam: SystemCtlSeam, runner: Runner, unit_path: Path, purge_model: bool
) -> dict[str, Any]:
    """Lane off and engine re-pointed first, then the unit and venv go."""
    problems: list[str] = []
    last_good = _last_good_memory()
    provider_error = _set_provider_safely("off")
    if provider_error:
        problems.append(provider_error)
    _reconcile_engine(seam, last_good)
    teardown = _teardown_unit(seam, unit_path)
    if teardown is not None:
        engine = _reconcile_engine(seam, last_good)
        return _failed(teardown, "retry; the venv was left in place", engine=engine)
    venv = venv_dir()
    try:
        for path in (
            venv,
            venv.with_name(".venv.new"),
            venv.with_name(".venv.prev"),
            server_script_path(),
            server_script_path().with_name(server_script_path().name + ".prev"),
        ):
            _fs(runner, "rmtree", str(path))
        if purge_model:
            _fs(runner, "rmtree", str(model_cache_dir()))
    except OSError as exc:
        problems.append(f"deleting files failed: {exc}")
    result: dict[str, Any] = {
        "status": "removed",
        "model_kept": not purge_model,
        "engine": _reconcile_engine(seam, last_good),
    }
    if problems:
        result["note"] = "; ".join(problems)
    return result


def remove_memory_decision(
    *,
    purge_model: bool = False,
    seam: SystemCtlSeam | None = None,
    runner: Runner | None = None,
    unit_path: Path | None = None,
) -> dict[str, Any]:
    """Remove the server: see :func:`_remove`."""
    return converge_memory_decision(
        action="remove",
        purge_model=purge_model,
        seam=seam,
        runner=runner,
        unit_path=unit_path,
    )


__all__ = [
    "CLI_WAIT_S",
    "CONVERGE_BUDGET_S",
    "INSTALL_COMMAND",
    "MODEL_REPO",
    "MODEL_REVISION",
    "PACKAGES",
    "TORCH_INDEX_URL",
    "TORCH_VERSION",
    "component_installed",
    "converge_memory_decision",
    "install_state",
    "installed_label",
    "parse_unit_params",
    "pinned_label",
    "remove_memory_decision",
    "set_provider",
    "unit_active",
]
