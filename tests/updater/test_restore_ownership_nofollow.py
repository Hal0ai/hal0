"""``_restore_service_ownership`` never follows a link in the service-owned cache (#2975)."""

from __future__ import annotations

import os
import pwd
from pathlib import Path
from typing import Any

import pytest

from hal0.updater import updater


def test_cache_chown_never_follows_a_planted_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache" / "1.6.1"
    cache.mkdir(parents=True)
    (tmp_path / "cache").chmod(0o2775)
    (cache / "hal0.tar.gz").write_text("")
    victim = tmp_path / "shadow"
    victim.write_text("root secret")
    (cache / "shadow").symlink_to(victim)
    (cache / "etc").symlink_to(tmp_path)

    me = pwd.getpwuid(os.getuid())
    monkeypatch.setattr(updater.os, "geteuid", lambda: 0)
    monkeypatch.setattr(pwd, "getpwnam", lambda _n: me)
    calls: list[tuple[str, bool]] = []
    real = os.chown

    def spy(p: Any, uid: int, gid: int, **kw: Any) -> None:
        calls.append((str(p), kw.get("follow_symlinks", True)))
        real(p, uid, gid, **kw)

    monkeypatch.setattr(os, "chown", spy)
    updater._restore_service_ownership(cache)

    assert {Path(p).name for p, _ in calls} == {"1.6.1", "hal0.tar.gz", "shadow", "etc"}
    assert all(not follow for _p, follow in calls), calls
