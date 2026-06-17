"""Task 2.4: Async model fetch wrapper for ComfyUI scripts.

Public API:
    fetch_model(variant)  -> job_id   (runs all fetch_steps sequentially)
    get_job(job_id)       -> dict | None
    cancel_job(job_id)    -> bool

Fix #872: scripts take POSITIONAL args (not --precision flags) and require
MULTIPLE invocations per variant.  fetch_steps on ModelVariant encodes the
exact argv for each invocation; we iterate them sequentially, stopping on
the first nonzero exit.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from hal0.comfyui.capabilities import ModelVariant

# Scripts live at <repo-root>/installer/comfyui/scripts/
_SCRIPTS_DIR: Path = (
    Path(__file__).parent.parent.parent.parent / "installer" / "comfyui" / "scripts"
)

# Module-level job registry
_JOBS: dict[str, dict] = {}


def fetch_model(variant: ModelVariant) -> str:
    """Run all fetch_steps for *variant* sequentially.

    Each step: bash <script> *step_args.  Stops on first nonzero exit.
    Returns a job_id.  Job status is final when fetch_model returns.
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
    }
    _JOBS[job_id] = rec

    for step_args in variant.fetch_steps:
        cmd = ["bash", script_path, *step_args]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        rec["_proc"] = proc

        rc = proc.wait()

        if rec["status"] == "cancelled":
            return job_id

        if rc != 0:
            rec["returncode"] = rc
            rec["status"] = "failed"
            return job_id

    rec["returncode"] = 0
    rec["status"] = "done"
    return job_id


def get_job(job_id: str) -> dict | None:
    """Return job dict (without internal fields) or None if unknown."""
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
