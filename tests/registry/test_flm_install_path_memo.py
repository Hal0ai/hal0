"""#2379: an FLM pull runs ``flm list -j`` once, not once per progress tick.

``run_flm_pull`` retries the install-path lookup every tick until it resolves.
The lookup used to shell ``flm list -j`` (``_probe_flm_catalog``, uncached, up
to 30 s) on every retry, so a tag whose ``url`` never yields a path spawned
``flm list`` about once a second for the whole download. The lookup now keeps
the catalog it read for the life of the pull, and probes again only while the
catalog comes back empty (the transient that the per-tick retry exists for).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

import hal0.capabilities.catalog as cat_mod
import hal0.providers.flm as flm_mod
from hal0.registry import pull as pull_mod
from hal0.registry.pull import PullJob, run_flm_pull

# A fake "flm pull" that prints a line every 0.1 s for ~1.5 s, so the pull
# loop runs many progress ticks.
_FAKE_PULL = """
import os, sys, time
os.makedirs(os.path.join(sys.argv[1], "Qwen3-0.6B-NPU2"), exist_ok=True)
for i in range(15):
    print(f"Downloading: {i}", flush=True)
    time.sleep(0.1)
"""


class _CountingFlmList:
    """``_probe_flm_catalog`` stand-in that counts calls."""

    def __init__(self, answers: list[list[dict[str, Any]] | None]) -> None:
        """Answer with ``answers`` in turn, then repeat the last one."""
        self.answers = answers
        self.calls = 0

    def __call__(self) -> list[dict[str, Any]] | None:
        """Count the call and return the next answer."""
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return answer


@pytest.fixture
def fake_flm_pull(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stub every FLM side effect of ``run_flm_pull`` except the install-path probe."""
    host_models_dir = str(tmp_path)
    monkeypatch.setattr(
        flm_mod,
        "flm_pull_command",
        lambda tag: ([sys.executable, "-c", _FAKE_PULL, host_models_dir], host_models_dir),
    )
    monkeypatch.setattr(flm_mod, "ensure_host_flm_store_link", lambda: host_models_dir)
    monkeypatch.setattr(flm_mod, "flm_host_async_spawn", lambda argv: (argv, {}))
    # The advertised-size lookup reads the separate cached catalog; keep it
    # out of the probe count.
    monkeypatch.setattr(flm_mod, "flm_served_models", lambda: [])

    async def _no_models() -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(flm_mod, "flm_served_models_async", _no_models)
    monkeypatch.setattr(flm_mod, "reset_flm_catalog_cache", lambda: None)
    monkeypatch.setattr(cat_mod, "reset_flm_image_present_cache", lambda: None)
    monkeypatch.setattr(pull_mod, "_register_flm_pulled", lambda *a, **k: None)
    return tmp_path


async def test_unresolvable_tag_probes_flm_list_once_per_pull(
    fake_flm_pull: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's reproduction: a tag absent from ``flm list`` never resolves.

    Before the fix every tick re-ran ``flm list -j``; now the whole pull,
    including the post-pull path lookup, reads it once.
    """
    probe = _CountingFlmList([[{"model": "other:1b", "url": "https://huggingface.co/O/R/x"}]])
    monkeypatch.setattr(flm_mod, "_probe_flm_catalog", probe)

    job = PullJob(job_id="j1", model_id="ghost:1b")
    await run_flm_pull(job, tag="ghost:1b", registry=object())

    assert job.state == "completed", job
    assert job.path == str(fake_flm_pull)  # fell back to the bare host dir
    assert probe.calls == 1, f"`flm list -j` ran {probe.calls} times in one pull"


async def test_empty_catalog_is_retried_then_kept(
    fake_flm_pull: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transiently empty ``flm list`` is retried; the first real answer is kept."""
    entry = {
        "model": "qwen3:0.6b",
        "url": "https://huggingface.co/FastFlowLM/Qwen3-0.6B-NPU2/resolve/main/x",
    }
    probe = _CountingFlmList([[], None, [entry]])
    monkeypatch.setattr(flm_mod, "_probe_flm_catalog", probe)

    job = PullJob(job_id="j1", model_id="qwen3:0.6b")
    await run_flm_pull(job, tag="qwen3:0.6b", registry=object())

    assert job.state == "completed", job
    assert job.path == str(fake_flm_pull / "Qwen3-0.6B-NPU2")
    assert probe.calls == 3, f"expected two empty answers then one real one, got {probe.calls}"
