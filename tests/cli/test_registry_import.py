"""Tests for ``hal0 registry import`` — PR-21 of v0.2 Lemonade migration.

Covers the behaviour contract from lemonade-adoption-plan §9 + §11 PR-21:

* Happy path: tarball with ``registry/registry.toml`` → imported, success
  message printed.
* Missing tarball → clear error, non-zero exit.
* Tarball missing ``registry.toml`` → clear error, non-zero exit.
* Existing destination + no ``--force`` → refused with clear error.
* Existing destination + ``--force`` → overwritten.
* Permission errors handled gracefully.
* Tar path-traversal members rejected.
* Atomic copy (no partial file left on crash).
* Tempdir cleanup on success and failure.
* Bundle-picker recovery path printed on success.
"""

from __future__ import annotations

import tarfile
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from hal0.cli.registry_commands import (
    _TARBALL_REGISTRY_CANDIDATES,
    _atomic_copy,
    _find_registry_in_dir,
    _is_within,
)
from hal0.cli.registry_commands import (
    app as registry_app,
)

runner = CliRunner()


# ── Fixture helpers ───────────────────────────────────────────────────────────


REGISTRY_PAYLOAD = b"""# hal0 v0.1.x registry snapshot
[models.hermes-4-14b]
path = "/mnt/ai-models/local/hermes-4-14b.gguf"
backends = ["vulkan"]
capabilities = ["chat"]
"""


def _make_backup(
    tmp_path: Path,
    *,
    layout: str = "var-lib",
    include_registry: bool = True,
    extras: dict[str, bytes] | None = None,
) -> Path:
    """Build a synthetic hal0-v0.1-backup tarball under ``tmp_path``.

    ``layout`` picks the relative path under which ``registry.toml``
    lives inside the tar:

    * ``var-lib``: ``var/lib/hal0/registry/registry.toml`` (canonical;
      produced by the install.sh backup instructions).
    * ``short``:   ``registry/registry.toml`` (hand-repacked variant).
    * ``flat``:    ``registry.toml`` (degenerate, but accepted).
    """
    layouts = {
        "var-lib": "var/lib/hal0/registry/registry.toml",
        "short": "registry/registry.toml",
        "flat": "registry.toml",
    }
    relpath = layouts[layout]

    staging = tmp_path / f"staging-{layout}"
    staging.mkdir()
    if include_registry:
        target = staging / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(REGISTRY_PAYLOAD)

    # Optional companion files — e.g. /etc/hal0 contents that should be
    # ignored by the importer.
    if extras:
        for name, data in extras.items():
            extra_path = staging / name
            extra_path.parent.mkdir(parents=True, exist_ok=True)
            extra_path.write_bytes(data)

    tar_path = tmp_path / f"hal0-v0.1-backup-{layout}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for item in staging.rglob("*"):
            if item.is_file():
                tar.add(item, arcname=str(item.relative_to(staging)))
    return tar_path


def _invoke(*, tar_path: Path, dest: Path, force: bool = False) -> object:
    args = ["import", str(tar_path), "--dest", str(dest)]
    if force:
        args.append("--force")
    return runner.invoke(registry_app, args)


# ── Pure-function tests ───────────────────────────────────────────────────────


def test_is_within_accepts_nested_path(tmp_path: Path) -> None:
    base = tmp_path / "root"
    base.mkdir()
    assert _is_within(base, base / "a" / "b")


def test_is_within_rejects_dotdot_escape(tmp_path: Path) -> None:
    base = tmp_path / "root"
    base.mkdir()
    # Path-traversal — `..` walks out of base.
    assert not _is_within(base, base / ".." / "other")


def test_is_within_rejects_absolute_escape(tmp_path: Path) -> None:
    base = tmp_path / "root"
    base.mkdir()
    assert not _is_within(base, Path("/etc/passwd"))


def test_find_registry_prefers_canonical_layout(tmp_path: Path) -> None:
    # All three layouts present — must pick the canonical one first.
    for rel in _TARBALL_REGISTRY_CANDIDATES:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    found = _find_registry_in_dir(tmp_path)
    assert found is not None
    assert found.relative_to(tmp_path) == Path(_TARBALL_REGISTRY_CANDIDATES[0])


def test_find_registry_returns_none_when_absent(tmp_path: Path) -> None:
    assert _find_registry_in_dir(tmp_path) is None


def test_atomic_copy_writes_destination(tmp_path: Path) -> None:
    src = tmp_path / "src.toml"
    src.write_bytes(b"hello")
    dst = tmp_path / "dst" / "dst.toml"
    _atomic_copy(src, dst)
    assert dst.read_bytes() == b"hello"


def test_atomic_copy_leaves_no_tempfile(tmp_path: Path) -> None:
    src = tmp_path / "src.toml"
    src.write_bytes(b"hello")
    dst = tmp_path / "dst.toml"
    _atomic_copy(src, dst)
    # No sibling tempfile (".dst.toml.XXXX.tmp") survives the copy.
    siblings = [p for p in tmp_path.iterdir() if p.name.startswith(".dst.toml.")]
    assert siblings == []


# ── CLI: happy paths ─────────────────────────────────────────────────────────


def test_import_happy_path_canonical_layout(tmp_path: Path) -> None:
    tar_path = _make_backup(tmp_path, layout="var-lib")
    dest = tmp_path / "var" / "lib" / "hal0" / "registry" / "registry.toml"

    result = _invoke(tar_path=tar_path, dest=dest)
    assert result.exit_code == 0, result.output
    assert dest.read_bytes() == REGISTRY_PAYLOAD
    # Success guidance must point operators at the next step.
    assert "hal0 capabilities sync" in result.output
    assert "bundle" in result.output.lower() or "slot add" in result.output


def test_import_accepts_short_layout(tmp_path: Path) -> None:
    tar_path = _make_backup(tmp_path, layout="short")
    dest = tmp_path / "out" / "registry.toml"
    result = _invoke(tar_path=tar_path, dest=dest)
    assert result.exit_code == 0, result.output
    assert dest.read_bytes() == REGISTRY_PAYLOAD


def test_import_accepts_flat_layout(tmp_path: Path) -> None:
    tar_path = _make_backup(tmp_path, layout="flat")
    dest = tmp_path / "out" / "registry.toml"
    result = _invoke(tar_path=tar_path, dest=dest)
    assert result.exit_code == 0, result.output
    assert dest.read_bytes() == REGISTRY_PAYLOAD


def test_import_ignores_extras_in_backup(tmp_path: Path) -> None:
    # Backup tarballs include /etc/hal0/ — we must extract them into the
    # tempdir (so the tar walks cleanly) but NOT copy them to disk.
    tar_path = _make_backup(
        tmp_path,
        layout="var-lib",
        extras={
            "etc/hal0/capabilities.toml": b"# v0.1.x -- should not be restored\n",
            "etc/hal0/slots/primary.toml": b"# v0.1.x slot -- must not appear in v0.2\n",
        },
    )
    dest = tmp_path / "out" / "registry.toml"
    result = _invoke(tar_path=tar_path, dest=dest)
    assert result.exit_code == 0, result.output
    # Only the registry made it to the destination.
    assert dest.read_bytes() == REGISTRY_PAYLOAD
    # The /etc/hal0 extras did NOT bleed into the destination tree.
    etc_root = dest.parent.parent / "etc"
    assert not etc_root.exists()


# ── CLI: error paths ─────────────────────────────────────────────────────────


def test_import_missing_tarball(tmp_path: Path) -> None:
    dest = tmp_path / "registry.toml"
    result = _invoke(tar_path=tmp_path / "does-not-exist.tar.gz", dest=dest)
    assert result.exit_code != 0
    assert "not found" in result.output
    assert not dest.exists()


def test_import_not_a_regular_file(tmp_path: Path) -> None:
    # A directory passed where a tarball is expected.
    dest = tmp_path / "registry.toml"
    result = _invoke(tar_path=tmp_path, dest=dest)
    assert result.exit_code != 0
    assert "not a regular file" in result.output


def test_import_not_a_tar(tmp_path: Path) -> None:
    not_a_tar = tmp_path / "garbage.tar.gz"
    not_a_tar.write_bytes(b"not a tar archive")
    dest = tmp_path / "registry.toml"
    result = _invoke(tar_path=not_a_tar, dest=dest)
    assert result.exit_code != 0
    assert "tar" in result.output.lower()
    assert not dest.exists()


def test_import_tarball_without_registry(tmp_path: Path) -> None:
    # Backup that DOESN'T contain registry.toml at any known layout.
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "etc" / "hal0").mkdir(parents=True)
    (staging / "etc" / "hal0" / "config.toml").write_bytes(b"# no registry\n")
    tar_path = tmp_path / "bad-backup.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(staging / "etc", arcname="etc")

    dest = tmp_path / "registry.toml"
    result = _invoke(tar_path=tar_path, dest=dest)
    assert result.exit_code != 0
    assert "registry.toml not found" in result.output
    assert not dest.exists()


def test_import_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    tar_path = _make_backup(tmp_path, layout="var-lib")
    dest = tmp_path / "registry.toml"
    dest.write_bytes(b"# pre-existing v0.2 registry -- do not clobber\n")

    result = _invoke(tar_path=tar_path, dest=dest)
    assert result.exit_code != 0
    assert "--force" in result.output
    # Destination is unchanged.
    assert dest.read_bytes() == b"# pre-existing v0.2 registry -- do not clobber\n"


def test_import_force_overwrites_existing(tmp_path: Path) -> None:
    tar_path = _make_backup(tmp_path, layout="var-lib")
    dest = tmp_path / "registry.toml"
    dest.write_bytes(b"# pre-existing v0.2 registry\n")

    result = _invoke(tar_path=tar_path, dest=dest, force=True)
    assert result.exit_code == 0, result.output
    assert dest.read_bytes() == REGISTRY_PAYLOAD


def test_import_rejects_path_traversal_member(tmp_path: Path) -> None:
    # Hand-craft a tar with an absolute member name; the importer must
    # refuse rather than write outside the tempdir.
    payload = tmp_path / "payload"
    payload.write_bytes(b"malicious")
    tar_path = tmp_path / "evil.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        info = tarfile.TarInfo(name="../../escape/registry.toml")
        info.size = len(REGISTRY_PAYLOAD)
        import io

        tar.addfile(info, io.BytesIO(REGISTRY_PAYLOAD))

    dest = tmp_path / "out" / "registry.toml"
    result = _invoke(tar_path=tar_path, dest=dest)
    assert result.exit_code != 0
    # Destination + escape dir are untouched.
    assert not dest.exists()
    assert not (tmp_path / "escape").exists()


def test_import_handles_permission_error_on_dest(tmp_path: Path) -> None:
    tar_path = _make_backup(tmp_path, layout="var-lib")
    dest = tmp_path / "out" / "registry.toml"

    # Force the atomic copy to raise PermissionError.
    def boom(*_args: object, **_kw: object) -> None:
        raise PermissionError("simulated permission denied")

    with patch("hal0.cli.registry_commands._atomic_copy", side_effect=boom):
        result = _invoke(tar_path=tar_path, dest=dest)

    assert result.exit_code != 0
    # The user must be told what to try next.
    assert "sudo" in result.output or "writable" in result.output


# ── CLI: tempdir cleanup ─────────────────────────────────────────────────────


def test_import_cleans_tempdir_on_success(tmp_path: Path) -> None:
    tar_path = _make_backup(tmp_path, layout="var-lib")
    dest = tmp_path / "out" / "registry.toml"

    import tempfile as tf_mod

    before = {
        p.name
        for p in Path(tf_mod.gettempdir()).iterdir()
        if p.name.startswith("hal0-registry-import-")
    }
    result = _invoke(tar_path=tar_path, dest=dest)
    assert result.exit_code == 0, result.output
    after = {
        p.name
        for p in Path(tf_mod.gettempdir()).iterdir()
        if p.name.startswith("hal0-registry-import-")
    }
    # No new orphaned tempdirs from this run.
    assert after - before == set()


def test_import_cleans_tempdir_on_failure(tmp_path: Path) -> None:
    # Bad tarball → cleanup must still happen.
    not_a_tar = tmp_path / "garbage.tar.gz"
    not_a_tar.write_bytes(b"nope")
    dest = tmp_path / "registry.toml"

    import tempfile as tf_mod

    before = {
        p.name
        for p in Path(tf_mod.gettempdir()).iterdir()
        if p.name.startswith("hal0-registry-import-")
    }
    result = _invoke(tar_path=not_a_tar, dest=dest)
    assert result.exit_code != 0
    after = {
        p.name
        for p in Path(tf_mod.gettempdir()).iterdir()
        if p.name.startswith("hal0-registry-import-")
    }
    assert after - before == set()


# ── CLI: wiring smoke ─────────────────────────────────────────────────────────


def test_registry_command_registered_on_main_app() -> None:
    """``hal0 registry import`` must be reachable from the top-level app.

    Guards against forgetting the ``app.add_typer(registry_app, ...)``
    wire-up in ``hal0.cli.main`` after the command was added.
    """
    from hal0.cli.main import app as main_app

    result = runner.invoke(main_app, ["registry", "--help"])
    assert result.exit_code == 0, result.output
    assert "import" in result.output


def test_registry_import_help_mentions_force_and_dest() -> None:
    # Strip ANSI escape codes + collapse all whitespace so typer's
    # rich-renderer line-wrap (CI defaults to narrow terminals and
    # splits `--force` across lines) does not break the substring
    # assertions.
    import re as _re

    result = runner.invoke(registry_app, ["import", "--help"])
    assert result.exit_code == 0, result.output
    plain = _re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    plain = _re.sub(r"\s+", "", plain)
    assert "--force" in plain
    assert "--dest" in plain
