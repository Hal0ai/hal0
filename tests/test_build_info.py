"""hal0.build_info.build_sha — #1550/H7.

`scripts/deploy.sh` polls `/api/status`'s `build_sha` after restarting
hal0-api to prove the RUNNING process picked up the new tree, not merely
that the checkout's files moved. Two properties matter: it finds the real
sha for a git-tracked tree, and it degrades to `None` (never raises) when
there's nothing to find — a broken probe must not take `/api/status` down
with it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import hal0
from hal0.build_info import build_sha, serving_release


def test_finds_the_sha_of_this_checkout() -> None:
    build_sha.cache_clear()
    repo_root = Path(__file__).resolve().parents[1]
    expected = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--short=12", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert build_sha() == expected


def test_returns_none_outside_any_git_tree(tmp_path, monkeypatch) -> None:
    build_sha.cache_clear()
    fake_pkg_file = tmp_path / "nested" / "hal0" / "build_info.py"
    fake_pkg_file.parent.mkdir(parents=True)
    fake_pkg_file.write_text("", encoding="utf-8")
    monkeypatch.setattr("hal0.build_info.__file__", str(fake_pkg_file))
    assert build_sha() is None


def test_never_raises_on_a_broken_git_binary(tmp_path, monkeypatch) -> None:
    build_sha.cache_clear()
    tree = tmp_path / "checkout" / "src" / "hal0"
    tree.mkdir(parents=True)
    (tmp_path / "checkout" / ".git").mkdir()
    monkeypatch.setattr("hal0.build_info.__file__", str(tree / "build_info.py"))

    def _boom(*a, **k):
        raise OSError("git not found")

    monkeypatch.setattr("hal0.build_info.subprocess.run", _boom)
    assert build_sha() is None
    build_sha.cache_clear()


def test_result_is_cached_for_the_process_lifetime(monkeypatch) -> None:
    """The whole point (#1550): a deploy's restart must be observable, so a
    stale worker must never re-read a `.git` HEAD that moved after it started."""
    build_sha.cache_clear()
    calls = {"n": 0}
    real_run = subprocess.run

    def _counting_run(*a, **k):
        calls["n"] += 1
        return real_run(*a, **k)

    monkeypatch.setattr("hal0.build_info.subprocess.run", _counting_run)
    first = build_sha()
    second = build_sha()
    assert first == second
    assert calls["n"] <= 1
    build_sha.cache_clear()


# serving_release — #3969. The release the process started from, nightly
# suffix included, read once so an update or rollback that moves ``current``
# under a running daemon does not change the answer.


@pytest.fixture
def release_root(tmp_path, monkeypatch):
    """A throwaway install root whose ``current`` tree the test controls."""
    root = tmp_path / "usr-lib" / "hal0"
    (root / "current").mkdir(parents=True)
    monkeypatch.setattr("hal0.updater.updater._usr_lib_root", lambda: root)
    serving_release.cache_clear()
    yield root
    serving_release.cache_clear()


def test_serving_release_reads_the_release_trees_version(release_root) -> None:
    (release_root / "current" / "VERSION").write_text("1.8.1-nightly.20261127155122\n")
    assert serving_release() == "1.8.1-nightly.20261127155122"


def test_serving_release_keeps_its_first_answer_when_current_moves(release_root) -> None:
    version = release_root / "current" / "VERSION"
    version.write_text("1.8.1-nightly.20261127155122\n")
    assert serving_release() == "1.8.1-nightly.20261127155122"
    version.write_text("1.8.1\n")  # a rollback swapped the tree under the daemon
    assert serving_release() == "1.8.1-nightly.20261127155122"


def test_serving_release_falls_back_to_the_package_version(release_root) -> None:
    assert not (release_root / "current" / "VERSION").exists()
    assert serving_release() == hal0.__version__


def test_api_startup_reads_the_serving_release_before_anything_can_move_it(
    release_root, tmp_path, monkeypatch
) -> None:
    """The lifespan fills the cache. Without that, the first reader would be
    the rollback route itself, after ``current`` had already moved."""
    from fastapi.testclient import TestClient

    from hal0.api import create_app

    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "etc" / "hal0").mkdir(parents=True)
    version = release_root / "current" / "VERSION"
    version.write_text("1.8.1-nightly.20261127155122\n")
    with TestClient(create_app()):
        assert serving_release.cache_info().currsize == 1
        version.write_text("1.8.1\n")
        assert serving_release() == "1.8.1-nightly.20261127155122"
