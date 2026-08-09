"""Red-first regression for #1723.

``_extract_tarball`` creates the staged install dir via ``Path.mkdir()``,
which is subject to the invoking process's umask. On a box configured with
``UMASK 002`` (Debian default in ``/etc/login.defs`` on some fleets), the
staged tree lands group-writable (mode 0775) and
:func:`hal0.updater.updater.assert_trusted_release_dir` — the activate-side
security gate — correctly refuses it. Stage must normalize the tree so the
gate's precondition holds by construction, regardless of the caller's umask.

This test drives the real extraction path under ``umask 002`` and asserts
the result against the *actual* gate predicate (imported, not reimplemented)
so the test cannot drift from what activate enforces.
"""

from __future__ import annotations

import os
import shutil
import tarfile
from pathlib import Path

import pytest

from hal0.updater.updater import _extract_tarball, assert_trusted_release_dir


def _build_tarball(tmp: Path, version: str) -> Path:
    src = tmp / f"hal0-{version}"
    src.mkdir(parents=True, exist_ok=True)
    (src / "VERSION").write_text(version, encoding="utf-8")
    (src / "bin").mkdir()
    (src / "bin" / "hal0").write_text("#!/usr/bin/env bash\necho hal0 stub\n", encoding="utf-8")
    (src / "site-packages" / "hal0").mkdir(parents=True)
    (src / "site-packages" / "hal0" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    tar_path = tmp / f"hal0-{version}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(src, arcname=f"hal0-{version}")
    shutil.rmtree(src)
    return tar_path


def test_extracted_tree_survives_the_activate_trust_gate_under_umask_002(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage under UMASK 002 must not manufacture a tree activate refuses.

    On the real seam path root does the extracting, so the staged tree is
    already uid-0-owned by construction — #1723's captured failure was
    ``uid=0, mode=0775``: ownership was never the bug, the group-writable
    *mode* was. This test's own process is unprivileged, so it cannot chown
    to root to reproduce that exactly; instead it patches ``Path.lstat`` to
    report uid 0 for the two paths the gate inspects, isolating the mode
    check — the actual thing stage's fix (and this regression) is about —
    while still calling the gate's real, unmodified predicate.
    """
    tarball = _build_tarball(tmp_path, "1.0.0")
    # Pre-create the parent the way install.sh does on a real box: the
    # <usr_lib> root already exists, correctly permissioned, before stage
    # ever runs. `_extract_tarball` only mkdir()s the leaf `dest` — it must
    # not need to touch (and cannot fix, being out of scope) a sibling-level
    # root it did not create.
    install_root = tmp_path / "install"
    install_root.mkdir(mode=0o755)
    dest = install_root / "hal0-1.0.0"

    old_umask = os.umask(0o002)
    try:
        _extract_tarball(tarball, dest)
    finally:
        os.umask(old_umask)

    real_lstat = Path.lstat

    def _lstat_as_root(self: Path) -> os.stat_result:
        st = real_lstat(self)
        if self in (dest, dest.parent):
            fields = (
                st.st_mode,
                st.st_ino,
                st.st_dev,
                st.st_nlink,
                0,  # st_uid, pretend root — see docstring
                0,  # st_gid
                st.st_size,
                int(st.st_atime),
                int(st.st_mtime),
                int(st.st_ctime),
            )
            st = os.stat_result(fields)
        return st

    monkeypatch.setattr(Path, "lstat", _lstat_as_root)

    # This is the exact predicate activate's security gate runs — reuse it
    # so the test can't silently drift from what the gate actually checks.
    # euid=0 rehearses the privileged seam path without requiring real root.
    assert_trusted_release_dir(dest, euid=0)


def test_extracted_tree_has_no_group_or_world_write_bits_recursively(
    tmp_path: Path,
) -> None:
    """Defense in depth: normalize every entry, not just the top-level dir.

    ``assert_trusted_release_dir`` only inspects ``dest`` and its parent
    today, but stage should not rely on the gate staying non-recursive —
    a umask-002 extraction must not leave group/world-writable files or
    subdirectories anywhere in the staged tree.
    """
    tarball = _build_tarball(tmp_path, "1.0.0")
    dest = tmp_path / "install" / "hal0-1.0.0"

    old_umask = os.umask(0o002)
    try:
        _extract_tarball(tarball, dest)
    finally:
        os.umask(old_umask)

    offenders = [
        str(p)
        for p in (dest, *dest.rglob("*"))
        if not p.is_symlink() and (p.stat().st_mode & 0o022)
    ]
    assert offenders == []


def test_extracted_tree_stays_readable_after_the_extraction_lockdown(
    tmp_path: Path,
) -> None:
    """Locking `dest` to owner-only during extraction must not stick.

    `_extract_tarball` chmods `dest` to 0700 for the duration of extraction
    (closing the plant-a-file race a co-resident process could otherwise
    win — see the module-level docstring above). If the final
    normalization pass only ever *masked off* write bits (`mode & ~0o022`)
    instead of setting directories back to a real, traversable mode, that
    0700 lockdown would become permanent: the activated tree would be
    unreadable by the very service account that has to run it. Every
    directory in the finished tree — including `dest` itself — must end up
    at least group/other readable and traversable.
    """
    tarball = _build_tarball(tmp_path, "1.0.0")
    dest = tmp_path / "install" / "hal0-1.0.0"

    _extract_tarball(tarball, dest)

    for d in (dest, *(p for p in dest.rglob("*") if p.is_dir())):
        mode = d.stat().st_mode & 0o777
        assert mode & 0o555 == 0o555, f"{d} is not group/other readable+traversable: {mode:04o}"
