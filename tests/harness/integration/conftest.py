"""Shared fixtures for the v0.3 persona δ-harness integration tests.

An isolated hal0-api client per test.

Lives in ``tests/harness/integration/`` so the δ-tier suite can be
filtered out of the normal ``pytest tests/`` collection if needed —
``pytest tests/harness/integration/`` runs only these. They DO get
picked up by ``pytest tests/`` though, because the master plan §4
PR-11 requires them in the default test pass.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def harness_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """TestClient pointed at a fresh hal0-api with isolated state.

    Sets:
      * ``HAL0_AGENT_SECRET_PATH`` — fresh HMAC secret per test
      * ``HAL0_HOME`` — fresh /etc/hal0 + /var/lib/hal0 surrogate
      * ``HAL0_ALLOWED_ORIGINS`` — only 127.0.0.1:8080 (tight default)
    """
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    (tmp_path / "hal0_home" / "etc" / "hal0").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "http://127.0.0.1:8080")

    from hal0.api import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
