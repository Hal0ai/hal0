"""Task 2.4: Async model fetch wrapper for ComfyUI scripts.

Public API:
    fetch_model(variant)  -> job_id   (NON-BLOCKING: starts background thread)
    get_job(job_id)       -> dict | None
    cancel_job(job_id)    -> bool

Fix #872: scripts take POSITIONAL args (not --precision flags) and require
MULTIPLE invocations per variant.  fetch_steps on ModelVariant encodes the
exact argv for each invocation; a background worker iterates them
sequentially, stopping on the first nonzero exit.

Fix #1110 (WS-G): the ``get_*.sh`` scripts run as HOST subprocesses but used
to hard-code the ComfyUI container's ``hf`` path (``/opt/venv/bin/hf``), so
every download failed with "no such file"; and no HF credential was ever
forwarded.  The scripts now resolve ``hf`` from the host PATH (falling back to
the container venv path), and this wrapper forwards ``HF_TOKEN`` /
``HF_HOME`` (the token source plumbed in #1094) into each subprocess env so
gated repos authenticate.  Selecting a variant also provisions its matching
curated workflow JSON into the ComfyUI workflows dir so it is launchable.

fetch_model returns immediately so POST /api/comfyui/models/fetch can reply
202 without blocking the FastAPI request for a multi-hour download.

Fix (Finding 5): ``_SCRIPTS_DIR`` / ``_WORKFLOWS_SRC_DIR`` used to be
module-level constants resolved only via the editable-checkout path
(``Path(__file__).parent * 4``), same class of bug as #1284/#1285. Under a
non-editable wheel install that resolves into site-packages, where
``installer/`` does not exist, so every download 202'd and then immediately
failed. They are now resolver functions that try the editable candidate
first and fall back to the FHS code root (``hal0.config.paths.usr_lib()``),
mirroring ``hal0.agents.manager.installer_script_path``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from hal0.comfyui.capabilities import ModelVariant
from hal0.config import paths as _paths

log = logging.getLogger(__name__)

# Module-level job registry
_JOBS: dict[str, dict] = {}


def _scripts_dir() -> Path:
    """Return the directory holding the ComfyUI model-fetch scripts.

    ``install.sh`` pip-installs hal0 as a NON-editable wheel in production
    (v0.9.7.1+; only ``pip install -e <repo>`` dev checkouts differ) — see
    the FHS-layout note in :mod:`hal0.config.paths`. The two install shapes
    resolve ``__file__`` completely differently, so we try both candidates
    and return whichever exists, mirroring
    :func:`hal0.agents.manager.installer_script_path`:

    * Editable / dev checkout — ``src/hal0/comfyui/fetch.py`` lives at
      ``<repo_root>/src/hal0/comfyui/fetch.py``; ``parents[3]`` is the repo
      root, so the scripts are at
      ``<repo_root>/installer/comfyui/scripts/``.
    * FHS / wheel install (the production case) — ``__file__`` resolves
      into ``<venv>/lib/pythonX/site-packages/hal0/...``, which has no
      ``installer/`` sibling. The scripts instead ship alongside the code
      root :func:`hal0.config.paths.usr_lib` resolves
      (``/usr/lib/hal0/current`` by default, honouring ``HAL0_HOME`` for
      dev/test root overrides) — ``install.sh`` already rsyncs
      ``installer/comfyui/scripts`` under ``PREFIX``.

    If neither candidate exists we return the FHS candidate anyway, so a
    downstream "no such file" subprocess error names the real production
    path rather than a venv path the operator won't recognise.
    """
    editable_candidate = Path(__file__).resolve().parents[3] / "installer" / "comfyui" / "scripts"
    fhs_candidate = _paths.usr_lib() / "installer" / "comfyui" / "scripts"

    if editable_candidate.is_dir():
        return editable_candidate
    return fhs_candidate


def _workflows_src_dir() -> Path:
    """Return the directory holding curated ComfyUI workflow JSONs.

    Ships alongside the fetch scripts (same source tree, same install-shape
    ambiguity), so this resolves the same way as :func:`_scripts_dir` — see
    that docstring for the editable-vs-FHS candidate rationale. Not to be
    confused with :func:`_workflows_dir`, which is the DESTINATION the
    curated JSON is copied to.
    """
    editable_candidate = Path(__file__).resolve().parents[3] / "installer" / "comfyui" / "workflows"
    fhs_candidate = _paths.usr_lib() / "installer" / "comfyui" / "workflows"

    if editable_candidate.is_dir():
        return editable_candidate
    return fhs_candidate


# Back-compat module-level alias: hal0.comfyui.orchestrate imports this name
# directly and uses it as a default-arg value. Now computed via the same
# editable/FHS resolver as fetch_model() (instead of the broken
# editable-only path), so that caller gets the fix too.
_SCRIPTS_DIR: Path = _scripts_dir()


def _fetch_env() -> dict[str, str]:
    """Build the subprocess env for a fetch step, forwarding HF credentials.

    Inherits the hal0 process env and normalises the Hugging Face token so the
    ``hf`` CLI in the vendored scripts authenticates gated pulls:

    * ``HF_TOKEN`` — set from ``HF_TOKEN`` or the legacy
      ``HUGGING_FACE_HUB_TOKEN`` (the token source plumbed in #1094). When only
      the legacy name is present it is mirrored to ``HF_TOKEN`` so both the CLI
      and the scripts see it.
    * ``HF_HOME`` — forwarded when set so the persistent cache is shared; the
      scripts default it to ``$HOME/.cache/huggingface`` otherwise.
    """
    env = dict(os.environ)
    token = env.get("HF_TOKEN") or env.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        env["HF_TOKEN"] = token
        env["HUGGING_FACE_HUB_TOKEN"] = token
    return env


def _workflows_dir() -> Path:
    """Target dir to provision curated workflow JSONs into.

    Honours ``COMFYUI_WORKFLOWS_DIR`` (same override the launch route reads),
    else ``<model-store>/comfyui/workflows`` (default ``/mnt/ai-models``).
    """
    override = os.environ.get("COMFYUI_WORKFLOWS_DIR", "").strip()
    if override:
        return Path(override)
    try:
        from hal0.config.paths import model_store_root

        return Path(model_store_root()) / "comfyui" / "workflows"
    except Exception:
        return Path("/mnt/ai-models/comfyui/workflows")


def _provision_workflow(variant: ModelVariant) -> str | None:
    """Copy the variant's curated workflow JSON into the workflows dir.

    Best-effort: returns the destination path on success, or ``None`` when the
    source asset is missing or the copy fails (a workflow-copy hiccup must not
    fail the model download). The launch route then discovers it via
    ``GET /api/comfyui/workflows``.
    """
    name = variant.workflow
    if not name:
        return None
    src = _workflows_src_dir() / name
    if not src.is_file():
        log.warning("comfyui.workflow_asset_missing", extra={"workflow": name})
        return None
    try:
        dest_dir = _workflows_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        shutil.copyfile(src, dest)
        return str(dest)
    except OSError as exc:
        log.warning(
            "comfyui.workflow_provision_failed",
            extra={"workflow": name, "error": str(exc)},
        )
        return None


def _run_sequence(rec: dict, script_path: str, fetch_steps: tuple[tuple[str, ...], ...]) -> None:
    """Background worker: run each fetch step sequentially.

    Stops on first nonzero exit (status='failed') or on cancellation
    (status='cancelled', set by cancel_job).  Marks 'done' when all
    steps exit 0.
    """
    env = _fetch_env()
    for step_args in fetch_steps:
        if rec["status"] == "cancelled":
            return

        cmd = ["bash", script_path, *step_args]
        # Discard the child's stdout/stderr rather than capturing to a PIPE we
        # never read: proc.wait() on an unread PIPE deadlocks once the child
        # fills the ~64 KB OS pipe buffer, and the get_*.sh downloaders emit
        # MBs of hf progress (Popen docs' deadlock warning). Nothing here ever
        # consumed the captured bytes, so DEVNULL is behaviour-preserving for
        # callers while removing the hang — and keeps wait() as the join point.
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        rec["_proc"] = proc

        rc = proc.wait()

        if rec["status"] == "cancelled":
            return

        if rc != 0:
            rec["returncode"] = rc
            rec["status"] = "failed"
            return

    rec["returncode"] = 0
    rec["status"] = "done"


def fetch_model(variant: ModelVariant) -> str:
    """Start a background fetch for *variant* and return its job_id immediately.

    Provisions the variant's curated workflow JSON synchronously (a fast file
    copy) before the download starts, then runs the fetch steps in a daemon
    thread; poll status via get_job(). Non-blocking so the 202-returning API
    endpoint does not stall on multi-hour downloads.
    """
    script_path = str(_scripts_dir() / variant.fetch_script)
    job_id = str(uuid.uuid4())

    workflow_path = _provision_workflow(variant)

    rec: dict = {
        "id": job_id,
        "family": variant.family,
        "status": "running",
        "returncode": None,
        "script": script_path,
        "workflow": workflow_path,
        "_proc": None,
        "_thread": None,
    }
    _JOBS[job_id] = rec

    t = threading.Thread(
        target=_run_sequence,
        args=(rec, script_path, variant.fetch_steps),
        daemon=True,
    )
    rec["_thread"] = t
    t.start()
    return job_id


def get_job(job_id: str) -> dict | None:
    """Return job dict (without internal fields) or None if unknown.  Live status."""
    rec = _JOBS.get(job_id)
    if rec is None:
        return None
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def cancel_job(job_id: str) -> bool:
    """Terminate the in-flight step.  Returns True if cancelled, False otherwise."""
    rec = _JOBS.get(job_id)
    if rec is None:
        return False

    if rec["status"] != "running":
        return False

    rec["status"] = "cancelled"
    proc = rec.get("_proc")
    if proc is not None:
        proc.terminate()
    return True
