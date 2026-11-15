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
    assert cfg.extraction_max_tokens == 16384
    assert cfg.extraction_llm_retries == 1
    assert cfg.extraction_task_retries == 2
    assert cfg.extraction_retry_backoff_s == 120
    assert cfg.extraction_worker_slots == 4


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
        # Hindsight reserves 2 slots for consolidation: 2 would starve retain.
        ("extraction_worker_slots", 2),
        ("extraction_worker_slots", 33),
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
        extraction_worker_slots=32,
    )
    assert cfg.model_dump()["extraction_max_tokens"] == 3072


# ── worker-slot reservations (#3700) ─────────────────────────────────────────


def test_reservation_defaults_give_retain_a_slot_and_keep_one_shared():
    cfg = MemoryGraphConfig()
    assert cfg.retain_reserved_slots == 1
    # Hindsight's own WORKER_SLOT_TYPE_DEFAULTS for the other two.
    assert cfg.consolidation_reserved_slots == 2
    assert cfg.refresh_reserved_slots == 0
    reserved = cfg.retain_reserved_slots + cfg.consolidation_reserved_slots
    assert cfg.extraction_worker_slots - reserved == 1


@pytest.mark.parametrize(
    "field", ["retain_reserved_slots", "consolidation_reserved_slots", "refresh_reserved_slots"]
)
@pytest.mark.parametrize("value", [-1, 32])
def test_reservation_out_of_range_is_rejected(field: str, value: int):
    with pytest.raises(ValidationError):
        MemoryGraphConfig(extraction_worker_slots=32, **{field: value})


@pytest.mark.parametrize(
    ("slots", "retain", "consolidation", "refresh"),
    [
        (4, 2, 2, 0),  # no shared slot left
        (4, 1, 2, 1),
        (6, 0, 0, 6),  # more reserved than there are slots
    ],
)
def test_reservations_must_leave_a_shared_slot(
    slots: int, retain: int, consolidation: int, refresh: int
):
    with pytest.raises(ValidationError) as exc:
        MemoryGraphConfig(
            extraction_worker_slots=slots,
            retain_reserved_slots=retain,
            consolidation_reserved_slots=consolidation,
            refresh_reserved_slots=refresh,
        )
    message = str(exc.value)
    assert f"extraction_worker_slots = {slots}" in message
    assert f"= {retain + consolidation + refresh}" in message
    assert f"at most {slots - 1} reserved" in message


def test_reservations_may_fill_all_but_one_slot():
    cfg = MemoryGraphConfig(
        extraction_worker_slots=6,
        retain_reserved_slots=2,
        consolidation_reserved_slots=2,
        refresh_reserved_slots=1,
    )
    assert cfg.refresh_reserved_slots == 1


def test_a_three_slot_toml_without_reservations_still_loads_with_retain_zero():
    """A hal0.toml from before #3700 holding extraction_worker_slots = 3 must
    load after an update: the unset retain reservation shrinks to 0, which is
    exactly what that box ran before (consolidation 2, one shared slot)."""
    cfg = MemoryGraphConfig.model_validate({"extraction_worker_slots": 3})
    assert cfg.retain_reserved_slots == 0
    assert cfg.consolidation_reserved_slots == 2


def test_an_unset_retain_reservation_fits_around_the_other_two():
    cfg = MemoryGraphConfig.model_validate(
        {"extraction_worker_slots": 4, "refresh_reserved_slots": 1}
    )
    assert cfg.retain_reserved_slots == 0
    cfg = MemoryGraphConfig.model_validate(
        {"extraction_worker_slots": 3, "consolidation_reserved_slots": 1}
    )
    assert cfg.retain_reserved_slots == 1


@pytest.mark.parametrize("slots", [4, 6, 32])
def test_a_toml_without_reservations_gets_the_retain_slot_when_it_fits(slots: int):
    cfg = MemoryGraphConfig.model_validate({"extraction_worker_slots": slots})
    assert cfg.retain_reserved_slots == 1


def test_an_explicit_retain_reservation_is_never_shrunk():
    with pytest.raises(ValidationError):
        MemoryGraphConfig.model_validate({"extraction_worker_slots": 3, "retain_reserved_slots": 1})
