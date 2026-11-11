"""``[memory.compat]``: the engine-compatible memory endpoint's tunables.

Three keys, each with a reader in ``hal0.api.routes.memory_compat``
(``_settings``): the shared document-id prefixes, the cap on concurrent
reflects, and the largest retained item.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import Hal0Config, MemoryCompatConfig, MemoryConfig


def test_the_shipped_defaults():
    compat = MemoryCompatConfig()
    # The ids the Hindsight coding-agents plugin derives from the repository
    # itself: one commit, the commit log, a codebase-survey marker.
    assert compat.shared_document_prefixes == ["git:", "gitlog:", "survey-baseline:"]
    assert compat.max_concurrent_reflects == 2
    assert compat.max_item_bytes == 16 * 1024 * 1024
    assert MemoryConfig().compat == compat
    assert Hal0Config().memory.compat == compat


def test_default_lists_are_not_shared_between_instances():
    first, second = MemoryCompatConfig(), MemoryCompatConfig()
    first.shared_document_prefixes.append("x:")
    assert "x:" not in second.shared_document_prefixes


def test_it_loads_from_toml_shaped_input():
    config = Hal0Config.model_validate(
        {
            "memory": {
                "compat": {
                    "shared_document_prefixes": ["adr:", "git:", "adr:"],
                    "max_concurrent_reflects": 4,
                    "max_item_bytes": 1048576,
                }
            }
        }
    )
    compat = config.memory.compat
    assert compat.shared_document_prefixes == ["adr:", "git:"]
    assert (compat.max_concurrent_reflects, compat.max_item_bytes) == (4, 1048576)
    # An empty list shares nothing; it is not "use the default".
    assert MemoryCompatConfig(shared_document_prefixes=[]).shared_document_prefixes == []


@pytest.mark.parametrize(
    "prefixes",
    [
        [""],
        ["has space:"],
        ["a::b"],
        ["::"],
        ["é:"],
        ["x" * 65],
        [7],
        ["a/b"],
        [f"p{n}:" for n in range(33)],
    ],
)
def test_a_prefix_that_could_not_start_a_stored_id_is_refused(prefixes):
    """A prefix is matched against a client's document id, and ``::`` is the
    separator of a stored id: it can never be part of one."""
    with pytest.raises(ValidationError):
        MemoryCompatConfig(shared_document_prefixes=prefixes)


@pytest.mark.parametrize(
    "values",
    [
        {"max_concurrent_reflects": 0},
        {"max_concurrent_reflects": 65},
        {"max_item_bytes": 1023},
        # The request body is capped at 48 MiB: an item must fit inside it.
        {"max_item_bytes": 32 * 1024 * 1024 + 1},
        {"shared_prefixes": ["git:"]},
    ],
)
def test_limits_out_of_range_and_unknown_keys_fail_the_load(values):
    with pytest.raises(ValidationError):
        MemoryCompatConfig(**values)
