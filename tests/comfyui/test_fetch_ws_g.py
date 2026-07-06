"""#1110 (WS-G): ComfyUI fetch fixes.

Covers the three ways the fetch was feature-dead before this change:
  1. The vendored ``get_*.sh`` scripts hard-coded the ComfyUI container's
     ``hf`` path (``/opt/venv/bin/hf``) but run as HOST subprocesses, so every
     download failed with "no such file". They must now resolve ``hf`` from the
     host PATH, falling back to the container venv path.
  2. ``HF_TOKEN`` / ``HF_HOME`` were never forwarded to the fetch subprocess.
  3. Most curated workflow JSONs were never shipped, and selecting a variant
     never provisioned its matching workflow.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from unittest.mock import MagicMock

from hal0.comfyui.capabilities import CAPABILITIES, default_variant
from hal0.comfyui.fetch import _fetch_env, _provision_workflow, fetch_model, get_job

REPO = Path(__file__).parent.parent.parent
SCRIPTS_DIR = REPO / "installer" / "comfyui" / "scripts"
WORKFLOWS_SRC = REPO / "installer" / "comfyui" / "workflows"

# Scripts that use the `hf` CLI (get_esrgan.sh uses curl only).
HF_SCRIPTS = [
    "get_ltx2.sh",
    "get_sdxl.sh",
    "get_qwen_image.sh",
    "get_hunyuan15.sh",
    "get_wan22.sh",
]


def _wait_done(job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = get_job(job_id)
        if job is not None and job["status"] != "running":
            return job
        time.sleep(0.005)
    raise AssertionError(f"job {job_id} still running after {timeout}s")


class _PopenRecorder:
    def __init__(self, procs):
        self._procs = list(procs)
        self._idx = 0
        self.calls: list[list[str]] = []
        self.envs: list[dict | None] = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        self.envs.append(kw.get("env"))
        p = self._procs[self._idx % len(self._procs)]
        self._idx += 1
        return p


def _make_proc(returncode=0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.wait.return_value = returncode
    return proc


# ── (1) host-resolvable hf ────────────────────────────────────────────────────


def test_no_script_hardcodes_container_hf_path():
    """No get_*.sh may pin the download tool to the container venv path alone."""
    offenders = []
    for name in HF_SCRIPTS:
        text = (SCRIPTS_DIR / name).read_text()
        if re.search(r'^HF="/opt/venv/bin/hf"\s*$', text, flags=re.MULTILINE):
            offenders.append(name)
    assert not offenders, (
        f"scripts still hard-code the container hf path: {offenders} — "
        "they must resolve `hf` from the host PATH"
    )


def test_hf_scripts_resolve_from_path_with_container_fallback():
    """Each hf-using script resolves `hf` from PATH, keeping the venv fallback."""
    for name in HF_SCRIPTS:
        text = (SCRIPTS_DIR / name).read_text()
        assert "command -v hf" in text, f"{name} does not resolve hf from PATH"
        assert "/opt/venv/bin/hf" in text, f"{name} dropped the container fallback"


# ── (2) HF_TOKEN / HF_HOME forwarding ─────────────────────────────────────────


def test_fetch_env_promotes_hf_token(monkeypatch):
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("HF_TOKEN", "hf_secret_abc")
    env = _fetch_env()
    assert env["HF_TOKEN"] == "hf_secret_abc"
    assert env["HUGGING_FACE_HUB_TOKEN"] == "hf_secret_abc"


def test_fetch_env_mirrors_legacy_token_name(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_legacy_xyz")
    env = _fetch_env()
    assert env["HF_TOKEN"] == "hf_legacy_xyz"


def test_fetch_env_forwards_hf_home(monkeypatch):
    monkeypatch.setenv("HF_HOME", "/mnt/ai-models/hf-cache")
    assert _fetch_env()["HF_HOME"] == "/mnt/ai-models/hf-cache"


def test_fetch_subprocess_receives_hf_token(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_TOKEN", "hf_run_token")
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(tmp_path / "wf"))
    rec = _PopenRecorder([_make_proc(0)])
    monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)

    job_id = fetch_model(default_variant("image_upscale"))
    _wait_done(job_id)

    assert rec.envs and rec.envs[0] is not None
    assert rec.envs[0]["HF_TOKEN"] == "hf_run_token"


# ── (3) curated workflow provisioning ─────────────────────────────────────────


def _registry_workflow_names() -> set[str]:
    names = set()
    for cap in CAPABILITIES.values():
        for v in cap.alternatives:
            names.add(v.workflow)
    return names


def test_every_registry_workflow_json_is_shipped():
    missing = [n for n in _registry_workflow_names() if not (WORKFLOWS_SRC / n).is_file()]
    assert not missing, f"curated workflow JSONs not shipped: {missing}"


def test_shipped_workflows_are_api_format_json():
    import json

    for name in _registry_workflow_names():
        data = json.loads((WORKFLOWS_SRC / name).read_text())
        assert isinstance(data, dict) and data, f"{name} is not a non-empty object"
        # API-format ComfyUI graphs are keyed by node id → {class_type, inputs}.
        assert all("class_type" in node for node in data.values()), (
            f"{name} is not API-format (missing class_type on some node)"
        )


def test_provision_workflow_copies_matching_json(tmp_path, monkeypatch):
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(tmp_path / "wf"))
    variant = CAPABILITIES["txt2img"].alternatives[2]  # sdxl
    dest = _provision_workflow(variant)
    assert dest is not None
    assert Path(dest).is_file()
    assert Path(dest).name == variant.workflow


def test_fetch_model_provisions_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(tmp_path / "wf"))
    rec = _PopenRecorder([_make_proc(0)])
    monkeypatch.setattr("hal0.comfyui.fetch.subprocess.Popen", rec)

    variant = CAPABILITIES["txt2img"].alternatives[2]  # sdxl
    job_id = fetch_model(variant)
    job = _wait_done(job_id)

    assert job["workflow"] is not None
    assert Path(job["workflow"]).name == variant.workflow
    assert Path(job["workflow"]).is_file()
