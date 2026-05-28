"""Wrapper-level namespace tests — fast, no Cognee init.

PR #366 review found that the singleton ``app.state.memory_wrapper``
(``client_id="anonymous"``, ``private_mode=False``) collapsed
``private:<x>`` writes to ``shared`` inside ``_effective_write_dataset``.
That broke #317's REST fix end-to-end: the route resolved the right
dataset, the wrapper threw it away.

These tests pin the post-fix contract without standing up Cognee +
LanceDB + Kuzu — we bypass ``__init__`` so the fixtures stay sub-millisecond
and live in the default (non-``slow``) suite where CI runs them on every PR.

Issue #367 is the long-form discussion of the fix.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from hal0.memory.cognee_wrapper import (
    PRIVATE_PREFIX,
    SHARED_DATASET,
    CogneeWrapper,
)


def _bare_wrapper(*, client_id: str, private_mode: bool) -> CogneeWrapper:
    """Return a ``CogneeWrapper`` with only the fields ``_effective_write_dataset``
    needs — skips ``__init__`` so we don't touch Cognee or the sidecar.

    Tests that exercise the full ``add`` path (with the real Cognee
    backend) live in ``test_cognee_wrapper.py`` and are gated by
    ``@pytest.mark.slow``.
    """
    w = object.__new__(CogneeWrapper)
    w._client_id = client_id  # type: ignore[attr-defined]
    w._private_mode = private_mode  # type: ignore[attr-defined]
    w._write_dataset = (  # type: ignore[attr-defined]
        f"{PRIVATE_PREFIX}{client_id}" if private_mode else SHARED_DATASET
    )
    return w


# ── Non-private singleton wrapper (the production shape, #367) ─────────────


def test_effective_write_dataset_passthrough_shared() -> None:
    """Non-private wrapper, body asks for ``shared`` → ``shared``."""
    w = _bare_wrapper(client_id="anonymous", private_mode=False)
    assert w._effective_write_dataset(SHARED_DATASET) == SHARED_DATASET


def test_effective_write_dataset_passthrough_custom() -> None:
    """Non-private wrapper, body asks for a custom dataset (e.g.
    ``agents``) → unchanged."""
    w = _bare_wrapper(client_id="anonymous", private_mode=False)
    assert w._effective_write_dataset("agents") == "agents"


def test_effective_write_dataset_persists_private_for_singleton() -> None:
    """Non-private wrapper, body carries ``private:hermes-agent``
    (resolved upstream by REST/MCP from headers) → persisted verbatim.

    Pre-#367 this collapsed to ``shared`` and silently leaked every
    agent's "private" write into the global bucket.
    """
    w = _bare_wrapper(client_id="anonymous", private_mode=False)
    assert w._effective_write_dataset("private:hermes-agent") == "private:hermes-agent"


def test_effective_write_dataset_empty_falls_back_to_shared() -> None:
    """Non-private wrapper, empty requested → ``shared`` default."""
    w = _bare_wrapper(client_id="anonymous", private_mode=False)
    assert w._effective_write_dataset("") == SHARED_DATASET


# ── Private wrapper (legacy per-client shape — still works) ────────────────


def test_effective_write_dataset_private_mode_pins_to_own_namespace() -> None:
    """``private_mode=True`` wrapper, any body value → caller's own
    namespace. Smuggling ``shared`` doesn't escape.
    """
    w = _bare_wrapper(client_id="alice", private_mode=True)
    assert w._effective_write_dataset(SHARED_DATASET) == "private:alice"
    assert w._effective_write_dataset("private:bob") == "private:alice"
    assert w._effective_write_dataset("agents") == "private:alice"


# ── End-to-end add() routes resolved dataset to cognee ─────────────────────


@pytest.mark.asyncio
async def test_add_forwards_resolved_private_dataset_to_cognee(tmp_path: Any) -> None:
    """The full ``add`` path passes ``dataset="private:hermes-agent"``
    through to ``cognee.add`` instead of folding to ``shared``.

    Patches ``cognee.add`` + the chunk/embed pipeline so we only assert
    that the wrapper hands the resolved dataset to the engine. Sidecar
    SQLite write still lands so the same row is queryable via
    ``list_items`` (covered by the slow suite).
    """
    captured: dict[str, Any] = {}

    class _StubAddResult:
        dataset_id = "stub-ds-id"

    async def _fake_add(*args: Any, **kwargs: Any) -> _StubAddResult:
        # cognee.add(texts, dataset_name=..., node_set=...)
        captured["dataset_name"] = kwargs.get("dataset_name")
        captured["node_set"] = kwargs.get("node_set")
        return _StubAddResult()

    async def _fake_chunk_and_embed(self: Any, dataset: str) -> None:
        captured.setdefault("embed_datasets", []).append(dataset)

    async def _fake_latest_data_id(self: Any, dataset_name: str) -> str | None:
        return None

    # Stub the cognee module before construction so _configure_cognee
    # doesn't try to wire LanceDB + Kuzu.
    class _FakeConfig:
        def system_root_directory(self, *a: Any, **kw: Any) -> None:
            pass

        def data_root_directory(self, *a: Any, **kw: Any) -> None:
            pass

        def set_vector_db_provider(self, *a: Any, **kw: Any) -> None:
            pass

        def set_graph_database_provider(self, *a: Any, **kw: Any) -> None:
            pass

        def set_embedding_provider(self, *a: Any, **kw: Any) -> None:
            pass

    class _FakeCognee:
        config = _FakeConfig()

        @staticmethod
        async def add(*args: Any, **kwargs: Any) -> _StubAddResult:
            return await _fake_add(*args, **kwargs)

    with (
        patch("hal0.memory.cognee_wrapper._cognee", return_value=_FakeCognee),
        patch("hal0.memory.cognee_wrapper._clear_cognee_caches", return_value=None),
        patch.object(CogneeWrapper, "_chunk_and_embed", _fake_chunk_and_embed),
        patch.object(CogneeWrapper, "_latest_cognee_data_id", _fake_latest_data_id),
    ):
        w = CogneeWrapper(
            cognee_dir=tmp_path / "cognee",
            client_id="anonymous",
            private_mode=False,
        )
        out = await w.add(
            text="probe",
            dataset="private:hermes-agent",
            tags=[],
            source="hermes-agent",
            metadata={},
        )

    assert "id" in out
    # ✅ The contract: dataset_name reaching cognee.add is the
    # resolved string, NOT "shared".
    assert captured["dataset_name"] == "private:hermes-agent"
    assert captured["embed_datasets"] == ["private:hermes-agent"]
