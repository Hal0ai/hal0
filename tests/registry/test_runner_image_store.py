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
