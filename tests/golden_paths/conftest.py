"""Shared fixtures for the golden-path harness.

The whole harness drives the PUBLIC surface (FastAPI ``TestClient`` +
documented ``SlotManager`` routes) against an in-memory ``ContainerProvider``
double — real podman/systemd is never touched. The fake records every
``load_sync`` / ``unload_sync`` dispatch so deploy-only steps (unit start /
stop, container create / remove) can be asserted at the INTENT boundary
rather than by shelling out.

Two things this conftest owns that the api/slots conftests keep local:

* ``fake_container`` — a stateful ContainerProvider double whose ``active``
  set survives across two ``create_app()`` cycles in one test. That is what
  makes the "API restart" scenario (#14) expressible at the interface: the
  containers keep running while the API process is rebuilt.
* ``client_factory`` — a callable that yields a fresh ``TestClient`` over a
  fresh ``create_app()``, so a single test can boot the API twice over the
  same persisted ``HAL0_HOME`` state (SQLite identity/port claims + the
  per-slot ``state.json``) and observe reconciliation.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from tests.fixtures.slot_probe import probe_slot_name


@pytest.fixture(autouse=True)
def _hermetic_port_listeners(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blind the port registry to the HOST's real sockets.

    ``hal0.ports`` counts live listeners as claims (correct on a real box),
    but tests asserting auto-assigned ports would otherwise depend on
    whatever happens to be listening on the CI machine. Mirrors the same
    fixture in ``tests/api/conftest.py`` (not inherited across sibling
    packages).
    """
    from hal0 import ports as _ports

    monkeypatch.setattr(_ports, "_listener_claims", lambda start, end: [])


class FakeContainerProvider:
    """In-memory ContainerProvider double mirroring the surface SlotManager
    touches, with per-call recording.

    State is intentionally mutable and long-lived so a test can:

      * drive load/unload through the API and inspect the recorded
        ``load_calls`` / ``unload_calls`` (the deploy intent boundary), and
      * keep a slot's unit "running" (an entry in ``active``) across an API
        restart, modelling containers that outlive the ``hal0-api`` process.

    Mirrors the established ``tests/slots`` / ``tests/api`` fakes so the
    strongest existing stub pattern is reused rather than reinvented.
    """

    def __init__(self) -> None:
        self.active: set[str] = set()
        self.load_calls: list[dict[str, Any]] = []
        self.unload_calls: list[dict[str, Any]] = []
        self.healthy: bool = True

    # — dispatch (sync, executor-run in production) —

    def load_sync(self, cfg: dict[str, Any], model_info: dict[str, Any]) -> None:
        self.load_calls.append(dict(cfg))
        self.active.add(str(cfg.get("name")))

    def unload_sync(self, cfg: dict[str, Any]) -> None:
        self.unload_calls.append(dict(cfg))
        self.active.discard(str(cfg.get("name")))

    # — probes —

    def is_active(self, slot: Any) -> bool:
        # Production passes the slot CONFIG (#1417); this double keys by name.
        return probe_slot_name(slot) in self.active

    async def wait_ready(self, port: int, timeout_s: float | None = None) -> None:
        return None

    async def health(self, port: int, slot_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": self.healthy}

    # — slot_view enrichment extras —

    def running_image(self, slot: Any) -> str | None:
        return None

    def running_argv(self, slot: Any) -> list[str] | None:
        return None

    def expected_argv(
        self, slot_cfg: dict[str, Any], model_info: dict[str, Any]
    ) -> list[str] | None:
        return None

    def image_present(self, image: str) -> bool:
        return True


@pytest.fixture
def fake_container(monkeypatch: pytest.MonkeyPatch) -> FakeContainerProvider:
    """Patch the process-wide ``container_provider`` factory with the fake.

    SlotManager looks up ``container_provider`` lazily from
    ``hal0.providers.container`` inside each dispatch method, so patching the
    module attribute covers every call site — including a second
    ``create_app()`` built later in the same test (restart scenario).
    """
    fake = FakeContainerProvider()
    monkeypatch.setattr(
        "hal0.providers.container.container_provider",
        lambda: fake,
    )
    return fake


@pytest.fixture
def client_factory(
    tmp_hal0_home: str,
) -> Iterator[Callable[[], contextlib.AbstractContextManager[TestClient]]]:
    """Yield a factory that boots a fresh ``TestClient`` over a fresh app.

    ``create_app()`` must run AFTER ``tmp_hal0_home`` has set ``HAL0_HOME``
    (the top-level fixture does), so the app resolves every path — the
    slots TOML tree, ``state.json``, and the SQLite identity / port-claim
    stores — under the isolated temp home. Calling the factory twice in one
    test rebuilds the API over the SAME persisted home, which is exactly an
    API restart from the caller's point of view.
    """

    @contextlib.contextmanager
    def _make() -> Iterator[TestClient]:
        app = create_app()
        with TestClient(app) as client:
            yield client

    yield _make


# The canonical create body used across scenarios: a GPU/vulkan llama-server
# slot bound to a catalogue model. The model need not be registry-resolvable
# for create/load — the load route only pre-validates an EXPLICIT model_id, and
# a bodyless /load falls back to the slot's TOML default.
SLOT_CREATE_BODY: dict[str, Any] = {
    "model": "qwen3-4b-q4_k_m",
    "device": "gpu-vulkan",
    "provider": "llama-server",
    "runtime": "container",
    "profile": "vulkan-radv",
}


def make_create_body(name: str, **overrides: Any) -> dict[str, Any]:
    """Return a POST /api/slots body for ``name`` with optional overrides."""
    return {"name": name, **SLOT_CREATE_BODY, **overrides}
