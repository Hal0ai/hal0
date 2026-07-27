"""Regression: the model-fetch worker must not deadlock on large script output.

``_run_sequence`` used to launch each ``get_*.sh`` step with
``stdout=subprocess.PIPE`` (``stderr=STDOUT``) and then call ``proc.wait()``
WITHOUT ever draining the pipe. The Python docs warn this deadlocks once the
child fills the OS pipe buffer (~64 KB): the child blocks writing, the parent
blocks in ``wait()``. Real hf-download scripts emit MBs of progress output, so
every genuine multi-GB pull would hang forever in status ``running`` — the
worker never advances and the download never lands.

This test exercises the real I/O path (real ``subprocess.Popen``) with a step
that emits ~1 MB and asserts the job reaches a terminal state promptly.
"""

from __future__ import annotations

import subprocess as _subprocess
import time

from hal0.comfyui.capabilities import default_variant
from hal0.comfyui.fetch import fetch_model, get_job


def test_fetch_does_not_deadlock_on_large_script_output(monkeypatch, tmp_path):
    # A stand-in fetch step that writes ~1 MB to stdout — far past the OS pipe
    # buffer, like an hf download's progress stream.
    big = tmp_path / "big_output.sh"
    big.write_text("head -c 1000000 /dev/zero | tr '\\0' 'x'\n")

    real_popen = _subprocess.Popen

    def _redirect_popen(cmd, **kw):
        # Ignore the resolved get_*.sh path; run the big-output script with the
        # EXACT stdio kwargs _run_sequence chose, so the pipe-drain behaviour is
        # what's under test.
        return real_popen(["bash", str(big)], **kw)

    monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", _redirect_popen)

    variant = default_variant("image_upscale")  # esrgan: single empty-args step
    job_id = fetch_model(variant)

    deadline = time.monotonic() + 15.0
    status = "running"
    while time.monotonic() < deadline:
        job = get_job(job_id)
        if job is not None and job["status"] != "running":
            status = job["status"]
            break
        time.sleep(0.02)

    assert status == "done", (
        f"fetch worker did not finish (status={status!r}) — the child's stdout "
        "pipe was not drained, so proc.wait() deadlocked on >64 KB of output"
    )
