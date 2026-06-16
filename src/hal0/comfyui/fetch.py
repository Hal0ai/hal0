"""Task 2.4: Async model fetch wrapper for ComfyUI scripts.

Public API:
    fetch_model(variant)  -> job_id   (starts subprocess, non-blocking)
    get_job(job_id)       -> dict | None
    cancel_job(job_id)    -> bool
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

# Module-level job registry: job_id -> {"id", "family", "status", "returncode", "script", "_proc"}
_JOBS: dict[str, dict] = {}


def fetch_model(variant: ModelVariant) -> str:
    """Launch fetch script for *variant* as a background subprocess.

    Returns a job_id.  The script is run from _SCRIPTS_DIR; stdout/stderr
    go to PIPE (captured but not streamed — YAGNI until progress API exists).
    """
    script_path = str(_SCRIPTS_DIR / variant.fetch_script)
    cmd = [script_path]
    if variant.precision is not None:
        cmd += ["--precision", variant.precision]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {
        "id": job_id,
        "family": variant.family,
        "status": "running",
        "returncode": None,
        "script": script_path,
        "_proc": proc,
    }
    return job_id


def get_job(job_id: str) -> dict | None:
    """Return job dict (without _proc) or None if unknown.

    Status is refreshed from proc.poll() on each call.
    """
    rec = _JOBS.get(job_id)
    if rec is None:
        return None

    # Refresh status if still running
    if rec["status"] == "running":
        rc = rec["_proc"].poll()
        if rc is not None:
            rec["returncode"] = rc
            rec["status"] = "done" if rc == 0 else "failed"

    return {k: v for k, v in rec.items() if k != "_proc"}


def cancel_job(job_id: str) -> bool:
    """Terminate a running job.  Returns True if terminated, False otherwise."""
    rec = _JOBS.get(job_id)
    if rec is None:
        return False

    # Refresh status first
    get_job(job_id)

    if rec["status"] != "running":
        return False

    rec["_proc"].terminate()
    rec["status"] = "cancelled"
    return True
