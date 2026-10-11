"""Unit tests for hal0.registry.runner_image_store.RunnerImageStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.registry.runner_image import RunnerImage, RunnerImageTag
from hal0.registry.runner_image_store import RunnerImageStore


@pytest.fixture
def store(tmp_path: Path) -> RunnerImageStore:
    return RunnerImageStore(db_path=tmp_path / "hal0.db")


def _image(image_id: str = "cpu", **kw) -> RunnerImage:
    kw.setdefault("tag", "b1-r1")
    return RunnerImage(id=image_id, image=f"ghcr.io/hal0ai/hal0-runner-{image_id}", **kw)


class TestEmptyStore:
    def test_list_empty(self, store: RunnerImageStore) -> None:
        assert store.list() == []

    def test_get_unknown_returns_none(self, store: RunnerImageStore) -> None:
        assert store.get("nope") is None

    def test_list_downloaded_empty(self, store: RunnerImageStore) -> None:
        assert store.list_downloaded() == []


class TestUpsert:
    def test_insert_then_get(self, store: RunnerImageStore) -> None:
        store.upsert(_image(tag="v1", digest="sha256:abc", size_bytes=123, title="CPU"))
        got = store.get("cpu")
        assert got is not None
        assert got.tag == "v1"
        assert got.digest == "sha256:abc"
        assert got.size_bytes == 123
        assert got.title == "CPU"
        assert got.discovered_at is not None
        assert got.updated_at is not None

    def test_upsert_updates_existing_row(self, store: RunnerImageStore) -> None:
        store.upsert(_image(tag="v1"))
        first = store.get("cpu")
        store.upsert(_image(tag="v2", digest="sha256:def"))
        second = store.get("cpu")
        assert second is not None
        assert second.tag == "v2"
        assert second.digest == "sha256:def"
        # discovered_at preserved across the update (not treated as a
        # brand-new discovery).
        assert second.discovered_at == first.discovered_at

    def test_extra_round_trips_json(self, store: RunnerImageStore) -> None:
        store.upsert(_image(extra={"foo": "bar"}))
        got = store.get("cpu")
        assert got is not None
        assert got.extra == {"foo": "bar"}

    def test_catalogue_columns_round_trip(self, store: RunnerImageStore) -> None:
        # Migration 011: title/summary/family/tier are scalar columns,
        # backends/lanes_gated JSON-list columns — all must survive a
        # write/read cycle.
        store.upsert(
            _image(
                title="Standard",
                summary="llama.cpp on ROCm and Vulkan.",
                family="llama-server",
                tier="default",
                backends=["rocm", "vulkan"],
                lanes_gated=["vulkan"],
            )
        )
        got = store.get("cpu")
        assert got is not None
        assert got.title == "Standard"
        assert got.summary == "llama.cpp on ROCm and Vulkan."
        assert got.family == "llama-server"
        assert got.tier == "default"
        assert got.backends == ["rocm", "vulkan"]
        assert got.lanes_gated == ["vulkan"]

    def test_json_lists_keep_order(self, store: RunnerImageStore) -> None:
        store.upsert(
            _image(
                backends=["vulkan", "rocm", "cpu"],
                lanes_gated=["rocm", "vulkan"],
                available_tags=["b3", "b2", "b1"],
            )
        )
        got = store.get("cpu")
        assert got is not None
        assert got.backends == ["vulkan", "rocm", "cpu"]
        assert got.lanes_gated == ["rocm", "vulkan"]
        assert got.available_tags == ["b3", "b2", "b1"]

    def test_catalogue_columns_default_when_absent(self, store: RunnerImageStore) -> None:
        # A row written without the optional facts reads back as "not
        # declared": None scalars, empty lists.
        store.upsert(_image())
        got = store.get("cpu")
        assert got is not None
        assert got.title is None
        assert got.summary is None
        assert got.family is None
        assert got.tier is None
        assert got.backends == []
        assert got.lanes_gated == []
        assert got.available_tags == []

    def test_empty_lists_overwrite_previous_lists(self, store: RunnerImageStore) -> None:
        store.upsert(_image(backends=["rocm"], lanes_gated=["rocm"]))
        store.upsert(_image(backends=[], lanes_gated=[]))
        got = store.get("cpu")
        assert got is not None
        assert got.backends == []
        assert got.lanes_gated == []

    def test_list_returns_catalogue_columns(self, store: RunnerImageStore) -> None:
        store.upsert(_image("cpu", backends=["cpu"], tier="default"))
        store.upsert(_image("flm", backends=["npu"], tier="optional", title="FLM"))
        by_id = {i.id: i for i in store.list()}
        assert by_id["cpu"].backends == ["cpu"]
        assert by_id["flm"].backends == ["npu"]
        assert by_id["flm"].tier == "optional"
        assert by_id["flm"].title == "FLM"

    def test_persists_across_instances(self, store: RunnerImageStore, tmp_path: Path) -> None:
        store.upsert(_image())
        store2 = RunnerImageStore(db_path=tmp_path / "hal0.db")
        assert store2.get("cpu") is not None

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
            "cpu",
            local_path="/var/lib/hal0/runner-images/hal0-runner-cpu",
            downloaded_at="2026-07-31T00:00:00Z",
        )
        assert updated is not None
        assert updated.downloaded is True
        assert updated.local_path.endswith("hal0-runner-cpu")
        assert "cpu" in [i.id for i in store.list_downloaded()]

    def test_set_local_state_unknown_id_returns_none(self, store: RunnerImageStore) -> None:
        assert store.set_local_state("nope", local_path="/x") is None

    def test_upsert_without_local_state_keeps_the_recorded_download(
        self, store: RunnerImageStore
    ) -> None:
        store.upsert(_image())
        store.set_local_state("cpu", local_path="/x", downloaded_at="t")
        store.upsert(_image())  # a sync row carries no download state
        got = store.get("cpu")
        assert got is not None and got.local_path == "/x" and got.downloaded_at == "t"

    def test_clearing_local_path_removes_from_downloaded(self, store: RunnerImageStore) -> None:
        store.upsert(_image())
        store.set_local_state("cpu", local_path="/x")
        store.set_local_state("cpu", local_path=None)
        assert store.list_downloaded() == []
        got = store.get("cpu")
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


class TestPruneAbsent:
    """``prune_absent`` — the sync's post-fetch sweep of delisted rows."""

    def test_deletes_rows_not_kept(self, store: RunnerImageStore) -> None:
        store.upsert(_image("cpu"))
        store.upsert(_image("flm"))
        store.upsert(_image("kokoro"))
        assert store.prune_absent({"cpu"}) == 2
        assert [i.id for i in store.list()] == ["cpu"]

    def test_nothing_to_prune_returns_zero(self, store: RunnerImageStore) -> None:
        store.upsert(_image("cpu"))
        assert store.prune_absent({"cpu", "flm"}) == 0
        assert store.get("cpu") is not None

    def test_ignores_local_path(self, store: RunnerImageStore) -> None:
        """A downloaded row is pruned like any other: the catalogue is the
        list of runners hal0 can launch."""
        store.upsert(_image("cpu"))
        store.upsert(_image("flm"))
        store.set_local_state("flm", local_path="/var/lib/hal0/flm.json", downloaded_at="t")
        assert store.prune_absent({"cpu"}) == 1
        assert store.get("flm") is None
        assert store.list_downloaded() == []

    def test_empty_keep_set_deletes_everything(self, store: RunnerImageStore) -> None:
        store.upsert(_image("cpu"))
        store.upsert(_image("flm"))
        assert store.prune_absent(set()) == 2
        assert store.list() == []

    def test_removes_orphaned_tag_rows(self, store: RunnerImageStore) -> None:
        store.upsert(_image("cpu"))
        store.upsert(_image("flm"))
        store.set_tags("cpu", [RunnerImageTag(tag="b1-r1")])
        store.set_tags("flm", [RunnerImageTag(tag="v1"), RunnerImageTag(tag="v0")])
        store.prune_absent({"cpu"})
        assert store.tags_for("flm") == []
        assert [t.tag for t in store.tags_for("cpu")] == ["b1-r1"]

    def test_notifies_on_change_only_when_rows_were_deleted(self, store: RunnerImageStore) -> None:
        store.upsert(_image("cpu"))
        calls: list[int] = []
        store.on_change = lambda: calls.append(1)
        assert store.prune_absent({"cpu"}) == 0
        assert calls == []
        assert store.prune_absent(set()) == 1
        assert calls == [1]
