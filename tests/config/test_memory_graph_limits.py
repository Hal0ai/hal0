"""[memory.graph] extraction limits (#1834).

Bounds are the contract the dashboard inputs and the root-side drop-in
validator both rely on; a value outside them must fail at schema time, not
at hindsight-api start-up.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import MemoryGraphConfig


def test_defaults_cap_a_shared_slot():
    cfg = MemoryGraphConfig()
    assert cfg.extraction_max_concurrent == 1
    assert cfg.extraction_max_tokens == 4096
    assert cfg.extraction_llm_retries == 1
    assert cfg.extraction_task_retries == 2
    assert cfg.extraction_retry_backoff_s == 120


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("extraction_max_concurrent", 0),
        ("extraction_max_concurrent", 9),
        # hindsight-api refuses to start unless the completion budget exceeds
        # its retain chunk size (3000 chars); the floor keeps that unreachable.
        ("extraction_max_tokens", 3000),
        ("extraction_max_tokens", 65536),
        ("extraction_llm_retries", -1),
        ("extraction_llm_retries", 6),
        ("extraction_task_retries", -1),
        ("extraction_task_retries", 11),
        ("extraction_retry_backoff_s", 5),
        ("extraction_retry_backoff_s", 4000),
    ],
)
def test_out_of_range_values_are_rejected(field: str, value: int):
    with pytest.raises(ValidationError):
        MemoryGraphConfig(**{field: value})


def test_in_range_values_round_trip():
    cfg = MemoryGraphConfig(
        extraction_max_concurrent=8,
        extraction_max_tokens=3072,
        extraction_llm_retries=0,
        extraction_task_retries=0,
        extraction_retry_backoff_s=10,
    )
    assert cfg.model_dump()["extraction_max_tokens"] == 3072
