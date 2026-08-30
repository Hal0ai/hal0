"""Tests for ``hal0 runner-images ls|sync|pull`` (#2106).

Isolation mirrors ``tests/cli/test_config_migrate.py``: ``HAL0_HOME`` is
set to a fresh ``tmp_path`` so :func:`hal0.config.paths.db_path` (and
every other path helper the CLI touches) resolves under the sandbox
rather than the real host install. The CLI module always builds a bare
``RunnerImageStore()`` (no ``db_path=`` override), so seeding through a
bare ``RunnerImageStore()`` in the fixture lands in the exact same
sqlite file the command under test will open.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hal0.cli.runner_image_commands import app as runner_images_app
from hal0.registry.runner_image import RunnerImage
from hal0.registry.runner_image_store import RunnerImageStore

runner = CliRunner()


@pytest.fixture
def hal0_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HAL0_HOME", str(home))
    return home


@pytest.fixture
def store(hal0_home: Path) -> RunnerImageStore:
    """A ``RunnerImageStore()`` pointed at the same DB the CLI will open."""
    return RunnerImageStore()


def _image(**kw: object) -> RunnerImage:
    defaults: dict[str, object] = {
        "id": "x/a",
        "image": "ghcr.io/x/a",
        "tag": "0826",
        "available_tags": ["0826"],
    }
    defaults.update(kw)
    return RunnerImage(**defaults)  # type: ignore[arg-type]


class TestLs:
    def test_lists_seeded_row(self, store: RunnerImageStore) -> None:
        store.upsert(_image())

        result = runner.invoke(runner_images_app, ["ls"])

        assert result.exit_code == 0, result.output
        assert "ghcr.io/x/a:0826" in result.output
        assert "x/a" in result.output

    def test_empty_catalogue_still_exits_zero(self, store: RunnerImageStore) -> None:
        result = runner.invoke(runner_images_app, ["ls"])

        assert result.exit_code == 0, result.output
        assert "no runner images" in result.output.lower()


class TestPull:
    def test_unknown_id_errors(self, store: RunnerImageStore) -> None:
        result = runner.invoke(runner_images_app, ["pull", "nope"])

        assert result.exit_code != 0
        assert "not in catalogue" in result.output

    def test_invalid_tag_errors_readably(self, store: RunnerImageStore) -> None:
        store.upsert(_image())

        result = runner.invoke(runner_images_app, ["pull", "x/a", "--tag", "does-not-exist"])

        assert result.exit_code != 0
        assert "does-not-exist" in result.output
        assert "not a catalogued tag" in result.output

    def test_malformed_tag_rejected_before_lookup(self, store: RunnerImageStore) -> None:
        """A tag outside the OCI tag grammar is rejected by
        ``validate_pull_tag`` itself — a distinct, earlier failure mode
        from "not a catalogued tag" (no ``available_tags`` lookup happens
        at all), but still a readable non-zero exit."""
        store.upsert(_image())

        result = runner.invoke(runner_images_app, ["pull", "x/a", "--tag", "../escape"])

        assert result.exit_code != 0
        assert result.output.strip()
