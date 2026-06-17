"""#872: fetch_model TDD — recording shim captures every Popen call argv.

Replaces old wrong-contract test that asserted --precision flag form for
positional-arg scripts.

fetch_model is NON-BLOCKING: it starts a daemon-thread worker and returns
immediately.  Tests wait for the worker to finish (via _wait_done) before
asserting terminal status / reading the recorder.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from hal0.comfyui.capabilities import CAPABILITIES, default_variant
from hal0.comfyui.fetch import cancel_job, fetch_model, get_job

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_proc(returncode=0, pid=12345):
    """Return a mock Popen process.  wait() returns returncode."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.poll.return_value = returncode
    proc.wait.return_value = returncode
    return proc


def _wait_done(job_id, timeout=5.0):
    """Poll get_job until status != 'running' (worker finished).  Returns the job."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = get_job(job_id)
        if job is not None and job["status"] != "running":
            return job
        time.sleep(0.005)
    raise AssertionError(f"job {job_id} still running after {timeout}s")


class _PopenRecorder:
    """Monkeypatch target: records every Popen call's argv list."""

    def __init__(self, procs):
        self._procs = list(procs)
        self._idx = 0
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        p = self._procs[self._idx % len(self._procs)]
        self._idx += 1
        return p


# ── variant fixtures ───────────────────────────────────────────────────────────

LTX2_T2V = default_variant("txt2video")  # ltx2: common / checkpoint bf16 / lora
LTX2_I2V = default_variant("img2video")  # ltx2 i2v
ESRGAN = default_variant("image_upscale")  # esrgan: single empty-args call
QWEN4STEP = default_variant("txt2img")  # qwen-image 4-step: choices 1+3
QWEN4STEP_IMG = default_variant("img2img")  # qwen-image-edit 4-step: choices 2+4

WAN22_T2V = CAPABILITIES["txt2video"].alternatives[2]
WAN22_I2V = CAPABILITIES["img2video"].alternatives[2]
HUNYUAN_T2V = CAPABILITIES["txt2video"].alternatives[1]


# ── TestFetchSteps: ModelVariant carries correct fetch_steps ──────────────────


class TestFetchSteps:
    def test_ltx2_t2v_has_three_steps(self):
        assert len(LTX2_T2V.fetch_steps) == 3

    def test_ltx2_t2v_step_order(self):
        steps = LTX2_T2V.fetch_steps
        assert steps[0] == ("common",)
        assert steps[1] == ("checkpoint", "bf16")
        assert steps[2] == ("lora",)

    def test_wan22_t2v_three_steps(self):
        steps = WAN22_T2V.fetch_steps
        assert len(steps) == 3
        assert steps[0] == ("common", "fp16")
        assert steps[1] == ("14b-t2v", "fp16")
        assert steps[2] == ("lora",)

    def test_wan22_i2v_uses_i2v_target(self):
        steps = WAN22_I2V.fetch_steps
        assert steps[1] == ("14b-i2v", "fp16")

    def test_qwen_4step_two_calls(self):
        steps = QWEN4STEP.fetch_steps
        assert len(steps) == 2
        assert steps[0] == ("1", "bf16")
        assert steps[1] == ("3", "bf16")

    def test_qwen_edit_4step_choices_2_and_4(self):
        steps = QWEN4STEP_IMG.fetch_steps
        assert steps[0] == ("2", "bf16")
        assert steps[1] == ("4", "bf16")

    def test_esrgan_one_empty_step(self):
        steps = ESRGAN.fetch_steps
        assert len(steps) == 1
        assert steps[0] == ()

    def test_hunyuan_t2v_no_precision_in_steps(self):
        for step in HUNYUAN_T2V.fetch_steps:
            assert "bf16" not in step and "fp16" not in step

    def test_sdxl_flag_form(self):
        sdxl = CAPABILITIES["txt2img"].alternatives[2]
        assert sdxl.fetch_steps == (("--precision", "fp16"),)


# ── TestFetchModel: multi-step invocation ─────────────────────────────────────


class TestFetchModel:
    def test_returns_job_id_string(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(0)] * 3)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(LTX2_T2V)
        assert isinstance(job_id, str) and len(job_id) > 0
        _wait_done(job_id)

    def test_non_blocking_returns_while_step_running(self, monkeypatch):
        """fetch_model must return BEFORE a slow step finishes; status=='running' then."""
        release = threading.Event()
        started = threading.Event()
        proc = _make_proc(0)

        def slow_wait():
            started.set()
            release.wait(timeout=5.0)
            return 0

        proc.wait.side_effect = slow_wait
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", lambda *a, **kw: proc)

        job_id = fetch_model(ESRGAN)
        # worker has entered the (blocked) wait()
        assert started.wait(timeout=5.0)
        # fetch_model already returned while the step is still in wait()
        assert get_job(job_id)["status"] == "running"
        # let it finish cleanly
        release.set()
        _wait_done(job_id)

    def test_ltx2_t2v_invokes_script_three_times(self, monkeypatch):
        """get_ltx2.sh called 3 times: common, checkpoint bf16, lora."""
        rec = _PopenRecorder([_make_proc(0)] * 3)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(LTX2_T2V)
        _wait_done(job_id)
        assert len(rec.calls) == 3

    def test_ltx2_t2v_step_argv(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(0)] * 3)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(LTX2_T2V)
        _wait_done(job_id)
        # call[0]: bash get_ltx2.sh common
        assert rec.calls[0][0] == "bash"
        assert rec.calls[0][1].endswith("get_ltx2.sh")
        assert rec.calls[0][2] == "common"
        # call[1]: bash get_ltx2.sh checkpoint bf16
        assert rec.calls[1][2:] == ["checkpoint", "bf16"]
        # call[2]: bash get_ltx2.sh lora
        assert rec.calls[2][2] == "lora"

    def test_wan22_t2v_14b_t2v_step(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(0)] * 3)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(WAN22_T2V)
        _wait_done(job_id)
        assert rec.calls[1][2:] == ["14b-t2v", "fp16"]

    def test_qwen_4step_two_calls_choices_1_and_3(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(0)] * 2)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(QWEN4STEP)
        _wait_done(job_id)
        assert len(rec.calls) == 2
        assert rec.calls[0][2:] == ["1", "bf16"]
        assert rec.calls[1][2:] == ["3", "bf16"]

    def test_esrgan_one_call_no_extra_args(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(0)])
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(ESRGAN)
        _wait_done(job_id)
        assert len(rec.calls) == 1
        # argv: ["bash", "<path>/get_esrgan.sh"]
        assert len(rec.calls[0]) == 2
        assert rec.calls[0][1].endswith("get_esrgan.sh")

    def test_job_has_family_field(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(0)] * 3)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(LTX2_T2V)
        assert get_job(job_id)["family"] == "ltx2"
        _wait_done(job_id)

    def test_job_has_script_field(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(0)] * 3)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(LTX2_T2V)
        job = get_job(job_id)
        assert "script" in job
        assert job["script"].endswith("get_ltx2.sh")
        _wait_done(job_id)

    def test_nonzero_step_marks_failed_and_stops(self, monkeypatch):
        """Step 0 fails → job=failed, step 1+ NOT called."""
        rec = _PopenRecorder([_make_proc(1)])  # only 1 proc; if called again it would loop
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(LTX2_T2V)
        job = _wait_done(job_id)
        assert job["status"] == "failed"
        assert len(rec.calls) == 1

    def test_all_steps_done_marks_done(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(0)] * 3)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(LTX2_T2V)
        job = _wait_done(job_id)
        assert job["status"] == "done"
        assert job["returncode"] == 0


# ── TestGetJob ────────────────────────────────────────────────────────────────


class TestGetJob:
    def test_unknown_job_id_returns_none(self):
        assert get_job("nonexistent-xyz") is None

    def test_job_done_when_all_procs_exit_0(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(0)] * 3)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(LTX2_T2V)
        assert _wait_done(job_id)["status"] == "done"

    def test_job_failed_when_first_step_nonzero(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(1)])
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(ESRGAN)
        job = _wait_done(job_id)
        assert job["status"] == "failed"
        assert job["returncode"] == 1


# ── TestCancelJob ─────────────────────────────────────────────────────────────


class TestCancelJob:
    def test_cancel_unknown_job_returns_false(self):
        assert cancel_job("does-not-exist") is False

    def test_cancel_done_job_returns_false(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(0)])
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(ESRGAN)
        _wait_done(job_id)
        # job is done now; cancel should return False
        assert cancel_job(job_id) is False

    def test_cancel_failed_job_returns_false(self, monkeypatch):
        rec = _PopenRecorder([_make_proc(1)])
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)
        job_id = fetch_model(ESRGAN)
        _wait_done(job_id)
        assert cancel_job(job_id) is False

    def test_cancel_running_job_terminates_and_marks_cancelled(self, monkeypatch):
        """Cancel a job whose current step is still in wait(); proc.terminate() fires."""
        release = threading.Event()
        started = threading.Event()
        proc = _make_proc(0)

        def slow_wait():
            started.set()
            release.wait(timeout=5.0)
            return 0

        proc.wait.side_effect = slow_wait
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", lambda *a, **kw: proc)

        job_id = fetch_model(LTX2_T2V)
        assert started.wait(timeout=5.0)  # worker is blocked in step 0's wait()

        result = cancel_job(job_id)
        assert result is True
        proc.terminate.assert_called_once()
        assert get_job(job_id)["status"] == "cancelled"

        # unblock worker; it must observe cancelled and NOT advance to step 1
        release.set()
        time.sleep(0.05)
        assert get_job(job_id)["status"] == "cancelled"
