"""ADR-0014 graph-extraction gate tests.

We do NOT run the real cognify pipeline here — that requires an LLM
with structured-output reliability and is the v0.4 eval suite's
problem (ADR-0014 §4). Instead we monkeypatch ``cognee.cognify`` so
we can assert:

  - disabled wrappers never enqueue a build task.
  - enabled wrappers DO enqueue a build task after ``add`` returns.
  - failures get counted + recorded in ``graph_status()``.
  - ``set_graph_enabled(False)`` cancels in-flight builds.
  - search ``mode`` falls back to vector when graph is off.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Tests in this module need a real Cognee install (the wrapper still
# imports it for the vector path). Skip if missing so the suite stays
# green on stripped CI environments.
pytest.importorskip("cognee")


pytestmark = pytest.mark.asyncio


@pytest.fixture
def patch_cognify(monkeypatch: pytest.MonkeyPatch):
    """Replace ``cognee.cognify`` with a stub we can assert on.

    Returns a MagicMock that records the calls. We also stub
    ``cognee.add`` + the helper imports the wrapper's ``_chunk_and_embed``
    pulls so we don't have to run the real embedding pipeline either.
    """
    import cognee

    calls: list[dict[str, Any]] = []

    async def fake_cognify(**kwargs):
        calls.append(kwargs)

    async def fake_add(*args, **kwargs):
        # Mimic Cognee's add result shape — wrapper inspects dataset_id.
        m = MagicMock()
        m.dataset_id = "fake-dataset-id"
        return m

    async def fake_chunk_embed(*args, **kwargs):
        return None

    async def fake_latest_data_id(*args, **kwargs):
        return "fake-data-id"

    monkeypatch.setattr(cognee, "cognify", fake_cognify)
    monkeypatch.setattr(cognee, "add", fake_add)

    from hal0.memory import cognee_wrapper as cw_mod

    monkeypatch.setattr(cw_mod.CogneeWrapper, "_chunk_and_embed", fake_chunk_embed)
    monkeypatch.setattr(cw_mod.CogneeWrapper, "_latest_cognee_data_id", fake_latest_data_id)
    return calls


@pytest.fixture
def wrapper_factory(cognee_dir: Path):
    """Build a CogneeWrapper pointed at the per-test dir."""
    from hal0.memory import CogneeWrapper

    def _build(**kwargs) -> Any:
        return CogneeWrapper(cognee_dir=cognee_dir, **kwargs)

    return _build


class TestGraphStatusDefaults:
    async def test_disabled_default(self, wrapper_factory, patch_cognify) -> None:
        w = wrapper_factory()
        s = w.graph_status()
        assert s["enabled"] is False
        assert s["route"] == "upstream"
        assert s["builds_ok"] == 0
        assert s["errors"] == 0
        assert s["in_flight"] == 0
        assert s["last_built_at"] is None

    async def test_enabled_construction(self, wrapper_factory, patch_cognify) -> None:
        w = wrapper_factory(graph_enabled=True, graph_route="primary")
        s = w.graph_status()
        assert s["enabled"] is True
        assert s["route"] == "primary"


class TestAddDispatch:
    async def test_disabled_does_not_call_cognify(self, wrapper_factory, patch_cognify) -> None:
        w = wrapper_factory(graph_enabled=False)
        await w.add("hello world")
        # Give any rogue background task a turn — there shouldn't be one.
        await asyncio.sleep(0)
        assert patch_cognify == []
        assert w.graph_status()["builds_ok"] == 0
        assert w.graph_status()["errors"] == 0

    async def test_enabled_enqueues_cognify(self, wrapper_factory, patch_cognify) -> None:
        w = wrapper_factory(graph_enabled=True)
        await w.add("hello world")
        # Drain background tasks — wrapper holds a set we can await.
        for t in list(w._graph_tasks):
            await t
        assert len(patch_cognify) == 1
        kw = patch_cognify[0]
        assert kw["datasets"] == ["shared"]
        assert kw["run_in_background"] is False
        s = w.graph_status()
        assert s["builds_ok"] == 1
        assert s["errors"] == 0
        assert s["last_built_at"] is not None


class TestBuildFailureCounter:
    async def test_failed_build_increments_errors(
        self, wrapper_factory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import cognee

        async def boom(**kwargs):
            raise RuntimeError("structured-output parse failed")

        async def fake_add(*args, **kwargs):
            m = MagicMock()
            m.dataset_id = "fake-dataset-id"
            return m

        async def fake_chunk_embed(*args, **kwargs):
            return None

        async def fake_latest_data_id(*args, **kwargs):
            return "fake-data-id"

        monkeypatch.setattr(cognee, "cognify", boom)
        monkeypatch.setattr(cognee, "add", fake_add)
        from hal0.memory import cognee_wrapper as cw_mod

        monkeypatch.setattr(cw_mod.CogneeWrapper, "_chunk_and_embed", fake_chunk_embed)
        monkeypatch.setattr(cw_mod.CogneeWrapper, "_latest_cognee_data_id", fake_latest_data_id)

        w = wrapper_factory(graph_enabled=True)
        await w.add("hello world")
        for t in list(w._graph_tasks):
            await t
        s = w.graph_status()
        assert s["errors"] == 1
        assert s["builds_ok"] == 0
        assert s["last_error"] is not None
        assert "RuntimeError" in s["last_error"]


class TestDisableCancelsInFlight:
    async def test_disable_cancels(self, wrapper_factory, monkeypatch: pytest.MonkeyPatch) -> None:
        import cognee

        long_running = asyncio.Event()

        async def slow_cognify(**kwargs):
            # Park until cancelled (or until the test releases).
            try:
                await asyncio.wait_for(long_running.wait(), timeout=5)
            except TimeoutError:
                return

        async def fake_add(*args, **kwargs):
            m = MagicMock()
            m.dataset_id = "fake-dataset-id"
            return m

        async def fake_chunk_embed(*args, **kwargs):
            return None

        async def fake_latest_data_id(*args, **kwargs):
            return "fake-data-id"

        monkeypatch.setattr(cognee, "cognify", slow_cognify)
        monkeypatch.setattr(cognee, "add", fake_add)
        from hal0.memory import cognee_wrapper as cw_mod

        monkeypatch.setattr(cw_mod.CogneeWrapper, "_chunk_and_embed", fake_chunk_embed)
        monkeypatch.setattr(cw_mod.CogneeWrapper, "_latest_cognee_data_id", fake_latest_data_id)

        w = wrapper_factory(graph_enabled=True)
        await w.add("hello world")
        # Build task is pending.
        await asyncio.sleep(0)
        assert len(w._graph_tasks) == 1
        # Disable — cancels in-flight per ADR-0014 §6.
        w.set_graph_enabled(False)
        # Give cancellation a beat to propagate.
        for t in list(w._graph_tasks):
            with pytest.raises((asyncio.CancelledError, BaseException)):
                await t
        s = w.graph_status()
        assert s["enabled"] is False
        # No build completed — neither ok nor error counter ticked.
        assert s["builds_ok"] == 0
        long_running.set()


class TestSetGraphEnabled:
    async def test_route_change_persists(self, wrapper_factory, patch_cognify) -> None:
        w = wrapper_factory()
        w.set_graph_enabled(True, route="agent")
        s = w.graph_status()
        assert s["enabled"] is True
        assert s["route"] == "agent"

    async def test_invalid_route_raises(self, wrapper_factory, patch_cognify) -> None:
        w = wrapper_factory()
        with pytest.raises(ValueError):
            w.set_graph_enabled(True, route="bogus")


class TestSearchModeFallback:
    async def test_invalid_mode_raises(self, wrapper_factory, patch_cognify) -> None:
        w = wrapper_factory()
        with pytest.raises(ValueError):
            await w.search("query", mode="invalid")

    async def test_graph_mode_without_gate_falls_back_to_vector(
        self, wrapper_factory, patch_cognify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import cognee

        async def fake_search(**kwargs):
            return []

        monkeypatch.setattr(cognee, "search", fake_search)
        w = wrapper_factory(graph_enabled=False)
        # Should not raise; should not blow up downstream either.
        results = await w.search("query", mode="graph")
        assert results == []
