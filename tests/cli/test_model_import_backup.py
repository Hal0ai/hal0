"""Tests for ``hal0 model import-backup`` (CLI consolidation, 2026-07).

``hal0 registry import`` collided in name with the much bigger ``hal0
model`` group; the canonical home is now ``hal0 model import-backup``,
reusing :func:`hal0.cli.registry_commands._do_import_backup` so the
tarball-handling/atomic-copy logic isn't duplicated. Full behavioural
coverage (path traversal, atomic copy, tempdir cleanup, ...) lives in
``tests/cli/test_registry_import.py`` against the shared implementation;
these tests just pin the new command's wiring.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from typer.testing import CliRunner

from hal0.cli.model_commands import app as model_app

runner = CliRunner()

REGISTRY_PAYLOAD = b"""# hal0 v0.1.x registry snapshot
[models.hermes-4-14b]
path = "/mnt/ai-models/local/hermes-4-14b.gguf"
backends = ["vulkan"]
capabilities = ["chat"]
"""


def _make_backup(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    target = staging / "var" / "lib" / "hal0" / "registry" / "registry.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(REGISTRY_PAYLOAD)
    tar_path = tmp_path / "hal0-v0.1-backup.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for item in staging.rglob("*"):
            if item.is_file():
                tar.add(item, arcname=str(item.relative_to(staging)))
    return tar_path


def test_model_import_backup_is_registered_and_not_hidden() -> None:
    """Unlike the deprecated ``registry import`` alias, this is the
    canonical command and must show up in ``model --help``."""
    from hal0.cli.main import app as main_app

    result = runner.invoke(main_app, ["model", "--help"])
    assert result.exit_code == 0, result.output
    assert "import-backup" in result.output


def test_model_import_backup_happy_path(tmp_path: Path) -> None:
    tar_path = _make_backup(tmp_path)
    dest = tmp_path / "out" / "registry.toml"

    result = runner.invoke(model_app, ["import-backup", str(tar_path), "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    assert dest.read_bytes() == REGISTRY_PAYLOAD
    # No deprecation notice on the canonical command.
    assert "deprecat" not in result.output.lower()


def test_model_import_backup_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    tar_path = _make_backup(tmp_path)
    dest = tmp_path / "registry.toml"
    dest.write_bytes(b"# pre-existing v0.2 registry -- do not clobber\n")

    result = runner.invoke(model_app, ["import-backup", str(tar_path), "--dest", str(dest)])
    assert result.exit_code != 0
    assert "--force" in result.output
    assert dest.read_bytes() == b"# pre-existing v0.2 registry -- do not clobber\n"
