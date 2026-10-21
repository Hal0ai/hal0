"""The release builder's git steps against a real local upstream (no network).

A ``file://`` bare upstream with a tagged commit stands in for GitHub. git
runs for real (mirror init with the partial-clone config, fetch by sha with
tags, worktree add, describe, worktree remove and prune); uv and the venv's
python are faked, since installing Hermes needs PyPI.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_provision as hp
from hal0.agents import hermes_release as rel

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

UV = "/fake/uv"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(*args: Any, cwd: Path | None = None) -> str:
    import os

    return subprocess.run(
        ["git", *map(str, args)],
        cwd=cwd,
        env={**os.environ, **GIT_ENV},
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[str, str]:
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    (work / "hermes_cli").mkdir()
    (work / "hermes_cli" / "__init__.py").write_text("")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "one", cwd=work)
    _git("tag", "-a", "v2026.7.20", "-m", "v2026.7.20", cwd=work)
    (work / "README").write_text("two\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "two", cwd=work)
    sha = _git("rev-parse", "HEAD", cwd=work)
    bare = tmp_path / "upstream.git"
    _git("clone", "-q", "--bare", work, bare)
    _git("-C", bare, "config", "uploadpack.allowfilter", "true")
    _git("-C", bare, "config", "uploadpack.allowanysha1inwant", "true")
    return f"file://{bare}", sha


def _runner() -> Any:
    def run(argv: list[Any], **kw: Any) -> subprocess.CompletedProcess:
        argv = [str(a) for a in argv]
        if argv[0] == "git":
            return rel._run(argv, **kw)
        if argv[0] == UV and argv[1] == "venv":
            venv = Path(argv[-1])
            (venv / "bin").mkdir(parents=True)
            for name in ("python", "hermes"):
                (venv / "bin" / name).write_text("")
            (venv / "pyvenv.cfg").write_text("home = /usr/bin\nversion_info = 3.12.9\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def test_mirror_fetch_worktree_describe_and_prune(tmp_path: Path, upstream, monkeypatch) -> None:
    url, sha = upstream
    monkeypatch.setattr(hp, "VETTED_HERMES_REFS", frozenset({sha}))
    layout = rel.Layout.for_venv(tmp_path / "var" / "venvs" / "hermes")
    pin = rel.Pin(url=url, extras=("web", "mcp"), ref=sha)
    run = _runner()
    releases = []
    for stamp in ("20261021T000001Z", "20261021T000002Z", "20261021T000003Z"):
        releases.append(
            rel.build_release(
                sha,
                layout=layout,
                pin=pin,
                python="/usr/bin/python3.12",
                uv=UV,
                run=run,
                probe=lambda _p: {"ok": True},
                stamp=lambda s=stamp: s,
            )
        )
    first = releases[0]
    # The mirror is a blobless partial clone of the upstream, with its tags.
    assert _git("-C", layout.mirror, "config", "remote.origin.promisor") == "true"
    assert _git("-C", layout.mirror, "config", "remote.origin.partialclonefilter") == "blob:none"
    assert _git("-C", layout.mirror, "tag") == "v2026.7.20"
    # A detached worktree at the sha, with describe from the tag.
    assert _git("-C", first.src, "rev-parse", "HEAD") == sha
    assert (first.src / "README").read_text() == "two\n"
    assert first.data["describe"] == "v2026.7.20"
    # Activate the last two, then prune: the first release and its worktree go.
    for r in releases[1:]:
        rel._swap_link(layout.previous, Path("releases") / releases[1].id)
        rel._swap_link(layout.current, Path("releases") / r.id)
    rel.prune_releases(layout, run=run, gateway_unit=tmp_path / "none.service")
    assert not first.path.exists()
    listed = _git("-C", layout.mirror, "worktree", "list", "--porcelain")
    assert str(first.src) not in listed
    assert str(releases[2].src) in listed and str(releases[1].src) in listed


def test_a_half_initialised_real_mirror_recovers(tmp_path: Path, upstream, monkeypatch) -> None:
    url, sha = upstream
    layout = rel.Layout.for_venv(tmp_path / "var" / "venvs" / "hermes")
    layout.root.mkdir(parents=True)
    _git("init", "--bare", "-q", layout.mirror)  # a first setup that died here
    rel._ensure_mirror(layout, url, run=rel._run, env=rel.build_env(layout=layout))
    assert _git("-C", layout.mirror, "remote", "get-url", "origin") == url
    _git("-C", layout.mirror, "fetch", "-q", "--filter=blob:none", "--tags", "origin", sha)
