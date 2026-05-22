"""Pytest fixtures for the Cognee wrapper.

These tests stand up a real, isolated Cognee install per test —
fastembed + LanceDB + Kuzu — and shut it down on teardown. The
embedding model (bge-small-en-v1.5, 70 MB) is cached under
``~/.cache/huggingface`` by fastembed across tests; the first
test run downloads it, subsequent runs are warm.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _cognee_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point Cognee at the test's tmp dir + opt out of multi-user RBAC.

    Cognee reads its env vars (LLM_API_KEY, EMBEDDING_*,
    ENABLE_BACKEND_ACCESS_CONTROL, COGNEE_SKIP_CONNECTION_TEST) at
    config time, so we plant them BEFORE the wrapper constructor
    runs (the wrapper sets them too, but autouse keeps the values
    sticky for any helper that imports cognee outside the wrapper).
    """
    # System-wide cognee log dir — out of the user's home, in tmp.
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    # Embedding model env match the wrapper's defaults — pinning here
    # so tests don't depend on the wrapper's exact constructor values.
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fastembed")
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "384")
    monkeypatch.setenv("HUGGINGFACE_TOKENIZER", "BAAI/bge-small-en-v1.5")
    monkeypatch.setenv("ENABLE_BACKEND_ACCESS_CONTROL", "false")
    monkeypatch.setenv("CACHING", "false")
    monkeypatch.setenv("COGNEE_SKIP_CONNECTION_TEST", "true")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-noop")


@pytest.fixture
def cognee_dir(tmp_path: Path) -> Path:
    """Per-test Cognee storage root.

    Returned as a ``Path`` so tests can poke at the sidecar SQLite
    directly when they need to assert on what the wrapper wrote.
    """
    p = tmp_path / "cognee"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def reset_cognee_singletons() -> Iterator[None]:
    """Reset cognee's process-wide globals between tests.

    Cognee caches its config + DB engine handles on module-level
    pydantic-settings singletons. Between tests we point the
    wrapper at a NEW directory, so the cached engines (which still
    hold the previous test's paths) lie about where data lives.
    Easiest fix: drop cognee + its submodules and let the next
    wrapper construction re-import.
    """
    yield
    drop = [m for m in sys.modules if m == "cognee" or m.startswith("cognee.")]
    for m in drop:
        del sys.modules[m]
    # Also clear the wrapper's cached cognee handle so the next
    # construction re-imports.
    if "hal0.memory.cognee_wrapper" in sys.modules:
        wrap_mod = sys.modules["hal0.memory.cognee_wrapper"]
        wrap_mod._COGNEE = None  # type: ignore[attr-defined]


# Note: there's no ``captured_audit_events`` fixture anymore. Cognee's
# ``setup_logging`` (fires on the first ``add``) calls
# ``structlog.configure`` itself and blows away anything a fixture
# installs on the processor chain — including ``capture_logs``. The
# wrapper instead mirrors every audit event to ``CogneeWrapper.audit_tail``,
# which tests inspect directly. The structlog channel remains the
# production audit surface (journald reads it).
