"""A stand-in for ``hal0.api.memory_source.collect_memory_budget``.

The collector reads the machine's RAM, its cgroup limit and its GPU. The fit preflight on
``POST /v1/images/generations`` and the fit on the ComfyUI model routes both call it, so a
test that goes through either would otherwise judge a render against whichever host runs the
suite. ``tests/conftest.py`` patches this stub in for every test (the ``memory_budget``
fixture); a test that needs the real collector asks for ``real_memory_budget``. The collector
itself is covered by ``tests/hardware/test_memory_budget.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import create_autospec

from hal0.api.memory_source import collect_memory_budget
from hal0.hardware.memory_budget import MemoryBudget, RamBudget

#: A box with no GPU lane: its fit is unknown, and an unknown fit never refuses a render.
NO_GPU_BUDGET = MemoryBudget(
    gpu=None,
    ram=RamBudget(limit_gb=128.0, used_gb=28.0, free_gb=100.0, source="container"),
    render_free_gb=None,
    render_ram_free_gb=100.0,
)


class MemoryBudgetStub:
    """Answers ``collect_memory_budget(app, ...)`` with ``result`` and records each ``app``.

    ``result`` is a :class:`MemoryBudget` to return or an exception to raise. Every call is
    checked against the real collector's signature (``create_autospec``), so a rename or a
    new required argument in ``collect_memory_budget`` cannot hide behind this stub. The
    preflight fails open on any exception, which would swallow the ``TypeError`` too, so a
    drifted call is also recorded in ``drift`` and the ``memory_budget`` fixture fails the
    test on it at teardown.
    """

    def __init__(self) -> None:
        self.result: MemoryBudget | Exception = NO_GPU_BUDGET
        self.apps: list[Any] = []
        self.drift: list[TypeError] = []
        # Built on the first call: most tests never reach the collector.
        self._signature: Any = None

    async def __call__(self, *args: Any, **kwargs: Any) -> MemoryBudget:
        if self._signature is None:
            self._signature = create_autospec(collect_memory_budget)
        try:
            await self._signature(*args, **kwargs)
        except TypeError as exc:  # the only thing the bare autospec can raise
            self.drift.append(exc)
            raise
        self.apps.append(args[0] if args else kwargs["app"])
        if isinstance(self.result, Exception):
            raise self.result
        return self.result
