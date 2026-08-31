"""Unit tests for hal0.registry.runner_image_store.RunnerImageStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.registry.runner_image import RunnerImage, RunnerImageTag
from hal0.registry.runner_image_store import RunnerImageStore


@pytest.fixture
def store(tmp_path: Path) -> RunnerImageStore:
    return RunnerImageStore(db_path=tmp_path / "hal0.db")


def _image(image_id: str = "hal0ai/hal0-toolbox-cpu", **kw) -> RunnerImage:
    return RunnerImage(id=image_id, image=f"ghcr.io/{image_id}", **kw)


class TestEmptyStore:
    def test_list_empty(self, store: RunnerImageStore) -> None:
        assert store.list() == []

    def test_get_unknown_returns_none(self, store: RunnerImageStore) -> None:
        assert store.get("nope") is None

    def test_list_downloaded_empty(self, store: RunnerImageStore) -> None:
        assert store.list_downloaded() == []


class TestUpsert:
    def test_insert_then_get(self, store: RunnerImageStore) -> None:
        store.upsert(_image(tag="v1", digest="sha256:abc", size_bytes=123, notes="hello"))
        got = store.get("hal0ai/hal0-toolbox-cpu")
        assert got is not None
        assert got.tag == "v1"
        assert got.digest == "sha256:abc"
        assert got.size_bytes == 123
        assert got.notes == "hello"
        assert got.discovered_at is not None
        assert got.updated_at is not None

    def test_upsert_updates_existing_row(self, store: RunnerImageStore) -> None:
        store.upsert(_image(tag="v1"))
        first = store.get("hal0ai/hal0-toolbox-cpu")
        store.upsert(_image(tag="v2", digest="sha256:def"))
        second = store.get("hal0ai/hal0-toolbox-cpu")
        assert second is not None
        assert second.tag == "v2"
        assert second.digest == "sha256:def"
        # discovered_at preserved across the update (not treated as a
        # brand-new discovery).
        assert second.discovered_at == first.discovered_at

    def test_build_and_extra_round_trip_json(self, store: RunnerImageStore) -> None:
        store.upsert(
            _image(build={"context": ".", "dockerfile": "Dockerfile"}, extra={"foo": "bar"})
        )
        got = store.get("hal0ai/hal0-toolbox-cpu")
        assert got.build == {"context": ".", "dockerfile": "Dockerfile"}
        assert got.extra == {"foo": "bar"}

    def test_runtime_metadata_round_trips(self, store: RunnerImageStore) -> None:
        # feat/catalogue-runtime-metadata: runtime_family is a scalar column,
        # supported_backends a JSON column (supported_backends_json,
        # migration 010) — both must survive a write/read cycle.
        store.upsert(_image(runtime_family="comfyui", supported_backends=["rocm"]))
        got = store.get("hal0ai/hal0-toolbox-cpu")
        assert got is not None
        assert got.runtime_family == "comfyui"
        assert got.supported_backends == ["rocm"]

    def test_runtime_metadata_defaults_when_absent(self, store: RunnerImageStore) -> None:
        # A row written without the fields (pre-010 shape) reads back as
        # "not declared", never as a veto.
        store.upsert(_image())
        got = store.get("hal0ai/hal0-toolbox-cpu")
        assert got is not None
        assert got.runtime_family is None
        assert got.supported_backends == []

    def test_persists_across_instances(self, store: RunnerImageStore, tmp_path: Path) -> None:
        store.upsert(_image())
        store2 = RunnerImageStore(db_path=tmp_path / "hal0.db")
        assert store2.get("hal0ai/hal0-toolbox-cpu") is not None

    def test_on_change_invoked(self, store: RunnerImageStore) -> None:
        calls = []
        store.on_change = lambda: calls.append(1)
        store.upsert(_image())
        assert calls == [1]

    def test_on_change_failure_is_swallowed(self, store: RunnerImageStore) -> None:
        def _boom() -> None:
            raise RuntimeError("boom")

        store.on_change = _boom
        # Must not raise despite the hook blowing up.
        store.upsert(_image())


class TestLocalState:
    def test_set_local_state_marks_downloaded(self, store: RunnerImageStore) -> None:
        store.upsert(_image())
        updated = store.set_local_state(
            "hal0ai/hal0-toolbox-cpu",
            local_path="/var/lib/hal0/runner-images/hal0-toolbox-cpu",
            downloaded_at="2026-07-31T00:00:00Z",
        )
        assert updated is not None
        assert updated.downloaded is True
        assert updated.local_path.endswith("hal0-toolbox-cpu")
        assert "hal0ai/hal0-toolbox-cpu" in [i.id for i in store.list_downloaded()]

    def test_set_local_state_unknown_id_returns_none(self, store: RunnerImageStore) -> None:
        assert store.set_local_state("nope", local_path="/x") is None

    def test_clearing_local_path_removes_from_downloaded(self, store: RunnerImageStore) -> None:
        store.upsert(_image())
        store.set_local_state("hal0ai/hal0-toolbox-cpu", local_path="/x")
        store.set_local_state("hal0ai/hal0-toolbox-cpu", local_path=None)
        assert store.list_downloaded() == []
        got = store.get("hal0ai/hal0-toolbox-cpu")
        assert got.downloaded is False


class TestRunnerImageTag:
    def test_set_and_read_tags(self, store: RunnerImageStore) -> None:
        store.upsert(RunnerImage(id="x", image="ghcr.io/x/a", tag="0826"))
        tags = [
            RunnerImageTag(tag="0826", digest="sha256:" + "a" * 64, size_bytes=10),
            RunnerImageTag(tag="0824", digest="sha256:" + "b" * 64, size_bytes=9),
        ]
        store.set_tags("x", tags)
        row = store.get("x")
        assert row is not None
        assert [t.tag for t in row.tags] == ["0826", "0824"]
        assert row.tags[0].digest == "sha256:" + "a" * 64

    def test_set_tags_replaces(self, store: RunnerImageStore) -> None:
        store.upsert(RunnerImage(id="x", image="ghcr.io/x/a", tag="0826"))
        store.set_tags("x", [RunnerImageTag(tag="old")])
        store.set_tags("x", [RunnerImageTag(tag="new")])
        got = store.get("x")
        assert got is not None
        assert [t.tag for t in got.tags] == ["new"]

    def test_list_populates_tags_for_multiple_images(self, store: RunnerImageStore) -> None:
        # Upsert two images with different tag sets
        store.upsert(RunnerImage(id="a", image="ghcr.io/a/img", tag="v1"))
        store.upsert(RunnerImage(id="b", image="ghcr.io/b/img", tag="v2"))

        # Set tags for image a (2 tags)
        tags_a = [
            RunnerImageTag(tag="v1", digest="sha256:" + "a" * 64, size_bytes=100),
            RunnerImageTag(tag="v0", digest="sha256:" + "c" * 64, size_bytes=95),
        ]
        store.set_tags("a", tags_a)

        # Set tags for image b (1 tag)
        tags_b = [RunnerImageTag(tag="v2", digest="sha256:" + "b" * 64, size_bytes=200)]
        store.set_tags("b", tags_b)

        # Verify list() populates tags correctly for each image, no cross-contamination
        images = store.list()
        assert len(images) == 2

        img_a = next(i for i in images if i.id == "a")
        assert [t.tag for t in img_a.tags] == ["v1", "v0"]
        assert img_a.tags[0].digest == "sha256:" + "a" * 64
        assert img_a.tags[1].digest == "sha256:" + "c" * 64

        img_b = next(i for i in images if i.id == "b")
        assert [t.tag for t in img_b.tags] == ["v2"]
        assert img_b.tags[0].digest == "sha256:" + "b" * 64

        # Mark image b as downloaded and verify list_downloaded() also populates tags correctly
        store.set_local_state("b", local_path="/var/lib/hal0/b")
        downloaded = store.list_downloaded()
        assert len(downloaded) == 1

        dl_img_b = downloaded[0]
        assert dl_img_b.id == "b"
        assert [t.tag for t in dl_img_b.tags] == ["v2"]
        assert dl_img_b.tags[0].digest == "sha256:" + "b" * 64


class TestRemoveTag:
    """``RunnerImageStore.remove_tag`` — per-tag DELETE, disk-reclaim task (D2, #2106)."""

    def _seed(self, store: RunnerImageStore, *, tags: list[str], **overrides) -> None:
        store.upsert(RunnerImage(id="x", image="ghcr.io/x/a", tag=tags[0], **overrides))
        store.set_tags("x", [RunnerImageTag(tag=t) for t in tags])

    def test_deletes_the_tag_row(self, store: RunnerImageStore) -> None:
        self._seed(store, tags=["0826", "0824"])
        assert store.remove_tag("x", "0824") is True
        row = store.get("x")
        assert [t.tag for t in row.tags] == ["0826"]

    def test_unknown_image_id_returns_false(self, store: RunnerImageStore) -> None:
        assert store.remove_tag("nope", "0824") is False

    def test_unknown_tag_on_known_image_returns_false(self, store: RunnerImageStore) -> None:
        self._seed(store, tags=["0826"])
        assert store.remove_tag("x", "9999") is False
        # the real row is untouched
        assert [t.tag for t in store.get("x").tags] == ["0826"]

    def test_prunes_available_tags_list(self, store: RunnerImageStore) -> None:
        self._seed(store, tags=["0826", "0824"], available_tags=["0826", "0824"])
        store.remove_tag("x", "0824")
        assert store.get("x").available_tags == ["0826"]

    def test_prunes_available_tags_to_empty_list(self, store: RunnerImageStore) -> None:
        self._seed(store, tags=["0826"], available_tags=["0826"])
        store.remove_tag("x", "0826")
        assert store.get("x").available_tags == []

    def test_tag_absent_from_available_tags_is_a_noop_there(self, store: RunnerImageStore) -> None:
        """The removed tag might not be images.json's ``available_tags`` at
        all (e.g. a probe-discovered tag) — pruning must not KeyError."""
        self._seed(store, tags=["0826", "0824"], available_tags=["0826"])
        store.remove_tag("x", "0824")
        assert store.get("x").available_tags == ["0826"]

    def test_notifies_on_change_only_when_a_row_is_deleted(self, store: RunnerImageStore) -> None:
        self._seed(store, tags=["0826"])
        calls: list[int] = []
        store.on_change = lambda: calls.append(1)
        assert store.remove_tag("x", "nope") is False
        assert calls == []
        assert store.remove_tag("x", "0826") is True
        assert calls == [1]

    # ── marker ruling: clear local_path/downloaded_at iff (a) the marker's
    # recorded ref matches the removed <image>:<tag>, or (b) no tags remain.

    def test_marker_cleared_when_no_tags_remain(
        self, store: RunnerImageStore, tmp_path: Path
    ) -> None:
        self._seed(store, tags=["0826"])
        store.set_local_state("x", local_path="/nonexistent/marker.json", downloaded_at="t")
        assert store.remove_tag("x", "0826") is True
        row = store.get("x")
        assert row.local_path is None
        assert row.downloaded_at is None

    def test_marker_cleared_when_it_matches_the_removed_tag(
        self, store: RunnerImageStore, tmp_path: Path
    ) -> None:
        import json

        self._seed(store, tags=["0826", "0824"])
        marker = tmp_path / "marker.json"
        marker.write_text(json.dumps({"image_id": "x", "image": "ghcr.io/x/a:0824"}))
        store.set_local_state("x", local_path=str(marker), downloaded_at="t")

        assert store.remove_tag("x", "0824") is True
        row = store.get("x")
        assert row.local_path is None
        assert row.downloaded_at is None
        # the sibling tag row (unrelated to the marker) is untouched
        assert [t.tag for t in row.tags] == ["0826"]

    def test_marker_untouched_when_it_names_a_surviving_tag(
        self, store: RunnerImageStore, tmp_path: Path
    ) -> None:
        import json

        self._seed(store, tags=["0826", "0824"])
        marker = tmp_path / "marker.json"
        marker.write_text(json.dumps({"image_id": "x", "image": "ghcr.io/x/a:0826"}))
        store.set_local_state("x", local_path=str(marker), downloaded_at="t")

        # Deleting 0824 must not disturb the marker recording 0826's pull.
        assert store.remove_tag("x", "0824") is True
        row = store.get("x")
        assert row.local_path == str(marker)
        assert row.downloaded_at == "t"

    def test_marker_read_failure_is_fail_soft_and_leaves_marker_alone(
        self, store: RunnerImageStore
    ) -> None:
        """A local_path that isn't readable JSON (missing file, garbage
        content) must not raise, and — since it can't be confirmed to match
        the removed tag, and a sibling tag still exists — the marker is
        left as-is rather than guessed-cleared."""
        self._seed(store, tags=["0826", "0824"])
        store.set_local_state("x", local_path="/definitely/does/not/exist.json", downloaded_at="t")
        assert store.remove_tag("x", "0824") is True
        row = store.get("x")
        assert row.local_path == "/definitely/does/not/exist.json"
        assert row.downloaded_at == "t"

    def test_no_marker_set_stays_none(self, store: RunnerImageStore) -> None:
        self._seed(store, tags=["0826", "0824"])
        assert store.remove_tag("x", "0824") is True
        row = store.get("x")
        assert row.local_path is None
        assert row.downloaded_at is None
