"""Regression test for FLM pull progress recovering a transient-None target_dir.

The install-path probe (``flm list``) can transiently return an empty catalog
right at pull start, leaving ``target_dir`` None. Before the fix that killed
progress for the whole download — the dir grew on disk but the job reported
``0/0`` until completion. ``run_flm_pull`` now re-resolves ``target_dir`` each
tick, so progress recovers mid-download.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hal0.registry import pull as pull_mod
from hal0.registry.pull import PullJob, run_flm_pull

# A fake "flm pull": create <host_models_dir>/Repo and grow model.bin in
# chunks, printing an FLM-style Downloading line + flushing between writes so
# the poller sees real on-disk growth across several ticks.
_FAKE_PULL = """
import os, sys, time
target = os.path.join(sys.argv[1], "Repo")
os.makedirs(target, exist_ok=True)
p = os.path.join(target, "model.bin")
with open(p, "wb") as f:
    for i in range(5):
        f.write(b"x" * (2 * 1024 * 1024))
        f.flush(); os.fsync(f.fileno())
        print(f"Downloading: {(i + 1) * 20}%", flush=True)
        time.sleep(0.4)
"""


class _RecordingJob(PullJob):
    """PullJob that snapshots (state, bytes_downloaded) on every _signal()."""

    def __init__(self) -> None:
        super().__init__(job_id="j1", model_id="fake:tag")
        self.snapshots: list[tuple[str, int]] = []

    def _signal(self) -> None:
        self.snapshots.append((self.state, self.bytes_downloaded))
        super()._signal()


async def test_progress_recovers_transient_none_target_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_models_dir = str(tmp_path)
    target = tmp_path / "Repo"

    import hal0.capabilities.catalog as cat_mod
    import hal0.providers.flm as flm_mod

    monkeypatch.setattr(
        flm_mod,
        "flm_pull_command",
        lambda tag: ([sys.executable, "-c", _FAKE_PULL, host_models_dir], host_models_dir),
    )
    monkeypatch.setattr(flm_mod, "ensure_host_flm_store_link", lambda: host_models_dir)
    monkeypatch.setattr(flm_mod, "flm_host_async_spawn", lambda argv: (argv, {}))
    monkeypatch.setattr(flm_mod, "flm_served_models", lambda: [])
    monkeypatch.setattr(flm_mod, "reset_flm_catalog_cache", lambda: None)
    monkeypatch.setattr(cat_mod, "reset_flm_image_present_cache", lambda: None)
    monkeypatch.setattr(pull_mod, "_register_flm_pulled", lambda *a, **k: None)

    # Simulate the transient: the probe returns None for the first two calls
    # (pull-start + first tick), then resolves. Without the per-tick retry this
    # would strand target_dir at None for the whole download.
    calls = {"n": 0}

    def _flaky_install_path(hmd: str, tag: str) -> str | None:
        calls["n"] += 1
        return None if calls["n"] <= 2 else str(target)

    monkeypatch.setattr(pull_mod, "_flm_install_path", _flaky_install_path)

    job = _RecordingJob()
    registry = object()  # unused: _register_flm_pulled is stubbed
    await run_flm_pull(job, tag="fake:tag", registry=registry)

    assert job.state == "completed"
    # The fix: at least one signal WHILE running carried non-zero bytes — i.e.
    # progress moved mid-download, not only at completion.
    running_progress = [b for (state, b) in job.snapshots if state == "running" and b > 0]
    assert running_progress, f"no mid-download progress; snapshots={job.snapshots}"
    # And it ended reporting the full on-disk size.
    assert job.bytes_downloaded == 10 * 1024 * 1024
