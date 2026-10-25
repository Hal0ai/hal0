"""The suite-wide ``memory_budget`` stub (``tests/conftest.py``) does what it promises."""

from __future__ import annotations

import inspect

import pytest

from hal0.api import memory_source
from tests.fixtures.memory_budget import NO_GPU_BUDGET, MemoryBudgetStub


def test_every_test_gets_the_stub_not_the_collector(memory_budget: MemoryBudgetStub) -> None:
    assert memory_source.collect_memory_budget is memory_budget


async def test_the_stub_answers_a_box_with_no_gpu_and_records_the_app(
    memory_budget: MemoryBudgetStub,
) -> None:
    app = object()

    answer = await memory_source.collect_memory_budget(app, gpu_capable=True)

    assert answer is NO_GPU_BUDGET
    assert answer.render_free_gb is None  # an unknown fit never refuses a render
    assert memory_budget.apps == [app]


async def test_the_stub_raises_what_it_was_told_to(memory_budget: MemoryBudgetStub) -> None:
    memory_budget.result = RuntimeError("nvidia-smi hung")

    with pytest.raises(RuntimeError, match="nvidia-smi hung"):
        await memory_source.collect_memory_budget(object())

    assert memory_budget.apps  # the call was valid; it was the answer that failed


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        ((), {}),  # the app is required
        (("app", "positional-per-slot"), {}),  # per_slot is keyword-only
        (("app",), {"per_slot_rows": {}}),  # an argument the collector does not have
    ],
    ids=["no-app", "extra-positional", "unknown-keyword"],
)
async def test_a_call_the_collector_would_reject_is_recorded_as_drift(
    memory_budget: MemoryBudgetStub, args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    """The preflight fails open on any exception, so the stub must also flag the drift itself."""
    with pytest.raises(TypeError):
        await memory_source.collect_memory_budget(*args, **kwargs)

    assert len(memory_budget.drift) == 1
    assert memory_budget.apps == []
    memory_budget.drift.clear()  # this test provoked it; teardown must not fail it


def test_a_test_can_opt_out_for_the_real_collector(real_memory_budget: None) -> None:
    assert memory_source.collect_memory_budget.__name__ == "collect_memory_budget"
    assert not isinstance(memory_source.collect_memory_budget, MemoryBudgetStub)
    assert inspect.iscoroutinefunction(memory_source.collect_memory_budget)
