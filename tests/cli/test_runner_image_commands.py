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
from hal0.providers.podman_introspect import LocalImagesDigests
from hal0.registry.runner_image import RunnerImage, RunnerImageTag
from hal0.registry.runner_image_store import RunnerImageStore
from hal0.registry.runner_image_sync import SyncResult
from hal0.runners.pins import RunnerPin, load_runner_pins

runner = CliRunner()

STD_REPO = "ghcr.io/hal0ai/hal0-runner-standard"
_DIGEST = "sha256:" + "d" * 64


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
        "id": "standard",
        "image": STD_REPO,
        "tag": "b2",
        "available_tags": ["b2"],
    }
    defaults.update(kw)
    return RunnerImage(**defaults)  # type: ignore[arg-type]


class TestLs:
    def test_lists_seeded_row(self, store: RunnerImageStore) -> None:
        store.upsert(_image())

        result = runner.invoke(runner_images_app, ["ls"])

        assert result.exit_code == 0, result.output
        assert f"{STD_REPO}:b2" in result.output
        assert "standard" in result.output

    def test_empty_catalogue_still_exits_zero(self, store: RunnerImageStore) -> None:
        result = runner.invoke(runner_images_app, ["ls"])

        assert result.exit_code == 0, result.output
        assert "no runner images" in result.output.lower()

    def test_digest_match_shows_present_even_when_ref_differs(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Store state must be derived from the row's per-tag digest facts
        (``image.tags``, the ``runner_image_tag`` rows), not just an exact
        ``image:tag`` ref match — the same digest-first rule
        ``hal0.api.routes.runner_images.enrich_row`` uses (v3 store-truth).
        Here the headline tag's own digest matches a LOCAL ref filed under a
        completely different name, which an exact-ref-only check (the old
        CLI behaviour, keyed off ``image.digest`` alone) would miss and
        wrongly report "missing"."""
        store.upsert(_image())
        store.set_tags("standard", [RunnerImageTag(tag="b2", digest="sha256:" + "a" * 64)])
        monkeypatch.setattr(
            "hal0.providers.podman_introspect.images_digests",
            lambda: LocalImagesDigests(
                refs={f"{STD_REPO}:renamed": "sha256:" + "a" * 64}, context="rootful"
            ),
        )

        result = runner.invoke(runner_images_app, ["ls"])

        assert result.exit_code == 0, result.output
        assert "present" in result.output.lower()
        assert "missing" not in result.output.lower()

    def test_badges_column_shown_when_the_headline_is_the_pinned_tag(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``Badges`` column appears (reusing the API's ``_tag_badges``)
        when at least one catalogued row's headline tag is its runner's
        release pin."""
        table = dict(load_runner_pins())
        table["standard"] = RunnerPin(key="standard", image=STD_REPO, tag="b2", digest=_DIGEST)
        monkeypatch.setattr("hal0.runners.pins.runner_pins", lambda: table)
        store.upsert(_image())

        result = runner.invoke(runner_images_app, ["ls"])

        assert result.exit_code == 0, result.output
        assert "badges" in result.output.lower()
        assert "pinned" in result.output.lower()

    def test_no_badges_column_when_no_headline_is_pinned(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        table = dict(load_runner_pins())
        table["standard"] = RunnerPin(key="standard", image=STD_REPO, tag="b1", digest=_DIGEST)
        monkeypatch.setattr("hal0.runners.pins.runner_pins", lambda: table)
        store.upsert(_image())  # headline b2, the pin names b1

        result = runner.invoke(runner_images_app, ["ls"])

        assert result.exit_code == 0, result.output
        assert "badges" not in result.output.lower()
        assert "pinned" not in result.output.lower()

    def test_rows_are_listed_by_runner_key(self, store: RunnerImageStore) -> None:
        store.upsert(_image())
        store.upsert(_image(id="cpu", image="ghcr.io/hal0ai/hal0-runner-cpu", tag="b1"))

        result = runner.invoke(runner_images_app, ["ls"])

        assert result.exit_code == 0, result.output
        out = result.output
        assert out.index("cpu") < out.index("standard")  # store.list() sorts by id
        assert "ghcr.io/hal0ai/hal0-runner-cpu:b1" in out


class TestSync:
    @staticmethod
    def _stub(monkeypatch: pytest.MonkeyPatch, result: SyncResult) -> None:
        async def _fake(store: RunnerImageStore, **kw: object) -> SyncResult:
            return result

        monkeypatch.setattr("hal0.registry.runner_image_sync.sync_runner_images", _fake)

    def test_reports_the_synced_count(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub(monkeypatch, SyncResult(images=[_image()], images_json_ok=True))

        result = runner.invoke(runner_images_app, ["sync"])

        assert result.exit_code == 0, result.output
        assert "synced 1 runner image" in result.output

    def test_probe_errors_are_listed_by_runner_key_but_do_not_fail(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub(
            monkeypatch,
            SyncResult(images=[_image()], images_json_ok=True, probe_errors={"flm": "HTTP 500"}),
        )

        result = runner.invoke(runner_images_app, ["sync"])

        assert result.exit_code == 0, result.output
        assert "flm" in result.output
        assert "HTTP 500" in result.output

    def test_skipped_catalogue_entries_are_listed_with_reasons(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub(
            monkeypatch,
            SyncResult(
                images=[_image()],
                images_json_ok=True,
                skipped={"strix": "'strix' is not a runner this hal0 ships"},
            ),
        )

        result = runner.invoke(runner_images_app, ["sync"])

        assert result.exit_code == 0, result.output
        out = " ".join(result.output.split())
        assert "skipped catalogue entry strix" in out
        assert "is not a runner this hal0 ships" in out

    def test_failed_catalogue_fetch_exits_nonzero(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stub(
            monkeypatch,
            SyncResult(
                images_json_ok=False,
                images_json_error="images.json schema 'hal0.runner-images.v1' is not 'hal0.runner-images.v2'",
            ),
        )

        result = runner.invoke(runner_images_app, ["sync"])

        assert result.exit_code != 0
        assert "hal0.runner-images.v2" in result.output


class TestPull:
    def test_unknown_id_errors(self, store: RunnerImageStore) -> None:
        result = runner.invoke(runner_images_app, ["pull", "nope"])

        assert result.exit_code != 0
        assert "not in catalogue" in result.output

    def test_invalid_tag_errors_readably(self, store: RunnerImageStore) -> None:
        store.upsert(_image())

        result = runner.invoke(runner_images_app, ["pull", "standard", "--tag", "does-not-exist"])

        assert result.exit_code != 0
        assert "does-not-exist" in result.output
        assert "not a catalogued tag" in result.output

    def test_malformed_tag_rejected_before_lookup(self, store: RunnerImageStore) -> None:
        """A tag outside the OCI tag grammar is rejected by
        ``validate_pull_tag`` itself — a distinct, earlier failure mode
        from "not a catalogued tag" (no ``available_tags`` lookup happens
        at all), but still a readable non-zero exit."""
        store.upsert(_image())

        result = runner.invoke(runner_images_app, ["pull", "standard", "--tag", "../escape"])

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
        result = runner.invoke(runner_images_app, ["rm", "nope", "--tag", "b2"])

        assert result.exit_code != 0
        assert "not in catalogue" in result.output

    def test_missing_tag_option_errors(self, store: RunnerImageStore) -> None:
        store.upsert(_image())

        result = runner.invoke(runner_images_app, ["rm", "standard"])

        assert result.exit_code != 0

    def test_unknown_tag_errors(self, store: RunnerImageStore) -> None:
        store.upsert(_image())

        result = runner.invoke(runner_images_app, ["rm", "standard", "--tag", "does-not-exist"])

        assert result.exit_code != 0
        assert "not catalogued" in result.output

    def test_removed_happy_path(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.upsert(_image())
        monkeypatch.setattr(podman_mutate, "remove_image", lambda ref, **kw: ("removed", None))

        result = runner.invoke(runner_images_app, ["rm", "standard", "--tag", "b2"])

        assert result.exit_code == 0, result.output
        assert f"removed {STD_REPO}:b2" in result.output
        assert store.get("standard") is None or "b2" not in [
            t.tag for t in store.get("standard").tags
        ]

    def test_missing_outcome_still_exits_zero_and_clears_catalogue(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.upsert(_image())
        monkeypatch.setattr(podman_mutate, "remove_image", lambda ref, **kw: ("missing", None))

        result = runner.invoke(runner_images_app, ["rm", "standard", "--tag", "b2"])

        assert result.exit_code == 0, result.output
        assert "not on disk" in result.output.lower()
        # rich wraps long refs at the terminal width: compare unwrapped.
        assert "catalogue entry cleared" in " ".join(result.output.lower().split())

    def test_seam_in_use_refuses(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.upsert(_image())
        monkeypatch.setattr(podman_mutate, "remove_image", lambda ref, **kw: ("in-use", None))

        result = runner.invoke(runner_images_app, ["rm", "standard", "--tag", "b2"])

        assert result.exit_code != 0
        assert "in use" in result.output.lower()

    def test_slot_in_use_refuses_and_names_slot(
        self, store: RunnerImageStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.upsert(_image())
        monkeypatch.setattr(
            "hal0.api.routes.runner_images._slot_image_usage",
            lambda: {"brain": f"{STD_REPO}:b2"},
        )

        def _boom(ref: str, **kw: object) -> tuple[str, None]:
            raise AssertionError("seam-level remove_image should not be reached")

        monkeypatch.setattr(podman_mutate, "remove_image", _boom)

        result = runner.invoke(runner_images_app, ["rm", "standard", "--tag", "b2"])

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

        result = runner.invoke(runner_images_app, ["rm", "standard", "--tag", "b2"])

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

        result = runner.invoke(runner_images_app, ["rm", "standard", "--tag", "b2"])

        assert result.exit_code != 0
        assert "not-service-user" in result.output
        assert "sudo -u hal0 hal0 runner-images rm" in result.output
        assert "root" in result.output.lower()
