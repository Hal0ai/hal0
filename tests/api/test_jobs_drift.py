"""``hal0.jobs`` stays in step with the dispatcher's capability tables.

``jobs.py`` imports the request-path fragments from the resolver but cannot derive which slot
type or seed name serves which job. These tests fail when someone adds a capability-only path,
slot type or slot name in ``_capability_resolve.py`` and forgets ``jobs.py``.
"""

from __future__ import annotations

import pytest

from hal0.dispatcher import _capability_resolve as resolver
from hal0.install.static_seeds import SEED_TEMPLATES
from hal0.jobs import JOBS, job_for_path, job_for_slot_name
from hal0.model_meta import SLOT_TYPES

#: Jobs whose slot is not an ordinary seed template: stt is created on demand, npu by setup.
_NO_SEED = {"stt", "npu"}


def _jobs_serving_type(slot_type: str) -> set[str]:
    return {job.capability for job in JOBS.values() if slot_type in job.slot_types}


@pytest.mark.parametrize("slot_type", sorted(resolver._CAPABILITY_ONLY_TYPE_PATHS))
def test_every_capability_only_slot_type_has_a_job(slot_type: str) -> None:
    paths = resolver._CAPABILITY_ONLY_TYPE_PATHS[slot_type]
    serving = _jobs_serving_type(slot_type)
    assert serving, f"no job in hal0.jobs.JOBS lists slot type {slot_type!r}"
    # A reranking slot also serves /embeddings, so only the type's first path names its job.
    assert job_for_path(paths[0]) in serving
    for fragment in paths:
        assert job_for_path(fragment) is not None, f"{fragment!r} maps to no job"


def test_every_job_slot_type_is_a_resolver_capability_type() -> None:
    for job in JOBS.values():
        for slot_type in job.slot_types:
            assert slot_type in resolver._CAPABILITY_ONLY_TYPE_PATHS, (job.capability, slot_type)
            assert slot_type in SLOT_TYPES, (job.capability, slot_type)


@pytest.mark.parametrize(
    ("fragments", "capability"),
    [
        (resolver._EMBED_PATHS, "embed"),
        (resolver._RERANK_PATHS, "rerank"),
        (resolver._TTS_PATHS, "tts"),
        (resolver._STT_PATHS, "stt"),
        (resolver._IMAGE_PATHS, "image"),
    ],
)
def test_resolver_path_fragments_map_to_their_job(
    fragments: tuple[str, ...], capability: str
) -> None:
    for fragment in fragments:
        assert job_for_path(f"/v1{fragment}") == capability


def test_every_resolver_path_tuple_is_known_to_jobs() -> None:
    """A new ``_*_PATHS`` tuple in the resolver must be given a job (or listed as chat-shaped)."""
    known = {
        resolver._EMBED_PATHS,
        resolver._RERANK_PATHS,
        resolver._TTS_PATHS,
        resolver._STT_PATHS,
        resolver._IMAGE_PATHS,
        resolver._CHAT_SHAPED_PATHS,
    }
    declared = {
        value
        for name, value in vars(resolver).items()
        if name.endswith("_PATHS") and isinstance(value, tuple)
    }
    assert declared <= known, f"resolver path tuples jobs.py does not know: {declared - known}"


def test_resolver_stock_slot_names_are_jobs() -> None:
    for name, paths in resolver._CAPABILITY_ONLY_SLOT_PATHS.items():
        capability = job_for_slot_name(name)
        assert capability is not None, f"{name!r} is not the stock name of any job"
        assert job_for_path(paths[0]) == capability


def test_every_seeded_job_stock_name_is_a_seed_template() -> None:
    for job in JOBS.values():
        if job.capability in _NO_SEED:
            continue
        assert job.stock_name in SEED_TEMPLATES, (job.capability, job.stock_name)
        assert job.stock_name in resolver._CAPABILITY_ONLY_SLOT_PATHS
