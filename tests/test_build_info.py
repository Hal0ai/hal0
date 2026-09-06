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

from hal0.build_info import build_sha


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
