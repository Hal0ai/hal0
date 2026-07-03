"""Test that ``_pull_root`` honours [models].store with pull_root fallback."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from hal0.config import paths
from hal0.config.loader import save_hal0_config
from hal0.config.schema import Hal0Config, ModelsConfig


def test_pull_root_defaults_to_pull_root_when_store_unset(
    tmp_hal0_home: str,
) -> None:
    cfg = Hal0Config()
    save_hal0_config(cfg)
    from hal0.registry.pull import _pull_root

    assert _pull_root() == Path(cfg.models.pull_root)


def test_pull_root_uses_store_when_set(tmp_hal0_home: str, tmp_path: Path) -> None:
    ext = tmp_path / "mnt-ai"
    ext.mkdir()
    cfg = Hal0Config(
        models=ModelsConfig(
            roots=[str(paths.models_dir())],
            pull_root=str(paths.models_dir()),
            store=str(ext),
        ),
    )
    save_hal0_config(cfg)
    from hal0.registry.pull import _pull_root

    assert _pull_root() == ext


def test_effective_store_picks_pull_root_fallback() -> None:
    """Backward compat — PR-#313 installs without `store` keep working."""
    models = ModelsConfig(
        roots=["/some/root"],
        pull_root="/legacy/path",
    )
    assert models.effective_store() == "/legacy/path"


def test_effective_store_prefers_explicit_store() -> None:
    models = ModelsConfig(
        roots=["/some/root"],
        pull_root="/legacy/path",
        store="/new/store",
    )
    assert models.effective_store() == "/new/store"


# ── sweep_pull_jobs — GC of stale terminal snapshots (#MR-8) ────────────────


def _write_snapshot(model_id: str, state: str, age_days: float) -> Path:
    """Drop a snapshot for ``model_id`` in ``state`` with an aged mtime."""
    from hal0.registry.pull import _pull_jobs_dir

    jobs_dir = _pull_jobs_dir()
    jobs_dir.mkdir(parents=True, exist_ok=True)
    path = jobs_dir / f"{model_id}.json"
    path.write_text(json.dumps({"model_id": model_id, "state": state}), encoding="utf-8")
    if age_days:
        when = time.time() - age_days * 86400
        os.utime(path, (when, when))
    return path


def test_sweep_pull_jobs_reaps_only_old_terminal(tmp_hal0_home: str) -> None:
    """Old terminal snapshot is reaped; fresh terminal + old running survive."""
    from hal0.registry.pull import sweep_pull_jobs

    old_terminal = _write_snapshot("old-done", "completed", age_days=30)
    fresh_terminal = _write_snapshot("fresh-done", "completed", age_days=0)
    old_running = _write_snapshot("old-running", "running", age_days=30)

    removed = sweep_pull_jobs(max_age_days=14)

    assert removed == 1
    assert not old_terminal.exists()
    assert fresh_terminal.exists()
    assert old_running.exists()  # non-terminal preserved regardless of age


def test_sweep_pull_jobs_missing_dir_returns_zero(tmp_hal0_home: str) -> None:
    """A missing jobs directory is a no-op, not an error."""
    from hal0.registry.pull import _pull_jobs_dir, sweep_pull_jobs

    assert not _pull_jobs_dir().exists()
    assert sweep_pull_jobs() == 0


# ── MR-13: persist_pull_job fsyncs the parent directory ──────────────────────


def test_persist_pull_job_fsyncs_parent_dir(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """persist_pull_job fsyncs the snapshot's parent dir after os.replace."""
    import hal0.registry.pull as pull_mod
    from hal0.registry.pull import make_job, persist_pull_job, pull_job_file

    calls: list[Path] = []
    real = pull_mod._fsync_dir

    def _spy(dir_path: Path) -> None:
        calls.append(dir_path)
        real(dir_path)

    monkeypatch.setattr(pull_mod, "_fsync_dir", _spy)

    job = make_job("qwen3-4b")
    persist_pull_job(job)

    snapshot = pull_job_file(job.model_id)
    assert snapshot.exists()
    assert calls == [snapshot.parent]


def test_persist_pull_job_survives_dir_fsync_failure(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed directory fsync stays fail-soft — the snapshot still lands."""
    import hal0.registry.pull as pull_mod
    from hal0.registry.pull import make_job, persist_pull_job, pull_job_file

    real_open = pull_mod.os.open

    def _flaky_open(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
        if flags & getattr(pull_mod.os, "O_DIRECTORY", 0):
            raise OSError("no O_DIRECTORY here")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(pull_mod.os, "open", _flaky_open)

    job = make_job("qwen3-4b")
    persist_pull_job(job)  # must not raise

    assert pull_job_file(job.model_id).exists()
