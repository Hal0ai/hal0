"""The ``memory-decision`` component: the local Hindsight decision model server (#3757).

``[memory.decision]`` with ``provider = "local"`` sends the engine's decision
calls (consolidation triage, reflect sufficiency, page delta gating) to
``hs-decide-1-flash`` served on CPU by ``hal0-hs-decide``. Nothing installed
that server before this module, so the lane was off on every box that had not
built a venv by hand.

This module is the single owner of the pins (model revision, PyTorch index,
package floor) and of the build. It is opt-in: the component converges only
when the operator asks (``hal0 memory decision install`` or ``hal0 update
component memory-decision``); a plain ``hal0 update`` re-converges it only on
a box where it is already installed.

Layout (all ``hal0``-owned, under ``${var_lib}/memory``):

* ``hindsight-decision/.venv`` — the server's venv. ``.venv/hal0-build.json``
  records which pins built it; the venv is rebuilt when they change.
* ``hindsight-decision/hs_decide_serve.py`` — the server script, copied from
  ``packaging/hindsight-decision`` on every converge.
* ``hindsight/hf-cache/hub/models--hindsight-ai--hs-decide-1-flash/snapshots/<rev>``
  — the pinned model snapshot, in the engine's own Hugging Face cache.

Build shape: the replacement venv is built at ``.venv.new`` while the live one
keeps serving; only a venv that imports its whole stack is swapped in, and
the previous one is kept as ``.venv.prev`` until the new server answers
``/health``. A server that does not answer after a rebuild gets the previous
venv back.

Privileges: hal0-api runs this as the ``hal0`` user. The venv, the model and
the script are hal0's own files. The unit file and the service verbs go
through :class:`hal0.system.seam.SystemCtlSeam`: ``write-hs-decide-unit``
renders the unit root-side from four validated numbers (it takes no unit
text), ``remove-hs-decide-unit`` deletes that one literal path, and
``svc-<verb> hs-decide`` reaches only ``hal0-hs-decide.service``. Run as root
(``sudo hal0 memory decision install``, or the installer's update pass), the
build steps drop to ``hal0`` and the unit is written directly.

Never raises for operational failures: every entry point returns a status
dict. ``build_failed`` is the one failure status, with ``error`` and
``remedy``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import structlog

from hal0.config.paths import var_lib
from hal0.system.seam import (
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
#: snapshot directory name is this exact string. 2026-11-09 revision; the one
#: the unit shipped before #3757 (``b9a782058d…``) no longer matches a pull.
MODEL_REVISION = "e2c789026dfd1f7608cafb2ef6a9bb31397af802"

#: CPU-only PyTorch wheels. torch is installed from this index ONLY, and is
#: then held by a constraint so no later pip step can swap in a CUDA build.
TORCH_INDEX_URL = "https://download.pytorch.org/whl/cpu"
TORCH_REQUIREMENT = "torch"

#: Package floor installed from PyPI after torch. The model repo's ``serving/``
#: package (``hindsight_decision``) is installed from the pinned snapshot.
PACKAGES: tuple[str, ...] = (
    "transformers>=5.16.1",
    "fastapi",
    "uvicorn",
    "accelerate",
    "huggingface-hub>=1.0",
)

#: Modules the built venv must import before it is swapped in.
_IMPORT_CHECK = "import torch, transformers, fastapi, uvicorn, accelerate, hindsight_decision"

# ── Budgets ──────────────────────────────────────────────────────────────────

_VENV_TIMEOUT_S = 180
_PIP_TIMEOUT_S = 1800
_PULL_TIMEOUT_S = 1800
_PROBE_TIMEOUT_S = 120
_SYSTEMCTL_TIMEOUT_S = 120.0
#: Model load on a cold page cache plus uvicorn start.
_HEALTH_POLL_TOTAL_S = 240.0
_HEALTH_POLL_STEP_S = 3.0
#: Venv ~0.7 GB + model ~1.5 GB, with headroom for the build-aside copy.
_MIN_FREE_BYTES = 4 * 1024**3

Runner = Callable[..., subprocess.CompletedProcess]
#: What a seam call raises operationally (a stale wrapper, a sudo refusal, a wedge).
_SEAM_ERRORS = (OSError, ValueError, subprocess.SubprocessError, SeamTimeout)
HttpGet = Callable[[str], dict[str, Any] | None]

STATUS_COMMAND = "hal0 memory decision status"
INSTALL_COMMAND = "hal0 memory decision install"


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


def _packages_digest() -> str:
    material = "\n".join((TORCH_INDEX_URL, TORCH_REQUIREMENT, *PACKAGES, MODEL_REVISION))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:8]


def pinned_label() -> str:
    """``<rev12> (packages <digest>)`` — what ``hal0 update status`` shows as pinned."""
    return f"{MODEL_REVISION[:12]} (packages {_packages_digest()})"


def _read_build_stamp(venv: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(_build_stamp_path(venv).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def installed_label(*, venv: Path | None = None, unit_path: Path | None = None) -> str | None:
    """The pins that built the installed server, or ``None`` when not installed.

    Installed means all three: the unit file, the venv's interpreter, and the
    build stamp inside that venv. A hand-built venv has no stamp and reads as
    not installed until ``hal0 memory decision install`` adopts it.
    """
    venv = venv or venv_dir()
    unit = unit_path or DECISION_UNIT_PATH
    try:
        if not unit.is_file() or not (venv / "bin" / "python").exists():
            return None
    except OSError:
        return None
    stamp = _read_build_stamp(venv)
    label = stamp.get("label") if stamp else None
    return label if isinstance(label, str) and label else None


def component_installed() -> bool:
    return installed_label() is not None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _as_hal0(argv: list[str]) -> list[str]:
    """Run build steps as ``hal0`` when this process is root (installer path)."""
    if os.geteuid() == 0 and hal0_service_account() is not None:
        return as_hal0_argv(argv)
    return argv


def _run(
    runner: Runner,
    argv: list[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": timeout}
    if env is not None:
        kwargs["env"] = {**os.environ, **env}
    return runner(_as_hal0(argv), **kwargs)


def _tail(proc: subprocess.CompletedProcess) -> str:
    text = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
    return text[-600:] if text else f"exit {proc.returncode}"


def _own(path: Path) -> None:
    """``chown hal0:hal0`` one path this process created as root.

    Not recursive: every build step that writes many files runs as ``hal0``
    already (:func:`_as_hal0`); only the few files written here need it.
    """
    if os.geteuid() != 0:
        return
    account = hal0_service_account()
    if account is None:
        return
    with contextlib.suppress(OSError):
        os.lchown(path, account.uid, account.gid)


def _rmtree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path, ignore_errors=True)


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


# ── Build ────────────────────────────────────────────────────────────────────


def _interpreter() -> str:
    return shutil.which("python3") or sys.executable


def _retarget_scripts(built: Path, final: Path) -> None:
    from hal0.memory.engine_upgrade import _retarget_scripts as retarget

    retarget(built, final)


def _build_venv(runner: Runner, new: Path, final: Path) -> str | None:
    """Build the server venv at ``new``; ``None`` on success, else the reason.

    Every network fetch is one of: the PyTorch CPU index (torch only), PyPI
    (:data:`PACKAGES`), and Hugging Face at :data:`MODEL_REVISION`.
    """
    _rmtree(new)
    new.parent.mkdir(parents=True, exist_ok=True)
    _own(new.parent)
    py = str(new / "bin" / "python")
    pip = [py, "-m", "pip", "install", "--no-input", "--disable-pip-version-check"]
    try:
        proc = _run(runner, [_interpreter(), "-m", "venv", str(new)], timeout=_VENV_TIMEOUT_S)
        if proc.returncode != 0:
            return f"python3 -m venv failed: {_tail(proc)}"
        proc = _run(
            runner,
            [*pip, TORCH_REQUIREMENT, "--index-url", TORCH_INDEX_URL],
            timeout=_PIP_TIMEOUT_S,
        )
        if proc.returncode != 0:
            return f"installing CPU torch failed: {_tail(proc)}"
        proc = _run(
            runner,
            [py, "-c", "import importlib.metadata as m; print(m.version('torch'))"],
            timeout=_PROBE_TIMEOUT_S,
        )
        torch_version = (proc.stdout or "").strip()
        if proc.returncode != 0 or not torch_version:
            return f"torch did not install: {_tail(proc)}"
        # Hold torch where the CPU index put it: without this an `accelerate`
        # floor could have pip replace it with a CUDA build from PyPI.
        constraints = new / "hal0-constraints.txt"
        constraints.write_text(f"torch=={torch_version}\n", encoding="utf-8")
        _own(constraints)
        proc = _run(runner, [*pip, "-c", str(constraints), *PACKAGES], timeout=_PIP_TIMEOUT_S)
        if proc.returncode != 0:
            return f"installing {' '.join(PACKAGES)} failed: {_tail(proc)}"
        snap = _pull_model(runner, py)
        if isinstance(snap, str):
            return snap
        proc = _run(
            runner,
            [*pip, "-c", str(constraints), str(snapshot_dir() / "serving")],
            timeout=_PIP_TIMEOUT_S,
        )
        if proc.returncode != 0:
            return f"installing the model's serving package failed: {_tail(proc)}"
        proc = _run(runner, [py, "-c", _IMPORT_CHECK], timeout=_PROBE_TIMEOUT_S)
        if proc.returncode != 0:
            return f"the built venv does not import its stack: {_tail(proc)}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"venv build failed: {exc}"
    _retarget_scripts(new, final)
    stamp = {
        "label": pinned_label(),
        "model_revision": MODEL_REVISION,
        "torch": torch_version,
        "packages": list(PACKAGES),
        "built_at": time.time(),
    }
    try:
        _build_stamp_path(new).write_text(json.dumps(stamp), encoding="utf-8")
    except OSError as exc:
        return f"could not write the build stamp: {exc}"
    _own(_build_stamp_path(new))
    return None


def _pull_model(runner: Runner, py: str) -> None | str:
    """Fetch :data:`MODEL_REVISION` into :func:`hf_home`; ``None`` or the reason."""
    code = (
        "import sys\n"
        "from huggingface_hub import snapshot_download\n"
        "print(snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2]))\n"
    )
    if not hf_home().is_dir():
        hf_home().mkdir(parents=True, exist_ok=True)
        _own(hf_home())
    proc = _run(
        runner,
        [py, "-c", code, MODEL_REPO, MODEL_REVISION],
        timeout=_PULL_TIMEOUT_S,
        env={"HF_HOME": str(hf_home()), "HF_HUB_DISABLE_TELEMETRY": "1"},
    )
    if proc.returncode != 0:
        return f"pulling {MODEL_REPO}@{MODEL_REVISION[:12]} failed: {_tail(proc)}"
    if not (snapshot_dir() / "serving").is_dir():
        return f"the {MODEL_REPO} snapshot at {snapshot_dir()} has no serving/ package"
    return None


def _sync_server_script() -> tuple[bool, str | None]:
    """Copy the packaged server next to the venv; ``(changed, error)``."""
    src = packaged_server_script()
    dst = server_script_path()
    try:
        body = src.read_bytes()
    except OSError as exc:
        return False, f"cannot read {src}: {exc}"
    try:
        if dst.is_file() and dst.read_bytes() == body:
            return False, None
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".py.tmp")
        tmp.write_bytes(body)
        tmp.chmod(0o644)
        _own(tmp)
        tmp.replace(dst)
    except OSError as exc:
        return False, f"cannot write {dst}: {exc}"
    return True, None


# ── Service ──────────────────────────────────────────────────────────────────


def _svc(seam: SystemCtlSeam, verb: str, *, check: bool = True) -> None:
    seam.systemctl("systemctl", verb, DECISION_UNIT_NAME, check=check, timeout=_SYSTEMCTL_TIMEOUT_S)


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


def _unit_params(decision: Any) -> dict[str, int | str]:
    return {
        "revision": MODEL_REVISION,
        "threads": int(decision.threads),
        "port": int(decision.port),
        "max_tokens": int(decision.max_request_tokens),
    }


def _write_unit(seam: SystemCtlSeam, params: dict[str, Any], unit_path: Path) -> bool:
    """Write the unit when it differs; True when it changed. Raises seam errors."""
    body = render_decision_unit(**params)
    try:
        if unit_path.read_text(encoding="utf-8") == body:
            return False
    except OSError:
        pass
    seam.write_decision_unit(**params, path=unit_path, timeout=_SYSTEMCTL_TIMEOUT_S)
    seam.systemctl("systemctl", "daemon-reload", check=True, timeout=_SYSTEMCTL_TIMEOUT_S)
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
        seam.remove_decision_unit(path=unit_path, timeout=_SYSTEMCTL_TIMEOUT_S)
        seam.systemctl("systemctl", "daemon-reload", check=False, timeout=_SYSTEMCTL_TIMEOUT_S)
    except _SEAM_ERRORS as exc:
        error = error or f"could not remove {unit_path}: {_seam_error(exc)}"
    return error


def _reconcile_engine(seam: SystemCtlSeam) -> dict[str, Any]:
    """Re-render the engine's showcase drop-in so it points at (or off) the server."""
    try:
        from hal0.memory.extraction_env import reconcile_showcase_dropin

        return reconcile_showcase_dropin(seam=seam)
    except Exception as exc:  # never fail the component on the engine pass
        log.warning("memory.decision_engine_reconcile_failed", error=str(exc))
        return {"status": "failed", "error": str(exc)}


# ── Converge ─────────────────────────────────────────────────────────────────


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
) -> dict[str, Any]:
    """Converge (or, with ``action="remove"``, remove) the decision server.

    * ``apply=False`` — diagnose only: ``converged`` / ``stale`` / ``skipped``.
    * not installed and not ``install`` — ``skipped``: opt-in, so a plain
      ``hal0 update`` never installs it.
    * otherwise build the venv if its pins changed, sync the server script,
      render the unit from ``[memory.decision]``, enable and (re)start it,
      wait for ``/health``, then re-render the engine drop-in.

    ``seam``/``runner``/``http_get``/``sleep``/``unit_path`` are test seams.
    """
    seam = seam if seam is not None else SystemCtlSeam()
    runner = runner if runner is not None else subprocess.run
    http_get = http_get if http_get is not None else _http_get_json
    unit_path = unit_path or DECISION_UNIT_PATH
    if action == "remove":
        return remove_memory_decision(purge_model=purge_model, seam=seam, unit_path=unit_path)

    installed = installed_label(unit_path=unit_path)
    pinned = pinned_label()
    if not apply:
        if installed is None:
            return {"status": "skipped", "reason": f"not installed (opt-in: {INSTALL_COMMAND})"}
        return {"status": "converged" if installed == pinned else "stale"}
    if installed is None and not install:
        return {"status": "skipped", "reason": f"not installed (opt-in: {INSTALL_COMMAND})"}

    first_install = installed is None
    # A unit already on the box (an earlier install, or a hand-built server
    # being adopted) is rolled back to, never torn down, when this pass fails.
    had_unit = unit_path.is_file()
    try:
        decision = _decision_config()
    except Exception as exc:
        return _failed(f"hal0.toml unreadable: {exc}", "fix hal0.toml, then retry")

    venv = venv_dir()
    new = venv.with_name(".venv.new")
    prev = venv.with_name(".venv.prev")
    stamp = _read_build_stamp(venv) if (venv / "bin" / "python").exists() else None
    needs_build = not stamp or stamp.get("label") != pinned
    rebuilt = False
    if needs_build:
        free = shutil.disk_usage(_existing_parent(decision_dir())).free
        if free < _MIN_FREE_BYTES:
            return _failed(
                f"{free // 1024**2} MiB free under {decision_dir()}; the build needs about "
                f"{_MIN_FREE_BYTES // 1024**3} GiB",
                "free disk space, then retry",
            )
        log.info("memory.decision_build_start", job_id=job_id, pinned=pinned)
        reason = _build_venv(runner, new, venv)
        if reason is not None:
            _rmtree(new)
            return _failed(
                reason,
                f"check network access to PyPI, {TORCH_INDEX_URL} and huggingface.co, then "
                f"retry with: {INSTALL_COMMAND}",
                lane="unchanged" if had_unit else "off",
            )

    changed_script, script_error = _sync_server_script()
    if script_error is not None:
        _rmtree(new)
        return _failed(script_error, "re-run the installer to restore the release tree")

    if needs_build:
        # Stop before the swap: a running server may still import from the old tree.
        if had_unit:
            with contextlib.suppress(*_SEAM_ERRORS):
                _svc(seam, "stop", check=False)
        _rmtree(prev)
        try:
            if venv.exists() or venv.is_symlink():
                venv.rename(prev)
            new.rename(venv)
        except OSError as exc:
            if prev.exists() and not venv.exists():
                with contextlib.suppress(OSError):
                    prev.rename(venv)
            _rmtree(new)
            return _failed(f"could not swap the new venv in: {exc}", "retry")
        rebuilt = True

    params = _unit_params(decision)
    try:
        unit_changed = _write_unit(seam, params, unit_path)
        _svc(seam, "enable")
        if rebuilt or unit_changed or changed_script:
            _svc(seam, "restart")
        else:
            _svc(seam, "start")
    except _SEAM_ERRORS as exc:
        error = f"could not install or start {DECISION_UNIT_NAME}: {_seam_error(exc)}"
        return _after_failure(seam, unit_path, venv, prev, not had_unit, rebuilt, error)

    if not _poll_health(http_get, int(params["port"]), _HEALTH_POLL_TOTAL_S, sleep):
        error = (
            f"{DECISION_UNIT_NAME} did not answer http://127.0.0.1:{params['port']}/health "
            f"within {_HEALTH_POLL_TOTAL_S:.0f}s"
        )
        return _after_failure(seam, unit_path, venv, prev, not had_unit, rebuilt, error)

    _rmtree(prev)
    result: dict[str, Any] = {
        "status": "installed" if first_install else ("upgraded" if rebuilt else "converged"),
        "to": pinned,
        "port": params["port"],
        "engine": _reconcile_engine(seam),
    }
    if installed and installed != pinned:
        result["from"] = installed
    if not decision.enabled or decision.provider != "local":
        result["note"] = (
            "decision server installed; the lane stays off until `hal0 memory decision enable`"
        )
    log.info("memory.decision_converged", job_id=job_id, status=result["status"])
    return result


def _existing_parent(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _after_failure(
    seam: SystemCtlSeam,
    unit_path: Path,
    venv: Path,
    prev: Path,
    fresh: bool,
    rebuilt: bool,
    error: str,
) -> dict[str, Any]:
    """Leave the lane off (fresh install) or on the previous venv (rebuild)."""
    remedy = f"check `journalctl -u {DECISION_UNIT_NAME}` and `{STATUS_COMMAND}`, then retry"
    if fresh:
        teardown = _teardown_unit(seam, unit_path)
        engine = _reconcile_engine(seam)
        return _failed(error, remedy, lane="off", teardown_error=teardown, engine=engine)
    if rebuilt and prev.exists():
        try:
            _rmtree(venv)
            prev.rename(venv)
            _svc(seam, "restart", check=False)
        except _SEAM_ERRORS as exc:
            return _failed(f"{error}; restoring the previous venv failed: {exc}", remedy)
        return {"status": "rolled_back", "error": error, "remedy": remedy}
    return _failed(error, remedy)


def remove_memory_decision(
    *,
    purge_model: bool = False,
    seam: SystemCtlSeam | None = None,
    unit_path: Path | None = None,
) -> dict[str, Any]:
    """Stop, disable and delete the unit, delete the venv; keep the model unless purged."""
    seam = seam if seam is not None else SystemCtlSeam()
    unit_path = unit_path or DECISION_UNIT_PATH
    error = _teardown_unit(seam, unit_path)
    if error is not None:
        return _failed(error, "retry; the venv was left in place")
    venv = venv_dir()
    for path in (venv, venv.with_name(".venv.new"), venv.with_name(".venv.prev")):
        _rmtree(path)
    server_script_path().unlink(missing_ok=True)
    if purge_model:
        _rmtree(model_cache_dir())
    return {
        "status": "removed",
        "model_kept": not purge_model,
        "engine": _reconcile_engine(seam),
    }


__all__ = [
    "INSTALL_COMMAND",
    "MODEL_REPO",
    "MODEL_REVISION",
    "PACKAGES",
    "TORCH_INDEX_URL",
    "component_installed",
    "converge_memory_decision",
    "installed_label",
    "pinned_label",
    "remove_memory_decision",
    "unit_active",
]
