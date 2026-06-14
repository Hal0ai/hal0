"""Shared pytest fixtures for ``tests/api/`` — module-level state isolation.

The composite ``hal0`` upstream introduced in PR-1 caches its aggregated
``/v1/models`` response in a module-level dict (``_HAL0_MODEL_CACHE``)
keyed by upstream name with a 5-second TTL. Because the cache lives at
module scope, it survives ``TestClient`` teardown — a slot configuration
seeded by one test leaks into the next under Python 3.12's collection
order (Python 3.11 happens to collect tests in an order that masks the
leak).

The helper ``_hal0_model_cache_clear`` is documented to support this
exact use case: "Tests also call this to keep state isolated between
cases." Wire it as an ``autouse`` fixture so every api test starts with
an empty cache, matching the documented contract.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import _hal0_model_cache_clear, create_app


@pytest.fixture(autouse=True)
def _reset_hal0_composite_model_cache() -> Iterator[None]:
    """Clear ``_HAL0_MODEL_CACHE`` before and after every api test."""
    _hal0_model_cache_clear()
    yield
    _hal0_model_cache_clear()


@pytest.fixture
def isolated_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    """TestClient whose lifespan resolves paths under tmp_hal0_home."""
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def isolated_app_client(tmp_hal0_home: str) -> Iterator[tuple[FastAPI, TestClient]]:
    """Like isolated_client, but also yields the app for state inspection."""
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield app, c
