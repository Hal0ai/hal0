"""#2380: ``run_flm_pull`` walks the model dir off the event loop.

``_dir_size`` is a recursive filesystem walk. ``run_flm_pull`` runs it at pull
start, on every progress tick, and once after the pull. On a large or slow
models dir each walk ran on the event loop and stalled all of hal0-api for
its length, for the whole download. Each walk now runs on a worker thread.

The test stubs ``_dir_size`` with one that blocks and records whether it ran
on the event-loop thread. That record is the assertion: a wall-clock heartbeat
bound used to sit beside it, and it failed on a loaded CI runner (a 0.81 s gap
with every walk already off the loop), so it proved nothing the record does not.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import hal0.capabilities.catalog as cat_mod
import hal0.providers.flm as flm_mod
from hal0.registry import pull as pull_mod
from hal0.registry.pull import PullJob, run_flm_pull

#: How long the stub directory walk blocks.
_WALK_BLOCK_S = 0.5

# A fake "flm pull" that prints a line every 0.1 s for ~2 s, so the pull loop
# runs several progress ticks (each throttled to one per 0.5 s).
_FAKE_PULL = """
import os, sys, time
os.makedirs(os.path.join(sys.argv[1], "Repo"), exist_ok=True)
for i in range(20):
    print(f"Downloading: {i}", flush=True)
    time.sleep(0.1)
"""


class _SlowDirSize:
    """``_dir_size`` stand-in that blocks like a walk of a big models dir."""

    def __init__(self) -> None:
        """Start with no recorded walks."""
        self.on_loop: list[bool] = []
        self.size = 0

    def __call__(self, path: str | Path) -> int:
        """Record whether this thread runs an event loop, block, then grow."""
        try:
            asyncio.get_running_loop()
            self.on_loop.append(True)
        except RuntimeError:
            self.on_loop.append(False)
        time.sleep(_WALK_BLOCK_S)
        self.size += 1024
        return self.size


async def test_dir_size_walks_run_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every walk of the pull, start, ticks and final, runs off the event loop."""
    host_models_dir = str(tmp_path)
    monkeypatch.setattr(
        flm_mod,
        "flm_pull_command",
        lambda tag: ([sys.executable, "-c", _FAKE_PULL, host_models_dir], host_models_dir),
    )
    monkeypatch.setattr(flm_mod, "flm_served_models", lambda: [])

    async def _no_models() -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(flm_mod, "flm_served_models_async", _no_models)
    monkeypatch.setattr(flm_mod, "reset_flm_catalog_cache", lambda: None)
    monkeypatch.setattr(cat_mod, "reset_flm_image_present_cache", lambda: None)
    monkeypatch.setattr(pull_mod, "_register_flm_pulled", lambda *a, **k: None)
    monkeypatch.setattr(
        pull_mod, "_FlmInstallPathLookup", lambda hmd, tag: lambda: str(tmp_path / "Repo")
    )
    walk = _SlowDirSize()
    monkeypatch.setattr(pull_mod, "_dir_size", walk)

    job = PullJob(job_id="j1", model_id="fake:tag")
    await run_flm_pull(job, tag="fake:tag", registry=object())

    assert job.state == "completed", job
    # Start, at least one tick, and the post-pull walk.
    assert len(walk.on_loop) >= 3, walk.on_loop
    assert not any(walk.on_loop), f"_dir_size ran on the event loop: {walk.on_loop}"
    # Mid-pull progress still comes from the walk.
    assert job.bytes_downloaded > 0
