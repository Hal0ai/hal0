"""#2993: cancelling an FLM pull removes its container when SIGTERM is not enough.

The pull runs as ``sudo hal0-flmctl run-pull`` (an exec'd ``podman run``).
SIGTERM reaches flm through podman's signal proxy, but SIGKILL is not
proxied: killing the client would leave the container writing into the
store. So when the process outlives the grace period, ``run_flm_pull`` runs
the seam's ``stop-pull`` verb for the tag before it kills the process.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from hal0.registry import pull as pull_mod
from hal0.registry.pull import PullJob, run_flm_pull

# A pull that ignores SIGTERM (like a podman client whose container will not
# stop) and keeps printing until killed.
_STUBBORN_PULL = """
import signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    print("Downloading: 1%", flush=True)
    time.sleep(0.1)
"""

# A pull that exits on SIGTERM, as a container stopped by podman does.
_POLITE_PULL = """
import time
while True:
    print("Downloading: 1%", flush=True)
    time.sleep(0.1)
"""


def _setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str) -> Path:
    import hal0.capabilities.catalog as cat_mod
    import hal0.providers.flm as flm_mod

    store = str(tmp_path)
    marker = tmp_path / "stopped"
    monkeypatch.setattr(
        flm_mod, "flm_pull_command", lambda tag: ([sys.executable, "-c", script], store)
    )
    monkeypatch.setattr(
        flm_mod,
        "flm_pull_stop_command",
        lambda tag: [sys.executable, "-c", f"open({str(marker)!r}, 'w').write({tag!r})"],
    )
    monkeypatch.setattr(flm_mod, "flm_served_models", lambda: [])
    monkeypatch.setattr(flm_mod, "reset_flm_catalog_cache", lambda: None)
    monkeypatch.setattr(cat_mod, "reset_flm_image_present_cache", lambda: None)
    monkeypatch.setattr(pull_mod, "_FlmInstallPathLookup", lambda hmd, tag: lambda: None)
    monkeypatch.setattr(pull_mod, "_FLM_CANCEL_GRACE_S", 0.5)
    return marker


async def _run_and_cancel(job: PullJob) -> None:
    task = asyncio.create_task(run_flm_pull(job, tag="qwen3:0.6b", registry=object()))
    while job.state != "running":
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.5)
    job.cancel_requested = True
    await asyncio.wait_for(task, timeout=20)


async def test_cancel_removes_the_container_when_sigterm_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = _setup(monkeypatch, tmp_path, _STUBBORN_PULL)
    job = PullJob(job_id="j1", model_id="qwen3:0.6b")
    await _run_and_cancel(job)
    assert job.state == "cancelled"
    assert marker.read_text() == "qwen3:0.6b"


async def test_cancel_that_sigterm_ends_does_not_need_stop_pull(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = _setup(monkeypatch, tmp_path, _POLITE_PULL)
    job = PullJob(job_id="j2", model_id="qwen3:0.6b")
    await _run_and_cancel(job)
    assert job.state == "cancelled"
    assert not marker.exists()


async def test_stop_helper_tolerates_no_route_and_failures(tmp_path: Path) -> None:
    await pull_mod._stop_flm_pull_container(None, "qwen3:0.6b")
    await pull_mod._stop_flm_pull_container([sys.executable, "-c", "raise SystemExit(3)"], "a:b")
    await pull_mod._stop_flm_pull_container([str(tmp_path / "missing-binary")], "a:b")


async def test_task_cancel_at_shutdown_also_removes_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API shutdown cancels the task; that path must not just SIGKILL the client."""
    marker = _setup(monkeypatch, tmp_path, _STUBBORN_PULL)
    job = PullJob(job_id="j3", model_id="qwen3:0.6b")
    task = asyncio.create_task(run_flm_pull(job, tag="qwen3:0.6b", registry=object()))
    while job.state != "running":
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=20)
    assert job.state == "cancelled"
    assert marker.read_text() == "qwen3:0.6b"
