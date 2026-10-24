"""#2446: an FLM pull only reports success at a path that exists.

``run_flm_pull`` used to synthesise ``job.path`` from the store and the tag's
HF repo name without looking at disk, and printed ``Done. <tag> → <store>/<repo>``
for a directory that did not exist. The pull now checks the path before it
registers the model. Since #2993 the pull runs in the flm runner image with the
store bind-mounted, so the job also fails cleanly when that image is missing or
unreachable.
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

_REPO = "Gemma3-1B-NPU2"
_ENTRY = {"model": "gemma3:1b", "url": f"https://huggingface.co/FastFlowLM/{_REPO}/resolve/main/x"}

# A fake "flm pull" that writes the repo dir under argv[1].
_FAKE_PULL = """
import os, sys
os.makedirs(os.path.join(sys.argv[1], sys.argv[2]), exist_ok=True)
print("Downloading: 100.0% (1.0MB / 1.0MB)", flush=True)
"""


@pytest.fixture
def stub_flm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Stub flm side effects; return a setter for where the fake pull writes."""
    store = tmp_path / "store"
    default = tmp_path / "home" / ".config" / "flm" / "models"
    store.mkdir(parents=True)
    default.mkdir(parents=True)
    registered: list[dict[str, Any]] = []

    def _configure(write_to: Path | None) -> tuple[Path, Path, list[dict[str, Any]]]:
        argv = (
            [sys.executable, "-c", _FAKE_PULL, str(write_to), _REPO]
            if write_to is not None
            else [sys.executable, "-c", "pass"]
        )
        monkeypatch.setattr(flm_mod, "flm_pull_command", lambda tag: (argv, str(store)))
        return store, default, registered

    monkeypatch.setattr("hal0.config.paths.default_flm_models_dir", lambda: str(default))
    monkeypatch.setattr(flm_mod, "_probe_flm_catalog", lambda: [_ENTRY])
    monkeypatch.setattr(flm_mod, "flm_served_models", lambda: [])

    async def _no_models() -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(flm_mod, "flm_served_models_async", _no_models)
    monkeypatch.setattr(flm_mod, "reset_flm_catalog_cache", lambda: None)
    monkeypatch.setattr(cat_mod, "reset_flm_image_present_cache", lambda: None)
    monkeypatch.setattr(
        pull_mod, "_register_flm_pulled", lambda registry, **kw: registered.append(kw)
    )
    return _configure


async def test_pull_into_store_completes_at_real_path(stub_flm) -> None:
    store, _default, registered = stub_flm(None)
    stub_flm(store)  # the link works: flm writes into the store
    job = PullJob(job_id="j1", model_id="gemma3:1b")
    await run_flm_pull(job, tag="gemma3:1b", registry=object())

    assert job.state == "completed", job
    assert job.path == str(store / _REPO)
    assert Path(job.path).is_dir()
    assert registered and registered[0]["path"] == str(store / _REPO)


async def test_pull_that_wrote_nothing_fails(stub_flm) -> None:
    store, _default, registered = stub_flm(None)
    job = PullJob(job_id="j1", model_id="gemma3:1b")
    await run_flm_pull(job, tag="gemma3:1b", registry=object())

    assert job.state == "failed", job
    assert str(store / _REPO) in (job.error or "")
    assert registered == []


async def test_missing_runner_image_fails_with_a_download_hint(stub_flm, monkeypatch) -> None:
    """hal0-flmctl rc 70 (image not in root's store) names the fix."""
    from hal0.providers.flm_cli import RC_IMAGE_MISSING

    store, _default, registered = stub_flm(None)
    argv = [sys.executable, "-c", f"import sys; sys.exit({RC_IMAGE_MISSING})"]
    monkeypatch.setattr(flm_mod, "flm_pull_command", lambda tag: (argv, str(store)))
    job = PullJob(job_id="j1", model_id="gemma3:1b")
    await run_flm_pull(job, tag="gemma3:1b", registry=object())

    assert job.state == "failed", job
    assert "hal0 runner-images pull flm" in (job.error or "")
    assert registered == []


async def test_unreachable_runner_fails_the_job_without_spawning(
    stub_flm, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No route into the runner image: the job fails, nothing runs on the host."""
    from hal0.providers.flm_cli import FLMRunnerUnavailableError

    _store, _default, registered = stub_flm(None)

    def _raise(tag: str) -> tuple[list[str], str]:
        raise FLMRunnerUnavailableError("cannot reach the flm runner image")

    monkeypatch.setattr(flm_mod, "flm_pull_command", _raise)
    job = PullJob(job_id="j1", model_id="gemma3:1b")
    await run_flm_pull(job, tag="gemma3:1b", registry=object())

    assert job.state == "failed", job
    assert job.error_code == "model.flm_runner_unavailable"
    assert registered == []
