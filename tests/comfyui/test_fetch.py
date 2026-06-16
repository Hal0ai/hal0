"""Task 2.4: fetch_model TDD — mocked subprocess, no real downloads."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hal0.comfyui.capabilities import ModelVariant, default_variant
from hal0.comfyui.fetch import cancel_job, fetch_model, get_job

# ── helpers ───────────────────────────────────────────────────────────────────

LTX2_VARIANT = default_variant("txt2video")  # family=ltx2, precision=bf16, fetch_script=get_ltx2.sh
ESRGAN_VARIANT = default_variant("image_upscale")  # family=esrgan, precision=None, fetch_script=get_esrgan.sh


def _make_proc(returncode=None, pid=12345):
    """Return a mock Popen process."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.poll.return_value = returncode
    return proc


# ── tests ─────────────────────────────────────────────────────────────────────


class TestFetchModel:
    def test_returns_job_id_string(self, monkeypatch):
        proc = _make_proc()
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", lambda *a, **kw: proc)
        job_id = fetch_model(LTX2_VARIANT)
        assert isinstance(job_id, str) and len(job_id) > 0

    def test_ltx2_invokes_correct_script_with_precision(self, monkeypatch):
        captured = {}
        proc = _make_proc()

        def fake_popen(cmd, **kw):
            captured["cmd"] = cmd
            return proc

        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", fake_popen)
        fetch_model(LTX2_VARIANT)

        cmd = captured["cmd"]
        # script name must end with get_ltx2.sh
        assert cmd[0].endswith("get_ltx2.sh"), f"expected get_ltx2.sh, got {cmd[0]}"
        # --precision bf16 must appear
        assert "--precision" in cmd
        assert "bf16" in cmd

    def test_esrgan_invoked_without_precision(self, monkeypatch):
        """esrgan has precision=None — must NOT pass --precision."""
        captured = {}
        proc = _make_proc()

        def fake_popen(cmd, **kw):
            captured["cmd"] = cmd
            return proc

        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", fake_popen)
        fetch_model(ESRGAN_VARIANT)

        cmd = captured["cmd"]
        assert cmd[0].endswith("get_esrgan.sh"), f"expected get_esrgan.sh, got {cmd[0]}"
        assert "--precision" not in cmd

    def test_job_registered_as_running(self, monkeypatch):
        proc = _make_proc(returncode=None)  # still running
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", lambda *a, **kw: proc)
        job_id = fetch_model(LTX2_VARIANT)
        job = get_job(job_id)
        assert job is not None
        assert job["id"] == job_id
        assert job["status"] == "running"
        assert job["family"] == "ltx2"

    def test_job_has_script_field(self, monkeypatch):
        proc = _make_proc()
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", lambda *a, **kw: proc)
        job_id = fetch_model(LTX2_VARIANT)
        job = get_job(job_id)
        assert "script" in job
        assert job["script"].endswith("get_ltx2.sh")


class TestGetJob:
    def test_unknown_job_id_returns_none(self):
        result = get_job("nonexistent-job-id-xyz")
        assert result is None

    def test_job_done_when_proc_exits_0(self, monkeypatch):
        proc = _make_proc(returncode=0)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", lambda *a, **kw: proc)
        job_id = fetch_model(LTX2_VARIANT)
        job = get_job(job_id)
        assert job["status"] == "done"
        assert job["returncode"] == 0

    def test_job_failed_when_proc_exits_nonzero(self, monkeypatch):
        proc = _make_proc(returncode=1)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", lambda *a, **kw: proc)
        job_id = fetch_model(LTX2_VARIANT)
        job = get_job(job_id)
        assert job["status"] == "failed"
        assert job["returncode"] == 1


class TestCancelJob:
    def test_cancel_running_job_terminates_and_marks_cancelled(self, monkeypatch):
        proc = _make_proc(returncode=None)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", lambda *a, **kw: proc)
        job_id = fetch_model(LTX2_VARIANT)

        result = cancel_job(job_id)

        assert result is True
        proc.terminate.assert_called_once()
        job = get_job(job_id)
        assert job["status"] == "cancelled"

    def test_cancel_unknown_job_returns_false(self):
        result = cancel_job("does-not-exist")
        assert result is False

    def test_cancel_already_done_job_returns_false(self, monkeypatch):
        proc = _make_proc(returncode=0)
        monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", lambda *a, **kw: proc)
        job_id = fetch_model(LTX2_VARIANT)

        result = cancel_job(job_id)
        assert result is False
        # status must not change
        assert get_job(job_id)["status"] == "done"
