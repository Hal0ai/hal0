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
from hal0.providers import podman_mutate
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


class TestRm:
    """``hal0 runner-images rm <id> --tag TAG`` (#2106 D2, CLI surface).

    Mirrors ``DELETE /api/runner-images/{id}/tags/{tag}``'s guard order —
    ``podman_mutate.remove_image`` is monkeypatched at the module attribute
    the CLI calls through (``hal0.providers.podman_mutate.remove_image``),
    same idiom the route's own tests use, so no real seam/subprocess is
    exercised.
    """

    def test_unknown_id_errors(self, store: RunnerImageStore) -> None:
        result = runner.invoke(runner_images_app, ["rm", "nope", "--tag", "0826"])

        assert result.exit_code != 0
        assert "not in catalogue" in result.output

    def test_missing_tag_option_errors(self, store: RunnerImageStore) -> None:
        store.upsert(_image())

        result = runner.invoke(runner_images_app, ["rm", "x/a"])

        assert result.exit_code != 0

    def test_unknown_tag_errors(self, store: RunnerImageStore) -> None:
        store.upsert(_image())

        result = runner.invoke(runner_images_app, ["rm", "x/a", "--tag", "does-not-exist"])

        assert result.exit_code != 0
        assert "not catalogued" in result.output

    def test_removed_happy_path(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.upsert(_image())
        monkeypatch.setattr(podman_mutate, "remove_image", lambda ref, **kw: ("removed", None))

        result = runner.invoke(runner_images_app, ["rm", "x/a", "--tag", "0826"])

        assert result.exit_code == 0, result.output
        assert "removed ghcr.io/x/a:0826" in result.output
        assert store.get("x/a") is None or "0826" not in [t.tag for t in store.get("x/a").tags]

    def test_missing_outcome_still_exits_zero_and_clears_catalogue(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.upsert(_image())
        monkeypatch.setattr(podman_mutate, "remove_image", lambda ref, **kw: ("missing", None))

        result = runner.invoke(runner_images_app, ["rm", "x/a", "--tag", "0826"])

        assert result.exit_code == 0, result.output
        assert "not on disk" in result.output.lower()
        assert "catalogue entry cleared" in result.output.lower()

    def test_seam_in_use_refuses(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.upsert(_image())
        monkeypatch.setattr(podman_mutate, "remove_image", lambda ref, **kw: ("in-use", None))

        result = runner.invoke(runner_images_app, ["rm", "x/a", "--tag", "0826"])

        assert result.exit_code != 0
        assert "in use" in result.output.lower()

    def test_slot_in_use_refuses_and_names_slot(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.upsert(_image())
        monkeypatch.setattr(
            "hal0.api.routes.runner_images._slot_image_usage",
            lambda: {"brain": "ghcr.io/x/a:0826"},
        )

        def _boom(ref: str, **kw: object) -> tuple[str, None]:
            raise AssertionError("seam-level remove_image should not be reached")

        monkeypatch.setattr(podman_mutate, "remove_image", _boom)

        result = runner.invoke(runner_images_app, ["rm", "x/a", "--tag", "0826"])

        assert result.exit_code != 0
        assert "brain" in result.output
        assert "in use" in result.output.lower()

    def test_unknown_seam_outcome_errors_with_reason(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.upsert(_image())
        monkeypatch.setattr(
            podman_mutate, "remove_image", lambda ref, **kw: ("unknown", "grant-denied")
        )

        result = runner.invoke(runner_images_app, ["rm", "x/a", "--tag", "0826"])

        assert result.exit_code != 0
        assert "grant-denied" in result.output

    def test_not_service_user_outcome_errors_actionably(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The not-service-user case is the one an operator hits interactively
        (running the CLI from a dev/admin shell, not as the hal0 service
        account) — its error text should tell them what to actually do next,
        not just name the reason code."""
        store.upsert(_image())
        monkeypatch.setattr(
            podman_mutate, "remove_image", lambda ref, **kw: ("unknown", "not-service-user")
        )

        result = runner.invoke(runner_images_app, ["rm", "x/a", "--tag", "0826"])

        assert result.exit_code != 0
        assert "not-service-user" in result.output
        assert "sudo -u hal0 hal0 runner-images rm" in result.output
        assert "root" in result.output.lower()
