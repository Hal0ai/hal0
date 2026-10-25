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

from collections import namedtuple
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import _hal0_model_cache_clear, create_app
from hal0.capabilities import catalog as capability_catalog
from hal0.comfyui import install as comfyui_install


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


@pytest.fixture(autouse=True)
def _hermetic_port_listeners(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blind the port registry to the HOST's real sockets.

    hal0.ports counts live listeners as claims (correct on a real box —
    auto-assign must not hand out a port something is bound to), but unit
    tests asserting exact auto-assigned ports would otherwise depend on
    whatever happens to be listening on the dev/CI machine.
    """
    from hal0 import ports as _ports

    monkeypatch.setattr(_ports, "_listener_claims", lambda start, end: [])


_Usage = namedtuple("_Usage", "total used free")


@pytest.fixture
def image_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A host that can install image models, all of it under ``tmp_path``.

    The model store, the ComfyUI data root and the download staging dir share ``tmp_path`` (so
    the same-filesystem guard passes anywhere), the disk reports far more free space than any
    model needs, and the ROCm lane is present (``comfyui_backends()`` is not empty).
    """
    root = tmp_path / "store"
    monkeypatch.setenv("HAL0_MODEL_STORE", str(root))
    monkeypatch.delenv("HAL0_COMFYUI_DATA_ROOT", raising=False)
    monkeypatch.setattr(
        comfyui_install.shutil, "disk_usage", lambda path: _Usage(10**16, 0, 10**16)
    )
    monkeypatch.setattr(capability_catalog, "comfyui_backends", lambda: ["gpu-vulkan"])
    return root
