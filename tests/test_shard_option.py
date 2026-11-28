"""``--shard I/N`` (tests/conftest.py) partitions the suite for the CI shards."""

from __future__ import annotations

import pytest

from tests.conftest import _parse_shard, assign_shards


def _nodeids() -> list[str]:
    ids = []
    for n in range(60):
        path = f"tests/pkg{n % 7}/test_mod{n}.py"
        ids += [f"{path}::test_{i}" for i in range(1 + (n * 37) % 90)]
    return ids


@pytest.mark.parametrize("total", [1, 2, 4])
def test_every_file_lands_in_exactly_one_shard(total: int) -> None:
    ids = _nodeids()
    assignment = assign_shards(ids, total)
    assert set(assignment) == {nodeid.split("::", 1)[0] for nodeid in ids}
    assert set(assignment.values()) == set(range(1, total + 1))


def test_shards_are_balanced_by_test_count() -> None:
    ids = _nodeids()
    assignment = assign_shards(ids, 4)
    load = [0, 0, 0, 0]
    for nodeid in ids:
        load[assignment[nodeid.split("::", 1)[0]] - 1] += 1
    assert max(load) - min(load) <= 90  # at most one file's worth apart


def test_assignment_ignores_collection_order() -> None:
    ids = _nodeids()
    assert assign_shards(ids, 4) == assign_shards(list(reversed(ids)), 4)


@pytest.mark.parametrize("spec", ["0/4", "5/4", "1/0", "x/4", "3"])
def test_bad_shard_spec_is_a_usage_error(spec: str) -> None:
    with pytest.raises(pytest.UsageError):
        _parse_shard(spec)


def test_shard_spec_parses() -> None:
    assert _parse_shard("2/4") == (2, 4)
