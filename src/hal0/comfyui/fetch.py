"""Task 2.4: Async model fetch wrapper for ComfyUI scripts.

Public API:
    fetch_model(variant)  -> job_id   (NON-BLOCKING: starts background thread)
    get_job(job_id)       -> dict | None
    cancel_job(job_id)    -> bool

Fix #872: scripts take POSITIONAL args (not --precision flags) and require
MULTIPLE invocations per variant.  fetch_steps on ModelVariant encodes the
exact argv for each invocation; a background worker iterates them
sequentially, stopping on the first nonzero exit.

fetch_model returns immediately so POST /api/comfyui/models/fetch can reply
202 without blocking the FastAPI request for a multi-hour download.
"""

from __future__ import annotations

import subprocess
import threading
import uuid
from pathlib import Path

from hal0.comfyui.capabilities import ModelVariant

# Scripts live at <repo-root>/installer/comfyui/scripts/
_SCRIPTS_DIR: Path = (
    Path(__file__).parent.parent.parent.parent / "installer" / "comfyui" / "scripts"
)

# Module-level job registry
_JOBS: dict[str, dict] = {}


def _run_sequence(rec: dict, script_path: str, fetch_steps: tuple[tuple[str, ...], ...]) -> None:
    """Background worker: run each fetch step sequentially.

    Stops on first nonzero exit (status='failed') or on cancellation
    (status='cancelled', set by cancel_job).  Marks 'done' when all
    steps exit 0.
    """
    for step_args in fetch_steps:
        if rec["status"] == "cancelled":
            return

        cmd = ["bash", script_path, *step_args]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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

    Steps run in a daemon thread; poll status via get_job().  Non-blocking so
    the 202-returning API endpoint does not stall on multi-hour downloads.
    """
    script_path = str(_SCRIPTS_DIR / variant.fetch_script)
    job_id = str(uuid.uuid4())

    rec: dict = {
        "id": job_id,
        "family": variant.family,
        "status": "running",
        "returncode": None,
        "script": script_path,
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
