"""Staging-path hardening regressions (#1738 + adjacent extraction defects).

Three defects, all on the privileged staging path:

* **P0 (#1738)** — the release tarball was downloaded into the hal0-owned
  ``/var/lib/hal0/cache/<version>/`` and re-opened *by path* four times
  (download → sha256 → cosign → extract). Anything that can write that
  directory could substitute its own bytes in the window between cosign
  exiting and ``tarfile.open``, and root would extract + pip-install them.
* **P2** — an interrupted ``extractall`` leaves ``dest`` holding only the
  un-flattened ``hal0-<version>/`` prefix directory (or a bare staging
  sentinel). ``_looks_like_hal0_install`` looked only at ``dest/VERSION``
  and ``dest/pyproject.toml``, so every retry raised "refusing to extract
  over non-empty directory" — a permanent wedge for that version, on a
  root-owned tree the hal0 daemon cannot clean up itself.
* **P3** — ``_looks_like_hal0_install``'s pyproject fallback read only the
  first 512 bytes, and hal0's own ``pyproject.toml`` has ``name = "hal0ai"``
  starting at byte 512 exactly. Dead code in practice, and wrong for any
  installer rsync tree (which has no ``VERSION`` file).

The fixtures are imported from :mod:`tests.updater.test_updater` so these
tests exercise the same synthetic release the rest of the updater suite does.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import pytest

from hal0.config.paths import var_lib
from hal0.updater import UpdateExtractError, Updater, UpdateVerifyError
from hal0.updater.updater import (
    _looks_like_hal0_install,
    _usr_lib_root,
    _versioned_install_dir,
)

# Fixtures live in the main updater test module; importing them here makes
# them resolvable by pytest in this module too.
from tests.updater.test_updater import (  # noqa: F401
    cosign_skip,
    synthetic_release,
)

# ── P0: the verified bytes are the extracted bytes ─────────────────────────────


def test_stage_rejects_tarball_swapped_after_cosign(
    synthetic_release: dict[str, Any],  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Substituting the tarball after cosign returns must abort the stage.

    The stub stands in for the #1738 attacker: it runs at exactly the moment
    ``cosign verify-blob`` returns, and rewrites the artifact *in place* at
    the path root is about to extract from — the strongest form of the race
    (it survives a plain ``os.replace`` guard and an inode pin alike). The
    re-digest immediately before extraction is what has to catch it.
    """
    evil = tmp_path / "evil.tar.gz"
    evil.write_bytes(b"\x1f\x8b" + b"malicious payload" * 64)

    def _swap_after_verify(tarball: Path, bundle: Path, **kwargs: Any) -> None:
        Path(tarball).write_bytes(evil.read_bytes())

    monkeypatch.setattr("hal0.updater.updater._verify_cosign", _swap_after_verify)

    with pytest.raises(UpdateVerifyError) as exc_info:
        asyncio.run(Updater().apply())

    assert "digest" in str(exc_info.value).lower()
    # Nothing malicious was ever unpacked.
    install = _versioned_install_dir(synthetic_release["version"])
    assert not (install / "VERSION").exists()


def test_stage_downloads_outside_the_service_owned_cache(
    synthetic_release: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The artifact never lands in the hal0-writable cache dir at all.

    ``/var/lib/hal0`` is hal0:hal0 0o2775 with no sticky bit, and
    ``_restore_service_ownership`` hands the cache version dir back to the
    service account after every stage — so the cache is not a place root may
    stage bytes it is about to trust. Staging happens in a root-only
    ``mkdtemp`` under the install root instead.
    """
    seen: list[Path] = []

    def _record(blob: Path, bundle: Path, **kwargs: Any) -> None:
        seen.append(Path(blob))

    monkeypatch.setattr("hal0.updater.updater._verify_cosign", _record)

    asyncio.run(Updater().apply())

    # The manifest itself is verified through the same seam; we want the
    # release artifact's staging path.
    tarballs = [p for p in seen if p.name.endswith(".tar.gz")]
    assert tarballs, f"cosign never saw the release tarball (saw {seen})"
    staged = tarballs[0]
    cache_root = var_lib() / "cache"
    assert cache_root not in staged.parents, f"{staged} is inside the service-owned cache"
    assert _usr_lib_root() in staged.parents, f"{staged} is not under the root-only install root"
    # Staging dir is torn down once the stage completes.
    assert not staged.exists()
    leftovers = [p for p in _usr_lib_root().iterdir() if p.name.startswith(".stage-")]
    assert leftovers == []


def test_stage_leaves_no_tarball_in_the_cache_dir(
    synthetic_release: dict[str, Any],  # noqa: F811
    cosign_skip: None,  # noqa: F811
) -> None:
    """Only the verified manifest is cached; the artifact bytes are not."""
    asyncio.run(Updater().prepare())
    cache = var_lib() / "cache" / synthetic_release["version"]
    assert cache.is_dir()
    assert sorted(p.name for p in cache.iterdir()) == ["manifest.json"]


# ── P2: an interrupted extraction must self-heal, not wedge ────────────────────


def test_interrupted_extraction_prefix_dir_is_quarantined(
    synthetic_release: dict[str, Any],  # noqa: F811
    cosign_skip: None,  # noqa: F811
) -> None:
    """A dest holding only the un-flattened ``hal0-<v>/`` prefix is recoverable.

    This is the exact on-disk shape ``extractall`` leaves when it is killed
    before the flatten step: no top-level ``VERSION``, no ``pyproject.toml``,
    just the tarball's own prefix directory.
    """
    version = synthetic_release["version"]
    install = _versioned_install_dir(version)
    inner = install / f"hal0-{version}"
    inner.mkdir(parents=True)
    (inner / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (inner / "half-written.txt").write_text("interrupted", encoding="utf-8")

    asyncio.run(Updater().apply())

    assert (install / "VERSION").read_text().strip() == version
    assert not inner.exists()
    quarantined = [
        p for p in install.parent.iterdir() if p.name.startswith(f"{install.name}.stale-")
    ]
    assert quarantined, "interrupted extraction was not quarantined"
    assert (quarantined[0] / f"hal0-{version}" / "half-written.txt").is_file()


def test_extraction_sentinel_marks_an_incomplete_stage(
    synthetic_release: dict[str, Any],  # noqa: F811
    cosign_skip: None,  # noqa: F811
) -> None:
    """A leftover ``.hal0-staging`` sentinel alone is enough to quarantine.

    Covers interruption shapes the prefix-dir heuristic cannot see (e.g. a
    kill after the flatten moved one file and before it moved the rest).
    """
    version = synthetic_release["version"]
    install = _versioned_install_dir(version)
    install.mkdir(parents=True)
    (install / ".hal0-staging").write_text("", encoding="utf-8")
    (install / "partial-file").write_text("half", encoding="utf-8")

    asyncio.run(Updater().apply())

    assert (install / "VERSION").read_text().strip() == version
    assert not (install / ".hal0-staging").exists(), "sentinel survived a successful stage"
    assert not (install / "partial-file").exists()


def test_foreign_directory_is_still_refused(
    synthetic_release: dict[str, Any],  # noqa: F811
    cosign_skip: None,  # noqa: F811
) -> None:
    """The widened heuristic must not start eating operator directories."""
    install = _versioned_install_dir(synthetic_release["version"])
    install.mkdir(parents=True)
    (install / "operator-notes.txt").write_text("do not delete", encoding="utf-8")

    with pytest.raises(UpdateExtractError):
        asyncio.run(Updater().apply())
    assert (install / "operator-notes.txt").is_file()


def test_old_quarantine_dirs_are_reaped(
    synthetic_release: dict[str, Any],  # noqa: F811
    cosign_skip: None,  # noqa: F811
) -> None:
    """``.stale-<ts>`` quarantines accumulated forever; old ones are now reaped."""
    version = synthetic_release["version"]
    install = _versioned_install_dir(version)
    root = install.parent
    root.mkdir(parents=True, exist_ok=True)

    now = int(time.time())
    ancient = root / f"{install.name}.stale-{now - 90 * 86400}"
    old = root / f"{install.name}.stale-{now - 60 * 86400}"
    recent = root / f"{install.name}.stale-{now - 60}"
    for d in (ancient, old, recent):
        d.mkdir()
        (d / "VERSION").write_text(f"{version}\n", encoding="utf-8")

    asyncio.run(Updater().apply())

    assert not ancient.exists(), "an ancient quarantine dir was not reaped"
    assert not old.exists(), "an old quarantine dir was not reaped"
    assert recent.is_dir(), "a recent quarantine dir must be kept for recovery"


def test_orphaned_staging_dirs_are_reaped(
    synthetic_release: dict[str, Any],  # noqa: F811
    cosign_skip: None,  # noqa: F811
) -> None:
    """A ``.stage-*`` dir left by a SIGKILLed stage is swept on the next apply.

    ``_root_only_staging_dir`` only cleans up in a ``finally``, so a stage
    killed by SIGKILL / OOM / power-cut leaks a fresh ``mkdtemp`` dir holding a
    full release tarball under the root filesystem, forever. An in-flight
    stage's own dir is fresh and must survive.
    """
    root = _usr_lib_root()
    root.mkdir(parents=True, exist_ok=True)

    orphan = root / ".stage-0.0.1-deadbeef"
    orphan.mkdir()
    (orphan / "hal0-0.0.1.tar.gz").write_bytes(b"\x1f\x8b" + b"leaked tarball" * 64)
    old_time = time.time() - 3 * 3600
    os.utime(orphan, (old_time, old_time))

    asyncio.run(Updater().apply())

    assert not orphan.exists(), "an orphaned staging dir was not reaped"
    # The apply's own (now torn-down) staging dir left nothing either.
    leftovers = [p for p in root.iterdir() if p.name.startswith(".stage-")]
    assert leftovers == [], f"staging dirs leaked: {leftovers}"


def test_live_staging_dir_is_not_reaped(
    synthetic_release: dict[str, Any],  # noqa: F811
    cosign_skip: None,  # noqa: F811
) -> None:
    """A freshly-created ``.stage-*`` dir (a concurrent live stage) is spared."""
    root = _usr_lib_root()
    root.mkdir(parents=True, exist_ok=True)

    fresh = root / ".stage-9.9.9-livestage"
    fresh.mkdir()
    (fresh / "in-flight.tar.gz").write_bytes(b"\x1f\x8b" + b"live" * 8)

    asyncio.run(Updater().apply())

    assert fresh.is_dir(), "a fresh (live) staging dir must not be reaped"


# ── P3: the pyproject fallback must actually work ──────────────────────────────


def test_looks_like_hal0_install_reads_past_512_bytes(tmp_path: Path) -> None:
    """A repo-shaped tree with no ``VERSION`` is recognised by pyproject alone.

    hal0's real ``pyproject.toml`` puts ``name = "hal0ai"`` at byte 512
    exactly, so the old ``[:512]`` slice never saw it — and an installer
    rsync tree has no ``VERSION`` file to fall back on.
    """
    tree = tmp_path / "repo-shaped"
    tree.mkdir()
    header = "# " + "x" * 600 + "\n"
    (tree / "pyproject.toml").write_text(
        f'{header}[project]\nname = "hal0ai"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    assert not (tree / "VERSION").exists()
    assert _looks_like_hal0_install(tree) is True


def test_looks_like_hal0_install_rejects_a_foreign_pyproject(tmp_path: Path) -> None:
    """Reading the whole file must not turn the check into a wildcard."""
    tree = tmp_path / "foreign"
    tree.mkdir()
    (tree / "pyproject.toml").write_text(
        "# " + "y" * 900 + '\n[project]\nname = "somebody-elses-project"\n', encoding="utf-8"
    )
    assert _looks_like_hal0_install(tree) is False
